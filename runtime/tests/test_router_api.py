"""Security-contract tests for runtime HTTP endpoints.

DB is mocked in conftest; these tests focus on route/auth behavior and
response shapes, not live database persistence.
"""

import hashlib
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


QA_API_KEY = "qa-runtime-key"
QA_API_KEY_SUBJECT = f"api_key:{hashlib.sha256(QA_API_KEY.encode()).hexdigest()[:12]}"
QA_TENANT = "qa-tenant"


def _api_key_subject(api_key: str) -> str:
    return f"api_key:{hashlib.sha256(api_key.encode()).hexdigest()[:12]}"


def _assert_redacted_api_key_subject(subject_id: str, raw_api_key: str = QA_API_KEY) -> None:
    assert subject_id == _api_key_subject(raw_api_key)
    assert subject_id != raw_api_key
    assert raw_api_key not in subject_id


def _portal_subject_token(router, *, sub="qa-user", tenant_id=QA_TENANT, email="qa@example.com", is_admin=False, exp=None):
    """Create a portal-session-style JWT for token-exchange tests."""
    import jwt

    return jwt.encode(
        {
            "sub": sub,
            "user_id": sub,
            "tenant_id": tenant_id,
            "email": email,
            "is_admin": is_admin,
            "exp": exp or int(time.time()) + 300,
        },
        router.NEXTAUTH_SECRET,
        algorithm="HS256",
    )


def _token_exchange_form(subject_token, *, resource="http://allowed-mcp.local", scope="mcp:call"):
    import router

    return {
        "grant_type": router.TOKEN_EXCHANGE_GRANT,
        "subject_token": subject_token,
        "subject_token_type": router.JWT_TOKEN_TYPE,
        "resource": resource,
        "scope": scope,
    }


@pytest.fixture()
def token_exchange_setup(monkeypatch):
    """Allow one registered MCP resource and capture token-exchange audit events."""
    import router

    events = []
    monkeypatch.setattr(router, "NEXTAUTH_SECRET", "test-nextauth-secret")
    monkeypatch.setenv("MCPFINDER_DEPLOYMENT_ENV", "development")
    monkeypatch.setenv("AUTH_ALLOW_LEGACY_PORTAL_HS256", "true")
    router.manifests["allowed-mcp"] = router.McpManifest(
        name="allowed-mcp",
        endpoint="http://allowed-mcp.local",
        publishes=[],
        subscribes=[],
        tools=["call"],
    )
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))
    if hasattr(router, "_token_exchange_rate_limits"):
        router._token_exchange_rate_limits.clear()
    yield router, events
    router.manifests.pop("allowed-mcp", None)
    if hasattr(router, "_token_exchange_rate_limits"):
        router._token_exchange_rate_limits.clear()


@pytest.fixture()
def auth_headers(monkeypatch):
    """Authorize requests through the runtime API-key middleware."""
    import router

    def validate(api_key):
        if api_key == QA_API_KEY:
            return {"tenant_id": QA_TENANT, "name": "qa", "actions": ["*"]}
        return None

    monkeypatch.setattr(router.api_key_manager, "validate", validate)
    return {"X-API-Key": QA_API_KEY}


# ---------------------------------------------------------------------------
# Public discovery endpoints
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_check_is_public(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "manifests" in data
    assert "typed_manifests" in data
    assert "scale_to_zero" in data


@pytest.mark.asyncio
async def test_ready_check_is_public_and_reports_runtime_dependencies(client):
    resp = await client.get("/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in {"ready", "degraded"}
    assert data["service"] == "mcpfinder-runtime"
    assert "checks" in data
    assert "database" in data["checks"]


@pytest.mark.asyncio
async def test_jwks_is_public(client):
    resp = await client.get("/.well-known/jwks.json")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/jwk-set+json")
    data = resp.json()
    assert isinstance(data.get("keys"), list)
    assert data["keys"]


@pytest.mark.asyncio
async def test_oauth_protected_resource_metadata_is_public(client):
    resp = await client.get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 200
    data = resp.json()
    assert data["resource"]
    assert "authorization_servers" in data
    assert "mcp:call" in data.get("scopes_supported", [])


# ---------------------------------------------------------------------------
# P0: endpoints that must not be anonymous in production mode
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,json_body",
    [
        ("GET", "/capabilities", None),
        ("GET", "/policy/rules", None),
        ("POST", "/policy/check", {"mcp": "weather-mcp", "tool": "get_weather"}),
        ("POST", "/policy/reload", None),
        ("GET", "/audit/events", None),
        ("POST", "/sealed", {"label": "api_key", "value": "secret", "expires_in_seconds": 300}),
        ("GET", "/sealed", None),
        ("GET", "/sealed/nonexistent-uuid", None),
        ("DELETE", "/sealed/nonexistent-uuid", None),
        ("GET", "/manifests", None),
        ("POST", "/manifests", {"name": "x", "endpoint": "http://x", "publishes": [], "subscribes": [], "tools": []}),
        ("GET", "/types", None),
        ("GET", "/pipelines", None),
        ("POST", "/call", {"mcp": "x", "tool": "y", "inputs": {}}),
        ("GET", "/credentials", None),
        ("POST", "/credentials", {"name": "x", "value": "secret"}),
    ],
)
async def test_protected_runtime_endpoints_reject_anonymous(client, method, path, json_body):
    resp = await client.request(method, path, json=json_body)
    assert resp.status_code == 401, f"{method} {path} should be protected"


# ---------------------------------------------------------------------------
# Authorized route behavior
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_policy_rules_returns_list_with_auth(client, auth_headers):
    resp = await client.get("/policy/rules", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "rules" in data
    assert "count" in data
    assert data["count"] == len(data["rules"])


@pytest.mark.asyncio
async def test_policy_check_allow_with_auth(client, auth_headers):
    resp = await client.post(
        "/policy/check",
        headers=auth_headers,
        json={"mcp": "weather-mcp", "tool": "get_weather"},
    )
    assert resp.status_code == 200
    # With no matching deny rule in test mode, default is allow.
    assert resp.json()["action"] == "allow"


@pytest.mark.asyncio
async def test_policy_check_deny_with_auth(client, auth_headers):
    """Load a deny rule into the policy engine, then check it."""
    import router

    router.policy_engine.rules = [
        {
            "id": "block-delete",
            "match": {"tool_pattern": "delete_*"},
            "action": "deny",
            "reason": "blocked",
        },
    ]
    resp = await client.post(
        "/policy/check",
        headers=auth_headers,
        json={"mcp": "any", "tool": "delete_account"},
    )
    assert resp.status_code == 200
    assert resp.json()["action"] == "deny"
    router.policy_engine.rules = []


@pytest.mark.asyncio
async def test_pipeline_mcp_call_audit_receipt_has_required_fields_and_trace_header(client, auth_headers, monkeypatch):
    import httpx
    import router

    events = []
    router.manifests["receipt-mcp"] = router.McpManifest(
        name="receipt-mcp",
        endpoint="http://receipt-mcp.local",
        publishes=[],
        subscribes=[],
        tools=["ping"],
    )
    async def noop_scale(_mcp):
        return None

    monkeypatch.setattr(router.scale_manager, "ensure_running", noop_scale)
    monkeypatch.setattr(router.scale_manager, "record_call", noop_scale)
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json=None):
            return httpx.Response(200, json={"ok": True}, request=httpx.Request("POST", url))

    monkeypatch.setattr(router.httpx, "AsyncClient", FakeAsyncClient)

    resp = await client.post(
        "/pipeline",
        headers={**auth_headers, "X-Trace-Id": "trace-from-client"},
        json={"steps": [{"mcp": "receipt-mcp", "tool": "ping", "inputs": {"api_key": "sk-live-hidden"}}]},
    )

    assert resp.status_code == 200
    assert resp.json()["trace_id"] == "trace-from-client"
    event = next(e for e in events if e["action"] == "tool_call")
    assert event["tenant_id"] == QA_TENANT
    _assert_redacted_api_key_subject(event["user_id"])
    assert QA_API_KEY not in str(resp.json())
    assert event["resource"] == "receipt-mcp/ping"
    assert event["server_name"] == "receipt-mcp"
    assert event["result"] == "ok"
    assert event["trace_id"] == "trace-from-client"
    assert "sk-live-hidden" not in str(event)
    router.manifests.pop("receipt-mcp", None)


@pytest.mark.asyncio
async def test_pipeline_require_confirm_policy_denies_execution_until_confirmed(client, auth_headers, monkeypatch):
    """A require_confirm policy must not log-and-proceed into tool dispatch."""
    import router

    events = []
    router.manifests["broker-mcp"] = router.McpManifest(
        name="broker-mcp",
        endpoint="http://broker-mcp.local",
        publishes=[],
        subscribes=[],
        tools=["execute_trade"],
    )
    router.policy_engine.rules = [
        {
            "id": "confirm-trade",
            "match": {"tool_pattern": "execute_*"},
            "action": "require_confirm",
            "reason": "destructive action requires confirmation",
        },
    ]
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))
    monkeypatch.setattr(
        router.scale_manager,
        "ensure_running",
        lambda *args, **kwargs: pytest.fail("require_confirm policy must stop before dispatch"),
    )

    resp = await client.post(
        "/pipeline",
        headers=auth_headers,
        json={"steps": [{"mcp": "broker-mcp", "tool": "execute_trade", "inputs": {"symbol": "MCPF"}}]},
    )

    assert resp.status_code == 409
    assert "confirmation required" in resp.text.lower()
    detail = resp.json()["detail"]
    assert any(
        event["action"] == "policy_confirm_required"
        and event["result"] == "denied"
        and event["tenant_id"] == QA_TENANT
        and event["user_id"] == QA_API_KEY_SUBJECT
        and QA_API_KEY not in event["user_id"]
        and event["resource"] == "broker-mcp/execute_trade"
        and event["trace_id"] == detail["trace_id"]
        for event in events
    )
    router.policy_engine.rules = []
    router.manifests.pop("broker-mcp", None)


@pytest.mark.asyncio
async def test_pipeline_require_confirm_policy_blocks_execution(client, auth_headers, monkeypatch):
    import router

    events = []
    router.manifests["confirm-mcp"] = router.McpManifest(
        name="confirm-mcp",
        endpoint="http://confirm-mcp.local",
        publishes=[],
        subscribes=[],
        tools=["execute_trade"],
    )
    router.policy_engine.rules = [
        {
            "id": "confirm-execute",
            "match": {"tool_pattern": "execute_*"},
            "action": "require_confirm",
            "reason": "Execution requires confirmation",
        }
    ]
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))
    monkeypatch.setattr(router.scale_manager, "ensure_running", lambda _mcp: pytest.fail("MCP dispatch must not happen"))

    resp = await client.post(
        "/pipeline",
        headers=auth_headers,
        json={"steps": [{"mcp": "confirm-mcp", "tool": "execute_trade", "inputs": {"symbol": "BTC"}}]},
    )

    assert resp.status_code == 409
    assert "Policy confirmation required" in resp.text
    assert any(
        event["action"] == "policy_confirm_required"
        and event["result"] == "denied"
        and event["payload"]["rule_id"] == "confirm-execute"
        for event in events
    )
    router.manifests.pop("confirm-mcp", None)
    router.policy_engine.rules = []


@pytest.mark.asyncio
async def test_named_pipeline_require_confirm_policy_returns_error_without_execution(monkeypatch):
    import router

    events = []
    router.manifests["confirm-mcp"] = router.McpManifest(
        name="confirm-mcp",
        endpoint="http://confirm-mcp.local",
        publishes=[],
        subscribes=[],
        tools=["execute_trade"],
    )
    pipeline = router.NamedPipeline(
        name="confirm-pipeline",
        description="confirmation gate regression",
        inputs={},
        stages=[router.PipelineStageSchema(name="execute", mcp="confirm-mcp", tool="execute_trade")],
        output_stage="execute",
    )
    router.policy_engine.rules = [
        {
            "id": "confirm-execute",
            "match": {"tool_pattern": "execute_*"},
            "action": "require_confirm",
            "reason": "Execution requires confirmation",
        }
    ]
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))
    monkeypatch.setattr(router.scale_manager, "ensure_running", lambda _mcp: pytest.fail("MCP dispatch must not happen"))

    result = await router._run_named_pipeline(pipeline, {}, tenant_id=QA_TENANT, subject_id=QA_API_KEY_SUBJECT)

    assert result["error"].startswith("Policy confirmation required")
    assert result["policy_action"] == "require_confirm"
    assert any(
        event["action"] == "policy_confirm_required"
        and event["result"] == "denied"
        and event["tenant_id"] == QA_TENANT
        and event["user_id"] == QA_API_KEY_SUBJECT
        and QA_API_KEY not in event["user_id"]
        and event["trace_id"] == result["trace_id"]
        for event in events
    )
    assert QA_API_KEY not in str(events)
    assert QA_API_KEY not in str(result)
    router.manifests.pop("confirm-mcp", None)
    router.policy_engine.rules = []


@pytest.mark.asyncio
async def test_named_pipeline_policy_deny_audit_uses_subject_and_trace(monkeypatch):
    import router

    events = []
    router.manifests["deny-mcp"] = router.McpManifest(
        name="deny-mcp",
        endpoint="http://deny-mcp.local",
        publishes=[],
        subscribes=[],
        tools=["delete_account"],
    )
    pipeline = router.NamedPipeline(
        name="deny-pipeline",
        description="deny gate regression",
        inputs={},
        stages=[router.PipelineStageSchema(name="delete", mcp="deny-mcp", tool="delete_account")],
        output_stage="delete",
    )
    router.policy_engine.rules = [
        {"id": "deny-delete", "match": {"tool_pattern": "delete_*"}, "action": "deny", "reason": "blocked"}
    ]
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))
    monkeypatch.setattr(router.scale_manager, "ensure_running", lambda _mcp: pytest.fail("deny policy must not dispatch"))

    result = await router._run_named_pipeline(pipeline, {}, tenant_id=QA_TENANT, subject_id=QA_API_KEY_SUBJECT)

    assert result["error"] == "Policy denied: blocked"
    assert any(
        event["action"] == "policy_deny"
        and event["result"] == "denied"
        and event["tenant_id"] == QA_TENANT
        and event["user_id"] == QA_API_KEY_SUBJECT
        and QA_API_KEY not in event["user_id"]
        and event["trace_id"] == result["trace_id"]
        for event in events
    )
    assert QA_API_KEY not in str(events)
    assert QA_API_KEY not in str(result)
    router.manifests.pop("deny-mcp", None)
    router.policy_engine.rules = []


@pytest.mark.asyncio
async def test_named_pipeline_sealed_handle_resolution_audits_trace_for_success_and_denial(monkeypatch):
    import httpx
    import router

    events = []
    posted_payloads = []
    resolved_calls = []
    router.manifests["sealed-mcp"] = router.McpManifest(
        name="sealed-mcp",
        endpoint="http://sealed-mcp.local",
        publishes=[],
        subscribes=[],
        tools=["use_secret"],
    )
    pipeline = router.NamedPipeline(
        name="sealed-pipeline",
        description="sealed trace regression",
        inputs={},
        stages=[router.PipelineStageSchema(name="use", mcp="sealed-mcp", tool="use_secret")],
        output_stage="use",
    )
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))

    def fake_resolve_handle(handle_id, **kwargs):
        resolved_calls.append((handle_id, kwargs))
        return {"label": "token", "value": "raw-secret"} if handle_id == "ok-handle" else None

    monkeypatch.setattr(router, "_resolve_handle_from_db", fake_resolve_handle)

    async def noop_scale(_mcp):
        return None

    monkeypatch.setattr(router.scale_manager, "ensure_running", noop_scale)
    monkeypatch.setattr(router.scale_manager, "record_call", noop_scale)

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json=None):
            posted_payloads.append(json)
            return httpx.Response(200, json={"ok": True}, request=httpx.Request("POST", url))

    monkeypatch.setattr(router.httpx, "AsyncClient", FakeAsyncClient)

    ok_result = await router._run_named_pipeline(
        pipeline,
        {"secret": {"__handle": "ok-handle"}},
        tenant_id=QA_TENANT,
        subject_id=QA_API_KEY_SUBJECT,
    )
    denied_result = await router._run_named_pipeline(
        pipeline,
        {"secret": {"__handle": "missing-handle"}},
        tenant_id=QA_TENANT,
        subject_id=QA_API_KEY_SUBJECT,
    )

    assert posted_payloads[0]["inputs"]["secret"] == "raw-secret"
    assert all(kwargs["subject_id"] == QA_API_KEY_SUBJECT for _handle, kwargs in resolved_calls)
    assert all(QA_API_KEY not in kwargs["subject_id"] for _handle, kwargs in resolved_calls)
    assert any(
        event["action"] == "sealed_handle.resolve"
        and event["resource"] == "sealed:ok-handle"
        and event["result"] == "ok"
        and event["user_id"] == QA_API_KEY_SUBJECT
        and QA_API_KEY not in event["user_id"]
        and event["trace_id"] == ok_result["trace_id"]
        for event in events
    )
    assert any(
        event["action"] == "sealed_handle.resolve"
        and event["resource"] == "sealed:missing-handle"
        and event["result"] == "denied"
        and event["user_id"] == QA_API_KEY_SUBJECT
        and QA_API_KEY not in event["user_id"]
        and event["trace_id"] == denied_result["trace_id"]
        for event in events
    )
    assert all(event["user_id"] == QA_API_KEY_SUBJECT for event in events if event["action"] in {"sealed_handle.resolve", "tool_call"})
    assert QA_API_KEY not in str(events)
    assert QA_API_KEY not in str(ok_result)
    assert QA_API_KEY not in str(denied_result)
    assert "raw-secret" not in str(events)
    router.manifests.pop("sealed-mcp", None)


@pytest.mark.asyncio
async def test_direct_call_audits_tool_and_sealed_resolution_with_trace(client, auth_headers, monkeypatch):
    import httpx
    import router

    events = []
    router.manifests["direct-mcp"] = router.McpManifest(
        name="direct-mcp",
        endpoint="http://direct-mcp.local",
        publishes=[],
        subscribes=[],
        tools=["ping"],
    )
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))
    monkeypatch.setattr(router, "_resolve_handle_from_db", lambda handle_id, **_kwargs: {"label": "token", "value": "direct-secret"})

    async def noop_scale(_mcp):
        return None

    monkeypatch.setattr(router.scale_manager, "ensure_running", noop_scale)
    monkeypatch.setattr(router.scale_manager, "record_call", noop_scale)

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json=None):
            assert url == "http://direct-mcp.local/call"
            assert json == {"tool": "ping", "inputs": {"token": "direct-secret"}}
            return httpx.Response(200, json={"ok": True}, request=httpx.Request("POST", url))

    monkeypatch.setattr(router.httpx, "AsyncClient", FakeAsyncClient)

    resp = await client.post(
        "/call",
        headers={**auth_headers, "X-Trace-Id": "trace-direct"},
        json={"mcp": "direct-mcp", "tool": "ping", "inputs": {"token": {"__handle": "direct-handle"}}},
    )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "trace_id": "trace-direct"}
    assert any(event["action"] == "sealed_handle.resolve" and event["trace_id"] == "trace-direct" for event in events)
    tool_event = next(event for event in events if event["action"] == "tool_call")
    assert tool_event["resource"] == "direct-mcp/ping"
    assert tool_event["tenant_id"] == QA_TENANT
    _assert_redacted_api_key_subject(tool_event["user_id"])
    assert any(_api_key_subject(QA_API_KEY) == event["user_id"] for event in events if event["action"] == "sealed_handle.resolve")
    assert QA_API_KEY not in str(resp.json())
    assert tool_event["trace_id"] == "trace-direct"
    assert "direct-secret" not in str(events)
    router.manifests.pop("direct-mcp", None)


@pytest.mark.asyncio
async def test_direct_call_stdio_uses_shared_execution_boundary_when_hooks_disabled(client, auth_headers, monkeypatch):
    import router

    events = []
    router.manifests["direct-stdio-mcp"] = router.McpManifest(
        name="direct-stdio-mcp",
        endpoint="",
        transport="stdio",
        image="stdio-img",
        publishes=[],
        subscribes=[],
        tools=["ping"],
    )
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))

    async def fail_http_scale_path(_mcp):
        pytest.fail("direct stdio /call must not enter the HTTP scale path")

    monkeypatch.setattr(router.scale_manager, "ensure_running", fail_http_scale_path)

    async def fake_stdio(image, tool, inputs):
        return {"stdio_ok": True, "image": image, "tool": tool, "inputs": inputs}

    monkeypatch.setattr(router, "run_docker_stdio", fake_stdio)

    resp = await client.post(
        "/call",
        headers={**auth_headers, "X-Trace-Id": "trace-stdio-direct"},
        json={"mcp": "direct-stdio-mcp", "tool": "ping", "inputs": {"x": 1}},
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "stdio_ok": True,
        "image": "stdio-img",
        "tool": "ping",
        "inputs": {"x": 1},
        "trace_id": "trace-stdio-direct",
    }
    tool_event = next(event for event in events if event["action"] == "tool_call")
    assert tool_event["resource"] == "direct-stdio-mcp/ping"
    assert tool_event["result"] == "ok"
    assert tool_event["tenant_id"] == QA_TENANT
    _assert_redacted_api_key_subject(tool_event["user_id"])
    assert QA_API_KEY not in str(resp.json())
    assert tool_event["trace_id"] == "trace-stdio-direct"
    router.manifests.pop("direct-stdio-mcp", None)


@pytest.mark.asyncio
async def test_direct_call_policy_deny_audits_trace_and_blocks_dispatch(client, auth_headers, monkeypatch):
    import router

    events = []
    router.manifests["direct-deny-mcp"] = router.McpManifest(
        name="direct-deny-mcp",
        endpoint="http://direct-deny-mcp.local",
        publishes=[],
        subscribes=[],
        tools=["delete_account"],
    )
    router.policy_engine.rules = [
        {"id": "direct-deny", "match": {"tool_pattern": "delete_*"}, "action": "deny", "reason": "blocked"}
    ]
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))
    monkeypatch.setattr(router.scale_manager, "ensure_running", lambda _mcp: pytest.fail("policy deny must not dispatch"))

    resp = await client.post(
        "/call",
        headers={**auth_headers, "X-Trace-Id": "trace-direct-deny"},
        json={"mcp": "direct-deny-mcp", "tool": "delete_account", "inputs": {}},
    )

    assert resp.status_code == 403
    assert any(
        event["action"] == "policy_deny"
        and event["result"] == "denied"
        and event["tenant_id"] == QA_TENANT
        and event["user_id"] == QA_API_KEY_SUBJECT
        and QA_API_KEY not in event["user_id"]
        and event["trace_id"] == "trace-direct-deny"
        for event in events
    )
    router.manifests.pop("direct-deny-mcp", None)
    router.policy_engine.rules = []


@pytest.mark.asyncio
async def test_policy_reload_with_auth(client, auth_headers):
    resp = await client.post("/policy/reload", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "reloaded"


@pytest.mark.asyncio
async def test_create_sealed_handle_schema_with_auth(client, auth_headers, monkeypatch):
    """Sealed creation returns only handle metadata, never plaintext input."""
    import router

    fake = {"handle_id": "test-uuid", "label": "api_key", "expires_at": None}
    monkeypatch.setattr(router, "_create_sealed_handle", lambda *a, **kw: fake)
    resp = await client.post(
        "/sealed",
        headers=auth_headers,
        json={"label": "api_key", "value": "super-secret", "expires_in_seconds": 300},
    )
    assert resp.status_code == 200
    assert resp.json() == fake
    assert "super-secret" not in resp.text


@pytest.mark.asyncio
async def test_create_sealed_handle_binds_request_tenant_subject_and_audits(client, auth_headers, monkeypatch):
    """Sealed handles are owned by the authenticated tenant/subject and create is audited."""
    import router

    calls = {}
    events = []

    def fake_create(label, value, expires_in_seconds, *, tenant_id, subject_id):
        calls.update(
            label=label,
            value=value,
            expires_in_seconds=expires_in_seconds,
            tenant_id=tenant_id,
            subject_id=subject_id,
        )
        return {"handle_id": "h-owned", "label": label, "expires_at": None}

    monkeypatch.setattr(router, "_create_sealed_handle", fake_create)
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))

    resp = await client.post(
        "/sealed",
        headers=auth_headers,
        json={"label": "api_key", "value": "super-secret", "expires_in_seconds": 60},
    )

    assert resp.status_code == 200
    assert calls == {
        "label": "api_key",
        "value": "super-secret",
        "expires_in_seconds": 60,
        "tenant_id": QA_TENANT,
        "subject_id": QA_API_KEY_SUBJECT,
    }
    assert any(
        event["action"] == "sealed_handle.create"
        and event["resource"] == "sealed:h-owned"
        and event["user_id"] == QA_API_KEY_SUBJECT
        and QA_API_KEY not in event["user_id"]
        and event["payload"]["tenant_id"] == QA_TENANT
        for event in events
    )
    assert "super-secret" not in resp.text
    assert "super-secret" not in str(events)


@pytest.mark.asyncio
async def test_list_sealed_handles_with_auth_does_not_expose_values(client, auth_headers, monkeypatch):
    import router

    calls = {}

    def fake_list(*, tenant_id, subject_id):
        calls.update(tenant_id=tenant_id, subject_id=subject_id)
        return [{"handle_id": "h1", "label": "api_key", "expires_at": "soon"}]

    monkeypatch.setattr(router, "_list_sealed_handles", fake_list)
    resp = await client.get("/sealed", headers=auth_headers)
    assert resp.status_code == 200
    assert calls == {"tenant_id": QA_TENANT, "subject_id": QA_API_KEY_SUBJECT}
    assert QA_API_KEY not in calls["subject_id"]
    assert "value" not in resp.text
    assert "secret" not in resp.text.lower()


@pytest.mark.asyncio
async def test_default_named_api_key_cannot_delegate_portal_identity_for_sealed_handles(client, monkeypatch):
    """Spoofing a historical portal key name must not make X-Sealfleet-* headers trusted."""
    import router

    calls = {}

    def validate(api_key):
        if api_key == "spoofed-shared-key":
            return {"tenant_id": "key-tenant", "name": "portal-shared-key", "actions": ["sealed_handle.create"]}
        return None

    def fake_create(label, value, expires_in_seconds, *, tenant_id, subject_id):
        calls.update(tenant_id=tenant_id, subject_id=subject_id)
        return {"handle_id": "h-spoof", "label": label, "expires_at": None}

    monkeypatch.setattr(router.api_key_manager, "validate", validate)
    monkeypatch.setattr(router, "_create_sealed_handle", fake_create)

    resp = await client.post(
        "/sealed",
        headers={
            "X-API-Key": "spoofed-shared-key",
            "X-Sealfleet-User-Id": "victim-user",
            "X-Sealfleet-Tenant-Id": "victim-tenant",
        },
        json={"label": "api_key", "value": "super-secret", "expires_in_seconds": 60},
    )

    assert resp.status_code == 200
    assert calls == {"tenant_id": "key-tenant", "subject_id": _api_key_subject("spoofed-shared-key")}
    assert "spoofed-shared-key" not in calls["subject_id"]


@pytest.mark.asyncio
async def test_explicitly_privileged_api_key_can_delegate_portal_identity_for_sealed_handles(client, monkeypatch):
    """Only durable key metadata/flags may opt a portal backend key into delegated identity."""
    import router

    calls = {}
    events = []

    def validate(api_key):
        if api_key == "trusted-portal-key":
            return {
                "tenant_id": "service-tenant",
                "name": "portal-shared-key",
                "actions": ["sealed_handle.create"],
                "allow_identity_delegation": True,
            }
        return None

    def fake_create(label, value, expires_in_seconds, *, tenant_id, subject_id):
        calls.update(tenant_id=tenant_id, subject_id=subject_id)
        return {"handle_id": "h-delegated", "label": label, "expires_at": None}

    monkeypatch.setattr(router.api_key_manager, "validate", validate)
    monkeypatch.setattr(router, "_create_sealed_handle", fake_create)
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))

    resp = await client.post(
        "/sealed",
        headers={
            "X-API-Key": "trusted-portal-key",
            "X-Sealfleet-User-Id": "portal-user-123",
            "X-Sealfleet-Tenant-Id": "portal-tenant-456",
        },
        json={"label": "api_key", "value": "super-secret", "expires_in_seconds": 60},
    )

    assert resp.status_code == 200
    assert calls == {"tenant_id": "portal-tenant-456", "subject_id": "portal-user-123"}
    create_event = next(event for event in events if event["action"] == "sealed_handle.create")
    assert create_event["user_id"] == "portal-user-123"
    assert create_event["tenant_id"] == "portal-tenant-456"
    assert create_event["payload"]["delegated_from"] == {
        "api_key_tenant_id": "service-tenant",
        "api_key_name": "portal-shared-key",
    }
    assert "trusted-portal-key" not in str(create_event)


@pytest.mark.asyncio
async def test_list_sealed_handles_denies_wrong_subject_by_scoping_to_caller(client, auth_headers, monkeypatch):
    """A caller must not see handles owned by another subject in the same tenant."""
    import router

    def fake_list(*, tenant_id, subject_id):
        if tenant_id == QA_TENANT and subject_id == "owner-user":
            return [{"handle_id": "h-owned", "label": "api_key", "expires_at": "soon"}]
        return []

    monkeypatch.setattr(router, "_list_sealed_handles", fake_list)

    resp = await client.get("/sealed", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json() == []
    assert "h-owned" not in resp.text


@pytest.mark.asyncio
async def test_public_http_sealed_resolve_is_disabled_and_never_returns_plaintext(client, auth_headers, monkeypatch):
    import router

    events = []

    def fake_resolve(*args, **kwargs):
        raise AssertionError("HTTP sealed resolve must not read or decrypt plaintext")

    monkeypatch.setattr(router, "_resolve_handle_from_db", fake_resolve)
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))
    resp = await client.get("/sealed/nonexistent-uuid", headers=auth_headers)
    assert resp.status_code == 403
    assert "secret" not in resp.text.lower()
    assert any(
        event["action"] == "sealed_handle.resolve"
        and event["result"] == "denied"
        and event["payload"]["reason"] == "http_plaintext_resolve_disabled"
        and event["user_id"] == QA_API_KEY_SUBJECT
        and QA_API_KEY not in event["user_id"]
        for event in events
    )


@pytest.mark.asyncio
async def test_delete_sealed_handle_requires_delete_permission_not_resolve(client, monkeypatch):
    """DELETE /sealed/{id} must require sealed_handle.delete, not resolve."""
    import router

    def validate(api_key):
        if api_key == "resolve-only-key":
            return {"tenant_id": QA_TENANT, "name": "resolve-only", "actions": ["sealed_handle.resolve"]}
        return None

    monkeypatch.setattr(router.api_key_manager, "validate", validate)
    monkeypatch.setattr(
        router,
        "_delete_sealed_handle",
        lambda *args, **kwargs: pytest.fail("delete must not run without sealed_handle.delete permission"),
    )

    resp = await client.delete("/sealed/h-owned", headers={"X-API-Key": "resolve-only-key"})

    assert resp.status_code == 403
    assert "sealed_handle.delete" in resp.text


@pytest.mark.asyncio
async def test_delete_sealed_handle_scopes_to_tenant_subject_and_audits(client, auth_headers, monkeypatch):
    import router

    calls = {}
    events = []

    def fake_delete(handle_id, *, tenant_id, subject_id):
        calls.update(handle_id=handle_id, tenant_id=tenant_id, subject_id=subject_id)
        return True

    monkeypatch.setattr(router, "_delete_sealed_handle", fake_delete)
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))

    resp = await client.delete("/sealed/h-owned", headers=auth_headers)

    assert resp.status_code == 200
    assert calls == {"handle_id": "h-owned", "tenant_id": QA_TENANT, "subject_id": QA_API_KEY_SUBJECT}
    assert QA_API_KEY not in calls["subject_id"]
    assert any(event["action"] == "sealed_handle.delete" and event["result"] == "ok" for event in events)


@pytest.mark.asyncio
async def test_delete_sealed_handle_denies_wrong_subject_and_wrong_tenant(client, auth_headers, monkeypatch):
    """A caller must not invalidate handles owned by another subject/tenant."""
    import router

    calls = []
    events = []

    def fake_delete(handle_id, *, tenant_id, subject_id):
        calls.append((handle_id, tenant_id, subject_id))
        return tenant_id == "owner-tenant" and subject_id == "owner-user"

    monkeypatch.setattr(router, "_delete_sealed_handle", fake_delete)
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))

    resp = await client.delete("/sealed/h-owned", headers=auth_headers)

    assert resp.status_code == 404
    assert calls == [("h-owned", QA_TENANT, QA_API_KEY_SUBJECT)]
    assert QA_API_KEY not in calls[0][2]
    assert any(
        event["action"] == "sealed_handle.delete"
        and event["result"] == "denied"
        and event["user_id"] == QA_API_KEY_SUBJECT
        and QA_API_KEY not in event["user_id"]
        and event["payload"]["tenant_id"] == QA_TENANT
        for event in events
    )


@pytest.mark.asyncio
async def test_failed_create_and_delete_sealed_handle_attempts_are_audited(client, auth_headers, monkeypatch):
    import router

    events = []
    monkeypatch.setattr(router, "_create_sealed_handle", lambda *args, **kwargs: None)
    monkeypatch.setattr(router, "_delete_sealed_handle", lambda *args, **kwargs: False)
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))

    create_resp = await client.post(
        "/sealed",
        headers=auth_headers,
        json={"label": "api_key", "value": "super-secret", "expires_in_seconds": 60},
    )
    delete_resp = await client.delete("/sealed/h-missing", headers=auth_headers)

    assert create_resp.status_code == 500
    assert delete_resp.status_code == 404
    assert any(event["action"] == "sealed_handle.create" and event["result"] == "denied" for event in events)
    assert any(event["action"] == "sealed_handle.delete" and event["result"] == "denied" for event in events)
    assert "super-secret" not in str(events)


@pytest.mark.asyncio
async def test_audit_events_returns_list_with_auth(client, auth_headers, monkeypatch):
    import router

    monkeypatch.setattr(router, "_list_audit_events", lambda *a, **kw: [])
    resp = await client.get("/audit/events", headers=auth_headers)
    assert resp.status_code == 200
    assert "events" in resp.json()


@pytest.mark.asyncio
async def test_list_manifests_with_auth(client, auth_headers):
    resp = await client.get("/manifests", headers=auth_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_register_manifest_with_auth(client, auth_headers):
    manifest = {
        "name": "test-mcp",
        "endpoint": "http://test-mcp:9999",
        "publishes": [],
        "subscribes": [],
        "tools": ["test_tool"],
        "transport": "http",
    }
    resp = await client.post("/manifests", headers=auth_headers, json=manifest)
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_list_types_with_auth(client, auth_headers):
    resp = await client.get("/types", headers=auth_headers)
    assert resp.status_code == 200
    assert "types" in resp.json()


@pytest.mark.asyncio
async def test_list_capabilities_with_auth(client, auth_headers):
    resp = await client.get("/capabilities", headers=auth_headers)
    assert resp.status_code == 200
    assert "capabilities" in resp.json()


# ---------------------------------------------------------------------------
# P0: OAuth token exchange hardening
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_router_issued_mcp_token_cannot_authenticate_runtime_admin_endpoint(client, token_exchange_setup):
    router, _events = token_exchange_setup
    mcp_token = router._issue_mcp_token(
        subject="qa-user",
        tenant_id=QA_TENANT,
        email="qa@example.com",
        audience="http://allowed-mcp.local",
        scope="mcp:call",
    )

    resp = await client.get("/audit/events", headers={"Authorization": f"Bearer {mcp_token}"})

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_token_exchange_rejects_non_admin_mcp_admin_scope(client, token_exchange_setup):
    router, events = token_exchange_setup
    subject = _portal_subject_token(router, is_admin=False)

    resp = await client.post("/token", data=_token_exchange_form(subject, scope="mcp:admin"))

    assert resp.status_code == 403
    assert resp.json()["error"] == "insufficient_scope"
    assert any(e["action"] == "token_exchange" and e["result"] == "denied" for e in events)


@pytest.mark.asyncio
async def test_token_exchange_rejects_arbitrary_unregistered_resource(client, token_exchange_setup):
    router, events = token_exchange_setup
    subject = _portal_subject_token(router)

    resp = await client.post(
        "/token",
        data=_token_exchange_form(subject, resource="https://evil.example.com"),
    )

    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_target"
    assert any(e["action"] == "token_exchange" and e["result"] == "denied" for e in events)


@pytest.mark.asyncio
async def test_token_exchange_issues_mcp_call_for_registered_resource(client, token_exchange_setup):
    import jwt

    router, events = token_exchange_setup
    subject = _portal_subject_token(router)

    resp = await client.post("/token", data=_token_exchange_form(subject, scope="mcp:call"))

    assert resp.status_code == 200
    data = resp.json()
    assert data["scope"] == "mcp:call"
    claims = jwt.decode(
        data["access_token"],
        router._router_public_key,
        algorithms=["RS256"],
        audience="http://allowed-mcp.local",
        issuer=router.ROUTER_ISSUER,
    )
    assert claims["scope"] == "mcp:call"
    assert claims["aud"] == "http://allowed-mcp.local"
    assert any(e["action"] == "token_exchange" and e["result"] == "ok" for e in events)


@pytest.mark.asyncio
async def test_token_exchange_audits_and_rejects_malformed_subject_token(client, token_exchange_setup):
    _router, events = token_exchange_setup

    resp = await client.post("/token", data=_token_exchange_form("not-a-jwt"))

    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_token"
    assert any(e["action"] == "token_exchange" and e["result"] == "denied" for e in events)


@pytest.mark.asyncio
async def test_token_exchange_rate_limits_by_ip_and_subject(client, token_exchange_setup, monkeypatch):
    router, events = token_exchange_setup
    monkeypatch.setattr(router, "TOKEN_EXCHANGE_RATE_LIMIT_MAX", 2)
    monkeypatch.setattr(router, "TOKEN_EXCHANGE_RATE_LIMIT_WINDOW_SECONDS", 60)
    subject = _portal_subject_token(router)

    for _ in range(2):
        resp = await client.post("/token", data=_token_exchange_form(subject))
        assert resp.status_code == 200

    limited = await client.post("/token", data=_token_exchange_form(subject))

    assert limited.status_code == 429
    assert limited.json()["error"] == "rate_limited"
    assert any(e["action"] == "token_exchange" and e["result"] == "rate_limited" for e in events)


def test_token_exchange_rate_limit_prunes_expired_subject_buckets(token_exchange_setup, monkeypatch):
    router, _events = token_exchange_setup
    monkeypatch.setattr(router, "TOKEN_EXCHANGE_RATE_LIMIT_MAX", 2)
    monkeypatch.setattr(router, "TOKEN_EXCHANGE_RATE_LIMIT_WINDOW_SECONDS", 0.01)
    monkeypatch.setattr(router, "TOKEN_EXCHANGE_RATE_LIMIT_MAX_BUCKETS", 100, raising=False)

    for round_no in range(3):
        for i in range(2):
            assert router._check_token_exchange_rate_limit("203.0.113.9", f"subject-{round_no}-{i}") is True
        time.sleep(0.02)

    assert router._check_token_exchange_rate_limit("203.0.113.9", "subject-final") is True
    assert sorted(router._token_exchange_rate_limits) == ["ip:203.0.113.9", "subject:subject-final"]


def test_token_exchange_rate_limit_bucket_count_is_bounded(token_exchange_setup, monkeypatch):
    router, _events = token_exchange_setup
    monkeypatch.setattr(router, "TOKEN_EXCHANGE_RATE_LIMIT_MAX", 1000)
    monkeypatch.setattr(router, "TOKEN_EXCHANGE_RATE_LIMIT_WINDOW_SECONDS", 60)
    monkeypatch.setattr(router, "TOKEN_EXCHANGE_RATE_LIMIT_MAX_BUCKETS", 6, raising=False)

    for i in range(20):
        assert router._check_token_exchange_rate_limit(f"203.0.113.{i}", f"subject-{i}") is True
        assert len(router._token_exchange_rate_limits) <= 6


@pytest.mark.asyncio
async def test_token_exchange_ignores_spoofed_x_forwarded_for_by_default(client, token_exchange_setup, monkeypatch):
    router, events = token_exchange_setup
    monkeypatch.setattr(router, "TOKEN_EXCHANGE_RATE_LIMIT_MAX", 1)
    monkeypatch.setattr(router, "TOKEN_EXCHANGE_RATE_LIMIT_WINDOW_SECONDS", 60)
    monkeypatch.setattr(router, "TRUSTED_PROXY_CIDRS", [], raising=False)

    first = await client.post(
        "/token",
        data=_token_exchange_form("not-a-jwt-1"),
        headers={"x-forwarded-for": "198.51.100.10"},
    )
    second = await client.post(
        "/token",
        data=_token_exchange_form("not-a-jwt-2"),
        headers={"x-forwarded-for": "198.51.100.11"},
    )

    assert first.status_code == 401
    assert second.status_code == 429
    assert second.json()["error"] == "rate_limited"
    assert any(e["action"] == "token_exchange" and e["result"] == "rate_limited" for e in events)


def test_client_ip_uses_x_forwarded_for_only_from_trusted_proxy(monkeypatch):
    import router

    request = router.Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/token",
            "headers": [(b"x-forwarded-for", b"198.51.100.10, 203.0.113.5")],
            "client": ("10.0.0.7", 12345),
        }
    )

    monkeypatch.setattr(router, "TRUSTED_PROXY_CIDRS", [])
    assert router._client_ip(request) == "10.0.0.7"

    monkeypatch.setattr(router, "TRUSTED_PROXY_CIDRS", ["10.0.0.0/8"])
    assert router._client_ip(request) == "198.51.100.10"


def test_write_audit_event_redacts_secret_payload_fields(monkeypatch):
    import json
    import router

    executed = []

    class Cursor:
        def execute(self, sql, params=None):
            executed.append((sql, params))

        def fetchone(self):
            return None  # no prior audit row -> empty prev_hash

        def close(self):
            pass

    class Conn:
        def cursor(self):
            return Cursor()

    monkeypatch.setattr(router, "_get_db", lambda: Conn())

    router._write_audit_event(
        action="tool_call",
        resource="secret-mcp/call",
        server_name="secret-mcp",
        result="ok",
        trace_id="trace-redact",
        user_id="user-a",
        tenant_id="tenant-a",
        payload={
            "input": {"api_key": "sk-live-123", "safe": "visible"},
            "authorization": "Bearer secret-token",
        },
    )

    params = next(p for sql, p in executed if "INSERT INTO audit_events" in sql)
    assert params[:8] == (
        "tenant-a",
        "user-a",
        "tool_call",
        "secret-mcp/call",
        "secret-mcp",
        "ok",
        "trace-redact",
        0,
    )
    payload = json.loads(params[8])
    assert payload["input"]["safe"] == "visible"
    assert payload["input"]["api_key"] == "[REDACTED]"
    assert payload["authorization"] == "[REDACTED]"
    assert "sk-live-123" not in params[8]
    assert "secret-token" not in params[8]


@pytest.mark.asyncio
async def test_token_exchange_audit_event_has_required_receipt_fields(client, token_exchange_setup):
    router, events = token_exchange_setup
    subject = _portal_subject_token(router, sub="user-a", tenant_id="tenant-a")

    resp = await client.post("/token", data=_token_exchange_form(subject))

    assert resp.status_code == 200
    event = next(e for e in events if e["action"] == "token_exchange" and e["result"] == "ok")
    assert event["tenant_id"] == "tenant-a"
    assert event["user_id"] == "user-a"
    assert event["resource"] == "http://allowed-mcp.local"
    assert event["server_name"] == "allowed-mcp"
    assert event["trace_id"]
    assert "subject_token" not in str(event)


