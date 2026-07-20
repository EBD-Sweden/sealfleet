#!/usr/bin/env python3
"""Sealfleet MCP server Command Line Interface.

Canonical CLI module for project-scoped runtime/router operations. It maps to
real Sealfleet runtime endpoints and fails non-zero when a backend/control-plane
operation cannot be reached instead of returning fake success.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_URL = "http://localhost:8040"
DEFAULT_DEPLOY_URL = "http://localhost:8030"
CONFIG_SCHEMA = "mcpfinder.cli.config/v1"
CONTRACT_SCHEMA = "mcpfinder.cli.contract/v1"
# Scopes an agent config may declare. Additive over time; membership is what the
# contract and validation assert, never an exact-set equality.
_ALLOWED_SCOPES = {
    "runtime",
    "registry",
    "control-plane",
    "portal",
    "mcps",
    "docs",
    "scripts",
    "deploy",
    "cluster",
}
# Extra name-substrings that mark a config as belonging to a DIFFERENT product and
# must be rejected. Empty by default (the product!="mcpfinder" check below is the
# real guard); operators running a multi-product fleet can set
# MCPFINDER_CROSS_PROJECT_MARKERS=foo,bar to also reject by name. No project names
# are hardcoded here so the public kit stays product-neutral.
_CROSS_PROJECT_MARKERS = tuple(
    m.strip().lower()
    for m in os.environ.get("MCPFINDER_CROSS_PROJECT_MARKERS", "").split(",")
    if m.strip()
)
_SECRET_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "password",
    "passwd",
    "pass",
    "pwd",
    "secret",
    "token",
    "credential",
    "private_key",
    "access_key",
    "secret_key",
    "connection_string",
    "dsn",
)
LOCAL_DEMO_CALL = {"mcp": "demo-sandbox-mcp", "tool": "get_demo_customer", "inputs": {"customer_id": "cust_123"}}
_CLUSTER_MODES = ("local", "k3d", "remote")
_DEFAULT_K3D_CLUSTER = "mcpfinder"
# Local dev start/stop is driven by the repo's start-local.sh; k3d brings up a
# real container cluster. Both must fail honestly when their tooling is absent.
_START_LOCAL_SCRIPT = ROOT / "scripts" / "start-local.sh"
_DEV_LOCAL_KUSTOMIZE = ROOT / "k8s" / "dev-local"


class CliError(Exception):
    """Structured CLI error."""

    def __init__(self, code: str, message: str, *, target: str = "", detail: Any | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.target = target
        self.detail = detail

    def to_payload(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.target:
            error["target"] = self.target
        if self.detail is not None:
            error["detail"] = _redact(self.detail)
        return {"ok": False, "error": error}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower().replace("-", "_")
            if lowered == "secrets" and isinstance(item, str) and item.startswith("never pass raw secrets"):
                result[key] = item
            elif any(part in lowered for part in _SECRET_KEY_PARTS):
                result[key] = "[REDACTED]"
            else:
                result[key] = _redact(item)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _print_payload(payload: dict[str, Any], *, as_json: bool) -> None:
    payload = _redact(payload)
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    if payload.get("ok") is False:
        err = payload["error"]
        target = f" ({err['target']})" if err.get("target") else ""
        print(f"ERROR {err['code']}: {err['message']}{target}", file=sys.stderr)
        return
    print(json.dumps(payload, indent=2, sort_keys=True))


def _load_document(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text()
    except OSError as exc:
        raise CliError("config_unreadable", f"Could not read {path}: {exc}", target=str(path)) from exc
    try:
        if path.suffix.lower() in {".yaml", ".yml"}:
            try:
                import yaml  # type: ignore[import-not-found]
            except Exception as exc:  # pragma: no cover - depends on packaging
                raise CliError("dependency_missing", "PyYAML is required for YAML config; use JSON or install pyyaml", target=str(path)) from exc
            data = yaml.safe_load(text)
        else:
            data = json.loads(text)
    except CliError:
        raise
    except Exception as exc:
        raise CliError("config_invalid", f"Could not parse {path}: {exc}", target=str(path)) from exc
    if not isinstance(data, dict):
        raise CliError("config_invalid", "Document root must be a JSON/YAML object", target=str(path))
    return data


def _validate_project_markers(data: dict[str, Any], *, target: str) -> None:
    product = str(data.get("product", "mcpfinder")).lower()
    name = str(data.get("name", "")).lower()
    if product != "mcpfinder" or any(marker in name for marker in _CROSS_PROJECT_MARKERS):
        raise CliError(
            "project_scope_violation",
            "CLI config must be scoped to product=mcpfinder and must not reference other products' surfaces",
            target=target,
            detail={"product": data.get("product"), "name": data.get("name")},
        )


def validate_config(path: Path) -> dict[str, Any]:
    data = _load_document(path)
    _validate_project_markers(data, target=str(path))
    if data.get("schema") != CONFIG_SCHEMA:
        raise CliError("schema_invalid", f"Expected schema {CONFIG_SCHEMA}", target=str(path), detail={"schema": data.get("schema")})
    runtime_url = data.get("runtime_url")
    if not isinstance(runtime_url, str) or not runtime_url.startswith(("http://", "https://")):
        raise CliError("config_invalid", "runtime_url must be an http(s) URL", target=str(path))
    scopes = data.get("allowed_scopes", [])
    if not isinstance(scopes, list) or not all(isinstance(item, str) for item in scopes):
        raise CliError("config_invalid", "allowed_scopes must be a list of strings", target=str(path))
    unknown = sorted(set(scopes) - _ALLOWED_SCOPES)
    if unknown:
        raise CliError("config_invalid", "allowed_scopes contains unsupported scopes", target=str(path), detail={"unknown_scopes": unknown})
    # Optional keys (additive — do not change the required set). deploy_url is the
    # separate deploy service (:8030); validated like runtime_url when present.
    deploy_url = data.get("deploy_url")
    if deploy_url is not None and (not isinstance(deploy_url, str) or not deploy_url.startswith(("http://", "https://"))):
        raise CliError("config_invalid", "deploy_url must be an http(s) URL", target=str(path))
    cluster_mode = data.get("cluster_mode")
    if cluster_mode is not None and cluster_mode not in _CLUSTER_MODES:
        raise CliError(
            "config_invalid",
            f"cluster_mode must be one of {sorted(_CLUSTER_MODES)}",
            target=str(path),
            detail={"cluster_mode": cluster_mode},
        )
    kube_context = data.get("kube_context")
    if kube_context is not None and not isinstance(kube_context, str):
        raise CliError("config_invalid", "kube_context must be a string", target=str(path))
    redacted = _redact(data)
    serialized = json.dumps(redacted, sort_keys=True)
    config_out: dict[str, Any] = {
        "schema": data["schema"],
        "product": data.get("product"),
        "runtime_url": runtime_url,
        "allowed_scopes": scopes,
    }
    if deploy_url is not None:
        config_out["deploy_url"] = deploy_url
    if cluster_mode is not None:
        config_out["cluster_mode"] = cluster_mode
    if kube_context is not None:
        config_out["kube_context"] = kube_context
    return {
        "ok": True,
        "command": "validate",
        "config": config_out,
        "redacted": serialized != json.dumps(data, sort_keys=True),
    }


def contract_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "contract": {
            "schema": CONTRACT_SCHEMA,
            "product": "mcpfinder",
            "entrypoint": "python -m runtime.cli",
            "script_wrapper": "scripts/mcpfinder-cli",
            "definition": "Sealfleet CLI is the MCP server Command Line Interface for project-scoped agent and operator workflows.",
            "commands": [
                "contract",
                "validate",
                "status",
                "invoke",
                "registry",
                "manifest",
                "smoke",
                "cluster",
                "mcp",
                "pipeline",
                "workflow",
            ],
            "config_schema": {
                "schema": CONFIG_SCHEMA,
                "required": ["schema", "product", "runtime_url", "allowed_scopes"],
                "optional": ["deploy_url", "kube_context", "cluster_mode"],
                "allowed_scopes": sorted(_ALLOWED_SCOPES),
                "cluster_modes": list(_CLUSTER_MODES),
                "env": [
                    "MCPFINDER_RUNTIME_URL",
                    "MCPFINDER_DEPLOY_URL",
                    "MCPFINDER_KUBE_CONTEXT",
                    "MCPFINDER_API_KEY",
                ],
            },
            "runtime_api": {
                "status": ["GET /health", "GET /ready"],
                "invoke": "POST /call {mcp, tool, inputs}",
                "registry_export": "GET /registry/export",
                "registry_import": "POST /registry/import?dry_run=<bool>",
                "manifest_list": "GET /manifests",
                "manifest_get": "GET /manifests/{name}",
                "manifest_register": "POST /manifests or /manifests/typed",
                "pipeline_list": ["GET /pipelines", "GET /v2/pipelines"],
                "pipeline_get": ["GET /pipelines/{name}", "GET /v2/pipelines/{name}", "GET /pipelines/{name}/type-check"],
                "pipeline_deploy": ["POST /v2/pipelines/deploy {yaml}", "POST /pipelines/register {pipeline}"],
                "pipeline_run": ["POST /v2/pipelines/run {pipeline, inputs}", "POST /pipelines/{name}/run {inputs}"],
                "pipeline_reload": "POST /pipelines/reload",
                "job_create": "POST /jobs {pipeline, inputs, name}",
                "job_get": "GET /jobs/{job_id}",
                "job_list": "GET /jobs?status=&pipeline=&limit=",
                "job_cancel": "POST /jobs/{job_id}/cancel",
                "local_demo_smoke": ["GET /health", "GET /ready", "GET /manifests", "POST /call demo-sandbox-mcp.get_demo_customer"],
            },
            "deploy_api": {
                "service": "mcpfinder deploy service is separate from the router; default :8030",
                "env": ["MCPFINDER_DEPLOY_URL"],
                "mcp_deploy": "POST /deploy (text/event-stream) {repo_url, branch, name, description, tags, port, is_public, env_vars}",
                "mcp_list": "GET /deployments",
                "mcp_get": "GET /deployments/{name}",
                "status": ["GET /health", "GET /ready"],
            },
            "cluster": {
                "modes": list(_CLUSTER_MODES),
                "local": "scripts/start-local.sh starts/stops/reports host services",
                "k3d": ["k3d cluster create/delete <name>", "kubectl apply -k k8s/dev-local/", "kubectl get deploy -l part-of=mcpfinder"],
                "tooling": ["k3d", "kubectl", "docker"],
                "guards": "cluster down requires --yes; k3d delete requires an mcpfinder-scoped cluster name unless --force",
            },
            "workflow_model": {
                "summary": "workflow is a CLI facade over v1 named pipelines + jobs; there is no separate workflow primitive in the runtime. pipeline=v2 templated + synchronous; workflow=v1 named + async job.",
                "engine_note": "POST /jobs resolves ONLY v1 named pipelines, so workflow create/deploy/run default to v1. Use the pipeline group for v2 templated pipelines (synchronous run).",
                "create": "scaffold a v1 named-pipeline definition file locally (v1 default; --engine v2 for a v2 scaffold to run via the pipeline group)",
                "deploy": "same as pipeline deploy (v1 -> POST /pipelines/register; v2 -> POST /v2/pipelines/deploy)",
                "run": "submit a durable async job for a v1 named pipeline (POST /jobs) and return a job_id; distinct from synchronous pipeline run",
                "status": "GET /jobs/{job_id} (or GET /jobs to list)",
                "cancel": "POST /jobs/{job_id}/cancel",
            },
            "agent_contract": {
                "project_scope": "mcpfinder only; no cross-project CLI naming or config bleed",
                "secrets": "never pass raw secrets in prompts or payload logs",
                "failure_semantics": "control-plane calls return not_implemented/backend_unavailable/auth_missing errors instead of success-looking stubs",
                "auditability": "runtime calls should preserve trace_id/audit events returned by the router",
            },
        },
    }


def _cli_config_path() -> Path:
    """Persisted CLI config location (XDG-aware). Written only by `cluster connect --save`."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "mcpfinder" / "cli.config.json"


def _load_cli_config() -> dict[str, Any]:
    """Load the persisted CLI config tolerantly. Never raises; absent/invalid -> {}."""
    path = _cli_config_path()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_cli_config(values: dict[str, Any]) -> Path:
    """Merge non-empty, non-secret values into the persisted CLI config and return its path."""
    path = _cli_config_path()
    current = _load_cli_config()
    for key, value in values.items():
        if value:
            current[key] = value
    # Defensive: never persist secret-looking keys.
    current = {k: v for k, v in current.items() if not any(part in str(k).lower().replace("-", "_") for part in _SECRET_KEY_PARTS)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2, sort_keys=True))
    return path


def _runtime_url(args: argparse.Namespace) -> str:
    value = (
        getattr(args, "runtime_url", None)
        or os.environ.get("MCPFINDER_RUNTIME_URL")
        or _load_cli_config().get("runtime_url")
        or DEFAULT_RUNTIME_URL
    )
    return str(value).rstrip("/")


def _deploy_url(args: argparse.Namespace) -> str:
    value = (
        getattr(args, "deploy_url", None)
        or os.environ.get("MCPFINDER_DEPLOY_URL")
        or _load_cli_config().get("deploy_url")
        or DEFAULT_DEPLOY_URL
    )
    return str(value).rstrip("/")


def _kube_context(args: argparse.Namespace) -> str | None:
    value = (
        getattr(args, "kube_context", None)
        or os.environ.get("MCPFINDER_KUBE_CONTEXT")
        or _load_cli_config().get("kube_context")
    )
    return str(value) if value else None


def _which(tool: str) -> str:
    """Resolve a required external tool, failing honestly if it is not installed."""
    path = shutil.which(tool)
    if not path:
        raise CliError("dependency_missing", f"Required tool not found on PATH: {tool}", target=tool)
    return path


def _run_tool(cmd: list[str], *, timeout: float = 120.0, check: bool = True) -> dict[str, Any]:
    """Run an external command, capturing redacted output. Fails honestly on error.

    Never echoes the process environment; only the (redacted) stdout/stderr the tool emits.
    """
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise CliError("dependency_missing", f"Command not found: {cmd[0]}", target=cmd[0]) from exc
    except subprocess.TimeoutExpired as exc:
        raise CliError("backend_unavailable", f"Command timed out after {timeout}s: {cmd[0]}", target=cmd[0]) from exc
    result = {
        "command": cmd,
        "returncode": proc.returncode,
        "stdout": _redact_text(proc.stdout),
        "stderr": _redact_text(proc.stderr),
    }
    if check and proc.returncode != 0:
        raise CliError(
            "backend_error",
            f"Command failed ({proc.returncode}): {' '.join(cmd[:2])}",
            target=cmd[0],
            detail=result,
        )
    return result


def _redact_text(text: str) -> str:
    """Best-effort redaction of secret-looking KEY=VALUE / KEY: VALUE lines in tool output."""
    if not text:
        return text
    lines = []
    for line in text.splitlines():
        lowered = line.lower()
        if any(part in lowered for part in _SECRET_KEY_PARTS):
            for sep in ("=", ":"):
                if sep in line:
                    head, _, _tail = line.partition(sep)
                    line = f"{head}{sep} [REDACTED]"
                    break
        lines.append(line)
    return "\n".join(lines)


def _parse_kv_pairs(pairs: list[str] | None, *, kind: str = "env") -> dict[str, str]:
    """Parse repeatable KEY=VALUE flags into a dict. Values are redacted only on echo, not here."""
    out: dict[str, str] = {}
    for raw in pairs or []:
        if "=" not in raw:
            raise CliError("payload_invalid", f"--{kind} expects KEY=VALUE, got: {raw}")
        key, _, value = raw.partition("=")
        key = key.strip()
        if not key:
            raise CliError("payload_invalid", f"--{kind} key may not be empty: {raw}")
        out[key] = value
    return out


def _sse_request(url: str, body: dict[str, Any], *, api_key: str | None = None, timeout: float = 600.0) -> dict[str, Any]:
    """POST and consume a text/event-stream, accumulating redacted events until done/error.

    Fails honestly (backend_unavailable/backend_error) rather than hanging or faking success.
    """
    headers = {"Accept": "text/event-stream", "Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    events: list[dict[str, Any]] = []
    final: dict[str, Any] | None = None
    errored = False
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 # nosec B310 — operator-provided URL by CLI design
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if not payload:
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    event = {"message": payload[:500]}
                event = _redact(event)
                events.append(event)
                step = str(event.get("step") or "").lower()
                status = str(event.get("status") or "").lower()
                # Any error status is a terminal failure (the deploy service emits a
                # fatal {step:<stage>, status:error} then ends the stream). step==done
                # is terminal success; step==error is the top-level catch.
                if status in {"error", "failed"} or step == "error":
                    final, errored = event, True
                    break
                if step == "done":
                    final = event
                    break
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise CliError("backend_error", f"Deploy service returned HTTP {exc.code}", target=url, detail={"status": exc.code, "body": raw[:500]}) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CliError("backend_unavailable", f"Deploy service unavailable: {exc}", target=url) from exc
    if final is None and events:
        final = events[-1]
    return {"events": events, "final": final, "event_count": len(events), "errored": errored}


def _api_key(args: argparse.Namespace) -> str | None:
    value = getattr(args, "api_key", None) or os.environ.get("MCPFINDER_API_KEY")
    return str(value) if value else None


def _auth_required(args: argparse.Namespace, operation: str) -> str:
    api_key = _api_key(args)
    if not api_key:
        raise CliError("auth_missing", f"{operation} requires --api-key or MCPFINDER_API_KEY")
    return api_key


def _json_payload(args: argparse.Namespace) -> dict[str, Any]:
    if getattr(args, "payload_file", None):
        data = _load_document(Path(args.payload_file))
    else:
        try:
            data = json.loads(args.payload or "{}")
        except json.JSONDecodeError as exc:
            raise CliError("payload_invalid", f"Payload must be a JSON object: {exc}") from exc
    if not isinstance(data, dict):
        raise CliError("payload_invalid", "Payload must be a JSON object")
    return data


def _request(method: str, url: str, *, api_key: str | None = None, body: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    data: bytes | None = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 # nosec B310 — operator-provided URL by CLI design
            raw = resp.read().decode("utf-8")
            parsed = json.loads(raw) if raw else {}
            return {"status": resp.status, "body": _redact(parsed)}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed: Any = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = raw[:500]
        raise CliError("backend_error", f"Runtime returned HTTP {exc.code}", target=url, detail={"status": exc.code, "body": parsed}) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CliError("backend_unavailable", f"Runtime backend unavailable: {exc}", target=url) from exc


def command_status(args: argparse.Namespace) -> dict[str, Any]:
    base = _runtime_url(args)
    health = _request("GET", f"{base}/health", timeout=args.timeout)
    ready = _request("GET", f"{base}/ready", timeout=args.timeout)
    return {"ok": True, "command": "status", "runtime_url": base, "health": health, "ready": ready}


def command_invoke(args: argparse.Namespace) -> dict[str, Any]:
    payload = _json_payload(args)
    request = {"mcp": args.mcp, "tool": args.tool, "inputs": payload}
    if args.dry_run:
        return {"ok": True, "command": "invoke", "dry_run": True, "request": _redact(request)}
    api_key = _auth_required(args, "invoke")
    base = _runtime_url(args)
    result = _request("POST", f"{base}/call", api_key=api_key, body=request, timeout=args.timeout)
    return {"ok": True, "command": "invoke", "runtime_url": base, "response": result}


def command_registry_export(args: argparse.Namespace) -> dict[str, Any]:
    api_key = _auth_required(args, "registry export")
    base = _runtime_url(args)
    result = _request("GET", f"{base}/registry/export", api_key=api_key, timeout=args.timeout)
    if args.output:
        out = Path(args.output)
        out.write_text(json.dumps(result["body"], indent=2, sort_keys=True))
        return {"ok": True, "command": "registry export", "runtime_url": base, "output": str(out), "status": result["status"]}
    return {"ok": True, "command": "registry export", "runtime_url": base, "response": result}


def command_registry_import(args: argparse.Namespace) -> dict[str, Any]:
    api_key = _auth_required(args, "registry import")
    bundle = _load_document(Path(args.input))
    base = _runtime_url(args)
    result = _request("POST", f"{base}/registry/import?dry_run={str(args.dry_run).lower()}", api_key=api_key, body=bundle, timeout=args.timeout)
    return {"ok": True, "command": "registry import", "runtime_url": base, "dry_run": args.dry_run, "response": result}


def command_manifest_list(args: argparse.Namespace) -> dict[str, Any]:
    base = _runtime_url(args)
    result = _request("GET", f"{base}/manifests", api_key=_api_key(args), timeout=args.timeout)
    return {"ok": True, "command": "manifest list", "runtime_url": base, "response": result}


def command_manifest_get(args: argparse.Namespace) -> dict[str, Any]:
    base = _runtime_url(args)
    result = _request("GET", f"{base}/manifests/{args.name}", api_key=_api_key(args), timeout=args.timeout)
    return {"ok": True, "command": "manifest get", "runtime_url": base, "name": args.name, "response": result}


def command_manifest_register(args: argparse.Namespace) -> dict[str, Any]:
    manifest = _load_document(Path(args.file))
    base = _runtime_url(args)
    path = "/manifests/typed" if args.typed else "/manifests"
    if args.dry_run:
        return {"ok": True, "command": "manifest register", "dry_run": True, "target": f"{base}{path}", "manifest": _redact(manifest)}
    api_key = _auth_required(args, "manifest register")
    result = _request("POST", f"{base}{path}", api_key=api_key, body=manifest, timeout=args.timeout)
    return {"ok": True, "command": "manifest register", "runtime_url": base, "response": result}


def command_smoke_local_demo(args: argparse.Namespace) -> dict[str, Any]:
    checks = [
        {"method": "GET", "path": "/health"},
        {"method": "GET", "path": "/ready"},
        {"method": "GET", "path": "/manifests"},
        {"method": "POST", "path": "/call", "body": LOCAL_DEMO_CALL},
    ]
    base = _runtime_url(args)
    if args.dry_run:
        return {"ok": True, "command": "smoke local-demo", "dry_run": True, "runtime_url": base, "checks": checks}
    api_key = _auth_required(args, "smoke local-demo")
    results = []
    for check in checks:
        results.append(
            {
                "check": check,
                "response": _request(
                    check["method"],
                    f"{base}{check['path']}",
                    api_key=api_key if check["path"] == "/call" else None,
                    body=check.get("body"),
                    timeout=args.timeout,
                ),
            }
        )
    return {"ok": True, "command": "smoke local-demo", "dry_run": False, "runtime_url": base, "results": results}


# ---------------------------------------------------------------------------
# Cluster lifecycle (local host services or k3d). Shells out to real tooling and
# fails honestly when tooling/backends are absent — never fakes success.
# ---------------------------------------------------------------------------


def _inputs_payload(args: argparse.Namespace) -> dict[str, Any]:
    """Resolve --inputs (JSON string) / --inputs-file into a dict."""
    if getattr(args, "inputs_file", None):
        data = _load_document(Path(args.inputs_file))
    else:
        try:
            data = json.loads(getattr(args, "inputs", None) or "{}")
        except json.JSONDecodeError as exc:
            raise CliError("payload_invalid", f"--inputs must be a JSON object: {exc}") from exc
    if not isinstance(data, dict):
        raise CliError("payload_invalid", "Inputs must be a JSON object")
    return data


def command_cluster_create(args: argparse.Namespace) -> dict[str, Any]:
    mode = args.mode
    if mode == "remote":
        raise CliError("config_invalid", "cluster create supports --mode local|k3d; use 'cluster connect' for remote backends")
    if mode == "local":
        cmd = ["bash", str(_START_LOCAL_SCRIPT)]
        if args.bg:
            cmd.append("--bg")
        plan = {"mode": "local", "commands": [cmd]}
        if args.dry_run:
            return {"ok": True, "command": "cluster create", "dry_run": True, **plan}
        result = _run_tool(cmd, timeout=args.timeout)
        return {"ok": True, "command": "cluster create", "mode": "local", "result": result}
    # k3d mode
    name = args.name or _DEFAULT_K3D_CLUSTER
    create_cmd = ["k3d", "cluster", "create", name]
    apply_cmd = ["kubectl", "apply", "-k", str(_DEV_LOCAL_KUSTOMIZE)]
    plan = {"mode": "k3d", "name": name, "commands": [create_cmd, apply_cmd], "tooling": ["k3d", "kubectl", "docker"]}
    if args.dry_run:
        return {"ok": True, "command": "cluster create", "dry_run": True, **plan}
    for tool in ("k3d", "kubectl", "docker"):
        _which(tool)
    results = [_run_tool(create_cmd, timeout=args.timeout), _run_tool(apply_cmd, timeout=args.timeout)]
    return {"ok": True, "command": "cluster create", "mode": "k3d", "name": name, "results": results}


def command_cluster_connect(args: argparse.Namespace) -> dict[str, Any]:
    runtime_url = _runtime_url(args)
    deploy_url = _deploy_url(args)
    kube_context = _kube_context(args)
    plan: dict[str, Any] = {
        "mode": args.mode,
        "runtime_url": runtime_url,
        "deploy_url": deploy_url,
        "kube_context": kube_context,
        "probes": [f"{runtime_url}/health", f"{deploy_url}/health"],
    }
    if args.mode == "k3d" and kube_context:
        plan["kubectl_verify"] = ["kubectl", "--context", kube_context, "cluster-info"]
    if args.dry_run:
        return {"ok": True, "command": "cluster connect", "dry_run": True, **plan}
    verify: dict[str, Any] = {}
    if args.mode == "k3d" and kube_context:
        _which("kubectl")
        verify["kubectl"] = _run_tool(["kubectl", "--context", kube_context, "cluster-info"], timeout=args.timeout)
    verify["runtime_health"] = _request("GET", f"{runtime_url}/health", timeout=args.timeout)
    verify["deploy_health"] = _request("GET", f"{deploy_url}/health", timeout=args.timeout)
    saved = None
    if args.save:
        saved = str(_save_cli_config({
            "runtime_url": runtime_url,
            "deploy_url": deploy_url,
            "kube_context": kube_context,
            "cluster_mode": args.mode,
        }))
    return {"ok": True, "command": "cluster connect", "mode": args.mode, "verify": verify, "saved": saved}


def command_cluster_status(args: argparse.Namespace) -> dict[str, Any]:
    runtime_url = _runtime_url(args)
    deploy_url = _deploy_url(args)
    kube_context = _kube_context(args)
    plan: dict[str, Any] = {
        "runtime": [f"{runtime_url}/health", f"{runtime_url}/ready"],
        "deploy": [f"{deploy_url}/health", f"{deploy_url}/ready"],
    }
    kube_cmd = None
    if args.mode == "k3d":
        ctx = kube_context or _DEFAULT_K3D_CLUSTER
        # Status-only columns, never `-o json`: full Deployment specs can carry
        # inline container env secrets that line-based redaction would miss.
        kube_cmd = [
            "kubectl",
            "--context",
            f"k3d-{ctx}" if not ctx.startswith("k3d-") else ctx,
            "-n",
            "default",
            "get",
            "deploy",
            "-l",
            "part-of=mcpfinder",
            "-o",
            "custom-columns=NAME:.metadata.name,DESIRED:.spec.replicas,READY:.status.readyReplicas,AVAILABLE:.status.availableReplicas",
        ]
        plan["kubectl"] = kube_cmd
    if args.dry_run:
        return {"ok": True, "command": "cluster status", "dry_run": True, "mode": args.mode, **plan}
    report: dict[str, Any] = {"runtime_url": runtime_url, "deploy_url": deploy_url}
    report["runtime"] = {
        "health": _request("GET", f"{runtime_url}/health", timeout=args.timeout),
        "ready": _request("GET", f"{runtime_url}/ready", timeout=args.timeout),
    }
    report["deploy"] = {
        "health": _request("GET", f"{deploy_url}/health", timeout=args.timeout),
        "ready": _request("GET", f"{deploy_url}/ready", timeout=args.timeout),
    }
    if kube_cmd:
        _which("kubectl")
        report["kubernetes"] = _run_tool(kube_cmd, timeout=args.timeout)
    return {"ok": True, "command": "cluster status", "mode": args.mode, "report": report}


def command_cluster_down(args: argparse.Namespace) -> dict[str, Any]:
    mode = args.mode
    if mode == "remote":
        raise CliError("config_invalid", "cluster down supports --mode local|k3d only")
    if mode == "local":
        cmd = ["bash", str(_START_LOCAL_SCRIPT), "--stop"]
    else:
        name = args.name or _DEFAULT_K3D_CLUSTER
        scoped = name == _DEFAULT_K3D_CLUSTER or name.startswith(f"{_DEFAULT_K3D_CLUSTER}-")
        if not scoped and not args.force:
            raise CliError(
                "scope_violation",
                f"Refusing to delete non-mcpfinder-scoped cluster '{name}' without --force",
                target=name,
            )
        cmd = ["k3d", "cluster", "delete", name]
    if args.dry_run:
        return {"ok": True, "command": "cluster down", "dry_run": True, "mode": mode, "commands": [cmd]}
    if not args.yes:
        raise CliError("confirmation_required", "cluster down is destructive; pass --yes to proceed", detail={"mode": mode, "command": cmd})
    result = _run_tool(cmd, timeout=args.timeout)
    return {"ok": True, "command": "cluster down", "mode": mode, "result": result}


# ---------------------------------------------------------------------------
# MCP lifecycle via the SEPARATE deploy service (:8030).
# ---------------------------------------------------------------------------


def command_mcp_deploy(args: argparse.Namespace) -> dict[str, Any]:
    env_vars = _parse_kv_pairs(args.env, kind="env")
    request = {
        "repo_url": args.repo_url,
        "branch": args.branch,
        "name": args.name,
        "description": args.description or "",
        "tags": list(args.tag or []),
        "port": args.port,
        "is_public": args.public,
        "env_vars": env_vars,
    }
    base = _deploy_url(args)
    target = f"{base}/deploy"
    # env_vars are operator-supplied secrets; mask every value on echo (keys stay
    # visible). The live request still sends them to the deploy backend unredacted.
    echo_request = {**request, "env_vars": {key: "[REDACTED]" for key in env_vars}}
    if args.dry_run:
        return {"ok": True, "command": "mcp deploy", "dry_run": True, "target": target, "request": echo_request}
    api_key = _auth_required(args, "mcp deploy")
    stream = _sse_request(target, request, api_key=api_key, timeout=args.timeout)
    final = stream.get("final") or {}
    if stream.get("errored") or not stream.get("events"):
        step = final.get("step", "unknown")
        raise CliError(
            "backend_error",
            f"MCP deploy failed at step '{step}'" if stream.get("events") else "MCP deploy produced no events",
            target=target,
            detail=stream,
        )
    return {"ok": True, "command": "mcp deploy", "deploy_url": base, "name": args.name, "stream": stream}


def command_mcp_list(args: argparse.Namespace) -> dict[str, Any]:
    base = _deploy_url(args)
    if args.dry_run:
        return {"ok": True, "command": "mcp list", "dry_run": True, "target": f"{base}/deployments"}
    result = _request("GET", f"{base}/deployments", timeout=args.timeout)
    return {"ok": True, "command": "mcp list", "deploy_url": base, "response": result}


def command_mcp_get(args: argparse.Namespace) -> dict[str, Any]:
    base = _deploy_url(args)
    if args.dry_run:
        return {"ok": True, "command": "mcp get", "dry_run": True, "target": f"{base}/deployments/{args.name}"}
    result = _request("GET", f"{base}/deployments/{args.name}", timeout=args.timeout)
    return {"ok": True, "command": "mcp get", "deploy_url": base, "name": args.name, "response": result}


# ---------------------------------------------------------------------------
# Pipelines (router :8040). v2 (templated YAML) is the default engine.
# ---------------------------------------------------------------------------


def command_pipeline_list(args: argparse.Namespace) -> dict[str, Any]:
    base = _runtime_url(args)
    engine = args.engine
    if args.dry_run:
        targets = []
        if engine in ("v1", "all"):
            targets.append(f"{base}/pipelines")
        if engine in ("v2", "all"):
            targets.append(f"{base}/v2/pipelines")
        return {"ok": True, "command": "pipeline list", "dry_run": True, "engine": engine, "targets": targets}
    api_key = _api_key(args)
    out: dict[str, Any] = {"ok": True, "command": "pipeline list", "runtime_url": base, "engine": engine}
    if engine in ("v1", "all"):
        out["v1"] = _request("GET", f"{base}/pipelines", api_key=api_key, timeout=args.timeout)
    if engine in ("v2", "all"):
        out["v2"] = _request("GET", f"{base}/v2/pipelines", api_key=api_key, timeout=args.timeout)
    return out


def command_pipeline_get(args: argparse.Namespace) -> dict[str, Any]:
    base = _runtime_url(args)
    path = f"/v2/pipelines/{args.name}" if args.engine == "v2" else f"/pipelines/{args.name}"
    if args.dry_run:
        targets = [f"{base}{path}"]
        if args.type_check and args.engine == "v1":
            targets.append(f"{base}/pipelines/{args.name}/type-check")
        return {"ok": True, "command": "pipeline get", "dry_run": True, "engine": args.engine, "targets": targets}
    api_key = _api_key(args)
    out: dict[str, Any] = {"ok": True, "command": "pipeline get", "runtime_url": base, "engine": args.engine, "name": args.name}
    out["definition"] = _request("GET", f"{base}{path}", api_key=api_key, timeout=args.timeout)
    if args.type_check and args.engine == "v1":
        out["type_check"] = _request("GET", f"{base}/pipelines/{args.name}/type-check", api_key=api_key, timeout=args.timeout)
    return out


def command_pipeline_deploy(args: argparse.Namespace) -> dict[str, Any]:
    base = _runtime_url(args)
    definition = _load_document(Path(args.file))
    if args.engine == "v2":
        path = "/v2/pipelines/deploy"
        body = {"pipeline": definition}
    else:
        path = "/pipelines/register"
        body = {"pipeline": definition}
    if args.dry_run:
        return {"ok": True, "command": "pipeline deploy", "dry_run": True, "engine": args.engine, "target": f"{base}{path}", "pipeline": _redact(definition)}
    api_key = _auth_required(args, "pipeline deploy")
    result = _request("POST", f"{base}{path}", api_key=api_key, body=body, timeout=args.timeout)
    return {"ok": True, "command": "pipeline deploy", "runtime_url": base, "engine": args.engine, "response": result}


def command_pipeline_run(args: argparse.Namespace) -> dict[str, Any]:
    base = _runtime_url(args)
    inputs = _inputs_payload(args)
    if args.engine == "v2":
        path = "/v2/pipelines/run"
        body = {"pipeline": args.name, "inputs": inputs}
    else:
        path = f"/pipelines/{args.name}/run"
        body = {"inputs": inputs}
    if args.dry_run:
        return {"ok": True, "command": "pipeline run", "dry_run": True, "engine": args.engine, "target": f"{base}{path}", "request": _redact(body)}
    api_key = _auth_required(args, "pipeline run")
    result = _request("POST", f"{base}{path}", api_key=api_key, body=body, timeout=args.timeout)
    return {"ok": True, "command": "pipeline run", "runtime_url": base, "engine": args.engine, "response": result}


def command_pipeline_reload(args: argparse.Namespace) -> dict[str, Any]:
    base = _runtime_url(args)
    if args.dry_run:
        return {"ok": True, "command": "pipeline reload", "dry_run": True, "target": f"{base}/pipelines/reload"}
    api_key = _auth_required(args, "pipeline reload")
    result = _request("POST", f"{base}/pipelines/reload", api_key=api_key, timeout=args.timeout)
    return {"ok": True, "command": "pipeline reload", "runtime_url": base, "response": result}


# ---------------------------------------------------------------------------
# Workflow facade: pipelines + jobs. There is no separate workflow primitive in
# the runtime; see contract.workflow_model.
# ---------------------------------------------------------------------------


def _build_workflow_definition(name: str, engine: str, steps: list[str] | None) -> dict[str, Any]:
    """Scaffold a pipeline definition from --step mcp.tool tokens (pure-local)."""
    parsed: list[tuple[str, str]] = []
    for token in steps or []:
        if "." not in token:
            raise CliError("payload_invalid", f"--step expects mcp.tool, got: {token}")
        mcp, _, tool = token.partition(".")
        if not mcp or not tool:
            raise CliError("payload_invalid", f"--step expects mcp.tool, got: {token}")
        parsed.append((mcp, tool))
    if not parsed:
        parsed = [("demo-sandbox-mcp", "get_demo_customer")]
    if engine == "v2":
        return {
            "name": name,
            "version": 2,
            "description": f"Scaffolded workflow {name}",
            "inputs": {},
            "steps": [
                {"id": f"step_{i + 1}", "mcp": mcp, "tool": tool, "inputs": {}}
                for i, (mcp, tool) in enumerate(parsed)
            ],
        }
    return {
        "name": name,
        "description": f"Scaffolded workflow {name}",
        "inputs": {},
        "stages": [
            {"name": f"stage_{i + 1}", "mcp": mcp, "tool": tool}
            for i, (mcp, tool) in enumerate(parsed)
        ],
        "output_stage": f"stage_{len(parsed)}",
        "tags": ["scaffold"],
    }


def _dump_document(path: Path, data: dict[str, Any]) -> None:
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover - depends on packaging
            raise CliError("dependency_missing", "PyYAML is required to write YAML; use a .json --output or install pyyaml", target=str(path)) from exc
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=False))


def command_workflow_create(args: argparse.Namespace) -> dict[str, Any]:
    definition = _build_workflow_definition(args.name, args.engine, args.step)
    if args.output:
        output = Path(args.output)
    else:
        suffix = "yaml"
        subdir = "v2" if args.engine == "v2" else ""
        output = ROOT / "runtime" / "pipelines" / subdir / f"{args.name}.{suffix}" if subdir else ROOT / "runtime" / "pipelines" / f"{args.name}.{suffix}"
    if args.dry_run:
        return {"ok": True, "command": "workflow create", "dry_run": True, "engine": args.engine, "output": str(output), "definition": _redact(definition)}
    _dump_document(output, definition)
    return {"ok": True, "command": "workflow create", "engine": args.engine, "output": str(output), "definition": _redact(definition)}


def command_workflow_deploy(args: argparse.Namespace) -> dict[str, Any]:
    payload = command_pipeline_deploy(args)
    payload["command"] = "workflow deploy"
    return payload


def command_workflow_run(args: argparse.Namespace) -> dict[str, Any]:
    base = _runtime_url(args)
    inputs = _inputs_payload(args)
    body: dict[str, Any] = {"pipeline": args.name, "inputs": inputs}
    if args.job_name:
        body["name"] = args.job_name
    target = f"{base}/jobs"
    if args.dry_run:
        return {"ok": True, "command": "workflow run", "dry_run": True, "target": target, "request": _redact(body)}
    api_key = _auth_required(args, "workflow run")
    result = _request("POST", target, api_key=api_key, body=body, timeout=args.timeout)
    return {"ok": True, "command": "workflow run", "runtime_url": base, "response": result}


def command_workflow_status(args: argparse.Namespace) -> dict[str, Any]:
    base = _runtime_url(args)
    if args.list:
        query = f"?status={args.status}" if args.status else ""
        target = f"{base}/jobs{query}"
        if args.dry_run:
            return {"ok": True, "command": "workflow status", "dry_run": True, "target": target}
        result = _request("GET", target, api_key=_api_key(args), timeout=args.timeout)
        return {"ok": True, "command": "workflow status", "runtime_url": base, "response": result}
    if not args.job_id:
        raise CliError("payload_invalid", "workflow status requires --job-id or --list")
    target = f"{base}/jobs/{args.job_id}"
    if args.dry_run:
        return {"ok": True, "command": "workflow status", "dry_run": True, "target": target}
    result = _request("GET", target, api_key=_api_key(args), timeout=args.timeout)
    return {"ok": True, "command": "workflow status", "runtime_url": base, "job_id": args.job_id, "response": result}


def command_workflow_cancel(args: argparse.Namespace) -> dict[str, Any]:
    base = _runtime_url(args)
    target = f"{base}/jobs/{args.job_id}/cancel"
    if args.dry_run:
        return {"ok": True, "command": "workflow cancel", "dry_run": True, "target": target}
    api_key = _auth_required(args, "workflow cancel")
    result = _request("POST", target, api_key=api_key, timeout=args.timeout)
    return {"ok": True, "command": "workflow cancel", "runtime_url": base, "job_id": args.job_id, "response": result}


def command_smoke_zero_to_hero(args: argparse.Namespace) -> dict[str, Any]:
    """End-to-end public-preview readiness smoke across deploy (:8030) + runtime (:8040)."""
    runtime_base = _runtime_url(args)
    deploy_base = _deploy_url(args)
    checks = [
        {"service": "deploy", "method": "GET", "path": "/health"},
        {"service": "deploy", "method": "GET", "path": "/ready"},
        {"service": "deploy", "method": "GET", "path": "/deployments"},
        {"service": "runtime", "method": "GET", "path": "/health"},
        {"service": "runtime", "method": "GET", "path": "/ready"},
        {"service": "runtime", "method": "GET", "path": "/manifests"},
        {"service": "runtime", "method": "GET", "path": "/pipelines"},
        {"service": "runtime", "method": "GET", "path": "/v2/pipelines"},
    ]
    if args.dry_run:
        return {"ok": True, "command": "smoke zero-to-hero", "dry_run": True, "runtime_url": runtime_base, "deploy_url": deploy_base, "checks": checks}
    api_key = _auth_required(args, "smoke zero-to-hero")
    results = []
    for check in checks:
        base = runtime_base if check["service"] == "runtime" else deploy_base
        results.append({
            "check": check,
            "response": _request(check["method"], f"{base}{check['path']}", api_key=api_key, timeout=args.timeout),
        })
    return {"ok": True, "command": "smoke zero-to-hero", "dry_run": False, "runtime_url": runtime_base, "deploy_url": deploy_base, "results": results}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sealfleet MCP server Command Line Interface (CLI)")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON for both success and error responses")
    sub = parser.add_subparsers(dest="command", required=True)

    contract = sub.add_parser("contract", help="Print the Sealfleet CLI contract")
    contract.set_defaults(func=lambda _args: contract_payload())

    validate = sub.add_parser("validate", help="Validate Sealfleet CLI config")
    validate.add_argument("--config", required=True, help="Path to mcpfinder.cli.config/v1 JSON/YAML")
    validate.set_defaults(func=lambda args: validate_config(Path(args.config)))

    status = sub.add_parser("status", help="Check runtime health/readiness through GET /health and GET /ready")
    _add_runtime_options(status, include_api_key=False)
    status.set_defaults(func=command_status)

    invoke = sub.add_parser("invoke", help="Invoke a runtime MCP tool through POST /call")
    _add_runtime_options(invoke, include_api_key=True)
    invoke.add_argument("--mcp", required=True, help="Runtime manifest/MCP name")
    invoke.add_argument("--tool", required=True, help="Tool name")
    invoke.add_argument("--payload", default="{}", help="JSON object to send as inputs")
    invoke.add_argument("--payload-file", help="JSON/YAML object to send as inputs")
    invoke.add_argument("--dry-run", action="store_true", help="Validate and print request without network call")
    invoke.set_defaults(func=command_invoke)

    registry = sub.add_parser("registry", help="Registry control-plane operations")
    registry_sub = registry.add_subparsers(dest="registry_command", required=True)
    export = registry_sub.add_parser("export", help="Export tenant-scoped registry metadata")
    _add_runtime_options(export, include_api_key=True)
    export.add_argument("--output", help="Write exported bundle to file instead of stdout")
    export.set_defaults(func=command_registry_export)
    import_cmd = registry_sub.add_parser("import", help="Import tenant-scoped registry metadata")
    _add_runtime_options(import_cmd, include_api_key=True)
    import_cmd.add_argument("--input", required=True, help="Registry export bundle JSON/YAML")
    import_cmd.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True, help="Validate without mutation by default")
    import_cmd.set_defaults(func=command_registry_import)

    manifest = sub.add_parser("manifest", help="Manifest/service registry operations")
    manifest_sub = manifest.add_subparsers(dest="manifest_command", required=True)
    list_cmd = manifest_sub.add_parser("list", help="List runtime manifests through GET /manifests")
    _add_runtime_options(list_cmd, include_api_key=True)
    list_cmd.set_defaults(func=command_manifest_list)
    get_cmd = manifest_sub.add_parser("get", help="Fetch one runtime manifest through GET /manifests/{name}")
    _add_runtime_options(get_cmd, include_api_key=True)
    get_cmd.add_argument("name", help="Manifest/MCP name")
    get_cmd.set_defaults(func=command_manifest_get)
    register = manifest_sub.add_parser("register", help="Register a runtime manifest")
    _add_runtime_options(register, include_api_key=True)
    register.add_argument("--file", required=True, help="Manifest JSON/YAML")
    register.add_argument("--typed", action="store_true", help="Register typed manifest via /manifests/typed")
    register.add_argument("--dry-run", action="store_true", help="Validate and print target without network call")
    register.set_defaults(func=command_manifest_register)

    smoke = sub.add_parser("smoke", help="Runtime/deploy smoke checks")
    smoke_sub = smoke.add_subparsers(dest="smoke_command", required=True)
    local_demo = smoke_sub.add_parser("local-demo", help="Run local demo smoke against health, readiness, manifests, and demo tool invocation")
    _add_runtime_options(local_demo, include_api_key=True)
    local_demo.add_argument("--dry-run", action="store_true", help="Print the real runtime operations without calling backend")
    local_demo.set_defaults(func=command_smoke_local_demo)
    zero = smoke_sub.add_parser("zero-to-hero", help="End-to-end public-preview smoke across the deploy service and runtime router")
    _add_runtime_options(zero, include_api_key=True)
    zero.add_argument("--deploy-url", default=None, help="Deploy service URL (default env MCPFINDER_DEPLOY_URL or http://localhost:8030)")
    zero.add_argument("--dry-run", action="store_true", help="Print the real deploy+runtime operations without calling backend")
    zero.set_defaults(func=command_smoke_zero_to_hero)

    _add_cluster_commands(sub)
    _add_mcp_commands(sub)
    _add_pipeline_commands(sub)
    _add_workflow_commands(sub)
    return parser


def _add_cluster_commands(sub: argparse._SubParsersAction) -> None:
    cluster = sub.add_parser("cluster", help="Cluster lifecycle: create/connect/status/down (local host services or k3d)")
    cluster_sub = cluster.add_subparsers(dest="cluster_command", required=True)

    create = cluster_sub.add_parser("create", help="Provision a local dev cluster (host services or k3d)")
    create.add_argument("--mode", choices=_CLUSTER_MODES, default="local", help="local host services or a k3d cluster")
    create.add_argument("--name", help="k3d cluster name (default mcpfinder)")
    create.add_argument("--bg", action="store_true", help="local mode: start services in background")
    create.add_argument("--timeout", type=float, default=600.0, help="Tooling timeout seconds")
    create.add_argument("--dry-run", action="store_true", help="Print the exact commands without executing")
    create.set_defaults(func=command_cluster_create)

    connect = cluster_sub.add_parser("connect", help="Point the CLI at an existing cluster/backend and verify reachability")
    connect.add_argument("--mode", choices=_CLUSTER_MODES, default="remote", help="cluster mode to record")
    connect.add_argument("--runtime-url", default=None, help="Runtime router URL")
    connect.add_argument("--deploy-url", default=None, help="Deploy service URL")
    connect.add_argument("--kube-context", default=None, help="kubectl context to verify (k3d mode)")
    connect.add_argument("--timeout", type=float, default=10.0, help="Probe timeout seconds")
    connect.add_argument("--save", action="store_true", help="Persist resolved URLs/context to ~/.config/mcpfinder/cli.config.json")
    connect.add_argument("--dry-run", action="store_true", help="Print the probes without executing")
    connect.set_defaults(func=command_cluster_connect)

    status = cluster_sub.add_parser("status", help="Aggregate health of router + deploy service (+ kubectl in k3d mode)")
    status.add_argument("--mode", choices=_CLUSTER_MODES, default="remote", help="cluster mode")
    status.add_argument("--runtime-url", default=None, help="Runtime router URL")
    status.add_argument("--deploy-url", default=None, help="Deploy service URL")
    status.add_argument("--kube-context", default=None, help="kubectl context (k3d mode)")
    status.add_argument("--timeout", type=float, default=10.0, help="HTTP/tooling timeout seconds")
    status.add_argument("--dry-run", action="store_true", help="Print the operations without executing")
    status.set_defaults(func=command_cluster_status)

    down = cluster_sub.add_parser("down", help="Tear down local services or delete a k3d cluster (guarded)")
    down.add_argument("--mode", choices=_CLUSTER_MODES, default="local", help="local host services or a k3d cluster")
    down.add_argument("--name", help="k3d cluster name (default mcpfinder)")
    down.add_argument("--yes", action="store_true", help="Confirm the destructive operation")
    down.add_argument("--force", action="store_true", help="Allow deleting a non-mcpfinder-scoped k3d cluster name")
    down.add_argument("--timeout", type=float, default=300.0, help="Tooling timeout seconds")
    down.add_argument("--dry-run", action="store_true", help="Print the command without executing")
    down.set_defaults(func=command_cluster_down)


def _add_mcp_commands(sub: argparse._SubParsersAction) -> None:
    mcp = sub.add_parser("mcp", help="MCP lifecycle via the deploy service (:8030) and router manifests")
    mcp_sub = mcp.add_subparsers(dest="mcp_command", required=True)

    deploy = mcp_sub.add_parser("deploy", help="Deploy an MCP from a git repo through the deploy service")
    _add_deploy_options(deploy, include_api_key=True, timeout_default=600.0)
    deploy.add_argument("--repo-url", required=True, help="Git repository URL to build/deploy")
    deploy.add_argument("--name", required=True, help="MCP deployment name/slug")
    deploy.add_argument("--branch", default="main", help="Git branch (default main)")
    deploy.add_argument("--description", default="", help="MCP description")
    deploy.add_argument("--tag", action="append", help="Tag (repeatable)")
    deploy.add_argument("--port", type=int, default=8000, help="Container port (default 8000)")
    deploy.add_argument("--public", action=argparse.BooleanOptionalAction, default=True, help="List in public catalog")
    deploy.add_argument("--env", action="append", help="Env var KEY=VALUE (repeatable; redacted on echo)")
    deploy.add_argument("--dry-run", action="store_true", help="Print the redacted deploy request without calling backend")
    deploy.set_defaults(func=command_mcp_deploy)

    list_cmd = mcp_sub.add_parser("list", help="List deployed MCPs from the deploy service")
    _add_deploy_options(list_cmd, include_api_key=False)
    list_cmd.add_argument("--dry-run", action="store_true", help="Print the target without calling backend")
    list_cmd.set_defaults(func=command_mcp_list)

    get_cmd = mcp_sub.add_parser("get", help="Get one deployment's status/endpoint by name")
    _add_deploy_options(get_cmd, include_api_key=False)
    get_cmd.add_argument("name", help="MCP deployment name")
    get_cmd.add_argument("--dry-run", action="store_true", help="Print the target without calling backend")
    get_cmd.set_defaults(func=command_mcp_get)

    register = mcp_sub.add_parser("register", help="Register an already-running MCP manifest directly in the router")
    _add_runtime_options(register, include_api_key=True)
    register.add_argument("--file", required=True, help="Manifest JSON/YAML")
    register.add_argument("--typed", action="store_true", help="Register typed manifest via /manifests/typed")
    register.add_argument("--dry-run", action="store_true", help="Validate and print target without network call")
    register.set_defaults(func=command_manifest_register)


def _add_pipeline_commands(sub: argparse._SubParsersAction) -> None:
    pipeline = sub.add_parser("pipeline", help="Pipeline operations on the router (v2 default; v1 via --engine)")
    pipeline_sub = pipeline.add_subparsers(dest="pipeline_command", required=True)

    list_cmd = pipeline_sub.add_parser("list", help="List v1 named and/or v2 pipelines")
    _add_runtime_options(list_cmd, include_api_key=True)
    list_cmd.add_argument("--engine", choices=("v1", "v2", "all"), default="all", help="Which pipeline engines to list")
    list_cmd.add_argument("--dry-run", action="store_true", help="Print targets without calling backend")
    list_cmd.set_defaults(func=command_pipeline_list)

    get_cmd = pipeline_sub.add_parser("get", help="Get a pipeline definition (+ optional type-check)")
    _add_runtime_options(get_cmd, include_api_key=True)
    get_cmd.add_argument("name", help="Pipeline name")
    get_cmd.add_argument("--engine", choices=("v1", "v2"), default="v2", help="Pipeline engine")
    get_cmd.add_argument("--type-check", action="store_true", help="Also fetch v1 type-check warnings")
    get_cmd.add_argument("--dry-run", action="store_true", help="Print targets without calling backend")
    get_cmd.set_defaults(func=command_pipeline_get)

    deploy = pipeline_sub.add_parser("deploy", help="Deploy a pipeline definition from a local file")
    _add_runtime_options(deploy, include_api_key=True)
    deploy.add_argument("--file", required=True, help="Pipeline definition JSON/YAML")
    deploy.add_argument("--engine", choices=("v1", "v2"), default="v2", help="Pipeline engine")
    deploy.add_argument("--dry-run", action="store_true", help="Validate and print target without network call")
    deploy.set_defaults(func=command_pipeline_deploy)

    run = pipeline_sub.add_parser("run", help="Run a pipeline synchronously")
    _add_runtime_options(run, include_api_key=True)
    run.add_argument("--name", required=True, help="Pipeline name")
    run.add_argument("--engine", choices=("v1", "v2"), default="v2", help="Pipeline engine")
    run.add_argument("--inputs", default="{}", help="JSON object of inputs")
    run.add_argument("--inputs-file", help="JSON/YAML object of inputs")
    run.add_argument("--dry-run", action="store_true", help="Print the request without network call")
    run.set_defaults(func=command_pipeline_run)

    reload_cmd = pipeline_sub.add_parser("reload", help="Hot-reload pipeline definitions on the runtime host")
    _add_runtime_options(reload_cmd, include_api_key=True)
    reload_cmd.add_argument("--dry-run", action="store_true", help="Print the target without network call")
    reload_cmd.set_defaults(func=command_pipeline_reload)


def _add_workflow_commands(sub: argparse._SubParsersAction) -> None:
    workflow = sub.add_parser("workflow", help="Workflow facade over pipelines + jobs (see contract.workflow_model)")
    workflow_sub = workflow.add_subparsers(dest="workflow_command", required=True)

    # Workflow == v1 named pipeline + async jobs. POST /jobs resolves only v1 named
    # pipelines, so the workflow facade defaults to v1 to stay coherent end-to-end.
    # (Use the `pipeline` group for v2 templated pipelines, which run synchronously.)
    create = workflow_sub.add_parser("create", help="Scaffold a v1 named-pipeline definition file locally (no network)")
    create.add_argument("--name", required=True, help="Workflow/pipeline name")
    create.add_argument("--engine", choices=("v1", "v2"), default="v1", help="Definition engine to scaffold (workflow run needs v1)")
    create.add_argument("--step", action="append", help="Step as mcp.tool (repeatable)")
    create.add_argument("--output", help="Output file path (default runtime/pipelines[/v2]/{name}.yaml)")
    create.add_argument("--dry-run", action="store_true", help="Print the definition without writing a file")
    create.set_defaults(func=command_workflow_create)

    deploy = workflow_sub.add_parser("deploy", help="Deploy a scaffolded workflow (same as pipeline deploy)")
    _add_runtime_options(deploy, include_api_key=True)
    deploy.add_argument("--file", required=True, help="Pipeline definition JSON/YAML")
    deploy.add_argument("--engine", choices=("v1", "v2"), default="v1", help="Definition engine (v1 for workflow run via /jobs)")
    deploy.add_argument("--dry-run", action="store_true", help="Validate and print target without network call")
    deploy.set_defaults(func=command_workflow_deploy)

    run = workflow_sub.add_parser("run", help="Run a v1 named pipeline as a durable async job (POST /jobs)")
    _add_runtime_options(run, include_api_key=True)
    run.add_argument("--name", required=True, help="Pipeline name to run as a job")
    run.add_argument("--inputs", default="{}", help="JSON object of inputs")
    run.add_argument("--inputs-file", help="JSON/YAML object of inputs")
    run.add_argument("--job-name", help="Optional human-friendly job name")
    run.add_argument("--dry-run", action="store_true", help="Print the job request without network call")
    run.set_defaults(func=command_workflow_run)

    status = workflow_sub.add_parser("status", help="Poll a job by id, or list jobs")
    _add_runtime_options(status, include_api_key=True)
    status.add_argument("--job-id", help="Job id to poll")
    status.add_argument("--list", action="store_true", help="List jobs instead of polling one")
    status.add_argument("--status", help="Filter by status when listing")
    status.add_argument("--dry-run", action="store_true", help="Print the target without network call")
    status.set_defaults(func=command_workflow_status)

    cancel = workflow_sub.add_parser("cancel", help="Cancel a running job")
    _add_runtime_options(cancel, include_api_key=True)
    cancel.add_argument("--job-id", required=True, help="Job id to cancel")
    cancel.add_argument("--dry-run", action="store_true", help="Print the target without network call")
    cancel.set_defaults(func=command_workflow_cancel)


def _add_runtime_options(parser: argparse.ArgumentParser, *, include_api_key: bool) -> None:
    # Default None so resolution order is flag > env > persisted config > default.
    parser.add_argument("--runtime-url", default=None, help="Runtime router URL (default env MCPFINDER_RUNTIME_URL or http://localhost:8040)")
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout seconds")
    if include_api_key:
        parser.add_argument("--api-key", help="API key; defaults to MCPFINDER_API_KEY")


def _add_deploy_options(parser: argparse.ArgumentParser, *, include_api_key: bool, timeout_default: float = 5.0) -> None:
    parser.add_argument("--deploy-url", default=None, help="Deploy service URL (default env MCPFINDER_DEPLOY_URL or http://localhost:8030)")
    parser.add_argument("--timeout", type=float, default=timeout_default, help="HTTP timeout seconds")
    if include_api_key:
        parser.add_argument("--api-key", help="API key; defaults to MCPFINDER_API_KEY")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = args.func(args)
        _print_payload(payload, as_json=args.json)
        return 0
    except CliError as exc:
        _print_payload(exc.to_payload(), as_json=args.json)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
