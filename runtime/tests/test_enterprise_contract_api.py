import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runtime"))
sys.path.insert(0, str(ROOT / "packages" / "mcpfinder-auth" / "src"))


@pytest.fixture()
def test_app():
    with patch("router._get_db", return_value=None), \
         patch("router._ensure_audit_table"), \
         patch("router._ensure_audit_events_table"), \
         patch("router._ensure_sealed_table"):
        import router
        yield router.app


@pytest_asyncio.fixture()
async def client(test_app):
    import httpx
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app),
        base_url="http://test",
    ) as ac:
        yield ac


def _assert_contract_response(resp):
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == "enterprise-auth-contract/v1"
    assert "mcp.tool.call" in data["mcpfinder_adapter"]["actions"]
    assert "audit_event_schema" in data
    assert data["boundary"]["llm_sees"] == ["opaque_handles", "receipts", "trace_ids", "redacted_metadata"]


@pytest.mark.asyncio
async def test_enterprise_contract_endpoint_is_public_discovery(client):
    resp = await client.get("/enterprise/contract")

    _assert_contract_response(resp)


@pytest.mark.asyncio
async def test_enterprise_contract_endpoint_also_works_with_authorization_header(client):
    resp = await client.get("/enterprise/contract", headers={"Authorization": "Bearer unused-discovery-token"})

    _assert_contract_response(resp)
