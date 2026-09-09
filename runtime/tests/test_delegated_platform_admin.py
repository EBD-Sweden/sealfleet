"""Authorization for platform admins acting through a delegating API key.

The portal proxies with its backend API key plus X-Sealfleet-* identity
headers; a user's JWT is_admin claim never reaches the router that way. So
_authorize_action consults the users table — but only for identities that a
delegation-enabled key actually vouched for, otherwise any API key could
escalate by inventing a user id header.
"""

from types import SimpleNamespace

import pytest

import router


class _Request:
    """Minimal stand-in for a Starlette Request as _authorize_action reads it."""

    def __init__(self, *, auth_type="api_key", identity=None, user_id="", is_admin=False):
        self.state = SimpleNamespace(
            auth_type=auth_type,
            identity=identity,
            user_id=user_id,
            is_admin=is_admin,
            tenant_id="tenant-1",
        )
        self.headers = {}


ADMIN_ID = "11111111-1111-1111-1111-111111111111"
OTHER_ID = "22222222-2222-2222-2222-222222222222"

DELEGATED = {
    "user_id": ADMIN_ID,
    "tenant_id": "tenant-1",
    "delegated_from": {"api_key_name": "portal", "api_key_tenant_id": "tenant-1"},
}


@pytest.fixture
def only_admin_in_db(monkeypatch):
    """ADMIN_ID is an active platform admin; nobody else is."""
    monkeypatch.setattr(router, "_db_user_is_platform_admin", lambda uid: uid == ADMIN_ID)
    # No explicit action grants exist, so the admin path is what is under test.
    monkeypatch.setattr(router, "_db_has_action_permission", lambda *a, **k: False)
    monkeypatch.setattr(router, "get_tenant_id", lambda request: "tenant-1")


def test_delegated_platform_admin_is_authorized(only_admin_in_db):
    req = _Request(identity=DELEGATED, user_id=ADMIN_ID)
    router._authorize_action(req, "credential.read")  # must not raise


def test_delegated_non_admin_is_denied(only_admin_in_db):
    identity = {**DELEGATED, "user_id": OTHER_ID}
    req = _Request(identity=identity, user_id=OTHER_ID)
    with pytest.raises(router.HTTPException) as exc:
        router._authorize_action(req, "credential.read")
    assert exc.value.status_code == 403


def test_api_key_without_delegation_cannot_escalate(only_admin_in_db):
    """No delegated_from means the key never opted in: the admin path is skipped
    even when the claimed user id really is an admin."""
    identity = {"user_id": ADMIN_ID, "tenant_id": "tenant-1"}  # no delegated_from
    req = _Request(identity=identity, user_id=ADMIN_ID)
    with pytest.raises(router.HTTPException) as exc:
        router._authorize_action(req, "credential.read")
    assert exc.value.status_code == 403


def test_forged_user_id_is_denied(only_admin_in_db):
    """A delegation-enabled key naming a user who is not an admin gets nothing."""
    identity = {**DELEGATED, "user_id": "99999999-9999-9999-9999-999999999999"}
    req = _Request(identity=identity, user_id="99999999-9999-9999-9999-999999999999")
    with pytest.raises(router.HTTPException) as exc:
        router._authorize_action(req, "credential.read")
    assert exc.value.status_code == 403


def test_inactive_admin_is_denied(monkeypatch):
    """_db_user_is_platform_admin filters on is_active, so a deactivated admin
    loses the bypass immediately."""
    monkeypatch.setattr(router, "_db_user_is_platform_admin", lambda uid: False)
    monkeypatch.setattr(router, "_db_has_action_permission", lambda *a, **k: False)
    monkeypatch.setattr(router, "get_tenant_id", lambda request: "tenant-1")
    req = _Request(identity=DELEGATED, user_id=ADMIN_ID)
    with pytest.raises(router.HTTPException) as exc:
        router._authorize_action(req, "credential.read")
    assert exc.value.status_code == 403


def test_explicit_grant_still_wins_without_admin(monkeypatch):
    """The new path is additive: an ordinary user with a real grant is unaffected."""
    monkeypatch.setattr(router, "_db_user_is_platform_admin", lambda uid: False)
    monkeypatch.setattr(router, "_db_has_action_permission", lambda *a, **k: True)
    monkeypatch.setattr(router, "get_tenant_id", lambda request: "tenant-1")
    identity = {**DELEGATED, "user_id": OTHER_ID}
    req = _Request(identity=identity, user_id=OTHER_ID)
    router._authorize_action(req, "credential.read")  # must not raise
