"""Platform control plane as an MCP server.

Mounts `POST /mcp` on the runtime router: MCP over streamable HTTP, JSON-RPC
2.0 — `initialize`, `ping`, `tools/list`, `tools/call`. Any MCP client (Claude
Code, Claude Desktop, Cursor, the built-in chat agent) can manage the platform
with it: register and deploy MCPs, invoke tools, build/run/delete pipelines,
submit jobs, manage credential metadata and policy, read audit.

Security model — a translator, never a second authorization path
  Every tool forwards to the existing REST route over loopback, carrying the
  caller's own credentials (X-API-Key / Authorization / X-McpFinder-* delegated
  identity). So `_authorize_action`, tenant scoping, rate limits, and audit run
  exactly as they do for REST. There is no tool that returns a secret value:
  `credential_use` is deliberately absent — an LLM must never hold a secret.

Loopback target: MCP_CONTROL_LOOPBACK_URL (default http://127.0.0.1:$APP_PORT).
Tests swap the transport for an in-process ASGI one via `set_transport_factory`.
"""

from __future__ import annotations

import json
import os
from typing import Any, Awaitable, Callable

import httpx
from fastapi import APIRouter, Request, Response

PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "sealfleet-platform", "version": "0.1.0"}
FORWARDED_HEADERS = (
    "x-api-key", "authorization",
    "x-mcpfinder-user-id", "x-mcpfinder-tenant-id", "x-mcpfinder-groups",
)
MAX_TEXT = int(os.environ.get("MCP_CONTROL_MAX_TEXT", "60000"))

router = APIRouter()

# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

def _s(desc: str, **extra) -> dict:
    return {"type": "string", "description": desc, **extra}


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


def _tool(name: str, description: str, schema: dict, method: str, path: str,
          *, body: Callable[[dict], Any] | None = None,
          query: Callable[[dict], dict] | None = None,
          service: str = "router") -> dict:
    return {
        "name": name, "description": description, "inputSchema": schema,
        "method": method, "path": path, "body": body, "query": query, "service": service,
    }


def _path(template: str, args: dict) -> str:
    return template.format(**{k: str(v) for k, v in args.items()})


TOOLS: list[dict] = [
    # --- platform ---
    _tool("platform_status", "Runtime health: registered manifests, pipelines, scale-to-zero state.",
          _obj({}), "GET", "/health"),

    # --- MCP servers ---
    _tool("mcp_list", "List registered MCP servers (manifests) visible to the caller.",
          _obj({}), "GET", "/manifests"),
    _tool("mcp_get", "Get one MCP server's manifest.",
          _obj({"name": _s("MCP name")}, ["name"]), "GET", "/manifests/{name}"),
    _tool("mcp_tools", "Every callable MCP tool with its input description — the vocabulary for building pipelines.",
          _obj({}), "GET", "/mcp-tools"),
    _tool("mcp_register",
          "Register an already-running MCP server in the router (requires mcp.server.register).",
          _obj({
              "name": _s("MCP name"), "endpoint": _s("Base URL, e.g. http://my-mcp:8080"),
              "tools": {"type": "array", "items": {"type": "string"}, "description": "Tool names it serves"},
              "publishes": {"type": "array", "items": {"type": "string"}, "default": []},
              "subscribes": {"type": "array", "items": {"type": "string"}, "default": []},
              "transport": _s("http or stdio", default="http"),
              "image": _s("Container image (stdio transport only)"),
          }, ["name", "endpoint", "tools"]),
          "POST", "/manifests",
          body=lambda a: {"publishes": [], "subscribes": [], "transport": "http", **a}),
    _tool("mcp_deploy",
          "Build and deploy an MCP from a git repository via the deploy service, then register it. "
          "Long-running; returns the deploy log.",
          _obj({
              "repo_url": _s("Git repository URL"), "name": _s("Deployment name/slug"),
              "branch": _s("Git branch", default="main"), "description": _s("Description", default=""),
              "tags": {"type": "array", "items": {"type": "string"}, "default": []},
              "port": {"type": "integer", "default": 8000, "description": "Container port"},
              "is_public": {"type": "boolean", "default": True},
              "env_vars": {"type": "object", "default": {}, "description": "Environment for the container"},
          }, ["repo_url", "name"]),
          "POST", "/deploy", body=lambda a: a, service="deploy"),
    _tool("mcp_deployments", "List MCPs deployed through the deploy service.",
          _obj({}), "GET", "/deployments", service="deploy"),

    # --- tools ---
    _tool("tool_invoke", "Invoke one tool on one MCP server directly.",
          _obj({"mcp": _s("MCP name"), "tool": _s("Tool name"),
                "inputs": {"type": "object", "default": {}, "description": "Tool arguments"}},
               ["mcp", "tool"]),
          "POST", "/call", body=lambda a: {"mcp": a["mcp"], "tool": a["tool"], "inputs": a.get("inputs", {})}),

    # --- pipelines ---
    _tool("pipeline_list", "List v2 pipelines with their input schemas.",
          _obj({}), "GET", "/v2/pipelines"),
    _tool("pipeline_get", "Get a v2 pipeline definition.",
          _obj({"name": _s("Pipeline name")}, ["name"]), "GET", "/v2/pipelines/{name}"),
    _tool("pipeline_run", "Run a v2 pipeline synchronously and return its results.",
          _obj({"name": _s("Pipeline name"),
                "inputs": {"type": "object", "default": {}, "description": "Pipeline inputs"}}, ["name"]),
          "POST", "/v2/pipelines/run", body=lambda a: {"pipeline": a["name"], "inputs": a.get("inputs", {})}),
    _tool("pipeline_deploy",
          "Create or replace a v2 pipeline from a definition object "
          "{name, version:2, description, inputs, steps:[{id, mcp, tool, inputs}], output}. "
          "Requires pipeline.deploy.",
          _obj({"pipeline": {"type": "object", "description": "v2 pipeline definition"}}, ["pipeline"]),
          "POST", "/v2/pipelines/deploy", body=lambda a: {"pipeline": a["pipeline"]}),
    _tool("pipeline_delete", "Delete a runtime-authored v2 pipeline (shipped examples are protected).",
          _obj({"name": _s("Pipeline name")}, ["name"]), "DELETE", "/v2/pipelines/{name}"),
    _tool("pipeline_reload", "Re-scan pipeline definitions from disk.",
          _obj({}), "POST", "/pipelines/reload"),

    # --- jobs ---
    _tool("job_create", "Run a pipeline as a background job; returns the job id.",
          _obj({"pipeline": _s("Pipeline name"),
                "inputs": {"type": "object", "default": {}}, "name": _s("Optional job name")}, ["pipeline"]),
          "POST", "/jobs", body=lambda a: {k: v for k, v in a.items() if k in ("pipeline", "inputs", "name")}),
    _tool("job_get", "Get a job's status and result.",
          _obj({"job_id": _s("Job id")}, ["job_id"]), "GET", "/jobs/{job_id}"),
    _tool("job_list", "List jobs, optionally filtered.",
          _obj({"status": _s("Filter by status", default=""), "pipeline": _s("Filter by pipeline", default=""),
                "limit": {"type": "integer", "default": 50}}),
          "GET", "/jobs", query=lambda a: {k: v for k, v in a.items() if v not in ("", None)}),
    _tool("job_cancel", "Cancel a running job.",
          _obj({"job_id": _s("Job id")}, ["job_id"]), "POST", "/jobs/{job_id}/cancel"),

    # --- credentials (metadata only; values are never returned) ---
    _tool("credential_list",
          "List the tenant's credentials — names and metadata only, never values (requires credential.read).",
          _obj({}), "GET", "/credentials"),
    _tool("credential_create",
          "Store a credential, encrypted at rest (requires credential.create). "
          "Scope with assigned_mcp; use 'llm:<provider>' to offer it as a chat brain.",
          _obj({"name": _s("Credential name"), "value": _s("The secret value"),
                "description": _s("Description", default=""),
                "secret_type": _s("api_key | oauth_token | basic_auth | custom", default="api_key"),
                "storage_mode": _s("platform | byok | k8s", default="platform"),
                "assigned_mcp": _s("What may use it, e.g. an MCP name or llm:openai")}, ["name", "value"]),
          "POST", "/credentials", body=lambda a: a),
    _tool("credential_update", "Update a credential's description, value, scope, or active flag.",
          _obj({"credential_id": _s("Credential id"), "description": _s("New description"),
                "value": _s("New secret value"), "assigned_mcp": _s("New scope"),
                "is_active": {"type": "boolean"}}, ["credential_id"]),
          "PATCH", "/credentials/{credential_id}",
          body=lambda a: {k: v for k, v in a.items() if k != "credential_id"}),
    _tool("credential_delete", "Delete a credential.",
          _obj({"credential_id": _s("Credential id")}, ["credential_id"]),
          "DELETE", "/credentials/{credential_id}"),

    # --- policy ---
    _tool("policy_rules", "List active policy rules.", _obj({}), "GET", "/policy/rules"),
    _tool("policy_check", "Evaluate policy for a prospective MCP tool call without executing it.",
          _obj({"mcp": _s("MCP name"), "tool": _s("Tool name")}, ["mcp", "tool"]),
          "POST", "/policy/check", body=lambda a: a),
    _tool("policy_reload", "Reload policy rules from disk (requires policy.admin).",
          _obj({}), "POST", "/policy/reload"),

    # --- audit / scale / registry ---
    _tool("audit_events", "Read recent audit events (requires audit.read).",
          _obj({"limit": {"type": "integer", "default": 100}, "server": _s("Filter by MCP", default="")}),
          "GET", "/audit/events", query=lambda a: {k: v for k, v in a.items() if v not in ("", None)}),
    _tool("scale_status", "Scale-to-zero status per MCP.", _obj({}), "GET", "/scale/status"),
    _tool("scale_up", "Scale an MCP up from zero.", _obj({"mcp": _s("MCP name")}, ["mcp"]),
          "POST", "/scale/{mcp}/up"),
    _tool("scale_down", "Scale an MCP down to zero.", _obj({"mcp": _s("MCP name")}, ["mcp"]),
          "POST", "/scale/{mcp}/down"),
    _tool("registry_export", "Export the registry bundle (requires registry.export).",
          _obj({}), "GET", "/registry/export"),
]
TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}

# Explicitly never exposed: the only route that returns a secret value.
FORBIDDEN_TOOLS = {"credential_use"}


def public_tools() -> list[dict]:
    return [{"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]} for t in TOOLS]


# ---------------------------------------------------------------------------
# Forwarding
# ---------------------------------------------------------------------------

def _loopback_url() -> str:
    port = os.environ.get("PORT") or os.environ.get("APP_PORT") or "8040"
    return os.environ.get("MCP_CONTROL_LOOPBACK_URL") or f"http://127.0.0.1:{port}"


def _deploy_url() -> str:
    return os.environ.get("DEPLOY_URL", "http://localhost:8030").rstrip("/")


_transport_factory: Callable[[], httpx.AsyncBaseTransport | None] = lambda: None


def set_transport_factory(factory: Callable[[], httpx.AsyncBaseTransport | None]) -> None:
    """Tests use this to route loopback calls in-process (httpx.ASGITransport)."""
    global _transport_factory
    _transport_factory = factory


def _caller_headers(request: Request) -> dict:
    return {h: request.headers[h] for h in FORWARDED_HEADERS if request.headers.get(h)}


async def _forward(request: Request, tool: dict, args: dict) -> tuple[int, Any]:
    base = _deploy_url() if tool["service"] == "deploy" else _loopback_url()
    url = base + _path(tool["path"], args)
    headers = _caller_headers(request)
    headers["content-type"] = "application/json"
    kwargs: dict = {"headers": headers}
    if tool["query"]:
        kwargs["params"] = tool["query"](args)
    if tool["body"] and tool["method"] in ("POST", "PATCH", "PUT"):
        kwargs["json"] = tool["body"](args)

    transport = _transport_factory()
    async with httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(600.0, connect=10.0)) as client:
        resp = await client.request(tool["method"], url, **kwargs)

    ctype = resp.headers.get("content-type", "")
    if resp.status_code == 204:
        return 204, {"ok": True}
    if "text/event-stream" in ctype:
        # Deploy streams progress; hand back the collected log.
        return resp.status_code, {"log": resp.text}
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, {"text": resp.text}


def _text(payload: Any) -> str:
    text = json.dumps(payload, indent=2, default=str)
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + f"\n… [truncated {len(text) - MAX_TEXT} chars]"
    return text


# ---------------------------------------------------------------------------
# JSON-RPC
# ---------------------------------------------------------------------------

def _rpc_error(id_: Any, code: int, message: str, data: Any = None) -> dict:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": id_, "error": err}


def _rpc_result(id_: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


async def _handle(request: Request, msg: dict) -> dict | None:
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        return _rpc_error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
    method, id_, params = msg["method"], msg.get("id"), msg.get("params") or {}

    if method.startswith("notifications/"):
        return None  # notifications get no response
    if method == "initialize":
        return _rpc_result(id_, {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": (
                "Sealfleet platform control. Tools mirror the REST API and run with the "
                "caller's permissions; credential values are never returned."
            ),
        })
    if method == "ping":
        return _rpc_result(id_, {})
    if method == "tools/list":
        return _rpc_result(id_, {"tools": public_tools()})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = TOOLS_BY_NAME.get(name)
        if not tool or name in FORBIDDEN_TOOLS:
            return _rpc_error(id_, -32602, f"Unknown tool: {name}")
        missing = [k for k in tool["inputSchema"].get("required", []) if k not in args]
        if missing:
            return _rpc_error(id_, -32602, f"Missing required arguments: {missing}")
        try:
            status, payload = await _forward(request, tool, args)
        except httpx.HTTPError as e:
            return _rpc_result(id_, {"content": [{"type": "text", "text": f"upstream error: {e}"}], "isError": True})
        return _rpc_result(id_, {
            "content": [{"type": "text", "text": _text(payload)}],
            "isError": status >= 400,
        })
    return _rpc_error(id_, -32601, f"Method not found: {method}")


@router.post("/mcp")
async def mcp_endpoint(request: Request):
    """MCP streamable-HTTP endpoint (JSON responses; no server-initiated stream)."""
    try:
        body = json.loads(await request.body() or b"null")
    except json.JSONDecodeError:
        return Response(json.dumps(_rpc_error(None, -32700, "Parse error")), status_code=400,
                        media_type="application/json")

    messages = body if isinstance(body, list) else [body]
    responses = [r for r in [await _handle(request, m) for m in messages] if r is not None]
    if not responses:
        return Response(status_code=202)  # notification(s) only
    payload = responses if isinstance(body, list) else responses[0]
    return Response(json.dumps(payload, default=str), media_type="application/json")


@router.get("/mcp")
async def mcp_get():
    # Streamable HTTP lets a server decline the GET stream with 405.
    return Response(status_code=405, headers={"Allow": "POST"})


def install(app) -> None:
    app.include_router(router)
