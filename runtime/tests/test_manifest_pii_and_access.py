"""Manifest-declared PII redaction, RBAC gaps, and GDPR audit tagging tests."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

QA_TENANT = "qa-tenant"


# ---------------------------------------------------------------------------
# redact_pii_fields — path semantics
# ---------------------------------------------------------------------------


def test_redact_pii_fields_nested_paths_lists_and_wildcards():
    from policy_hooks import PII_REDACTED_MARKER, redact_pii_fields

    value = {
        "customer": {"email": "a@b.se", "name": "Anna", "ssn": "19900101-1234"},
        "orders": [
            {"contact": "x@y.se", "total": 10},
            {"contact": "z@w.se", "total": 20},
        ],
        "meta": {"env": "prod"},
    }
    redacted, hits = redact_pii_fields(
        value, ["customer.email", "customer.ssn", "orders.contact", "missing.path"]
    )
    assert redacted["customer"]["email"] == PII_REDACTED_MARKER
    assert redacted["customer"]["ssn"] == PII_REDACTED_MARKER
    assert redacted["customer"]["name"] == "Anna"  # not declared -> untouched
    assert [o["contact"] for o in redacted["orders"]] == [PII_REDACTED_MARKER] * 2
    assert [o["total"] for o in redacted["orders"]] == [10, 20]
    assert hits == ["customer.email", "customer.ssn", "orders.contact"]
    # Original object is never mutated
    assert value["customer"]["email"] == "a@b.se"

    wild, wild_hits = redact_pii_fields({"users": {"u1": {"email": "a"}, "u2": {"email": "b"}}}, ["users.*.email"])
    assert wild["users"]["u1"]["email"] == PII_REDACTED_MARKER
    assert wild["users"]["u2"]["email"] == PII_REDACTED_MARKER
    assert wild_hits == ["users.*.email"]

    untouched, no_hits = redact_pii_fields({"a": 1}, ["b.c"])
    assert untouched == {"a": 1}
    assert no_hits == []


def test_manifest_pii_guard_is_always_on_even_with_hooks_disabled():
    from policy_hooks import build_runtime_hook_manager, ManifestPiiGuard

    manager = build_runtime_hook_manager({"runtime_hooks": {"enabled": False}})
    assert any(isinstance(h, ManifestPiiGuard) for h in manager.hooks)

    manager_cfg = build_runtime_hook_manager(
        {"runtime_hooks": {"enabled": True, "hooks": [{"type": "secrets_pii_guard"}]}}
    )
    assert any(isinstance(h, ManifestPiiGuard) for h in manager_cfg.hooks)


@pytest.mark.asyncio
async def test_manifest_pii_guard_redacts_declared_fields_and_audits_names_only():
    from policy_hooks import (
        PII_REDACTED_MARKER,
        HookManager,
        ManifestPiiGuard,
        RuntimeHookContext,
    )

    manager = HookManager([ManifestPiiGuard()])
    ctx = RuntimeHookContext(
        trace_id="t", tenant_id=QA_TENANT, subject_id="u", mcp="demo",
        tool="get_customer", transport="http", pii_fields=("customer.email",),
    )
    result = await manager.run_post_call(ctx, {"customer": {"email": "secret@pii.se"}})
    assert result["customer"]["email"] == PII_REDACTED_MARKER
    event = manager.audit_events[-1]
    assert event["result"] == "redacted"
    assert "customer.email" in event["reason"]
    assert "secret@pii.se" not in str(event)

    # No declared fields -> passthrough allow
    ctx_plain = RuntimeHookContext(
        trace_id="t", tenant_id=QA_TENANT, subject_id="u", mcp="demo",
        tool="get_customer", transport="http",
    )
    untouched = await manager.run_post_call(ctx_plain, {"customer": {"email": "kept@ok.se"}})
    assert untouched["customer"]["email"] == "kept@ok.se"


# ---------------------------------------------------------------------------
# Manifest parsing: pii_fields + access
# ---------------------------------------------------------------------------


def test_parse_manifest_pii_fields_and_access(test_app):
    import router

    data = {
        "name": "crm-mcp",
        "pii_fields": ["owner_email"],
        "tools": [
            {"name": "get_customer", "pii_fields": ["customer.email", "customer.ssn"]},
            {"name": "list_orders"},
            "untyped_tool",
        ],
    }
    declared = router._parse_manifest_pii_fields(data)
    assert declared == {
        "*": ["owner_email"],
        "get_customer": ["customer.email", "customer.ssn"],
    }

    access = router._parse_manifest_access({"allowed_roles": ["trading-ops"], "allowed_groups": ["g1"]})
    assert access is not None
    assert access.allowed_roles == ["trading-ops"]
    assert access.allowed_groups == ["g1"]
    assert access.restricted

    assert router._parse_manifest_access({}) is None
    assert router._parse_manifest_access(None) is None
    assert router._parse_manifest_access({"allowed_roles": []}) is None


def test_execution_boundary_passes_manifest_pii_fields(test_app, monkeypatch):
    import router
    from policy_hooks import PII_REDACTED_MARKER

    router.runtime_hook_manager = router.build_runtime_hook_manager({"runtime_hooks": {"enabled": False}})
    monkeypatch.setattr(router.scale_manager, "ensure_running", AsyncMock(return_value=True))
    monkeypatch.setattr(router.scale_manager, "record_call", AsyncMock(return_value=None))
    monkeypatch.setattr(router, "_write_runtime_hook_audit_events", lambda events: None)

    async def fake_post(self, url, json=None):
        return httpx.Response(
            200,
            json={"customer": {"email": "raw@pii.se"}, "ok": True},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    manifest = router.McpManifest(
        name="crm-mcp", endpoint="http://crm.local", publishes=[], subscribes=[],
        tools=["get_customer"], pii_fields={"get_customer": ["customer.email"]},
    )

    async def run():
        async with httpx.AsyncClient() as client:
            return await router._execute_mcp_tool(
                client, manifest=manifest, mcp_name="crm-mcp", tool="get_customer",
                inputs={}, trace_id="t", tenant_id=QA_TENANT,
            )

    result, error = asyncio.run(run())
    assert error is None
    assert result["customer"]["email"] == PII_REDACTED_MARKER
    assert result["ok"] is True


def test_seeded_manifest_guards_cannot_be_dropped_by_reregistration(test_app):
    import router

    router._yaml_seeded_access["locked-mcp"] = router.McpAccessPolicy(allowed_roles=["ops"])
    router._yaml_seeded_pii_fields["locked-mcp"] = {"tool_a": ["email"]}
    try:
        manifest = router.McpManifest(
            name="locked-mcp", endpoint="http://x", publishes=[], subscribes=[], tools=["tool_a"],
        )
        router._merge_seeded_manifest_guards(manifest)
        assert manifest.access is not None
        assert manifest.access.allowed_roles == ["ops"]
        assert manifest.pii_fields == {"tool_a": ["email"]}

        # A re-registration may ADD pii declarations but not drop seeded ones
        manifest2 = router.McpManifest(
            name="locked-mcp", endpoint="http://x", publishes=[], subscribes=[],
            tools=["tool_a"], pii_fields={"tool_a": ["phone"]},
        )
        router._merge_seeded_manifest_guards(manifest2)
        assert manifest2.pii_fields == {"tool_a": ["email", "phone"]}
    finally:
        router._yaml_seeded_access.pop("locked-mcp", None)
        router._yaml_seeded_pii_fields.pop("locked-mcp", None)


# ---------------------------------------------------------------------------
# RBAC: manifest access gate + user MCP enforcement
# ---------------------------------------------------------------------------


def _fake_request(*, auth_type, user_id="", is_admin=False, identity=None, tenant=QA_TENANT):
    return SimpleNamespace(
        state=SimpleNamespace(
            auth_type=auth_type,
            user_id=user_id,
            is_admin=is_admin,
            identity=identity or {},
            tenant_id=tenant,
        )
    )


def test_manifest_access_allows_roles_groups_and_admin(test_app, monkeypatch):
    import router

    manifest = router.McpManifest(
        name="gated", endpoint="http://x", publishes=[], subscribes=[], tools=["t"],
        access=router.McpAccessPolicy(allowed_roles=["trading-ops"], allowed_groups=["idp-traders"]),
    )

    monkeypatch.setattr(router, "_user_role_names", lambda tenant, user, groups=None: {"viewer"})
    assert not router._manifest_access_allows(
        manifest, tenant_id=QA_TENANT, user_id="u1", groups=[], is_admin=False
    )
    assert router._manifest_access_allows(
        manifest, tenant_id=QA_TENANT, user_id="u1", groups=[], is_admin=True
    )
    assert router._manifest_access_allows(
        manifest, tenant_id=QA_TENANT, user_id="u1", groups=["idp-traders"], is_admin=False
    )

    monkeypatch.setattr(router, "_user_role_names", lambda tenant, user, groups=None: {"trading-ops"})
    assert router._manifest_access_allows(
        manifest, tenant_id=QA_TENANT, user_id="u1", groups=[], is_admin=False
    )

    open_manifest = router.McpManifest(
        name="open", endpoint="http://x", publishes=[], subscribes=[], tools=["t"],
    )
    assert router._manifest_access_allows(
        open_manifest, tenant_id=QA_TENANT, user_id="u1", groups=[], is_admin=False
    )


def test_enforce_user_mcp_access_paths(test_app, monkeypatch):
    import router
    from fastapi import HTTPException

    router.manifests["plain-mcp"] = router.McpManifest(
        name="plain-mcp", endpoint="http://x", publishes=[], subscribes=[], tools=["t"],
    )

    # Pure API key (no delegated user) -> never gated here
    router._enforce_user_mcp_access(_fake_request(auth_type="api_key"), "plain-mcp", "t")

    # user JWT admin -> allowed without DB lookups
    router._enforce_user_mcp_access(
        _fake_request(auth_type="user_jwt", user_id="u1", is_admin=True), "plain-mcp", "t"
    )

    # user JWT non-admin without grant -> 403 with tool named
    monkeypatch.setattr(router, "_check_user_mcp_permission", lambda *a, **k: False)
    with pytest.raises(HTTPException) as exc:
        router._enforce_user_mcp_access(
            _fake_request(auth_type="user_jwt", user_id="u1"), "plain-mcp", "t"
        )
    assert exc.value.status_code == 403
    assert "plain-mcp" in str(exc.value.detail) and "'t'" in str(exc.value.detail)

    # Delegated API-key identity is enforced like a user
    delegated = _fake_request(
        auth_type="api_key", user_id="u2",
        identity={"delegated_from": {"api_key_name": "portal"}, "groups": ["g"]},
    )
    with pytest.raises(HTTPException):
        router._enforce_user_mcp_access(delegated, "plain-mcp", "t")

    # ...and allowed when a grant exists (tool + groups threaded through)
    seen = {}

    def grant(tenant_id, user_id, server, *, tool=None, groups=None):
        seen.update(tenant=tenant_id, user=user_id, server=server, tool=tool, groups=groups)
        return True

    monkeypatch.setattr(router, "_check_user_mcp_permission", grant)
    router._enforce_user_mcp_access(delegated, "plain-mcp", "t")
    assert seen == {
        "tenant": QA_TENANT, "user": "u2", "server": "plain-mcp",
        "tool": "t", "groups": ["g"],
    }

    # Manifest access gate rejects even before the permission lookup
    router.manifests["gated-mcp"] = router.McpManifest(
        name="gated-mcp", endpoint="http://x", publishes=[], subscribes=[], tools=["t"],
        access=router.McpAccessPolicy(allowed_roles=["trading-ops"]),
    )
    monkeypatch.setattr(router, "_user_role_names", lambda *a, **k: set())
    with pytest.raises(HTTPException) as exc2:
        router._enforce_user_mcp_access(
            _fake_request(auth_type="user_jwt", user_id="u1"), "gated-mcp", "t"
        )
    assert "roles/groups" in str(exc2.value.detail)

    router.manifests.pop("plain-mcp", None)
    router.manifests.pop("gated-mcp", None)


def test_allowed_tools_clause(test_app):
    import router

    sql, params = router._allowed_tools_clause(None)
    assert sql == "" and params == []

    sql, params = router._allowed_tools_clause("get_weather")
    assert "allowed_tools IS NULL" in sql
    assert "= ANY(allowed_tools)" in sql
    assert params == ["get_weather"]


@pytest.mark.asyncio
async def test_call_route_enforces_manifest_access_for_user_jwt(client, monkeypatch):
    import router

    monkeypatch.setattr(
        router,
        "_validate_user_jwt",
        lambda token: {
            "tenant_id": QA_TENANT,
            "user_id": "user-1",
            "email": "user@qa.se",
            "is_admin": False,
            "sub": "user-1",
            "groups": ["idp-others"],
        }
        if token == "user-token"
        else None,
    )
    router.manifests["gated-mcp"] = router.McpManifest(
        name="gated-mcp", endpoint="http://gated.local", publishes=[], subscribes=[],
        tools=["echo"], access=router.McpAccessPolicy(allowed_groups=["idp-traders"]),
    )
    try:
        resp = await client.post(
            "/call",
            headers={"Authorization": "Bearer user-token"},
            json={"mcp": "gated-mcp", "tool": "echo", "inputs": {}},
        )
        assert resp.status_code == 403
        assert "roles/groups" in resp.text
    finally:
        router.manifests.pop("gated-mcp", None)


# ---------------------------------------------------------------------------
# GDPR audit tagging
# ---------------------------------------------------------------------------


def test_default_audit_purpose_mapping(test_app):
    import router

    assert router._default_audit_purpose("privacy.export") == ("compliance", "legal_obligation")
    assert router._default_audit_purpose("audit.read") == ("compliance", "legal_obligation")
    assert router._default_audit_purpose("retention.prune") == ("compliance", "legal_obligation")
    assert router._default_audit_purpose("policy_deny") == ("security", "legitimate_interest")
    assert router._default_audit_purpose("token.exchange") == ("security", "legitimate_interest")
    assert router._default_audit_purpose("tool_call") == ("service_delivery", "contract")
    assert router._default_audit_purpose("") == ("service_delivery", "contract")


def test_audit_hash_fields_are_backward_compatible(test_app):
    import router

    base = dict(
        tenant_id="t", user_id="u", action="tool_call", resource="m/t",
        server_name="m", result="ok", trace_id="tr", duration_ms=1, payload_json=None,
    )
    legacy = router._audit_hash_fields(**base)
    assert "purpose" not in legacy and "lawful_basis" not in legacy

    tagged = router._audit_hash_fields(**base, purpose="service_delivery", lawful_basis="contract")
    assert tagged["purpose"] == "service_delivery"
    assert tagged["lawful_basis"] == "contract"

    # Same prev hash + different fields -> different chain entries
    h_legacy = router._audit_entry_hash("", legacy)
    h_tagged = router._audit_entry_hash("", tagged)
    assert h_legacy != h_tagged


def test_audit_payload_canonical_serialization_is_key_order_insensitive(test_app):
    """Postgres JSONB reorders keys; the hashed payload string must not care."""
    import json

    a = {"zeta": 1, "alpha": {"b": 2, "a": 1}, "mid": [1, 2]}
    b = {"alpha": {"a": 1, "b": 2}, "mid": [1, 2], "zeta": 1}  # same content, different order
    canon = lambda v: json.dumps(v, sort_keys=True, separators=(",", ":"), default=str)
    assert canon(a) == canon(b)


class _AuditVerifyCursor:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, *_args, **_kwargs):
        return None

    def fetchall(self):
        return self.rows

    def close(self):
        return None


class _AuditVerifyConn:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return _AuditVerifyCursor(self.rows)


def _audit_verify_row(router, *, payload, entry_payload_json, purpose="service_delivery", lawful_basis="contract", audit_hash_version=None):
    fields = dict(
        tenant_id="qa-tenant",
        user_id="user-1",
        action="tool_call",
        resource="demo/echo",
        server_name="demo",
        result="ok",
        trace_id="trace-1",
        duration_ms=7,
        payload_json=entry_payload_json,
        purpose=purpose,
        lawful_basis=lawful_basis,
    )
    entry_hash = router._audit_entry_hash("", router._audit_hash_fields(**fields))
    return (
        1,
        fields["tenant_id"],
        fields["user_id"],
        fields["action"],
        fields["resource"],
        fields["server_name"],
        fields["result"],
        fields["trace_id"],
        fields["duration_ms"],
        payload,
        "",
        entry_hash,
        purpose,
        lawful_basis,
        audit_hash_version,
    )


def test_audit_verify_fails_closed_for_tampered_canonical_multi_key_payload(test_app, monkeypatch):
    """A canonical/tagged row whose JSONB payload changed must not be excused as legacy."""
    import asyncio
    import json
    from types import SimpleNamespace

    import router

    original_payload = {"a": 1, "b": 2}
    tampered_payload = {"a": 999, "b": 2}
    row = _audit_verify_row(
        router,
        payload=tampered_payload,
        entry_payload_json=json.dumps(original_payload, sort_keys=True, separators=(",", ":"), default=str),
    )
    monkeypatch.setattr(router, "_authorize_action", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(router, "_get_db", lambda: _AuditVerifyConn([row]))

    result = asyncio.run(router.verify_audit_chain(SimpleNamespace(state=SimpleNamespace())))

    assert result["intact"] is False
    assert result["first_break"] == {"seq": 1, "reason": "entry_hash mismatch"}
    assert result["legacy_unverifiable_payload_seqs"] == []


def test_audit_verify_only_excuses_explicit_legacy_multi_key_payload(test_app, monkeypatch):
    """Purpose-tagged pre-canonical rows need an explicit schema marker to stay unverifiable."""
    import asyncio
    from types import SimpleNamespace

    import router

    row = _audit_verify_row(
        router,
        payload={"a": 1, "b": 2},
        entry_payload_json='{"b": 2, "a": 1}',
        audit_hash_version="legacy-json-payload-order",
    )
    monkeypatch.setattr(router, "_authorize_action", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(router, "_get_db", lambda: _AuditVerifyConn([row]))

    result = asyncio.run(router.verify_audit_chain(SimpleNamespace(state=SimpleNamespace())))

    assert result["intact"] is True
    assert result["first_break"] is None
    assert result["legacy_unverifiable_payload_seqs"] == [1]
