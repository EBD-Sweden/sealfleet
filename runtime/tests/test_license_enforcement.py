"""Router-side enforcement of the open-core license (GET /license, SCIM 402)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.mark.asyncio
async def test_license_endpoint_public_and_free_by_default(client, monkeypatch):
    import router
    import licensing
    monkeypatch.setattr(licensing, "_CACHE", {"ent": None, "at": 0.0})
    monkeypatch.delenv("SEALFLEET_LICENSE_KEY", raising=False)
    monkeypatch.delenv("SEALFLEET_AWS_MARKETPLACE_PRODUCT_CODE", raising=False)

    resp = await client.get("/license")  # no auth required
    assert resp.status_code == 200
    body = resp.json()
    assert body["tier"] == "free"
    assert body["features"] == []
    assert "sso" in body["enterprise_features"]


@pytest.mark.asyncio
async def test_scim_blocked_without_license(client, monkeypatch):
    import router
    # even an admin key is blocked when the feature isn't licensed
    monkeypatch.setattr(
        router.api_key_manager, "validate",
        lambda k: {"tenant_id": "t1", "name": "admin", "permissions": ["*"]} if k == "admin-key" else None,
    )
    monkeypatch.setattr(router.licensing, "feature_enabled", lambda f: False)
    resp = await client.post("/scim/v2/Users", headers={"X-API-Key": "admin-key"},
                             json={"userName": "x@y.com"})
    assert resp.status_code == 402
    assert "Enterprise" in resp.text


@pytest.mark.asyncio
async def test_scim_passes_feature_gate_when_licensed(client, monkeypatch):
    import router
    monkeypatch.setattr(
        router.api_key_manager, "validate",
        lambda k: {"tenant_id": "t1", "name": "admin", "permissions": ["*"]} if k == "admin-key" else None,
    )
    monkeypatch.setattr(router.licensing, "feature_enabled", lambda f: True)
    monkeypatch.setattr(router, "_scim_upsert_user", lambda tid, body: {"id": "u1", "userName": body.get("userName")})
    resp = await client.post("/scim/v2/Users", headers={"X-API-Key": "admin-key"},
                             json={"userName": "x@y.com"})
    # past the 402 feature gate (200/201), not blocked by licensing
    assert resp.status_code in (200, 201)
