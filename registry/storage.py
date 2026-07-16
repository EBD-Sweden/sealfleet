"""Tool metadata storage for the registry.

PostgreSQL-backed storage using asyncpg for tool and server metadata.
Supports CRUD operations, simple text search, and audit event retrieval.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional

import asyncpg


@dataclass
class ServerRecord:
    """An MCP server registered with the discovery service."""
    server_id: str
    name: str
    endpoint: str
    description: str = ""
    auth_methods: list[str] = field(default_factory=list)
    status: str = "online"  # online, inactive, deprecated
    registered_at: float = 0.0
    updated_at: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class ToolRecord:
    """A tool registered under an MCP server."""
    tool_id: str
    server_id: str
    name: str
    description: str = ""
    input_schema: dict = field(default_factory=dict)
    category: str = ""
    tags: list[str] = field(default_factory=list)
    version: str = "0.1.0"
    registered_at: float = 0.0
    updated_at: float = 0.0


@dataclass
class AuditRecord:
    """An audit event from the audit_events table."""
    event_id: str
    user_id: str
    action: str
    resource: str
    server_name: str
    result: str
    trace_id: str
    duration_ms: int
    created_at: str


class RegistryStorage:
    """PostgreSQL-backed storage for registry data using asyncpg."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    @classmethod
    async def create(cls, dsn: str) -> RegistryStorage:
        """Create a RegistryStorage with a connection pool."""
        pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
        return cls(pool)

    # --- Server CRUD ---

    async def register_server(self, server: ServerRecord) -> ServerRecord:
        """Register or update an MCP server."""
        now = time.time()
        server.registered_at = server.registered_at or now
        server.updated_at = now

        def _ts(v): return datetime.fromtimestamp(v, tz=timezone.utc) if isinstance(v, (int, float)) else v
        await self._pool.execute(
            """INSERT INTO servers
               (server_id, name, endpoint, description, auth_methods,
                status, registered_at, updated_at, metadata)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
               ON CONFLICT (server_id) DO UPDATE SET
                 name = EXCLUDED.name,
                 endpoint = EXCLUDED.endpoint,
                 description = EXCLUDED.description,
                 auth_methods = EXCLUDED.auth_methods,
                 status = EXCLUDED.status,
                 updated_at = EXCLUDED.updated_at,
                 metadata = EXCLUDED.metadata""",
            server.server_id,
            server.name,
            server.endpoint,
            server.description,
            json.dumps(server.auth_methods),
            server.status,
            float(server.registered_at),
            _ts(server.updated_at),
            json.dumps(server.metadata),
        )
        return server

    async def get_server(self, server_id: str) -> Optional[ServerRecord]:
        row = await self._pool.fetchrow(
            "SELECT * FROM servers WHERE server_id = $1", server_id
        )
        return self._row_to_server(row) if row else None

    async def list_servers(self, status: Optional[str] = None) -> list[ServerRecord]:
        if status:
            rows = await self._pool.fetch(
                "SELECT * FROM servers WHERE status = $1 ORDER BY name", status
            )
        else:
            rows = await self._pool.fetch(
                "SELECT * FROM servers ORDER BY name"
            )
        return [self._row_to_server(r) for r in rows]

    async def deactivate_server(self, server_id: str) -> bool:
        result = await self._pool.execute(
            "UPDATE servers SET status = 'inactive', updated_at = $1 WHERE server_id = $2",
            time.time(), server_id,
        )
        return result != "UPDATE 0"

    # --- Tool CRUD ---

    async def register_tool(self, tool: ToolRecord) -> ToolRecord:
        """Register or update a tool."""
        now = time.time()
        tool.registered_at = tool.registered_at or now
        tool.updated_at = now

        # Resolve the UUID `id` from servers table using the string server_id
        server_uuid = await self._pool.fetchval(
            "SELECT id FROM servers WHERE server_id = $1 OR id::text = $1",
            tool.server_id,
        )
        if not server_uuid:
            raise ValueError(f"Server not found for server_id: {tool.server_id}")

        def _ts(v): return datetime.fromtimestamp(v, tz=timezone.utc) if isinstance(v, (int, float)) else v
        await self._pool.execute(
            """INSERT INTO tools
               (tool_id, server_id, server_id_str, name, description, input_schema,
                category, tags, version, registered_at, updated_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
               ON CONFLICT (tool_id) DO UPDATE SET
                 server_id     = EXCLUDED.server_id,
                 server_id_str = EXCLUDED.server_id_str,
                 name = EXCLUDED.name,
                 description = EXCLUDED.description,
                 input_schema = EXCLUDED.input_schema,
                 category = EXCLUDED.category,
                 tags = EXCLUDED.tags,
                 version = EXCLUDED.version,
                 updated_at = EXCLUDED.updated_at""",
            tool.tool_id,
            server_uuid,
            tool.server_id,
            tool.name,
            tool.description,
            json.dumps(tool.input_schema),
            tool.category,
            json.dumps(tool.tags),
            tool.version,
            float(tool.registered_at),
            _ts(tool.updated_at),
        )
        return tool

    async def get_tool(self, tool_id: str) -> Optional[ToolRecord]:
        row = await self._pool.fetchrow(
            "SELECT * FROM tools WHERE tool_id = $1", tool_id
        )
        return self._row_to_tool(row) if row else None

    async def list_tools(
        self,
        server_id: Optional[str] = None,
        category: Optional[str] = None,
    ) -> list[ToolRecord]:
        query = "SELECT * FROM tools WHERE 1=1"
        params: list = []
        idx = 1

        if server_id:
            query += f" AND server_id_str = ${idx}"
            params.append(server_id)
            idx += 1
        if category:
            query += f" AND category = ${idx}"
            params.append(category)
            idx += 1

        query += " ORDER BY name"
        rows = await self._pool.fetch(query, *params)
        return [self._row_to_tool(r) for r in rows]

    async def search_tools(self, query: str) -> list[ToolRecord]:
        """Search tools by name or description (case-insensitive)."""
        pattern = f"%{query}%"
        rows = await self._pool.fetch(
            """SELECT * FROM tools
               WHERE name ILIKE $1 OR description ILIKE $2
               ORDER BY name""",
            pattern, pattern,
        )
        return [self._row_to_tool(r) for r in rows]

    async def delete_tool(self, tool_id: str) -> bool:
        result = await self._pool.execute(
            "DELETE FROM tools WHERE tool_id = $1", tool_id
        )
        return result != "DELETE 0"

    # --- Audit Events ---

    async def list_audit_events(self, limit: int = 50) -> list[AuditRecord]:
        """Return recent audit events ordered by creation time descending."""
        rows = await self._pool.fetch(
            "SELECT * FROM audit_events ORDER BY created_at DESC LIMIT $1", limit
        )
        return [self._row_to_audit(r) for r in rows]

    # --- Helpers ---

    @staticmethod
    def _to_epoch(val) -> float:
        """Convert datetime, float, int, or None to epoch float."""
        if val is None:
            return 0.0
        if isinstance(val, (int, float)):
            return float(val)
        try:
            return val.timestamp()
        except Exception:
            return 0.0

    @staticmethod
    def _parse_json(val, default):
        if val is None:
            return default
        if isinstance(val, str):
            return json.loads(val)
        return val  # already parsed (asyncpg returns dicts for jsonb)

    def _row_to_server(self, row: asyncpg.Record) -> ServerRecord:
        return ServerRecord(
            server_id=row["server_id"] or str(row["id"]),
            name=row["name"],
            endpoint=row["endpoint"],
            description=row["description"] or "",
            auth_methods=self._parse_json(row["auth_methods"], []),
            status=row["status"] or "active",
            registered_at=self._to_epoch(row["registered_at"] or row.get("created_at")),
            updated_at=self._to_epoch(row["updated_at"]),
            metadata=self._parse_json(row["metadata"], {}),
        )

    def _row_to_tool(self, row: asyncpg.Record) -> ToolRecord:
        return ToolRecord(
            tool_id=row["tool_id"] or str(row["id"]),
            server_id=str(row.get("server_id_str") or row["server_id"] or ""),
            name=row["name"],
            description=row["description"] or "",
            input_schema=self._parse_json(row["input_schema"], {}),
            category=row.get("category") or "",
            tags=self._parse_json(row.get("tags"), []),
            version=row.get("version") or "1.0.0",
            registered_at=self._to_epoch(row.get("registered_at") or row.get("created_at")),
            updated_at=self._to_epoch(row.get("updated_at")),
        )

    def _row_to_audit(self, row: asyncpg.Record) -> AuditRecord:
        created = row.get("created_at")
        created_str = created.isoformat() if hasattr(created, "isoformat") else str(created or "")
        return AuditRecord(
            event_id=str(row.get("event_id") or row.get("id") or ""),
            user_id=row.get("user_id", "") or "",
            action=row.get("action", "") or "",
            resource=row.get("resource", "") or "",
            server_name=row.get("server_name", "") or "",
            result=row.get("result", "") or "",
            trace_id=row.get("trace_id", "") or "",
            duration_ms=int(row.get("duration_ms", 0) or 0),
            created_at=created_str,
        )

    async def close(self) -> None:
        await self._pool.close()
