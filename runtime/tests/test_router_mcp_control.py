"""The platform control plane exposed as an MCP server (POST /mcp).

Every tool forwards to the existing REST route with the caller's own headers,
so authorization is the REST route's decision — these tests check that the
translation is faithful and that it cannot become a bypass.
"""

import json

import httpx
import pytest

import router
import router_mcp

QA_KEY = "qa-mcp-key"


@pytest.fixture()
def mcp_client(test_app, monkeypatch):
    """Client for /mcp whose loopback calls stay in-process."""
    monkeypatch.setattr(
        router.api_key_manager, "validate",
        lambda key: {"tenant_id": "qa", "name": "qa", "actions": ["*"]} if key == QA_KEY else None,
    )
    router_mcp.set_transport_factory(lambda: httpx.ASGITransport(app=test_app))
    monkeypatch.setenv("MCP_CONTROL_LOOPBACK_URL", "http://loopback")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=test_app), base_url="http://test")


def rpc(method, params=None, id_=1):
    return {"jsonrpc": "2.0", "id": id_, "method": method, **({"params": params} if params is not None else {})}


async def call(client, method, params=None, headers=None):
    r = await client.post("/mcp", json=rpc(method, params), headers=headers or {"X-API-Key": QA_KEY})
    return r


async def test_initialize_handshake(mcp_client):
    r = await call(mcp_client, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})
    assert r.status_code == 200
    res = r.json()["result"]
    assert res["protocolVersion"] == "2025-03-26"
    assert "tools" in res["capabilities"]
    assert res["serverInfo"]["name"] == "sealfleet-platform"


async def test_notification_gets_202_and_no_body(mcp_client):
    r = await mcp_client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                              headers={"X-API-Key": QA_KEY})
    assert r.status_code == 202 and r.content == b""


async def test_tools_list_covers_the_control_plane_and_never_credential_use(mcp_client):
    r = await call(mcp_client, "tools/list")
    names = {t["name"] for t in r.json()["result"]["tools"]}
    for expected in ("mcp_list", "mcp_register", "mcp_deploy", "tool_invoke", "pipeline_run",
                     "pipeline_deploy", "pipeline_delete", "job_create", "credential_list",
                     "credential_create", "policy_rules", "audit_events", "scale_up"):
        assert expected in names, expected
    assert "credential_use" not in names
    for t in r.json()["result"]["tools"]:
        assert t["inputSchema"]["type"] == "object"


async def test_tools_call_forwards_to_rest_and_wraps_result(mcp_client):
    r = await call(mcp_client, "tools/call", {"name": "platform_status", "arguments": {}})
    res = r.json()["result"]
    assert res["isError"] is False
    body = json.loads(res["content"][0]["text"])
    assert body["status"] == "ok"


async def test_pipeline_list_via_mcp_matches_rest(mcp_client):
    router._load_yaml_pipeline_v2()
    via_mcp = json.loads((await call(mcp_client, "tools/call", {"name": "pipeline_list", "arguments": {}}))
                         .json()["result"]["content"][0]["text"])
    via_rest = (await mcp_client.get("/v2/pipelines", headers={"X-API-Key": QA_KEY})).json()
    assert {p["name"] for p in via_mcp["pipelines"]} == {p["name"] for p in via_rest["pipelines"]}


async def test_rest_authorization_is_the_only_gate(test_app, monkeypatch):
    """A key without credential.read is refused by the REST route, and the MCP
    layer reports that refusal as an error result — it does not grant more."""
    monkeypatch.setattr(
        router.api_key_manager, "validate",
        lambda key: {"tenant_id": "qa", "name": "ro", "actions": ["pipeline.invoke"]} if key == "ro" else None,
    )
    router_mcp.set_transport_factory(lambda: httpx.ASGITransport(app=test_app))
    monkeypatch.setenv("MCP_CONTROL_LOOPBACK_URL", "http://loopback")
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=test_app), base_url="http://test")

    r = await call(client, "tools/call", {"name": "credential_list", "arguments": {}}, headers={"X-API-Key": "ro"})
    res = r.json()["result"]
    assert res["isError"] is True
    assert "credential.read" in res["content"][0]["text"]


async def test_unauthenticated_caller_is_rejected_before_any_tool_runs(test_app):
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=test_app), base_url="http://test")
    r = await client.post("/mcp", json=rpc("tools/list"))
    assert r.status_code == 401


async def test_delegated_identity_headers_are_forwarded(mcp_client, monkeypatch):
    seen = {}

    async def fake_forward(request, tool, args):
        seen.update({h: request.headers.get(h) for h in router_mcp.FORWARDED_HEADERS})
        return 200, {"ok": True}

    monkeypatch.setattr(router_mcp, "_forward", fake_forward)
    await call(mcp_client, "tools/call", {"name": "platform_status", "arguments": {}},
               headers={"X-API-Key": QA_KEY, "X-McpFinder-User-Id": "u1", "X-McpFinder-Tenant-Id": "t1"})
    assert seen["x-mcpfinder-user-id"] == "u1" and seen["x-mcpfinder-tenant-id"] == "t1"
    assert seen["x-api-key"] == QA_KEY


async def test_unknown_tool_and_missing_args_are_rpc_errors(mcp_client):
    r = await call(mcp_client, "tools/call", {"name": "nope", "arguments": {}})
    assert r.json()["error"]["code"] == -32602
    r = await call(mcp_client, "tools/call", {"name": "pipeline_get", "arguments": {}})
    assert "Missing required" in r.json()["error"]["message"]
    r = await call(mcp_client, "credential_use", {})
    assert r.json()["error"]["code"] == -32601


async def test_batch_requests_are_supported(mcp_client):
    r = await mcp_client.post("/mcp", json=[rpc("ping", id_=1), rpc("tools/list", id_=2)],
                              headers={"X-API-Key": QA_KEY})
    out = r.json()
    assert isinstance(out, list) and [m["id"] for m in out] == [1, 2]


async def test_get_declines_stream_with_405(mcp_client):
    r = await mcp_client.get("/mcp", headers={"X-API-Key": QA_KEY})
    assert r.status_code == 405


async def test_parse_error(mcp_client):
    r = await mcp_client.post("/mcp", content=b"{not json", headers={"X-API-Key": QA_KEY, "content-type": "application/json"})
    assert r.status_code == 400 and r.json()["error"]["code"] == -32700
