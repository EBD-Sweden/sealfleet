"""Tests for resolve_sealed_inputs() and sealed handle helpers."""

import base64
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import router


@pytest.mark.asyncio
async def test_resolve_sealed_inputs_passthrough():
    """Inputs with no __handle keys → returned unchanged."""
    inputs = {"location": "Stockholm", "units": "celsius"}
    with patch("router._get_db", return_value=None):
        result = await router.resolve_sealed_inputs(inputs)
    assert result == inputs


@pytest.mark.asyncio
async def test_resolve_sealed_inputs_with_handle(monkeypatch):
    """Mock _resolve_handle_from_db to return {"value": "secret-api-key"}.
    Input: {"api_key": {"__handle": "some-uuid"}}
    Result: {"api_key": "secret-api-key"}."""
    monkeypatch.setattr(
        router, "_resolve_handle_from_db",
        lambda h, **kwargs: {"label": "api_key", "value": "secret-api-key"},
    )
    inputs = {"api_key": {"__handle": "some-uuid"}}
    result = await router.resolve_sealed_inputs(inputs, tenant_id="tenant-a", subject_id="subject-a")
    assert result == {"api_key": "secret-api-key"}


@pytest.mark.asyncio
async def test_resolve_sealed_inputs_missing_handle(monkeypatch):
    """Mock _resolve_handle_from_db to return None.
    Input: {"api_key": {"__handle": "nonexistent-uuid"}}
    Result: {"api_key": {"__handle": "nonexistent-uuid"}} (passthrough on failure)."""
    monkeypatch.setattr(router, "_resolve_handle_from_db", lambda h, **kwargs: None)
    inputs = {"api_key": {"__handle": "nonexistent-uuid"}}
    result = await router.resolve_sealed_inputs(inputs, tenant_id="tenant-a", subject_id="subject-a")
    assert result == {"api_key": {"__handle": "nonexistent-uuid"}}


@pytest.mark.asyncio
async def test_resolve_sealed_inputs_mixed(monkeypatch):
    """Mix of normal and handle inputs.
    location unchanged, api_key resolved."""
    monkeypatch.setattr(
        router, "_resolve_handle_from_db",
        lambda h, **kwargs: {"label": "api_key", "value": "resolved-secret"} if h == "abc" else None,
    )
    inputs = {"location": "Berlin", "api_key": {"__handle": "abc"}}
    result = await router.resolve_sealed_inputs(inputs, tenant_id="tenant-a", subject_id="subject-a")
    assert result["location"] == "Berlin"
    assert result["api_key"] == "resolved-secret"


@pytest.mark.asyncio
async def test_in_process_resolve_requires_tenant_and_subject_scope(monkeypatch):
    """Sealed handles must not resolve without an authenticated owner scope."""
    calls = []
    events = []

    def fake_resolve(handle_id, *, tenant_id="system", subject_id=None):
        calls.append((handle_id, tenant_id, subject_id))
        return {"label": "api_key", "value": "secret-api-key"}

    monkeypatch.setattr(router, "_resolve_handle_from_db", fake_resolve)
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))

    inputs = {"api_key": {"__handle": "h-owned"}}
    result = await router.resolve_sealed_inputs(inputs)

    assert result == inputs
    assert calls == []
    assert any(
        event["action"] == "sealed_handle.resolve"
        and event["result"] == "denied"
        and event["resource"] == "sealed:h-owned"
        and event["payload"]["reason"] == "missing_owner_scope"
        for event in events
    )
    assert "secret-api-key" not in str(events)


def test_sealed_handle_base64_encoding():
    """Verify the encoding used: base64.b64encode(value.encode()).decode()
    and decode: base64.b64decode(encoded).decode()."""
    value = "sk-sup...-123"
    encoded = base64.b64encode(value.encode()).decode()
    decoded = base64.b64decode(encoded).decode()
    assert decoded == value


def test_get_fernet_fails_closed_without_key_in_production(monkeypatch):
    """Production-like deployments must not silently fall back to the dev encryption key."""
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("MCPFINDER_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("MCPFINDER_DEPLOYMENT_ENV", "production")

    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY"):
        router._get_fernet()


def test_resolve_handle_update_is_single_use_and_tenant_scoped(monkeypatch):
    """Resolve must be an atomic UPDATE with strict tenant/subject ownership."""
    executed = []

    class Cursor:
        def execute(self, sql, params=None):
            executed.append((" ".join(sql.split()), params))

        def fetchone(self):
            return None

        def close(self):
            pass

    class Conn:
        def cursor(self):
            return Cursor()

    monkeypatch.setattr(router, "_get_db", lambda: Conn())

    assert router._resolve_handle_from_db("h1", tenant_id="tenant-a", subject_id="user-a") is None

    sql, params = executed[0]
    assert sql.startswith("UPDATE sealed_handles SET used_at = now()")
    assert "tenant_id = %s" in sql
    assert "subject_id = %s" in sql
    assert "subject_id IS NULL" not in sql
    assert "used_at IS NULL" in sql
    assert "expires_at IS NULL OR expires_at > now()" in sql
    assert "RETURNING label, encrypted_value, created_at, expires_at" in sql
    assert params == ("h1", "tenant-a", "user-a")


def test_list_sealed_handles_requires_exact_subject_owner(monkeypatch):
    """List must not expose legacy/null-subject handles to normal callers."""
    executed = []

    class Cursor:
        def execute(self, sql, params=None):
            executed.append((" ".join(sql.split()), params))

        def fetchall(self):
            return []

        def close(self):
            pass

    class Conn:
        def cursor(self):
            return Cursor()

    monkeypatch.setattr(router, "_get_db", lambda: Conn())

    assert router._list_sealed_handles(tenant_id="tenant-a", subject_id="user-a") == []

    sql, params = executed[0]
    assert "tenant_id = %s" in sql
    assert "subject_id = %s" in sql
    assert "subject_id IS NULL" not in sql
    assert params == ("tenant-a", "user-a")


def test_delete_sealed_handle_requires_exact_subject_owner(monkeypatch):
    """Delete must not invalidate legacy/null-subject handles for normal callers."""
    executed = []

    class Cursor:
        def execute(self, sql, params=None):
            executed.append((" ".join(sql.split()), params))

        def fetchone(self):
            return None

        def close(self):
            pass

    class Conn:
        def cursor(self):
            return Cursor()

    monkeypatch.setattr(router, "_get_db", lambda: Conn())

    assert router._delete_sealed_handle("h1", tenant_id="tenant-a", subject_id="user-a") is False

    sql, params = executed[0]
    assert "tenant_id = %s" in sql
    assert "subject_id = %s" in sql
    assert "subject_id IS NULL" not in sql
    assert params == ("h1", "tenant-a", "user-a")


@pytest.mark.asyncio
async def test_in_process_resolve_denies_wrong_subject(monkeypatch):
    """Pipeline-time sealed resolution must leave wrong-subject handles unresolved."""
    calls = []

    def fake_resolve(handle_id, *, tenant_id="system", subject_id=None):
        calls.append((handle_id, tenant_id, subject_id))
        if tenant_id == "tenant-a" and subject_id == "owner-user":
            return {"label": "api_key", "value": "secret-api-key"}
        return None

    monkeypatch.setattr(router, "_resolve_handle_from_db", fake_resolve)

    inputs = {"api_key": {"__handle": "h-owned"}}
    wrong_subject = await router.resolve_sealed_inputs(inputs, tenant_id="tenant-a", subject_id="other-user")
    owner = await router.resolve_sealed_inputs(inputs, tenant_id="tenant-a", subject_id="owner-user")

    assert wrong_subject == inputs
    assert owner == {"api_key": "secret-api-key"}
    assert calls == [("h-owned", "tenant-a", "other-user"), ("h-owned", "tenant-a", "owner-user")]


@pytest.mark.asyncio
async def test_in_process_resolve_audits_success_and_denial_without_plaintext(monkeypatch):
    """Pipeline-time sealed resolution must emit redacted success/denial audit events."""
    events = []

    def fake_resolve(handle_id, *, tenant_id="system", subject_id=None):
        if tenant_id == "tenant-a" and subject_id == "owner-user":
            return {"label": "api_key", "value": "secret-api-key"}
        return None

    monkeypatch.setattr(router, "_resolve_handle_from_db", fake_resolve)
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))

    inputs = {"api_key": {"__handle": "h-owned"}}
    denied = await router.resolve_sealed_inputs(inputs, tenant_id="tenant-b", subject_id="owner-user", trace_id="trace-sealed")
    ok = await router.resolve_sealed_inputs(inputs, tenant_id="tenant-a", subject_id="owner-user", trace_id="trace-sealed")

    assert denied == inputs
    assert ok == {"api_key": "secret-api-key"}
    assert any(
        event["action"] == "sealed_handle.resolve"
        and event["result"] == "denied"
        and event["resource"] == "sealed:h-owned"
        and event["tenant_id"] == "tenant-b"
        and event["user_id"] == "owner-user"
        and event["trace_id"] == "trace-sealed"
        for event in events
    )
    assert any(
        event["action"] == "sealed_handle.resolve"
        and event["result"] == "ok"
        and event["resource"] == "sealed:h-owned"
        and event["tenant_id"] == "tenant-a"
        and event["user_id"] == "owner-user"
        and event["trace_id"] == "trace-sealed"
        and event["payload"]["label"] == "api_key"
        for event in events
    )
    assert "secret-api-key" not in str(events)
