"""Tests for privacy / GDPR endpoints (data-subject export + right-to-erasure).

Verifies auth-gating on the privileged actions, export shape, anonymization +
session revocation behaviour, and that audit events are written and the audit
log is explicitly preserved (never deleted) on erasure.
"""

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


QA_TENANT = "qa-tenant"
SUBJECT_USER_ID = "11111111-1111-1111-1111-111111111111"
SUBJECT_EMAIL = "subject@example.com"


@pytest.fixture()
def restricted_api_key(monkeypatch):
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
def privacy_admin_key(monkeypatch):
    import router

    monkeypatch.setattr(
        router.api_key_manager,
        "validate",
        lambda api_key: {
            "tenant_id": QA_TENANT,
            "name": "privacy-admin",
            "permissions": ["privacy.export", "privacy.erase"],
        }
        if api_key == "privacy-key"
        else None,
    )
    return {"X-API-Key": "privacy-key"}


class _FakeCursor:
    """Minimal stateful cursor backing the privacy endpoints."""

    def __init__(self, store):
        self.store = store
        self._result = None
        self.rowcount = 0

    def execute(self, query, params=None):
        q = " ".join(query.split())
        params = params or ()
        if "FROM users" in q and "WHERE tenant_id" in q:
            tenant, sid, email = params
            u = self.store["user"]
            if u and u["tenant_id"] == tenant and (str(u["id"]) == sid or u["email"] == email):
                self._result = dict(u)
            else:
                self._result = None
        elif "FROM user_roles" in q:
            self._result = list(self.store["roles"])
        elif "FROM user_sessions" in q and q.startswith("SELECT"):
            self._result = list(self.store["sessions"])
        elif "UPDATE user_sessions SET revoked_at" in q:
            n = 0
            for s in self.store["sessions"]:
                if s.get("revoked_at") is None:
                    s["revoked_at"] = dt.datetime.now(dt.timezone.utc)
                    n += 1
            self.rowcount = n
            self._result = None
        elif "UPDATE users" in q:
            self.store["user_updated"] = (q, params)
            self.rowcount = 1
            self._result = None
        else:
            self._result = None

    def fetchone(self):
        return self._result if isinstance(self._result, dict) else None

    def fetchall(self):
        return self._result if isinstance(self._result, list) else []

    def close(self):
        pass


class _FakeConn:
    def __init__(self, store):
        self.store = store
        self.committed = False
        self.rolled_back = False

    def cursor(self, *a, **k):
        return _FakeCursor(self.store)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


@pytest.fixture()
def fake_user_db(monkeypatch):
    import router

    store = {
        "user": {
            "id": SUBJECT_USER_ID,
            "tenant_id": QA_TENANT,
            "email": SUBJECT_EMAIL,
            "name": "Subject Person",
            "avatar_url": "https://example.com/a.png",
            "auth_provider": "native",
            "is_active": True,
            "is_admin": False,
            "last_login_at": dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
            "created_at": dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc),
            "updated_at": dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
        },
        "roles": [],
        "sessions": [
            {
                "id": "session-1",
                "created_at": dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
                "expires_at": dt.datetime(2026, 2, 1, tzinfo=dt.timezone.utc),
                "revoked_at": None,
            }
        ],
    }
    conn = _FakeConn(store)
    monkeypatch.setattr(router, "_get_db", lambda: conn)
    audit = []
    monkeypatch.setattr(router, "_write_audit_event", lambda **kw: audit.append(kw))
    return {"store": store, "conn": conn, "audit": audit}


# --- auth-gating -----------------------------------------------------------

@pytest.mark.asyncio
async def test_privacy_export_requires_privacy_export_action(client, restricted_api_key):
    resp = await client.get(
        "/privacy/export", params={"subject": SUBJECT_EMAIL}, headers=restricted_api_key
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Forbidden: missing permission privacy.export"


@pytest.mark.asyncio
async def test_privacy_erase_requires_privacy_erase_action(client, restricted_api_key):
    resp = await client.post(
        "/privacy/erase", json={"subject": SUBJECT_EMAIL}, headers=restricted_api_key
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Forbidden: missing permission privacy.erase"


# --- export ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_privacy_export_returns_subject_data(client, privacy_admin_key, fake_user_db):
    resp = await client.get(
        "/privacy/export", params={"subject": SUBJECT_EMAIL}, headers=privacy_admin_key
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema"] == "mcpfinder.privacy.export"
    assert body["user"]["email"] == SUBJECT_EMAIL
    assert body["user"]["name"] == "Subject Person"
    assert len(body["sessions"]) == 1
    # an audit event was written for the access
    actions = [e["action"] for e in fake_user_db["audit"]]
    assert "privacy.export" in actions


@pytest.mark.asyncio
async def test_privacy_export_unknown_subject_404(client, privacy_admin_key, fake_user_db):
    resp = await client.get(
        "/privacy/export", params={"subject": "nobody@example.com"}, headers=privacy_admin_key
    )
    assert resp.status_code == 404


# --- erasure ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_privacy_erase_anonymizes_and_revokes_sessions(
    client, privacy_admin_key, fake_user_db
):
    resp = await client.post(
        "/privacy/erase", json={"subject": SUBJECT_USER_ID}, headers=privacy_admin_key
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "erased"
    assert body["sessions_revoked"] == 1
    assert body["audit_events_preserved"] is True

    store = fake_user_db["store"]
    # sessions were revoked
    assert all(s["revoked_at"] is not None for s in store["sessions"])
    # users UPDATE anonymized PII (email rewritten, name nulled)
    q, params = store["user_updated"]
    assert "email = %s" in q and "name = NULL" in q and "is_active = FALSE" in q
    assert params[0].endswith("@redacted.invalid")

    # audit event written, marking the audit log as preserved
    erase_events = [e for e in fake_user_db["audit"] if e["action"] == "privacy.erase"]
    assert erase_events
    assert erase_events[0]["payload"]["audit_events_preserved"] is True


@pytest.mark.asyncio
async def test_privacy_erase_deactivate_mode(client, privacy_admin_key, fake_user_db):
    resp = await client.post(
        "/privacy/erase",
        json={"subject": SUBJECT_USER_ID, "mode": "deactivate"},
        headers=privacy_admin_key,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "deactivated"
    q, _ = fake_user_db["store"]["user_updated"]
    assert "is_active = FALSE" in q
    # deactivate must NOT rewrite the email/name
    assert "email = %s" not in q


@pytest.mark.asyncio
async def test_privacy_erase_invalid_mode_400(client, privacy_admin_key, fake_user_db):
    resp = await client.post(
        "/privacy/erase",
        json={"subject": SUBJECT_USER_ID, "mode": "obliterate"},
        headers=privacy_admin_key,
    )
    assert resp.status_code == 400


# --- retention pruning helper ---------------------------------------------

def test_prune_operational_data_never_touches_audit_events():
    import router

    executed = []

    class C:
        rowcount = 3

        def execute(self, q, p=None):
            executed.append(" ".join(q.split()))

        def close(self):
            pass

    class Conn:
        def cursor(self):
            return C()

        def commit(self):
            pass

    result = router.prune_operational_data(Conn(), retention_days=30)
    assert result["pruned_sessions"] == 3
    assert result["retention_days"] == 30
    # only user_sessions is pruned; audit_events is never referenced
    joined = " ".join(executed)
    assert "user_sessions" in joined
    assert "audit_events" not in joined
