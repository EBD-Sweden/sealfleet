"""Unit tests for versioned platform key rotation (CC6.1).

Verifies:
  * encrypt-with-current / decrypt round-trip (envelope format)
  * back-compat: single ENCRYPTION_KEY env still works as "v1"
  * legacy bare Fernet ciphertext still decrypts
  * rotation: ciphertext encrypted under an old key still decrypts after a new
    current key is introduced; re-encryption migrates onto the new key
  * a ciphertext from a key not in the ring fails to decrypt
  * fail-closed in production-like env when no key configured
"""

import json
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import credentials  # noqa: E402


# Two stable, distinct Fernet keys for deterministic rotation tests.
KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()


def _clear_key_env(monkeypatch):
    for var in (
        "ENCRYPTION_KEY",
        "MCPFINDER_ENCRYPTION_KEY",
        "MCPFINDER_ENCRYPTION_KEYS",
        "MCPFINDER_ENCRYPTION_KEY_ID",
        "MCPFINDER_DEPLOYMENT_ENV",
        "DEPLOYMENT_ENV",
        "ENVIRONMENT",
        "APP_ENV",
        "NODE_ENV",
    ):
        monkeypatch.delenv(var, raising=False)


def test_encrypt_current_decrypt_roundtrip(monkeypatch):
    _clear_key_env(monkeypatch)
    monkeypatch.setenv("ENCRYPTION_KEY", KEY_A)

    ct = credentials.encrypt_platform("super-secret")
    assert ct.startswith("mcpfk1:v1:")  # default key id is v1
    assert credentials.decrypt_platform(ct) == "super-secret"
    assert credentials.platform_current_key_id() == "v1"


def test_single_env_key_back_compat_is_v1(monkeypatch):
    _clear_key_env(monkeypatch)
    monkeypatch.setenv("ENCRYPTION_KEY", KEY_A)
    assert credentials.platform_current_key_id() == "v1"


def test_legacy_bare_ciphertext_still_decrypts(monkeypatch):
    """Ciphertext written before envelopes (bare Fernet token) still decrypts."""
    _clear_key_env(monkeypatch)
    monkeypatch.setenv("ENCRYPTION_KEY", KEY_A)

    legacy = Fernet(KEY_A.encode()).encrypt(b"legacy-value").decode()
    assert ":" not in legacy.split(".")[0] or not legacy.startswith("mcpfk1:")
    assert credentials.decrypt_platform(legacy) == "legacy-value"


def test_rotation_old_ciphertext_still_decrypts(monkeypatch):
    _clear_key_env(monkeypatch)
    # Phase 1: only KEY_A as v1, encrypt a value.
    monkeypatch.setenv("ENCRYPTION_KEY", KEY_A)
    old_ct = credentials.encrypt_platform("rotate-me")
    assert old_ct.startswith("mcpfk1:v1:")

    # Phase 2: rotate — introduce v2=KEY_B as the new current, keep v1=KEY_A.
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv(
        "MCPFINDER_ENCRYPTION_KEYS", json.dumps({"v1": KEY_A, "v2": KEY_B})
    )
    monkeypatch.setenv("MCPFINDER_ENCRYPTION_KEY_ID", "v2")

    assert credentials.platform_current_key_id() == "v2"
    # Old ciphertext (tagged v1) still decrypts using the historical key.
    assert credentials.decrypt_platform(old_ct) == "rotate-me"
    # New encryptions use v2.
    new_ct = credentials.encrypt_platform("fresh")
    assert new_ct.startswith("mcpfk1:v2:")
    assert credentials.decrypt_platform(new_ct) == "fresh"


def test_reencrypt_migrates_onto_current_key(monkeypatch):
    _clear_key_env(monkeypatch)
    monkeypatch.setenv("ENCRYPTION_KEY", KEY_A)
    old_ct = credentials.encrypt_platform("migrate-me")

    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv(
        "MCPFINDER_ENCRYPTION_KEYS", json.dumps({"v1": KEY_A, "v2": KEY_B})
    )
    monkeypatch.setenv("MCPFINDER_ENCRYPTION_KEY_ID", "v2")

    migrated = credentials.reencrypt_platform(old_ct)
    assert migrated.startswith("mcpfk1:v2:")
    assert credentials.decrypt_platform(migrated) == "migrate-me"

    # After fully decommissioning v1, the migrated ciphertext still decrypts.
    monkeypatch.setenv("MCPFINDER_ENCRYPTION_KEYS", json.dumps({"v2": KEY_B}))
    assert credentials.decrypt_platform(migrated) == "migrate-me"


def test_wrong_key_fails(monkeypatch):
    _clear_key_env(monkeypatch)
    monkeypatch.setenv("ENCRYPTION_KEY", KEY_A)
    ct = credentials.encrypt_platform("secret")

    # Swap to a ring that does NOT contain KEY_A — must fail.
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("MCPFINDER_ENCRYPTION_KEYS", json.dumps({"v9": KEY_B}))
    monkeypatch.setenv("MCPFINDER_ENCRYPTION_KEY_ID", "v9")
    with pytest.raises(InvalidToken):
        credentials.decrypt_platform(ct)


def test_unknown_current_key_id_raises(monkeypatch):
    _clear_key_env(monkeypatch)
    monkeypatch.setenv("MCPFINDER_ENCRYPTION_KEYS", json.dumps({"v1": KEY_A}))
    monkeypatch.setenv("MCPFINDER_ENCRYPTION_KEY_ID", "does-not-exist")
    with pytest.raises(RuntimeError):
        credentials.platform_current_key_id()


def test_invalid_keys_json_raises(monkeypatch):
    _clear_key_env(monkeypatch)
    monkeypatch.setenv("MCPFINDER_ENCRYPTION_KEYS", "{not valid json")
    with pytest.raises(RuntimeError):
        credentials.platform_current_key_id()


def test_fail_closed_in_production_without_key(monkeypatch):
    _clear_key_env(monkeypatch)
    monkeypatch.setenv("MCPFINDER_DEPLOYMENT_ENV", "production")
    with pytest.raises(RuntimeError):
        credentials.encrypt_platform("secret")


def test_dev_fallback_key_when_unset(monkeypatch):
    """In dev (no prod signal) with no keys, the deterministic dev key is used."""
    _clear_key_env(monkeypatch)
    ct = credentials.encrypt_platform("dev-secret")
    assert credentials.decrypt_platform(ct) == "dev-secret"
    assert credentials.platform_current_key_id() == "v1"
