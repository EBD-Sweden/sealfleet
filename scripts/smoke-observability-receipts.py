#!/usr/bin/env python3
"""Operator smoke for Sealfleet trace/audit receipts.

Runs bounded checks against local services and prints receipt-shaped outputs. It is
safe to run in dev/public-test: audit reads require an explicit runtime API key.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

RUNTIME_URL = os.getenv("RUNTIME_URL", "http://localhost:8040").rstrip("/")
REGISTRY_URL = os.getenv("REGISTRY_URL", "http://localhost:8010").rstrip("/")
DEPLOY_URL = os.getenv("DEPLOY_URL", "http://localhost:8030").rstrip("/")
RUNTIME_API_KEY = os.getenv("RUNTIME_API_KEY", "")


def request_json(method: str, url: str, *, body: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any] | str]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req_headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed: dict[str, Any] | str = json.loads(raw)
        except Exception:
            parsed = raw
        return exc.code, parsed
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def print_receipt(label: str, value: Any) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(value, indent=2, sort_keys=True) if not isinstance(value, str) else value)


def main() -> int:
    failures: list[str] = []

    for service, url in {
        "runtime.ready": f"{RUNTIME_URL}/ready",
        "registry.ready": f"{REGISTRY_URL}/ready",
        "deploy.ready": f"{DEPLOY_URL}/ready",
    }.items():
        status, payload = request_json("GET", url)
        print_receipt(service, {"http_status": status, "payload": payload})
        if status != 200:
            failures.append(f"{service} failed at {url}; start the service or set *_URL env vars")

    if not RUNTIME_API_KEY:
        failures.append("RUNTIME_API_KEY not set; skipping authenticated pipeline/audit receipt smoke")
    else:
        headers = {"X-API-Key": RUNTIME_API_KEY, "X-Trace-Id": "smoke-trace-observability"}
        pipeline_body = {
            "steps": [
                {"mcp": os.getenv("SMOKE_MCP", "weather-mcp"), "tool": os.getenv("SMOKE_TOOL", "get_weather"), "inputs": {"location": "Stockholm"}}
            ]
        }
        status, payload = request_json("POST", f"{RUNTIME_URL}/pipeline", body=pipeline_body, headers=headers)
        print_receipt("runtime.pipeline", {"http_status": status, "payload": payload})
        if status not in {200, 400, 403, 409}:
            failures.append("pipeline smoke returned unexpected status; verify RUNTIME_API_KEY and demo MCP manifest")

        status, payload = request_json("GET", f"{RUNTIME_URL}/audit/events?limit=10", headers=headers)
        print_receipt("runtime.audit.events", {"http_status": status, "payload": payload})
        if status != 200:
            failures.append("audit read failed; key must include audit.read and no anonymous audit endpoint is used")
        elif isinstance(payload, dict):
            events = payload.get("events", [])
            if not any(event.get("trace_id") for event in events):
                failures.append("audit endpoint returned no trace_id receipts in latest events")

    if failures:
        print_receipt("actionable_diagnostics", failures)
        return 1
    print("\nobservability smoke passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
