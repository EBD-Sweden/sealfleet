"""Registry discovery API server.

FastAPI service for registering and discovering MCP servers and tools.
Backed by PostgreSQL via asyncpg.

Endpoints:
    POST /servers                — Register an MCP server
    GET  /servers                — List registered servers
    GET  /servers/{id}           — Get server details
    POST /servers/{id}/tools     — Register a tool under a server
    GET  /tools                  — List/search tools
    GET  /tools/{id}             — Get tool details
    GET  /audit                  — List recent audit events
    GET  /health                 — Health check
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from registry.storage import RegistryStorage, ServerRecord, ToolRecord


# --- Request/Response models ---

class RegisterServerRequest(BaseModel):
    name: str
    endpoint: str
    description: str = ""
    auth_methods: list[str] = []
    metadata: dict[str, Any] = {}


class RegisterServerResponse(BaseModel):
    server_id: str
    name: str
    endpoint: str
    status: str


class RegisterToolRequest(BaseModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any] = {}
    category: str = ""
    tags: list[str] = []
    version: str = "0.1.0"


class RegisterToolResponse(BaseModel):
    tool_id: str
    server_id: str
    name: str


class ServerInfo(BaseModel):
    server_id: str
    name: str
    endpoint: str
    description: str
    auth_methods: list[str]
    status: str
    tool_count: int = 0


class ToolInfo(BaseModel):
    tool_id: str
    server_id: str
    name: str
    description: str
    input_schema: dict[str, Any]
    category: str
    tags: list[str]
    version: str


class AuditEventInfo(BaseModel):
    event_id: str
    user_id: str
    action: str
    resource: str
    server_name: str
    result: str
    trace_id: str
    duration_ms: int
    created_at: str


# --- App factory ---

def create_registry_app(dsn: Optional[str] = None) -> FastAPI:
    """Create the registry FastAPI application.

    Args:
        dsn: PostgreSQL connection string. Falls back to DATABASE_URL env var.
    """
    # Load .env from registry directory
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(env_path)

    database_url = dsn or os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise ValueError("DATABASE_URL must be set (via env or dsn parameter)")

    app = FastAPI(
        title="Sealfleet Registry",
        description="Discovery service for MCP servers and tools",
        version="0.1.0",
    )

    # CORS: default-deny cross-origin; opt in via MCPFINDER_CORS_ALLOW_ORIGINS.
    cors_origins = [o.strip() for o in os.getenv("MCPFINDER_CORS_ALLOW_ORIGINS", "").split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=bool(cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    storage: Optional[RegistryStorage] = None

    def _auth_required() -> bool:
        return os.getenv("REQUIRE_AUTH", "true").lower() in ("1", "true", "yes")

    async def require_registry_auth(x_api_key: str | None = Header(default=None)) -> str:
        """Authenticate mutating registry calls against an active api_keys row
        (same DB). Read endpoints stay open; writes fail closed unless
        REQUIRE_AUTH is explicitly disabled (dev)."""
        if not _auth_required():
            return "system"
        if not x_api_key:
            raise HTTPException(status_code=401, detail="X-API-Key required")
        s = _get_storage()
        try:
            row = await s._pool.fetchrow(
                "SELECT tenant_id FROM api_keys WHERE api_key = $1 AND is_active = TRUE LIMIT 1",
                x_api_key,
            )
        except Exception:
            raise HTTPException(status_code=503, detail="auth backend unavailable")
        if not row:
            raise HTTPException(status_code=401, detail="invalid API key")
        return str(row["tenant_id"])

    @app.on_event("startup")
    async def startup():
        nonlocal storage
        storage = await RegistryStorage.create(database_url)

    @app.on_event("shutdown")
    async def shutdown():
        if storage:
            await storage.close()

    def _get_storage() -> RegistryStorage:
        if storage is None:
            raise HTTPException(status_code=503, detail="Storage not initialized")
        return storage

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "mcpfinder-registry"}

    @app.get("/ready")
    async def ready():
        return {
            "status": "ready" if storage is not None else "degraded",
            "service": "mcpfinder-registry",
            "checks": {"storage": "ok" if storage is not None else "uninitialized"},
        }

    # --- Server endpoints ---

    @app.post("/servers", response_model=RegisterServerResponse)
    async def register_server(req: RegisterServerRequest, _t: str = Depends(require_registry_auth)):
        """Register a new MCP server."""
        s = _get_storage()
        server_id = str(uuid.uuid4())
        record = await s.register_server(ServerRecord(
            server_id=server_id,
            name=req.name,
            endpoint=req.endpoint,
            description=req.description,
            auth_methods=req.auth_methods,
            metadata=req.metadata,
        ))
        return RegisterServerResponse(
            server_id=record.server_id,
            name=record.name,
            endpoint=record.endpoint,
            status=record.status,
        )

    @app.get("/servers")
    async def list_servers(status: Optional[str] = None):
        """List registered MCP servers."""
        s = _get_storage()
        servers = await s.list_servers(status=status)
        result = []
        for srv in servers:
            tools = await s.list_tools(server_id=srv.server_id)
            result.append(ServerInfo(
                server_id=srv.server_id,
                name=srv.name,
                endpoint=srv.endpoint,
                description=srv.description,
                auth_methods=srv.auth_methods,
                status=srv.status,
                tool_count=len(tools),
            ))
        return {"servers": result}

    @app.get("/servers/{server_id}")
    async def get_server(server_id: str):
        """Get details for a specific server, including its tools."""
        s = _get_storage()
        server = await s.get_server(server_id)
        if not server:
            raise HTTPException(status_code=404, detail="Server not found")

        tools = await s.list_tools(server_id=server_id)
        return {
            "server": ServerInfo(
                server_id=server.server_id,
                name=server.name,
                endpoint=server.endpoint,
                description=server.description,
                auth_methods=server.auth_methods,
                status=server.status,
                tool_count=len(tools),
            ),
            "tools": [
                ToolInfo(
                    tool_id=t.tool_id,
                    server_id=t.server_id,
                    name=t.name,
                    description=t.description,
                    input_schema=t.input_schema,
                    category=t.category,
                    tags=t.tags,
                    version=t.version,
                )
                for t in tools
            ],
        }

    @app.delete("/servers/{server_id}")
    async def deactivate_server(server_id: str, _t: str = Depends(require_registry_auth)):
        """Deactivate (soft-delete) a server."""
        s = _get_storage()
        if not await s.deactivate_server(server_id):
            raise HTTPException(status_code=404, detail="Server not found")
        return {"status": "deactivated", "server_id": server_id}

    # --- Tool endpoints ---

    @app.post("/servers/{server_id}/tools", response_model=RegisterToolResponse)
    async def register_tool(server_id: str, req: RegisterToolRequest, _t: str = Depends(require_registry_auth)):
        """Register a tool under a server."""
        s = _get_storage()
        server = await s.get_server(server_id)
        if not server:
            raise HTTPException(status_code=404, detail="Server not found")

        tool_id = str(uuid.uuid4())
        record = await s.register_tool(ToolRecord(
            tool_id=tool_id,
            server_id=server_id,
            name=req.name,
            description=req.description,
            input_schema=req.input_schema,
            category=req.category,
            tags=req.tags,
            version=req.version,
        ))
        return RegisterToolResponse(
            tool_id=record.tool_id,
            server_id=record.server_id,
            name=record.name,
        )

    @app.get("/tools")
    async def list_tools(
        q: Optional[str] = Query(None, description="Search query"),
        category: Optional[str] = None,
        server_id: Optional[str] = None,
    ):
        """List or search tools."""
        s = _get_storage()
        if q:
            tools = await s.search_tools(q)
        else:
            tools = await s.list_tools(server_id=server_id, category=category)

        return {
            "tools": [
                ToolInfo(
                    tool_id=t.tool_id,
                    server_id=t.server_id,
                    name=t.name,
                    description=t.description,
                    input_schema=t.input_schema,
                    category=t.category,
                    tags=t.tags,
                    version=t.version,
                )
                for t in tools
            ]
        }

    @app.get("/tools/{tool_id}")
    async def get_tool(tool_id: str):
        """Get details for a specific tool."""
        s = _get_storage()
        tool = await s.get_tool(tool_id)
        if not tool:
            raise HTTPException(status_code=404, detail="Tool not found")

        return ToolInfo(
            tool_id=tool.tool_id,
            server_id=tool.server_id,
            name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
            category=tool.category,
            tags=tool.tags,
            version=tool.version,
        )

    @app.delete("/tools/{tool_id}")
    async def delete_tool(tool_id: str, _t: str = Depends(require_registry_auth)):
        """Delete a tool registration."""
        s = _get_storage()
        if not await s.delete_tool(tool_id):
            raise HTTPException(status_code=404, detail="Tool not found")
        return {"status": "deleted", "tool_id": tool_id}

    # --- Audit endpoint ---

    @app.get("/audit")
    async def list_audit_events(limit: int = Query(50, le=200)):
        """Return recent audit events."""
        s = _get_storage()
        events = await s.list_audit_events(limit=limit)
        return {
            "events": [
                AuditEventInfo(
                    event_id=e.event_id,
                    user_id=e.user_id,
                    action=e.action,
                    resource=e.resource,
                    server_name=e.server_name,
                    result=e.result,
                    trace_id=e.trace_id,
                    duration_ms=e.duration_ms,
                    created_at=e.created_at,
                )
                for e in events
            ]
        }

    return app


# Module-level app for uvicorn
app = create_registry_app()


# --- CLI entry point ---

def main():
    """Run the registry server standalone."""
    import uvicorn
    app = create_registry_app()
    uvicorn.run(app, host="0.0.0.0", port=8010)  # nosec B104 — container service bind


if __name__ == "__main__":
    main()
