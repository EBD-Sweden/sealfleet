"""Tenant-isolation regression tests for {{credential:NAME}} resolution.

Pentest 2026-07 C1: credential resolution was global-by-name — any tenant could
read any other tenant's stored secret. These tests lock the fix: resolution is
tenant-scoped in every mode, and a missing tenant_id fails closed.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import credentials  # noqa: E402


class _FakeCursor:
    """Minimal DB cursor that records the WHERE params and returns a row only
    when the query is scoped to the owning tenant."""

    def __init__(self, store, owner_tenant, envelope):
        self._store = store
        self._owner = owner_tenant
        self._envelope = envelope
        self._row = None

    def execute(self, sql, params=()):
        self._store["last_sql"] = sql
        self._store["last_params"] = params
        self._row = None
        if sql.strip().upper().startswith("SELECT"):
            name, tenant = params[0], params[1]
            # Only return the secret when BOTH name and tenant match the owner.
            if name == "victim_secret" and tenant == self._owner:
                self._row = {"encrypted_value": self._envelope, "storage_mode": "platform"}

    def fetchone(self):
        return self._row

    def close(self):
        pass


class _FakeConn:
    def __init__(self, store, owner_tenant, envelope):
        self._store = store
        self._owner = owner_tenant
        self._envelope = envelope

    def cursor(self, cursor_factory=None):
        return _FakeCursor(self._store, self._owner, self._envelope)

    def commit(self):
        pass


@pytest.fixture()
def platform_key(monkeypatch):
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    for var in ("MCPFINDER_ENCRYPTION_KEYS", "MCPFINDER_ENCRYPTION_KEY_ID",
                "MCPFINDER_DEPLOYMENT_ENV", "KUBERNETES_SERVICE_HOST", "BYOK_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    return key


def test_owner_tenant_resolves_but_other_tenant_cannot(platform_key):
    envelope = credentials.encrypt_platform("PENTEST_VICTIM_SECRET_sk_live_DEADBEEF")
    store = {}
    conn = _FakeConn(store, owner_tenant="victim-corp", envelope=envelope)

    # Owner tenant resolves the plaintext.
    owner = credentials.resolve_credential_token(
        "victim_secret", db_conn=conn, tenant_id="victim-corp"
    )
    assert owner == "PENTEST_VICTIM_SECRET_sk_live_DEADBEEF"
    # The query was tenant-scoped.
    assert "tenant_id = %s" in store["last_sql"]
    assert store["last_params"] == ("victim_secret", "victim-corp")

    # A different tenant gets nothing (the exploited cross-tenant read).
    attacker = credentials.resolve_credential_token(
        "victim_secret", db_conn=conn, tenant_id="attacker-tenant"
    )
    assert attacker is None


def test_missing_tenant_fails_closed(platform_key):
    envelope = credentials.encrypt_platform("secret")
    conn = _FakeConn({}, owner_tenant="victim-corp", envelope=envelope)
    assert credentials.resolve_credential_token("victim_secret", db_conn=conn, tenant_id=None) is None
    assert credentials.resolve_credential_token("victim_secret", db_conn=conn, tenant_id="") is None


def test_resolve_tokens_threads_tenant_into_string_and_nested(platform_key):
    envelope = credentials.encrypt_platform("sk-owned")
    conn = _FakeConn({}, owner_tenant="t1", envelope=envelope)

    out = credentials.resolve_credential_tokens(
        {"Authorization": "Bearer {{credential:victim_secret}}",
         "nested": ["{{credential:victim_secret}}"]},
        db_conn=conn, tenant_id="t1",
    )
    assert out["Authorization"] == "Bearer sk-owned"
    assert out["nested"] == ["sk-owned"]

    # Wrong tenant → token left unresolved (never leaks).
    out2 = credentials.resolve_credential_tokens(
        {"Authorization": "Bearer {{credential:victim_secret}}"},
        db_conn=conn, tenant_id="other",
    )
    assert out2["Authorization"] == "Bearer {{credential:victim_secret}}"


def test_k8s_secret_name_is_per_tenant(monkeypatch):
    captured = {}

    def fake_read(secret_name, key, namespace="default"):
        captured["secret_name"] = secret_name
        captured["key"] = key
        return "k8s-value" if secret_name.endswith("victim-corp") else None

    monkeypatch.setattr(credentials, "read_k8s_secret", fake_read)
    monkeypatch.delenv("MCPFINDER_K8S_SECRET_NAME", raising=False)

    val = credentials.read_k8s_secret_by_cred_name("db_password", "victim-corp")
    assert val == "k8s-value"
    assert captured["secret_name"] == "mcpfinder-creds-victim-corp"
    # A different tenant hits a different Secret → no cross-tenant read.
    assert credentials.read_k8s_secret_by_cred_name("db_password", "attacker") is None
    # No tenant → refused.
    assert credentials.read_k8s_secret_by_cred_name("db_password", "") is None
