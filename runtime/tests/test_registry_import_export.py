"""Tenant-scoped registry import/export regression tests."""

import pytest


TENANT_A = "tenant-a"
TENANT_B = "tenant-b"


def _auth_for(monkeypatch, tenant_id: str, permissions: list[str], key: str = "admin-key"):
    import router

    monkeypatch.setattr(
        router.api_key_manager,
        "validate",
        lambda api_key: {"tenant_id": tenant_id, "name": "backup-admin", "permissions": permissions}
        if api_key == key
        else None,
    )
    return {"X-API-Key": key}


def _typed_manifest(name: str, endpoint: str = "http://weather") -> dict:
    return {
        "name": name,
        "endpoint": endpoint,
        "publishes": ["weather.raw"],
        "subscribes": [],
        "transport": "http",
        "metadata": {
            "owner": "platform",
            "api_key": "should-not-export",
            "nested": {"password": "also-secret"},
        },
        "tools": [
            {
                "name": "forecast",
                "description": "Forecast weather",
                "inputs": {
                    "location": {"type": "String", "required": True},
                    "credential": {"type": "String", "default": "sk-live-raw-secret"},
                },
                "outputs": {"weather": {"type": "WeatherData"}},
            }
        ],
    }


def test_registry_redact_normalizes_hyphenated_private_key_fields():
    import router

    redacted = router._registry_redact(
        {
            "metadata": {
                "private-key": "SUPERSECRET",
                "private-key-id": "KEYIDSECRET",
                "nested": [
                    {"safe": "visible", "private-key": "NESTEDSECRET"},
                ],
            }
        }
    )

    assert isinstance(redacted, dict)
    metadata = redacted["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["private-key"] == "[REDACTED]"
    assert metadata["private-key-id"] == "[REDACTED]"
    assert metadata["nested"] == [{"safe": "visible", "private-key": "[REDACTED]"}]
    assert "SUPERSECRET" not in repr(redacted)
    assert "KEYIDSECRET" not in repr(redacted)
    assert "NESTEDSECRET" not in repr(redacted)


@pytest.mark.asyncio
async def test_registry_export_rejects_anonymous_client(client):
    resp = await client.get("/registry/export")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_registry_export_allows_platform_admin_without_explicit_action(client, monkeypatch):
    import router

    monkeypatch.setattr(
        router,
        "_validate_user_jwt",
        lambda token: {
            "tenant_id": TENANT_A,
            "user_id": "platform-admin",
            "workspace_id": "",
            "is_admin": True,
            "email": "admin@example.com",
            "sub": "platform-admin",
        } if token == "platform-token" else None,
    )

    resp = await client.get("/registry/export", headers={"Authorization": "Bearer platform-token"})

    assert resp.status_code == 200
    assert resp.json()["tenant_id"] == TENANT_A


@pytest.mark.asyncio
async def test_registry_export_requires_registry_export_permission(client, monkeypatch):
    headers = _auth_for(monkeypatch, TENANT_A, permissions=[])

    resp = await client.get("/registry/export", headers=headers)

    assert resp.status_code == 403
    assert "registry.export" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_registry_export_is_tenant_scoped_and_redacts_sensitive_values(client, monkeypatch):
    import router

    key_info = {
        "tenant-a-key": {"tenant_id": TENANT_A, "name": "tenant-a-admin", "permissions": ["mcp.server.register", "registry.export"]},
        "tenant-b-key": {"tenant_id": TENANT_B, "name": "tenant-b-admin", "permissions": ["mcp.server.register", "registry.export"]},
    }
    monkeypatch.setattr(router.api_key_manager, "validate", lambda api_key: key_info.get(api_key))
    headers_a = {"X-API-Key": "tenant-a-key"}
    tenant_a_manifest = _typed_manifest("tenant-a-weather")
    tenant_a_manifest["metadata"]["private-key"] = "SUPERSECRET"
    tenant_a_manifest["metadata"]["private-key-id"] = "KEYIDSECRET"
    tenant_a_manifest["metadata"]["nested"]["private-key"] = "NESTEDSECRET"
    resp = await client.post("/manifests/typed", headers=headers_a, json=tenant_a_manifest)
    assert resp.status_code == 201

    headers_b = {"X-API-Key": "tenant-b-key"}
    resp = await client.post("/manifests/typed", headers=headers_b, json=_typed_manifest("tenant-b-weather"))
    assert resp.status_code == 201

    resp = await client.get("/registry/export", headers=headers_a)

    assert resp.status_code == 200
    exported = resp.json()
    assert exported["schema"] == "mcpfinder.registry.export"
    assert exported["tenant_id"] == TENANT_A
    assert [m["name"] for m in exported["manifests"]] == ["tenant-a-weather"]
    assert [m["name"] for m in exported["typed_manifests"]] == ["tenant-a-weather"]
    serialized = resp.text
    assert "tenant-b-weather" not in serialized
    assert "should-not-export" not in serialized
    assert "also-secret" not in serialized
    assert "sk-liv...cret" not in serialized
    assert "SUPERSECRET" not in serialized
    assert "KEYIDSECRET" not in serialized
    assert "NESTEDSECRET" not in serialized
    assert router._registry_item_tenants["manifest:tenant-a-weather"] == TENANT_A


@pytest.mark.asyncio
async def test_registry_import_requires_registry_import_permission(client, monkeypatch):
    headers = _auth_for(monkeypatch, TENANT_A, permissions=[])
    bundle = {
        "schema": "mcpfinder.registry.export",
        "schema_version": 1,
        "tenant_id": TENANT_A,
        "manifests": [],
        "typed_manifests": [],
        "pipelines": [],
    }

    resp = await client.post("/registry/import?dry_run=true", headers=headers, json=bundle)

    assert resp.status_code == 403
    assert "registry.import" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_registry_import_dry_run_validates_without_mutating(client, monkeypatch):
    import router

    headers = _auth_for(monkeypatch, TENANT_A, permissions=["registry.import"])
    bundle = {
        "schema": "mcpfinder.registry.export",
        "schema_version": 1,
        "tenant_id": TENANT_A,
        "manifests": [_typed_manifest("dry-run-weather") | {"tools": ["forecast"]}],
        "typed_manifests": [_typed_manifest("dry-run-weather")],
        "pipelines": [],
    }

    resp = await client.post("/registry/import?dry_run=true", headers=headers, json=bundle)

    assert resp.status_code == 200
    report = resp.json()
    assert report["dry_run"] is True
    assert report["summary"] == {"applied": 0, "validated": 2, "errors": 0}
    assert "dry-run-weather" not in router.manifests
    assert "dry-run-weather" not in router.typed_manifests


@pytest.mark.asyncio
async def test_registry_import_apply_isolates_partial_failures(client, monkeypatch):
    import router

    headers = _auth_for(monkeypatch, TENANT_A, permissions=["registry.import"])
    bundle = {
        "schema": "mcpfinder.registry.export",
        "schema_version": 1,
        "tenant_id": TENANT_A,
        "manifests": [
            _typed_manifest("valid-weather") | {"tools": ["forecast"]},
            {"name": "broken-no-endpoint", "tools": ["forecast"]},
        ],
        "typed_manifests": [],
        "pipelines": [],
    }

    resp = await client.post("/registry/import?dry_run=false", headers=headers, json=bundle)

    assert resp.status_code == 200
    report = resp.json()
    assert report["summary"] == {"applied": 1, "validated": 1, "errors": 1}
    assert any(item["status"] == "applied" and item["name"] == "valid-weather" for item in report["items"])
    assert any(item["status"] == "error" and item["name"] == "broken-no-endpoint" for item in report["items"])
    assert "valid-weather" in router.manifests
    assert "broken-no-endpoint" not in router.manifests


@pytest.mark.asyncio
async def test_registry_import_rejects_cross_tenant_bundle(client, monkeypatch):
    headers = _auth_for(monkeypatch, TENANT_A, permissions=["registry.import"])
    bundle = {
        "schema": "mcpfinder.registry.export",
        "schema_version": 1,
        "tenant_id": TENANT_B,
        "manifests": [_typed_manifest("other-tenant") | {"tools": ["forecast"]}],
        "typed_manifests": [],
        "pipelines": [],
    }

    resp = await client.post("/registry/import?dry_run=false", headers=headers, json=bundle)

    assert resp.status_code == 400
    assert "tenant_id" in resp.json()["detail"]
