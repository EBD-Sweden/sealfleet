"""Enterprise RBAC and SCIM runtime enforcement regression tests."""

from pathlib import Path

import pytest


QA_TENANT = "qa-tenant"


@pytest.fixture()
def restricted_api_key(monkeypatch):
    """API key auth with no endpoint/action permissions."""
    import router

    monkeypatch.setattr(
        router.api_key_manager,
        "validate",
        lambda api_key: {"tenant_id": QA_TENANT, "name": "restricted", "permissions": []}
        if api_key == "restricted-key"
        else None,
    )
    return {"X-API-Key": "restricted-key"}


@pytest.fixture()
def enterprise_admin_key(monkeypatch):
    """API key auth with full enterprise/admin permissions."""
    import router

    monkeypatch.setattr(
        router.api_key_manager,
        "validate",
        lambda api_key: {"tenant_id": QA_TENANT, "name": "admin", "permissions": ["*"]}
        if api_key == "admin-key"
        else None,
    )
    return {"X-API-Key": "admin-key"}


@pytest.fixture()
def bearer_for_user_without_actions(monkeypatch):
    import router

    monkeypatch.setattr(
        router,
        "_validate_user_jwt",
        lambda token: {
            "tenant_id": QA_TENANT,
            "user_id": "user-no-actions",
            "email": "no-actions@example.com",
            "is_admin": False,
            "sub": "user-no-actions",
            "permissions": [],
            "groups": [],
        }
        if token == "valid-user-token"
        else None,
    )
    return {"Authorization": "Bearer valid-user-token"}


@pytest.fixture()
def bearer_for_user_with_register_action(monkeypatch):
    import router

    monkeypatch.setattr(
        router,
        "_validate_user_jwt",
        lambda token: {
            "tenant_id": QA_TENANT,
            "user_id": "user-register",
            "email": "register@example.com",
            "is_admin": False,
            "sub": "user-register",
            "permissions": ["mcp.server.register"],
            "groups": [],
        }
        if token == "register-token"
        else None,
    )
    return {"Authorization": "Bearer register-token"}


@pytest.fixture()
def bearer_for_scim_group_mapped_user(monkeypatch):
    import router
    seen = []

    monkeypatch.setattr(
        router,
        "_validate_user_jwt",
        lambda token: {
            "tenant_id": QA_TENANT,
            "user_id": "group-user",
            "email": "group-user@example.com",
            "is_admin": False,
            "sub": "group-user",
            "permissions": [],
            "groups": ["finance-admins"],
        }
        if token == "group-token"
        else None,
    )

    def fake_group_db_permission(tenant_id, user_id, groups, action):
        seen.append((tenant_id, user_id, groups, action))
        return groups == ["finance-admins"] and action == "policy.admin"

    monkeypatch.setattr(router, "_db_has_action_permission", fake_group_db_permission)
    return {"headers": {"Authorization": "Bearer group-token"}, "seen": seen}


@pytest.fixture()
def db_api_key_with_policy_admin(monkeypatch):
    import router

    monkeypatch.setattr(
        router.api_key_manager,
        "validate",
        lambda api_key: {"tenant_id": QA_TENANT, "name": "db-admin", "permissions": ["policy.admin"]}
        if api_key == "db-admin-key"
        else None,
    )
    return {"X-API-Key": "db-admin-key"}


@pytest.fixture()
def db_api_key_missing_action_metadata(monkeypatch):
    import router

    monkeypatch.setattr(
        router.api_key_manager,
        "validate",
        lambda api_key: {"tenant_id": QA_TENANT, "name": "legacy-db-key"}
        if api_key == "legacy-db-key"
        else None,
    )
    return {"X-API-Key": "legacy-db-key"}


@pytest.fixture()
def audit_reader_key(monkeypatch):
    import router

    monkeypatch.setattr(
        router.api_key_manager,
        "validate",
        lambda api_key: {"tenant_id": QA_TENANT, "name": "audit-reader", "permissions": ["audit.read"]}
        if api_key == "audit-reader-key"
        else None,
    )
    return {"X-API-Key": "audit-reader-key"}


def test_api_key_subject_id_uses_fingerprint_not_raw_secret():
    from types import SimpleNamespace

    import router

    request = SimpleNamespace(
        state=SimpleNamespace(api_key_id=router._api_key_subject_id("audit-reader-key"), api_key="audit-reader-key")
    )

    subject_id = router.get_subject_id(request)

    assert subject_id.startswith("api_key:")
    assert subject_id != "audit-reader-key"
    assert "audit-reader-key" not in subject_id


@pytest.mark.asyncio
async def test_audit_events_requires_audit_read_action(client, restricted_api_key, monkeypatch):
    import router

    monkeypatch.setattr(router, "_list_audit_events", lambda *a, **kw: [])

    resp = await client.get("/audit/events", headers=restricted_api_key)

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Forbidden: missing permission audit.read"


@pytest.mark.asyncio
async def test_audit_events_are_filtered_to_authenticated_tenant(client, audit_reader_key, monkeypatch):
    import router

    seen = []

    def fake_list_audit_events(*, limit=100, server="", tenant_id=None, include_all_tenants=False):
        seen.append(
            {
                "limit": limit,
                "server": server,
                "tenant_id": tenant_id,
                "include_all_tenants": include_all_tenants,
            }
        )
        return [{"event_id": "tenant-event", "tenant_id": tenant_id}]

    monkeypatch.setattr(router, "_list_audit_events", fake_list_audit_events)

    resp = await client.get("/audit/events?limit=25&server=weather-mcp", headers=audit_reader_key)

    assert resp.status_code == 200
    assert resp.json() == {"events": [{"event_id": "tenant-event", "tenant_id": QA_TENANT}]}
    assert seen == [
        {
            "limit": 25,
            "server": "weather-mcp",
            "tenant_id": QA_TENANT,
            "include_all_tenants": False,
        }
    ]


def test_list_audit_events_filters_rows_by_tenant(monkeypatch):
    import datetime as dt

    import router

    captured = []

    class FakeCursor:
        def execute(self, query, params):
            captured.append((query, params))
            assert "tenant_id = %s" in query
            assert params == (QA_TENANT, 10)

        def fetchall(self):
            return [
                (
                    "event-a",
                    QA_TENANT,
                    "user-a",
                    "tool_call",
                    "weather/get",
                    "weather",
                    "ok",
                    "trace-a",
                    7,
                    dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
                )
            ]

        def close(self):
            pass

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr(router, "_get_db", lambda: FakeConnection())

    events = router._list_audit_events(limit=10, tenant_id=QA_TENANT)

    assert events == [
        {
            "event_id": "event-a",
            "tenant_id": QA_TENANT,
            "user_id": "user-a",
            "action": "tool_call",
            "resource": "weather/get",
            "server_name": "weather",
            "result": "ok",
            "trace_id": "trace-a",
            "duration_ms": 7,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    ]
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_policy_reload_requires_policy_admin_action(client, restricted_api_key):
    resp = await client.post("/policy/reload", headers=restricted_api_key)

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Forbidden: missing permission policy.admin"


@pytest.mark.asyncio
async def test_manifest_registration_requires_server_register_action(client, restricted_api_key):
    resp = await client.post(
        "/manifests",
        headers=restricted_api_key,
        json={"name": "x", "endpoint": "http://x", "publishes": [], "subscribes": [], "tools": []},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Forbidden: missing permission mcp.server.register"


@pytest.mark.asyncio
async def test_jwt_action_permissions_allow_manifest_registration(client, bearer_for_user_with_register_action):
    resp = await client.post(
        "/manifests",
        headers=bearer_for_user_with_register_action,
        json={"name": "jwt-ok", "endpoint": "http://jwt-ok", "publishes": [], "subscribes": [], "tools": []},
    )

    assert resp.status_code == 201
    assert resp.json() == {"status": "registered", "mcp": "jwt-ok"}


@pytest.mark.asyncio
async def test_jwt_missing_action_permissions_denies_manifest_registration(client, bearer_for_user_without_actions):
    resp = await client.post(
        "/manifests",
        headers=bearer_for_user_without_actions,
        json={"name": "jwt-deny", "endpoint": "http://jwt-deny", "publishes": [], "subscribes": [], "tools": []},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Forbidden: missing permission mcp.server.register"


@pytest.mark.asyncio
async def test_db_loaded_api_key_action_permissions_allow_policy_read(client, db_api_key_with_policy_admin):
    resp = await client.get("/policy/rules", headers=db_api_key_with_policy_admin)

    assert resp.status_code == 200
    assert set(resp.json()) == {"rules", "count"}


@pytest.mark.asyncio
async def test_db_loaded_api_key_missing_action_metadata_denied(client, db_api_key_missing_action_metadata):
    resp = await client.get("/policy/rules", headers=db_api_key_missing_action_metadata)

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Forbidden: missing permission policy.admin"


@pytest.mark.asyncio
async def test_jwt_scim_group_role_mapping_path_allows_policy_admin(client, bearer_for_scim_group_mapped_user):
    bearer_for_scim_group_mapped_user["seen"].clear()

    resp = await client.get("/policy/rules", headers=bearer_for_scim_group_mapped_user["headers"])

    assert resp.status_code == 200
    assert bearer_for_scim_group_mapped_user["seen"] == [
        (QA_TENANT, "group-user", ["finance-admins"], "policy.admin")
    ]


def test_api_key_manager_loads_db_action_permissions(monkeypatch):
    import router

    class FakeCursor:
        def execute(self, query):
            assert "action_permissions" in query

        def fetchall(self):
            return [("db-key", QA_TENANT, "DB Key", ["policy.admin"], False, {})]

        def close(self):
            pass

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

    manager = router.ApiKeyManager()
    monkeypatch.setattr(router, "_get_db", lambda: FakeConnection())

    manager.load_keys()

    key_info = manager.validate("db-key")
    assert key_info is not None
    assert key_info["tenant_id"] == QA_TENANT
    assert key_info["name"] == "DB Key"
    assert key_info["permissions"] == ["policy.admin"]


def test_action_permissions_migration_has_no_dead_api_key_or_service_account_grants():
    migration = Path(__file__).resolve().parents[2] / "db/migrations/007_enterprise_rbac_scim.sql"
    sql = migration.read_text()

    assert "grantee_type IN ('user', 'role')" in sql
    assert "'api_key'" not in sql
    assert "'service_account'" not in sql


@pytest.mark.asyncio
async def test_sealed_handle_resolve_requires_specific_action(client, bearer_for_user_without_actions, monkeypatch):
    import router

    monkeypatch.setattr(router, "_resolve_handle_from_db", lambda handle_id: {"handle_id": handle_id, "value": "secret"})

    resp = await client.get("/sealed/handle-1", headers=bearer_for_user_without_actions)

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Forbidden: missing permission sealed_handle.resolve"


@pytest.mark.asyncio
async def test_credential_use_requires_credential_use_action(client, restricted_api_key):
    resp = await client.post("/credentials/cred-1/use", headers=restricted_api_key)

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Forbidden: missing permission credential.use"


def test_write_audit_event_persists_first_class_tenant_id(monkeypatch):
    import router

    executed = []

    class FakeCursor:
        def execute(self, query, params=None):
            executed.append((query, params))

        def fetchone(self):
            return None  # no prior audit row -> empty prev_hash

        def close(self):
            pass

    class FakeConnection:
        def cursor(self, *args, **kwargs):
            return FakeCursor()

    monkeypatch.setattr(router, "_get_db", lambda: FakeConnection())

    router._write_audit_event(
        tenant_id=QA_TENANT,
        action="tool_call",
        resource="weather/get_forecast",
        server_name="weather",
        user_id="user-1",
    )

    query, params = next((q, p) for q, p in executed if "INSERT INTO audit_events" in q)
    assert "tenant_id" in query
    assert params[0] == QA_TENANT


def test_audit_payload_redacts_hyphenated_private_key_fields():
    import router

    redacted = router._redact_audit_payload(
        {
            "metadata": {
                "private-key": "SUPERSECRET",
                "private-key-id": "KEYIDSECRET",
                "nested": [{"private-key": "NESTEDSECRET", "safe": "visible"}],
            }
        }
    )

    assert isinstance(redacted, dict)
    metadata = redacted["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["private-key"] == "[REDACTED]"
    assert metadata["private-key-id"] == "[REDACTED]"
    assert metadata["nested"] == [{"private-key": "[REDACTED]", "safe": "visible"}]
    assert "SUPERSECRET" not in repr(redacted)
    assert "KEYIDSECRET" not in repr(redacted)
    assert "NESTEDSECRET" not in repr(redacted)


@pytest.mark.asyncio
async def test_scim_user_lifecycle_requires_policy_admin_and_revokes_sessions(client, enterprise_admin_key, monkeypatch):
    import router

    calls = []

    def fake_upsert(tenant_id, user):
        calls.append(("upsert", tenant_id, user["userName"]))
        return {"id": "user-1", "userName": user["userName"], "active": user.get("active", True)}

    def fake_deactivate(tenant_id, user_id):
        calls.append(("deactivate", tenant_id, user_id))
        return {"id": user_id, "active": False, "sessions_revoked": True}

    monkeypatch.setattr(router, "_scim_upsert_user", fake_upsert)
    monkeypatch.setattr(router, "_scim_deactivate_user", fake_deactivate)

    created = await client.post(
        "/scim/v2/Users",
        headers=enterprise_admin_key,
        json={"userName": "scim-user@example.com", "active": True},
    )
    deactivated = await client.patch(
        "/scim/v2/Users/user-1",
        headers=enterprise_admin_key,
        json={"active": False},
    )

    assert created.status_code == 201
    assert deactivated.status_code == 200
    assert deactivated.json()["sessions_revoked"] is True
    assert calls == [
        ("upsert", QA_TENANT, "scim-user@example.com"),
        ("deactivate", QA_TENANT, "user-1"),
    ]


@pytest.mark.asyncio
async def test_scim_patch_rejects_same_tenant_body_username_for_different_path_user(
    client,
    enterprise_admin_key,
    monkeypatch,
):
    import router

    class FakeCursor:
        def execute(self, query, params):
            assert "FROM users" in query
            assert params == ("actual@example.com",)
            self._row = {"id": "actual-user-id", "tenant_id": QA_TENANT, "email": "actual@example.com"}

        def fetchone(self):
            return self._row

        def close(self):
            pass

    class FakeConnection:
        def __init__(self):
            self.rolled_back = False

        def cursor(self, *args, **kwargs):
            return FakeCursor()

        def rollback(self):
            self.rolled_back = True

    db = FakeConnection()
    monkeypatch.setattr(router, "_get_db", lambda: db)
    monkeypatch.setattr(
        router,
        "_scim_upsert_user",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("wrong-path PATCH must not upsert")),
    )

    resp = await client.patch(
        "/scim/v2/Users/wrong-path-id",
        headers=enterprise_admin_key,
        json={"userName": "actual@example.com", "active": True},
    )

    assert resp.status_code == 409
    assert "SCIM PATCH path id" in resp.json()["detail"]
    assert db.rolled_back is True


@pytest.mark.asyncio
async def test_scim_patch_rejects_body_id_that_differs_from_path_id(
    client,
    enterprise_admin_key,
    monkeypatch,
):
    import router

    class FakeConnection:
        def __init__(self):
            self.rolled_back = False

        def cursor(self, *args, **kwargs):
            raise AssertionError("body id mismatch should be rejected before lookup")

        def rollback(self):
            self.rolled_back = True

    db = FakeConnection()
    monkeypatch.setattr(router, "_get_db", lambda: db)
    monkeypatch.setattr(
        router,
        "_scim_upsert_user",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("body-id mismatch must not upsert")),
    )

    resp = await client.patch(
        "/scim/v2/Users/path-user-id",
        headers=enterprise_admin_key,
        json={"id": "different-body-id", "userName": "path-user@example.com", "active": True},
    )

    assert resp.status_code == 409
    assert "body id" in resp.json()["detail"]
    assert db.rolled_back is True


@pytest.mark.asyncio
async def test_scim_patch_deactivate_rejects_body_id_that_differs_from_path_id(
    client,
    enterprise_admin_key,
    monkeypatch,
):
    import router

    class FakeCursor:
        rowcount = 0

        def __init__(self, db):
            self.db = db

        def execute(self, query, params=None):
            if "UPDATE users SET is_active = FALSE" in query:
                self.db.deactivated = True
                self.rowcount = 1
            else:
                self.rowcount = 1

        def close(self):
            pass

    class FakeConnection:
        def __init__(self):
            self.deactivated = False
            self.rolled_back = False

        def cursor(self, *args, **kwargs):
            return FakeCursor(self)

        def commit(self):
            pass

        def rollback(self):
            self.rolled_back = True

    db = FakeConnection()
    monkeypatch.setattr(router, "_get_db", lambda: db)

    resp = await client.patch(
        "/scim/v2/Users/path-user-id",
        headers=enterprise_admin_key,
        json={"active": False, "id": "different-body-id", "userName": "path-user@example.com"},
    )

    assert resp.status_code == 409
    assert "body id" in resp.json()["detail"]
    assert db.deactivated is False
    assert db.rolled_back is True


@pytest.mark.asyncio
async def test_scim_patch_deactivate_rejects_same_tenant_body_email_for_different_path_user(
    client,
    enterprise_admin_key,
    monkeypatch,
):
    import router

    class FakeCursor:
        rowcount = 0

        def __init__(self, db):
            self.db = db
            self._row = None

        def execute(self, query, params=None):
            if "FROM users" in query:
                assert params == ("actual@example.com",)
                self._row = {"id": "actual-user-id", "tenant_id": QA_TENANT, "email": "actual@example.com"}
            elif "UPDATE users SET is_active = FALSE" in query:
                self.db.deactivated = True
                self.rowcount = 1
            else:
                self.rowcount = 1

        def fetchone(self):
            return self._row

        def close(self):
            pass

    class FakeConnection:
        def __init__(self):
            self.deactivated = False
            self.rolled_back = False

        def cursor(self, *args, **kwargs):
            return FakeCursor(self)

        def commit(self):
            pass

        def rollback(self):
            self.rolled_back = True

    db = FakeConnection()
    monkeypatch.setattr(router, "_get_db", lambda: db)

    resp = await client.patch(
        "/scim/v2/Users/path-user-id",
        headers=enterprise_admin_key,
        json={"active": False, "userName": "actual@example.com"},
    )

    assert resp.status_code == 409
    assert "SCIM PATCH path id" in resp.json()["detail"]
    assert db.deactivated is False
    assert db.rolled_back is True


@pytest.mark.asyncio
async def test_scim_patch_rejects_cross_tenant_body_email_before_mutation(
    client,
    enterprise_admin_key,
    monkeypatch,
):
    import router

    class FakeCursor:
        def execute(self, query, params):
            assert "FROM users" in query
            assert params == ("shared@example.com",)
            self._row = {"id": "tenant-b-user", "tenant_id": "other-tenant", "email": "shared@example.com"}

        def fetchone(self):
            return self._row

        def close(self):
            pass

    class FakeConnection:
        def __init__(self):
            self.rolled_back = False

        def cursor(self, *args, **kwargs):
            return FakeCursor()

        def rollback(self):
            self.rolled_back = True

    db = FakeConnection()
    monkeypatch.setattr(router, "_get_db", lambda: db)
    monkeypatch.setattr(
        router,
        "_scim_upsert_user",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cross-tenant PATCH must not upsert")),
    )

    resp = await client.patch(
        "/scim/v2/Users/tenant-b-user",
        headers=enterprise_admin_key,
        json={"userName": "shared@example.com", "active": True},
    )

    assert resp.status_code == 409
    assert "another tenant" in resp.json()["detail"]
    assert db.rolled_back is True


@pytest.mark.asyncio
async def test_scim_patch_deactivate_rejects_cross_tenant_body_email_before_mutation(
    client,
    enterprise_admin_key,
    monkeypatch,
):
    import router

    class FakeCursor:
        rowcount = 0

        def __init__(self, db):
            self.db = db
            self._row = None

        def execute(self, query, params=None):
            if "FROM users" in query:
                assert params == ("shared@example.com",)
                self._row = {"id": "tenant-b-user", "tenant_id": "other-tenant", "email": "shared@example.com"}
            elif "UPDATE users SET is_active = FALSE" in query:
                self.db.deactivated = True
                self.rowcount = 1
            else:
                self.rowcount = 1

        def fetchone(self):
            return self._row

        def close(self):
            pass

    class FakeConnection:
        def __init__(self):
            self.deactivated = False
            self.rolled_back = False

        def cursor(self, *args, **kwargs):
            return FakeCursor(self)

        def commit(self):
            pass

        def rollback(self):
            self.rolled_back = True

    db = FakeConnection()
    monkeypatch.setattr(router, "_get_db", lambda: db)

    resp = await client.patch(
        "/scim/v2/Users/path-user-id",
        headers=enterprise_admin_key,
        json={"active": False, "userName": "shared@example.com"},
    )

    assert resp.status_code == 409
    assert "another tenant" in resp.json()["detail"]
    assert db.deactivated is False
    assert db.rolled_back is True


def test_scim_upsert_rejects_same_email_owned_by_another_tenant(monkeypatch):
    import sys
    import types

    import router

    fake_psycopg2 = types.SimpleNamespace(extras=types.SimpleNamespace(RealDictCursor=object))
    monkeypatch.setitem(sys.modules, "psycopg2", fake_psycopg2)
    monkeypatch.setitem(sys.modules, "psycopg2.extras", fake_psycopg2.extras)

    class FakeCursor:
        def __init__(self):
            self.rowcount = 0
            self.closed = False
            self._last = None

        def execute(self, query, params):
            self._last = query
            if query.lstrip().upper().startswith("SELECT"):
                self._row = {"id": "tenant-a-user", "tenant_id": "tenant-a"}
            else:
                raise AssertionError("cross-tenant SCIM upsert must not mutate or insert")

        def fetchone(self):
            return self._row

        def close(self):
            self.closed = True

    class FakeConnection:
        def __init__(self):
            self.cursor_obj = FakeCursor()
            self.committed = False
            self.rolled_back = False

        def cursor(self, *args, **kwargs):
            return self.cursor_obj

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

    db = FakeConnection()
    monkeypatch.setattr(router, "_get_db", lambda: db)

    with pytest.raises(router.HTTPException) as exc:
        router._scim_upsert_user(
            "tenant-b",
            {"userName": "shared@example.com", "displayName": "Tenant B User", "active": True},
        )

    assert exc.value.status_code == 409
    assert "another tenant" in exc.value.detail
    assert db.committed is False
    assert db.rolled_back is True


@pytest.mark.asyncio
async def test_scim_group_role_mapping_syncs_idp_groups(client, enterprise_admin_key, monkeypatch):
    import router

    calls = []

    def fake_sync(tenant_id, group_id, group):
        calls.append((tenant_id, group_id, group["displayName"], group.get("roles", [])))
        return {"id": group_id, "displayName": group["displayName"], "roles": group.get("roles", [])}

    monkeypatch.setattr(router, "_scim_sync_group_roles", fake_sync)

    resp = await client.put(
        "/scim/v2/Groups/group-1",
        headers=enterprise_admin_key,
        json={"displayName": "Finance", "roles": ["audit-reader", "credential-user"]},
    )

    assert resp.status_code == 200
    assert resp.json()["roles"] == ["audit-reader", "credential-user"]
    assert calls == [(QA_TENANT, "group-1", "Finance", ["audit-reader", "credential-user"])]
