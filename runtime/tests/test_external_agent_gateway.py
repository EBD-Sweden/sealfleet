"""External agent gateway parity tests.

These tests exercise the minimal ContextForge-style slice where a tenant admin
registers an external agent as an MCP-callable tool without exposing the raw
agent token to catalogs, LLM-facing manifests, portal responses, or audit rows.
"""

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

QA_API_KEY = "qa-agent-admin-key"
QA_TENANT = "qa-agent-tenant"
OTHER_API_KEY = "qa-other-tenant-key"
OTHER_TENANT = "qa-other-tenant"
PIPELINE_ONLY_API_KEY = "qa-pipeline-only-key"
RAW_AGENT_TOKEN = "agent-secret-token-should-never-leak"
SEALED_AUTH_HANDLE = "11111111-1111-1111-1111-111111111111"


@pytest.fixture()
def agent_admin_headers(monkeypatch):
    import router

    def validate(api_key):
        if api_key == QA_API_KEY:
            return {
                "tenant_id": QA_TENANT,
                "name": "agent-admin",
                "actions": ["agent.register", "agent.invoke", "mcp.server.register", "pipeline.invoke"],
            }
        if api_key == OTHER_API_KEY:
            return {
                "tenant_id": OTHER_TENANT,
                "name": "other-admin",
                "actions": ["agent.register", "agent.invoke"],
            }
        if api_key == PIPELINE_ONLY_API_KEY:
            return {
                "tenant_id": QA_TENANT,
                "name": "pipeline-only",
                "actions": ["pipeline.invoke"],
            }
        return None

    monkeypatch.setattr(router.api_key_manager, "validate", validate)
    return {"X-API-Key": QA_API_KEY}


async def _register_fake_agent(client, headers, *, endpoint="http://fake-agent.local"):
    return await client.post(
        "/external-agents",
        headers=headers,
        json={
            "name": "qa-agent",
            "description": "QA fake local external agent",
            "endpoint": endpoint,
            "protocol": "json_rpc",
            "auth": {"type": "bearer", "sealed_handle": SEALED_AUTH_HANDLE},
            "timeout_ms": 250,
        },
    )


async def _register_fake_agent_pipeline(client, headers, *, name="qa-agent-pipeline"):
    resp = await client.post(
        "/pipelines/register",
        headers=headers,
        json={
            "pipeline": {
                "name": name,
                "description": "Calls a tenant-owned external agent",
                "inputs": {"prompt": "string"},
                "stages": [
                    {"name": "invoke", "mcp": "agent:qa-agent", "tool": "invoke", "output_channel": "agent"}
                ],
                "output_stage": "invoke",
            }
        },
    )
    assert resp.status_code == 201
    return name



@pytest.mark.asyncio
async def test_tenant_admin_registers_external_agent_as_catalog_tool_without_secret_leak(client, agent_admin_headers):
    import router

    assert hasattr(router, "ExternalAgentRegistrationRequest")
    assert hasattr(router, "ExternalAgentAuth")
    assert hasattr(router, "register_external_agent")
    assert hasattr(router, "_invoke_external_agent")
    assert hasattr(router, "_external_agent_rate_limits")

    resp = await _register_fake_agent(client, agent_admin_headers)

    assert resp.status_code == 201
    data = resp.json()
    assert data["mcp"] == "agent:qa-agent"
    assert data["tool"] == "invoke"
    assert data["auth"]["sealed_handle"] == SEALED_AUTH_HANDLE
    assert RAW_AGENT_TOKEN not in resp.text

    manifests = (await client.get("/manifests", headers=agent_admin_headers)).json()
    agent_manifest = next(m for m in manifests if m["name"] == "agent:qa-agent")
    assert agent_manifest["tools"] == ["invoke"]
    assert agent_manifest["transport"] == "external_agent"
    assert "auth" not in agent_manifest
    assert RAW_AGENT_TOKEN not in str(agent_manifest)


@pytest.mark.asyncio
async def test_external_agent_invoke_success_uses_sealed_auth_and_redacts_audit(client, agent_admin_headers, monkeypatch):
    import router

    events = []
    captured = {}

    await _register_fake_agent(client, agent_admin_headers)
    monkeypatch.setattr(
        router,
        "_resolve_handle_from_db",
        lambda handle_id, tenant_id, subject_id: {"label": "qa-agent-token", "value": RAW_AGENT_TOKEN},
    )
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return httpx.Response(200, json={"jsonrpc": "2.0", "result": {"answer": "pong"}})

    monkeypatch.setattr(router.httpx, "AsyncClient", FakeAsyncClient)

    resp = await client.post(
        "/call",
        headers=agent_admin_headers,
        json={"mcp": "agent:qa-agent", "tool": "invoke", "inputs": {"prompt": "ping", "secret": "prompt-secret"}},
    )

    assert resp.status_code == 200
    assert resp.json() == {"answer": "pong"}
    assert captured["url"] == "http://fake-agent.local"
    assert captured["headers"]["Authorization"] == f"Bearer {RAW_AGENT_TOKEN}"
    assert RAW_AGENT_TOKEN not in resp.text
    assert any(e["action"] == "external_agent.invoke" and e["result"] == "ok" for e in events)
    assert RAW_AGENT_TOKEN not in str(events)
    assert "prompt-secret" not in str(events)


@pytest.mark.asyncio
async def test_external_agent_pipeline_path_uses_dedicated_invoker(client, agent_admin_headers, monkeypatch):
    import router

    events = []
    captured = {}

    await _register_fake_agent(client, agent_admin_headers)
    monkeypatch.setattr(
        router,
        "_resolve_handle_from_db",
        lambda handle_id, tenant_id, subject_id: {"label": "qa-agent-token", "value": RAW_AGENT_TOKEN},
    )
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            captured.setdefault("timeouts", []).append(kwargs.get("timeout"))

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return httpx.Response(200, json={"jsonrpc": "2.0", "result": {"answer": "pipeline-pong"}})

    monkeypatch.setattr(router.httpx, "AsyncClient", FakeAsyncClient)

    resp = await client.post(
        "/pipeline",
        headers=agent_admin_headers,
        json={"steps": [{"mcp": "agent:qa-agent", "tool": "invoke", "inputs": {"prompt": "ping"}}]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["steps"][0]["result"] == {"answer": "pipeline-pong"}
    assert captured["url"] == "http://fake-agent.local"
    assert captured["headers"]["Authorization"] == f"Bearer {RAW_AGENT_TOKEN}"
    assert any(e["action"] == "external_agent.invoke" and e["result"] == "ok" for e in events)
    assert RAW_AGENT_TOKEN not in str(events)


@pytest.mark.asyncio
async def test_external_agent_invoke_denies_anonymous_and_wrong_tenant(client, agent_admin_headers):
    await _register_fake_agent(client, agent_admin_headers)

    anon = await client.post(
        "/call",
        json={"mcp": "agent:qa-agent", "tool": "invoke", "inputs": {"prompt": "ping"}},
    )
    assert anon.status_code == 401

    wrong = await client.post(
        "/call",
        headers={"X-API-Key": OTHER_API_KEY},
        json={"mcp": "agent:qa-agent", "tool": "invoke", "inputs": {"prompt": "ping"}},
    )
    assert wrong.status_code == 403


@pytest.mark.asyncio
async def test_external_agent_policy_deny_is_audited_and_blocks_network(client, agent_admin_headers, monkeypatch):
    import router

    events = []
    await _register_fake_agent(client, agent_admin_headers)
    monkeypatch.setattr(
        router.policy_engine,
        "check",
        lambda **kwargs: {"action": "deny", "rule_id": "deny-agent", "reason": "blocked by QA policy"},
    )
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))

    class ShouldNotCallNetwork:
        def __init__(self, *args, **kwargs):
            pytest.fail("policy-denied external agent invoke must not reach network")

    monkeypatch.setattr(router.httpx, "AsyncClient", ShouldNotCallNetwork)

    resp = await client.post(
        "/call",
        headers=agent_admin_headers,
        json={"mcp": "agent:qa-agent", "tool": "invoke", "inputs": {"prompt": "prompt-secret"}},
    )

    assert resp.status_code == 403
    assert "blocked by QA policy" in resp.text
    assert any(e["action"] == "external_agent.invoke" and e["result"] == "denied" for e in events)
    assert RAW_AGENT_TOKEN not in str(events)
    assert "prompt-secret" not in str(events)


@pytest.mark.asyncio
async def test_external_agent_rate_limit_blocks_before_network_call(client, agent_admin_headers, monkeypatch):
    import router

    events = []
    await _register_fake_agent(client, agent_admin_headers)
    router._external_agent_rate_limits[(QA_TENANT, "agent:qa-agent")] = [router.time.time()] * 60
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))

    class ShouldNotCallNetwork:
        def __init__(self, *args, **kwargs):
            pytest.fail("rate-limited external agent invoke must not reach network")

    monkeypatch.setattr(router.httpx, "AsyncClient", ShouldNotCallNetwork)

    resp = await client.post(
        "/call",
        headers=agent_admin_headers,
        json={"mcp": "agent:qa-agent", "tool": "invoke", "inputs": {"prompt": "ping"}},
    )

    assert resp.status_code == 429
    assert "rate limit" in resp.text.lower()
    assert any(e["action"] == "external_agent.invoke" and e["result"] == "rate_limited" for e in events)
    assert RAW_AGENT_TOKEN not in resp.text
    assert RAW_AGENT_TOKEN not in str(events)
    assert "ping" not in str(events)


@pytest.mark.asyncio
async def test_external_agent_timeout_is_bounded_and_audited(client, agent_admin_headers, monkeypatch):
    import router

    events = []
    await _register_fake_agent(client, agent_admin_headers)
    monkeypatch.setattr(
        router,
        "_resolve_handle_from_db",
        lambda handle_id, tenant_id, subject_id: {"label": "qa-agent-token", "value": RAW_AGENT_TOKEN},
    )
    monkeypatch.setattr(router, "_write_audit_event", lambda **kwargs: events.append(kwargs))

    class TimeoutAsyncClient:
        def __init__(self, *args, **kwargs):
            timeout = kwargs.get("timeout")
            assert timeout is not None
            assert timeout <= 0.25

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json=None, headers=None):
            raise httpx.TimeoutException("fake timeout")

    monkeypatch.setattr(router.httpx, "AsyncClient", TimeoutAsyncClient)

    resp = await client.post(
        "/call",
        headers=agent_admin_headers,
        json={"mcp": "agent:qa-agent", "tool": "invoke", "inputs": {"prompt": "ping"}},
    )

    assert resp.status_code == 504
    assert "timeout" in resp.text.lower()
    assert any(e["action"] == "external_agent.invoke" and e["result"] == "timeout" for e in events)
    assert RAW_AGENT_TOKEN not in str(events)


@pytest.mark.asyncio
async def test_named_pipeline_external_agent_requires_agent_invoke_before_network(client, agent_admin_headers, monkeypatch):
    import router

    await _register_fake_agent(client, agent_admin_headers)
    await _register_fake_agent_pipeline(client, agent_admin_headers)

    class ShouldNotCallNetwork:
        def __init__(self, *args, **kwargs):
            pytest.fail("named-pipeline external agent invoke without agent.invoke must not reach network")

    monkeypatch.setattr(router.httpx, "AsyncClient", ShouldNotCallNetwork)

    resp = await client.post(
        "/pipelines/tools/call",
        headers={"X-API-Key": PIPELINE_ONLY_API_KEY},
        json={"name": "qa-agent-pipeline", "arguments": {"prompt": "ping"}},
    )

    assert resp.status_code == 403
    assert "agent.invoke" in resp.text


@pytest.mark.asyncio
async def test_jobs_external_agent_pipeline_requires_agent_invoke_before_enqueue(client, agent_admin_headers, monkeypatch):
    import router

    await _register_fake_agent(client, agent_admin_headers)
    await _register_fake_agent_pipeline(client, agent_admin_headers, name="qa-agent-job-pipeline")

    monkeypatch.setattr(router, "_job_db_write", lambda *args, **kwargs: pytest.fail("unauthorized job must not be persisted"))

    class ShouldNotCallNetwork:
        def __init__(self, *args, **kwargs):
            pytest.fail("unauthorized /jobs external agent pipeline must not reach network")

    monkeypatch.setattr(router.httpx, "AsyncClient", ShouldNotCallNetwork)

    resp = await client.post(
        "/jobs",
        headers={"X-API-Key": PIPELINE_ONLY_API_KEY},
        json={"pipeline": "qa-agent-job-pipeline", "inputs": {"prompt": "ping"}, "tenant_id": "attacker-tenant"},
    )

    assert resp.status_code == 403
    assert "agent.invoke" in resp.text


@pytest.mark.asyncio
async def test_jobs_batch_external_agent_pipeline_requires_agent_invoke_before_enqueue(client, agent_admin_headers, monkeypatch):
    import router

    await _register_fake_agent(client, agent_admin_headers)
    await _register_fake_agent_pipeline(client, agent_admin_headers, name="qa-agent-batch-pipeline")

    monkeypatch.setattr(router, "_job_db_write", lambda *args, **kwargs: pytest.fail("unauthorized batch must not be persisted"))

    class ShouldNotCallNetwork:
        def __init__(self, *args, **kwargs):
            pytest.fail("unauthorized /jobs/batch external agent pipeline must not reach network")

    monkeypatch.setattr(router.httpx, "AsyncClient", ShouldNotCallNetwork)

    resp = await client.post(
        "/jobs/batch",
        headers={"X-API-Key": PIPELINE_ONLY_API_KEY},
        json={"pipeline": "qa-agent-batch-pipeline", "items": [{"prompt": "ping"}], "tenant_id": "attacker-tenant"},
    )

    assert resp.status_code == 403
    assert "agent.invoke" in resp.text


class _CapturedBackgroundTask:
    def done(self):
        return False

    def cancel(self):
        return None


def _capture_background_task(monkeypatch, router):
    captured = {}

    def fake_create_task(coro):
        captured["coro"] = coro
        return _CapturedBackgroundTask()

    monkeypatch.setattr(router.asyncio, "create_task", fake_create_task)
    return captured


class _FakeExternalAgentHttpClient:
    captured = {}

    def __init__(self, *args, **kwargs):
        self.captured["timeout"] = kwargs.get("timeout")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url, json=None, headers=None):
        self.captured["url"] = url
        self.captured["json"] = json
        self.captured["headers"] = headers
        return httpx.Response(200, json={"jsonrpc": "2.0", "result": {"answer": "job-pong"}})


def _grant_jobs_external_agent_via_scim_group(monkeypatch, router):
    def validate_user_jwt(token):
        if token != "group-job-token":
            return None
        return {
            "tenant_id": QA_TENANT,
            "user_id": "scim-job-user",
            "email": "scim-job-user@example.com",
            "is_admin": False,
            "sub": "scim-job-user",
            "permissions": [],
            "groups": ["scim-agent-runners"],
        }

    monkeypatch.setattr(router, "_validate_user_jwt", validate_user_jwt)
    monkeypatch.setattr(
        router,
        "_db_has_action_permission",
        lambda tenant_id, user_id, groups, action: (
            tenant_id == QA_TENANT
            and user_id == "scim-job-user"
            and groups == ["scim-agent-runners"]
            and action in {"pipeline.invoke", "agent.invoke"}
        ),
    )


@pytest.mark.asyncio
async def test_jobs_external_agent_pipeline_replays_db_scim_granted_agent_invoke(client, agent_admin_headers, monkeypatch):
    import router

    await _register_fake_agent(client, agent_admin_headers)
    await _register_fake_agent_pipeline(client, agent_admin_headers, name="qa-agent-db-job-pipeline")
    _grant_jobs_external_agent_via_scim_group(monkeypatch, router)
    background = _capture_background_task(monkeypatch, router)
    writes = []
    _FakeExternalAgentHttpClient.captured = {}

    monkeypatch.setattr(router, "_job_db_write", lambda sql, params=(): writes.append((sql, params)))
    monkeypatch.setattr(
        router,
        "_resolve_handle_from_db",
        lambda handle_id, tenant_id, subject_id: {"label": "qa-agent-token", "value": RAW_AGENT_TOKEN},
    )
    monkeypatch.setattr(router.httpx, "AsyncClient", _FakeExternalAgentHttpClient)

    resp = await client.post(
        "/jobs",
        headers={"Authorization": "Bearer group-job-token"},
        json={"pipeline": "qa-agent-db-job-pipeline", "inputs": {"prompt": "ping"}},
    )

    assert resp.status_code == 200
    await background["coro"]
    assert _FakeExternalAgentHttpClient.captured["url"] == "http://fake-agent.local"
    assert _FakeExternalAgentHttpClient.captured["headers"]["Authorization"] == f"Bearer {RAW_AGENT_TOKEN}"
    assert not any("missing permission agent.invoke" in str(write) for write in writes)


@pytest.mark.asyncio
async def test_jobs_batch_external_agent_pipeline_replays_db_scim_granted_agent_invoke(client, agent_admin_headers, monkeypatch):
    import router

    await _register_fake_agent(client, agent_admin_headers)
    await _register_fake_agent_pipeline(client, agent_admin_headers, name="qa-agent-db-batch-pipeline")
    _grant_jobs_external_agent_via_scim_group(monkeypatch, router)
    background = _capture_background_task(monkeypatch, router)
    writes = []
    _FakeExternalAgentHttpClient.captured = {}

    monkeypatch.setattr(router, "_job_db_write", lambda sql, params=(): writes.append((sql, params)))
    monkeypatch.setattr(router, "_job_db_read", lambda *args, **kwargs: [{"job_id": "child", "status": "completed", "result": {}, "error": None}])
    monkeypatch.setattr(
        router,
        "_resolve_handle_from_db",
        lambda handle_id, tenant_id, subject_id: {"label": "qa-agent-token", "value": RAW_AGENT_TOKEN},
    )
    monkeypatch.setattr(router.httpx, "AsyncClient", _FakeExternalAgentHttpClient)

    resp = await client.post(
        "/jobs/batch",
        headers={"Authorization": "Bearer group-job-token"},
        json={"pipeline": "qa-agent-db-batch-pipeline", "items": [{"prompt": "ping"}]},
    )

    assert resp.status_code == 200
    await background["coro"]
    assert _FakeExternalAgentHttpClient.captured["url"] == "http://fake-agent.local"
    assert _FakeExternalAgentHttpClient.captured["headers"]["Authorization"] == f"Bearer {RAW_AGENT_TOKEN}"
    assert not any("missing permission agent.invoke" in str(write) for write in writes)


@pytest.mark.asyncio
async def test_jobs_bind_tenant_to_authenticated_request_not_body(client, agent_admin_headers, monkeypatch):
    import router

    await _register_fake_agent(client, agent_admin_headers)
    await _register_fake_agent_pipeline(client, agent_admin_headers, name="qa-agent-tenant-bound-job")

    writes = []
    monkeypatch.setattr(router, "_job_db_write", lambda sql, params=(): writes.append((sql, params)))

    class FakeTask:
        def done(self):
            return False

        def cancel(self):
            return None

    def fake_create_task(coro):
        coro.close()
        return FakeTask()

    monkeypatch.setattr(router.asyncio, "create_task", fake_create_task)

    resp = await client.post(
        "/jobs",
        headers=agent_admin_headers,
        json={"pipeline": "qa-agent-tenant-bound-job", "inputs": {"prompt": "ping"}, "tenant_id": "attacker-tenant"},
    )

    assert resp.status_code == 200
    insert_sql, insert_params = writes[0]
    assert "INSERT INTO pipeline_jobs" in insert_sql
    assert insert_params[-1] == QA_TENANT
    assert "attacker-tenant" not in str(writes)


@pytest.mark.asyncio
async def test_external_agent_manifests_are_tenant_scoped(client, agent_admin_headers):
    await _register_fake_agent(client, agent_admin_headers, endpoint="http://tenant-a-agent.local")

    other_list = await client.get("/manifests", headers={"X-API-Key": OTHER_API_KEY})
    assert other_list.status_code == 200
    assert "agent:qa-agent" not in {m["name"] for m in other_list.json()}

    other_get = await client.get("/manifests/agent:qa-agent", headers={"X-API-Key": OTHER_API_KEY})
    assert other_get.status_code == 404


@pytest.mark.asyncio
async def test_external_agent_name_collision_across_tenants_is_rejected(client, agent_admin_headers):
    first = await _register_fake_agent(client, agent_admin_headers, endpoint="http://tenant-a-agent.local")
    assert first.status_code == 201

    overwrite = await client.post(
        "/external-agents",
        headers={"X-API-Key": OTHER_API_KEY},
        json={
            "name": "qa-agent",
            "description": "other tenant should not overwrite tenant A",
            "endpoint": "http://tenant-b-agent.local",
            "protocol": "json_rpc",
            "timeout_ms": 250,
        },
    )

    assert overwrite.status_code == 409

    manifests = (await client.get("/manifests", headers=agent_admin_headers)).json()
    agent_manifest = next(m for m in manifests if m["name"] == "agent:qa-agent")
    assert agent_manifest["endpoint"] == "http://tenant-a-agent.local"
