import asyncio
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

QA_API_KEY = "qa-runtime-key"
QA_API_KEY_SUBJECT = f"api_key:{hashlib.sha256(QA_API_KEY.encode()).hexdigest()[:12]}"
QA_TENANT = "qa-tenant"


class _StubResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._payload




@pytest.fixture()
def auth_headers(monkeypatch):
    import router

    def validate(api_key):
        if api_key == QA_API_KEY:
            return {"tenant_id": QA_TENANT, "name": "qa", "actions": ["*"]}
        return None

    monkeypatch.setattr(router.api_key_manager, "validate", validate)
    return {"X-API-Key": QA_API_KEY}


@pytest.fixture(autouse=True)
def reset_router_state(monkeypatch):
    import router

    router.manifests.clear()
    router.runtime_hook_manager = router.build_runtime_hook_manager({"runtime_hooks": {"enabled": False}})
    monkeypatch.setattr(router.scale_manager, "ensure_running", AsyncMock(return_value=True))
    monkeypatch.setattr(router.scale_manager, "record_call", AsyncMock(return_value=None))
    yield
    router.manifests.clear()
    router.runtime_hook_manager = router.build_runtime_hook_manager({"runtime_hooks": {"enabled": False}})


def test_configured_hooks_execute_in_order_and_block_fail_closed():
    from policy_hooks import HookDecision, HookManager, RuntimeHook, RuntimeHookContext

    events = []

    class RecordingHook(RuntimeHook):
        async def pre_call(self, ctx, payload):
            events.append((self.name, "pre"))
            return HookDecision.allow()

        async def post_call(self, ctx, result):
            events.append((self.name, "post"))
            return HookDecision.allow(result=result)

    class BlockingHook(RuntimeHook):
        async def pre_call(self, ctx, payload):
            events.append((self.name, "pre"))
            return HookDecision.block("blocked by policy")

    manager = HookManager([
        RecordingHook("first", phase="both", order=20, block_on_violation=True),
        BlockingHook("second", phase="pre", order=10, block_on_violation=True),
    ])
    ctx = RuntimeHookContext(
        trace_id="trace-1", tenant_id="tenant-a", subject_id="user-a",
        mcp="demo", tool="echo", transport="http", pipeline_name="pipe",
    )

    with pytest.raises(PermissionError, match="blocked by policy"):
        asyncio.run(manager.run_pre_call(ctx, {"input": "x"}))

    assert events == [("second", "pre")]
    assert manager.audit_events[-1]["hook_name"] == "second"
    assert manager.audit_events[-1]["action"] == "pre_call"
    assert manager.audit_events[-1]["result"] == "blocked"
    assert manager.audit_events[-1]["tenant"] == "tenant-a"
    assert manager.audit_events[-1]["subject"] == "user-a"


@pytest.mark.asyncio
async def test_output_length_guard_blocks_or_truncates_and_audits():
    from policy_hooks import HookManager, OutputLengthGuard, RuntimeHookContext

    ctx = RuntimeHookContext(
        trace_id="trace-2", tenant_id="tenant-a", subject_id="user-a",
        mcp="demo", tool="echo", transport="http",
    )

    blocking = HookManager([OutputLengthGuard("length", order=1, max_chars=8, mode="block")])
    with pytest.raises(PermissionError, match="output too long"):
        await blocking.run_post_call(ctx, {"text": "0123456789"})
    assert blocking.audit_events[-1]["result"] == "blocked"
    assert "0123456789" not in blocking.audit_events[-1]["reason"]

    truncating = HookManager([OutputLengthGuard("length", order=1, max_chars=8, mode="truncate")])
    result = await truncating.run_post_call(ctx, {"text": "0123456789"})
    assert result == {"text": "01234567", "_runtime_hooks": {"truncated_by": "length", "max_chars": 8}}
    assert truncating.audit_events[-1]["result"] == "redacted"


@pytest.mark.asyncio
async def test_secrets_pii_hook_redacts_or_blocks_nested_values_and_audits():
    from policy_hooks import HookManager, SecretsPiiGuard, RuntimeHookContext

    ctx = RuntimeHookContext(
        trace_id="trace-3", tenant_id="tenant-a", subject_id="user-a",
        mcp="demo", tool="echo", transport="http",
    )
    payload = {"profile": {"email": "hao@example.com", "token": "sk-1234567890abcdef"}}

    redacting = HookManager([SecretsPiiGuard("pii", order=1, mode="redact")])
    result = await redacting.run_post_call(ctx, payload)
    assert result["profile"]["email"] == "[REDACTED_EMAIL]"
    assert result["profile"]["token"] == "[REDACTED_SECRET]"
    assert redacting.audit_events[-1]["result"] == "redacted"
    assert "hao@example.com" not in redacting.audit_events[-1]["reason"]

    blocking = HookManager([SecretsPiiGuard("pii", order=1, mode="block")])
    with pytest.raises(PermissionError, match="sensitive data detected"):
        await blocking.run_post_call(ctx, payload)


@pytest.mark.asyncio
async def test_shared_execution_boundary_applies_hooks_to_http_and_stdio(monkeypatch):
    import router

    router.runtime_hook_manager = router.build_runtime_hook_manager({
        "runtime_hooks": {
            "enabled": True,
            "hooks": [
                {"name": "pii", "type": "secrets_pii_guard", "phase": "post", "order": 10, "mode": "redact"},
            ],
        }
    })

    async def fake_post(self, url, json):
        return _StubResponse({"email": "hao@example.com", "transport": "http"})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(router, "run_docker_stdio", AsyncMock(return_value={"email": "hao@example.com", "transport": "stdio"}))

    http_manifest = router.McpManifest(name="http-mcp", endpoint="http://example", publishes=[], subscribes=[], tools=["echo"])
    stdio_manifest = router.McpManifest(name="stdio-mcp", endpoint="stdio://local", publishes=[], subscribes=[], tools=["echo"], transport="stdio", image="demo:latest")

    async with httpx.AsyncClient() as client:
        http_result, http_error = await router._execute_mcp_tool(
            client, manifest=http_manifest, mcp_name="http-mcp", tool="echo", inputs={},
            trace_id="trace-http", tenant_id="tenant-a", subject_id="user-a", pipeline_name="pipe",
        )
        stdio_result, stdio_error = await router._execute_mcp_tool(
            client, manifest=stdio_manifest, mcp_name="stdio-mcp", tool="echo", inputs={},
            trace_id="trace-stdio", tenant_id="tenant-a", subject_id="user-a", pipeline_name="pipe",
        )

    assert http_error is None
    assert stdio_error is None
    assert http_result["email"] == "[REDACTED_EMAIL]"
    assert stdio_result["email"] == "[REDACTED_EMAIL]"
    assert {e["transport"] for e in router.runtime_hook_manager.audit_events if e["hook_name"] == "pii"} == {"http", "stdio"}



def test_runtime_hook_config_loads_json_file_and_fails_closed(monkeypatch, tmp_path):
    import router

    monkeypatch.setenv(
        "MCPFINDER_RUNTIME_HOOKS_JSON",
        json.dumps({"runtime_hooks": {"enabled": True, "hooks": [{"type": "secrets_pii_guard"}]}}),
    )
    loaded = router._load_runtime_hook_config()
    assert loaded["runtime_hooks"]["enabled"] is True

    monkeypatch.delenv("MCPFINDER_RUNTIME_HOOKS_JSON")
    config_path = tmp_path / "hooks.yaml"
    config_path.write_text("runtime_hooks:\n  enabled: true\n  hooks:\n    - type: output_length_guard\n      max_chars: 4\n")
    monkeypatch.setenv("MCPFINDER_RUNTIME_HOOKS_FILE", str(config_path))
    loaded = router._load_runtime_hook_config()
    assert loaded["runtime_hooks"]["hooks"][0]["type"] == "output_length_guard"

    monkeypatch.setenv("MCPFINDER_RUNTIME_HOOKS_JSON", "{bad-json")
    with pytest.raises(RuntimeError, match="Invalid enabled runtime hook config"):
        router._load_runtime_hook_config()


@pytest.mark.asyncio
async def test_pipeline_route_uses_shared_hook_boundary_for_http(client, auth_headers, monkeypatch):
    import httpx
    import router

    router.runtime_hook_manager = router.build_runtime_hook_manager({
        "runtime_hooks": {
            "enabled": True,
            "hooks": [{"name": "pii", "type": "secrets_pii_guard", "phase": "post", "mode": "redact"}],
        }
    })
    events = []
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))
    router.manifests["hook-http"] = router.McpManifest(
        name="hook-http", endpoint="http://hook-http.local", publishes=[], subscribes=[], tools=["echo"]
    )

    posted = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json=None):
            posted.append((url, json))
            return httpx.Response(200, json={"email": "hao@example.com"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(router.httpx, "AsyncClient", FakeAsyncClient)

    resp = await client.post(
        "/pipeline",
        headers={**auth_headers, "X-Trace-Id": "trace-pipeline-hooks"},
        json={"steps": [{"mcp": "hook-http", "tool": "echo", "inputs": {"x": "ok"}}]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["steps"][0]["result"]["email"] == "[REDACTED_EMAIL]"
    assert posted == [("http://hook-http.local/call", {"tool": "echo", "inputs": {"x": "ok"}})]
    assert any(e["action"] == "runtime_hook" and e["trace_id"] == "trace-pipeline-hooks" for e in events)
    assert "hao@example.com" not in str(events)


@pytest.mark.asyncio
async def test_direct_call_route_resolves_sealed_inputs_and_uses_hook_boundary(client, auth_headers, monkeypatch):
    import httpx
    import router

    router.runtime_hook_manager = router.build_runtime_hook_manager({
        "runtime_hooks": {
            "enabled": True,
            "hooks": [{"name": "pii", "type": "secrets_pii_guard", "phase": "post", "mode": "redact"}],
        }
    })
    events = []
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))
    monkeypatch.setattr(
        router,
        "_resolve_handle_from_db",
        lambda handle_id, **kwargs: {"label": "token", "value": "raw-secret"} if handle_id == "h1" else None,
    )
    router.manifests["hook-direct"] = router.McpManifest(
        name="hook-direct", endpoint="http://hook-direct.local", publishes=[], subscribes=[], tools=["echo"]
    )
    posted = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json=None):
            posted.append((url, json))
            return httpx.Response(200, json={"email": "hao@example.com"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(router.httpx, "AsyncClient", FakeAsyncClient)

    resp = await client.post(
        "/call",
        headers={**auth_headers, "X-Trace-Id": "trace-direct-hooks"},
        json={"mcp": "hook-direct", "tool": "echo", "inputs": {"token": {"__handle": "h1"}}},
    )

    assert resp.status_code == 200
    assert resp.json()["email"] == "[REDACTED_EMAIL]"
    assert posted == [("http://hook-direct.local/call", {"tool": "echo", "inputs": {"token": "raw-secret"}})]
    assert any(e["action"] == "sealed_handle.resolve" and e["user_id"] == QA_API_KEY_SUBJECT for e in events)
    assert any(e["action"] == "runtime_hook" and e["tenant_id"] == QA_TENANT and e["user_id"] == QA_API_KEY_SUBJECT for e in events)
    assert QA_API_KEY not in str(events)
    assert "hao@example.com" not in str(events)
    assert "raw-secret" not in str(events)


@pytest.mark.asyncio
async def test_named_pipeline_route_uses_shared_hook_boundary_for_stdio(monkeypatch):
    import router

    router.runtime_hook_manager = router.build_runtime_hook_manager({
        "runtime_hooks": {
            "enabled": True,
            "hooks": [{"name": "pii", "type": "secrets_pii_guard", "phase": "post", "mode": "redact"}],
        }
    })
    events = []
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))
    monkeypatch.setattr(router, "run_docker_stdio", AsyncMock(return_value={"email": "hao@example.com"}))
    router.manifests["hook-stdio"] = router.McpManifest(
        name="hook-stdio", endpoint="stdio://local", publishes=[], subscribes=[], tools=["echo"], transport="stdio", image="demo:latest"
    )
    pipeline = router.NamedPipeline(
        name="stdio-pipe",
        description="stdio hooks",
        inputs={"x": "string"},
        stages=[router.PipelineStageSchema(name="s", mcp="hook-stdio", tool="echo")],
        output_stage="s",
    )

    result = await router._run_named_pipeline(pipeline, {"x": "ok"}, tenant_id=QA_TENANT, subject_id=QA_API_KEY_SUBJECT)

    assert result["final"]["email"] == "[REDACTED_EMAIL]"
    router.run_docker_stdio.assert_awaited_once_with("demo:latest", "echo", {"x": "ok"})
    assert any(
        e["action"] == "runtime_hook"
        and e["transport"] == "stdio"
        and e["pipeline_name"] == "stdio-pipe"
        and e["user_id"] == QA_API_KEY_SUBJECT
        for e in events
    )
    assert QA_API_KEY not in str(events)
    assert QA_API_KEY not in str(result)
    assert "hao@example.com" not in str(events)
