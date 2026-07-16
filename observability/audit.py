"""Audit event schema and storage.

Provides immutable audit logging for compliance, debugging, and forensics.
Every tool call, policy decision, and credential access is recorded.

MVP: SQLite-backed audit log.
Later: Append-only log with cryptographic chaining, S3 archival.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("mcpfinder.observability.audit")


@dataclass
class AuditEvent:
    """An immutable audit log entry.

    Follows the 5W+H pattern:
        Who   — user_id, service
        What  — action performed
        Where — resource affected
        When  — timestamp
        Why   — context/reason
        How   — result, trace_id
    """
    event_id: str
    timestamp: float
    # Who
    user_id: str
    service: str = ""
    # What
    action: str = ""
    # Where
    resource: str = ""
    # Why
    reason: str = ""
    # How
    result: str = ""  # success, denied, error
    trace_id: str = ""
    # Extra context
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "user_id": self.user_id,
            "service": self.service,
            "action": self.action,
            "resource": self.resource,
            "reason": self.reason,
            "result": self.result,
            "trace_id": self.trace_id,
            "metadata": self.metadata,
        }


class AuditLog:
    """Immutable audit log backed by SQLite.

    Events are append-only — no updates or deletes.
    """

    def __init__(self, db_path: str = ":memory:"):
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS audit_events (
                event_id TEXT PRIMARY KEY,
                timestamp REAL NOT NULL,
                user_id TEXT NOT NULL,
                service TEXT DEFAULT '',
                action TEXT DEFAULT '',
                resource TEXT DEFAULT '',
                reason TEXT DEFAULT '',
                result TEXT DEFAULT '',
                trace_id TEXT DEFAULT '',
                metadata TEXT DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_events(user_id);
            CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_events(action);
            CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_events(resource);
            CREATE INDEX IF NOT EXISTS idx_audit_trace ON audit_events(trace_id);
            CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_events(timestamp);
        """)
        self._conn.commit()

    def record(
        self,
        user_id: str,
        action: str,
        resource: str,
        result: str,
        trace_id: str = "",
        service: str = "",
        reason: str = "",
        **metadata: Any,
    ) -> AuditEvent:
        """Record an audit event.

        Args:
            user_id: Who performed the action.
            action: What was done (e.g., "call_tool", "policy_check").
            resource: What was affected (e.g., "crypto.price_quote").
            result: Outcome ("success", "denied", "error").
            trace_id: Correlation ID for distributed tracing.
            service: Which service recorded this.
            reason: Why the action was taken/denied.
            **metadata: Additional context.

        Returns:
            The recorded AuditEvent.
        """
        event = AuditEvent(
            event_id=uuid.uuid4().hex,
            timestamp=time.time(),
            user_id=user_id,
            service=service,
            action=action,
            resource=resource,
            reason=reason,
            result=result,
            trace_id=trace_id,
            metadata=metadata,
        )

        self._conn.execute(
            """INSERT INTO audit_events
               (event_id, timestamp, user_id, service, action, resource,
                reason, result, trace_id, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.event_id,
                event.timestamp,
                event.user_id,
                event.service,
                event.action,
                event.resource,
                event.reason,
                event.result,
                event.trace_id,
                json.dumps(event.metadata),
            ),
        )
        self._conn.commit()

        logger.info(
            "audit user=%s action=%s resource=%s result=%s",
            user_id, action, resource, result,
        )
        return event

    def query(
        self,
        user_id: Optional[str] = None,
        action: Optional[str] = None,
        resource: Optional[str] = None,
        trace_id: Optional[str] = None,
        since: Optional[float] = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """Query audit events with filters."""
        query = "SELECT * FROM audit_events WHERE 1=1"
        params: list = []

        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        if action:
            query += " AND action = ?"
            params.append(action)
        if resource:
            query += " AND resource = ?"
            params.append(resource)
        if trace_id:
            query += " AND trace_id = ?"
            params.append(trace_id)
        if since:
            query += " AND timestamp >= ?"
            params.append(since)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_event(r) for r in rows]

    def count(
        self,
        user_id: Optional[str] = None,
        action: Optional[str] = None,
        since: Optional[float] = None,
    ) -> int:
        """Count audit events matching filters."""
        query = "SELECT COUNT(*) FROM audit_events WHERE 1=1"
        params: list = []

        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        if action:
            query += " AND action = ?"
            params.append(action)
        if since:
            query += " AND timestamp >= ?"
            params.append(since)

        return self._conn.execute(query, params).fetchone()[0]

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> AuditEvent:
        return AuditEvent(
            event_id=row["event_id"],
            timestamp=row["timestamp"],
            user_id=row["user_id"],
            service=row["service"],
            action=row["action"],
            resource=row["resource"],
            reason=row["reason"],
            result=row["result"],
            trace_id=row["trace_id"],
            metadata=json.loads(row["metadata"]),
        )

    def close(self) -> None:
        self._conn.close()
