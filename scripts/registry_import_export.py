#!/usr/bin/env python3
"""Tenant-scoped Sealfleet registry import/export helper.

The runtime endpoint redacts sensitive fields before export; this script only
transports JSON between the operator and the router. Do not use shell tracing
with API keys.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _request(method: str, url: str, api_key: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    req.add_header("X-API-Key", api_key)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 operator-supplied URL
            payload = resp.read().decode("utf-8")
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code} from {url}: {payload}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Export/import tenant-scoped Sealfleet registry JSON")
    parser.add_argument("--runtime-url", default="http://localhost:8040", help="Runtime router base URL")
    parser.add_argument("--api-key", required=True, help="API key with registry.export or registry.import permission")
    sub = parser.add_subparsers(dest="command", required=True)

    export_cmd = sub.add_parser("export", help="Export authenticated tenant registry metadata")
    export_cmd.add_argument("--output", "-o", required=True, help="Path to write JSON bundle")

    import_cmd = sub.add_parser("import", help="Validate/apply a registry JSON bundle")
    import_cmd.add_argument("--input", "-i", required=True, help="Path to read JSON bundle")
    import_cmd.add_argument("--apply", action="store_true", help="Apply changes; default is dry-run validation")

    args = parser.parse_args()
    base = args.runtime_url.rstrip("/")

    if args.command == "export":
        result = _request("GET", f"{base}/registry/export", args.api_key)
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"status": "exported", "output": str(out), "summary": {
            "manifests": len(result.get("manifests", [])),
            "typed_manifests": len(result.get("typed_manifests", [])),
            "pipelines": len(result.get("pipelines", [])),
        }}, indent=2))
        return 0

    bundle = json.loads(Path(args.input).read_text())
    result = _request("POST", f"{base}/registry/import?dry_run={'false' if args.apply else 'true'}", args.api_key, bundle)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result.get("summary", {}).get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
