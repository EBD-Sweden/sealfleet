"""Tests for the Sealfleet open-core licensing / entitlement resolver."""

import base64
import json
import sys
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import licensing  # noqa: E402


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _issue(priv: Ed25519PrivateKey, payload: dict) -> str:
    body = json.dumps(payload, separators=(",", ":")).encode()
    sig = priv.sign(body)
    return f"{_b64url(body)}.{_b64url(sig)}"


@pytest.fixture()
def keypair():
    priv = Ed25519PrivateKey.generate()
    pub_b64 = base64.b64encode(
        priv.public_key().public_bytes_raw()
    ).decode()
    return priv, pub_b64


def test_free_by_default_no_key(keypair):
    _priv, pub = keypair
    ent = licensing.verify_license_key("", pubkey_b64=pub)
    assert ent.tier == licensing.TIER_FREE
    assert ent.seats == 1
    assert not ent.is_enterprise
    assert not ent.has(licensing.FEATURE_SSO)


def test_valid_enterprise_key_unlocks_all_features(keypair):
    priv, pub = keypair
    token = _issue(priv, {"customer": "ACME Corp", "tier": "enterprise",
                          "iat": int(time.time()), "exp": int(time.time()) + 3600})
    ent = licensing.verify_license_key(token, pubkey_b64=pub)
    assert ent.is_enterprise
    assert ent.customer == "ACME Corp"
    for feat in licensing.ENTERPRISE_FEATURES:
        assert ent.has(feat)
    assert ent.seats > 1


def test_feature_metered_key_narrows_features(keypair):
    priv, pub = keypair
    token = _issue(priv, {"customer": "SSO-only Inc", "tier": "enterprise",
                          "features": [licensing.FEATURE_SSO], "seats": 25,
                          "exp": int(time.time()) + 3600})
    ent = licensing.verify_license_key(token, pubkey_b64=pub)
    assert ent.has(licensing.FEATURE_SSO)
    assert not ent.has(licensing.FEATURE_SCIM)
    assert ent.seats == 25


def test_expired_key_falls_back_to_free(keypair):
    priv, pub = keypair
    token = _issue(priv, {"customer": "X", "tier": "enterprise",
                          "exp": int(time.time()) - 10})
    ent = licensing.verify_license_key(token, pubkey_b64=pub)
    assert ent.tier == licensing.TIER_FREE
    assert "expired" in ent.reason


def test_tampered_payload_rejected(keypair):
    priv, pub = keypair
    token = _issue(priv, {"customer": "X", "tier": "enterprise",
                          "exp": int(time.time()) + 3600})
    body_b64, sig_b64 = token.split(".")
    forged = json.dumps({"customer": "Attacker", "tier": "enterprise",
                         "exp": int(time.time()) + 999999}, separators=(",", ":")).encode()
    tampered = f"{_b64url(forged)}.{sig_b64}"
    ent = licensing.verify_license_key(tampered, pubkey_b64=pub)
    assert ent.tier == licensing.TIER_FREE
    assert "invalid" in ent.reason or "signature" in ent.reason


def test_wrong_key_rejected(keypair):
    priv, _pub = keypair
    other_pub = base64.b64encode(
        Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    ).decode()
    token = _issue(priv, {"tier": "enterprise", "exp": int(time.time()) + 3600})
    ent = licensing.verify_license_key(token, pubkey_b64=other_pub)
    assert ent.tier == licensing.TIER_FREE


def test_malformed_token_is_free(keypair):
    _priv, pub = keypair
    assert licensing.verify_license_key("not-a-token", pubkey_b64=pub).tier == licensing.TIER_FREE
    assert licensing.verify_license_key("a.b.c", pubkey_b64=pub).tier == licensing.TIER_FREE


def test_resolver_env_and_cache(keypair, monkeypatch):
    priv, pub = keypair
    token = _issue(priv, {"customer": "Cached Co", "tier": "enterprise",
                          "exp": int(time.time()) + 3600})
    monkeypatch.setattr(licensing, "_DEFAULT_LICENSE_PUBKEY_B64", pub)
    monkeypatch.setenv("SEALFLEET_LICENSE_KEY", token)
    monkeypatch.setattr(licensing, "_CACHE", {"ent": None, "at": 0.0})
    ent = licensing.resolve_entitlement(force=True)
    assert ent.is_enterprise and ent.customer == "Cached Co"
    assert licensing.feature_enabled(licensing.FEATURE_SSO) is True
