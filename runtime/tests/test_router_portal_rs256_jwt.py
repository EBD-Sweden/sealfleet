"""Portal RS256 session JWT validation tests for runtime/router auth."""

import json
import time

import pytest

import router


@pytest.fixture()
def portal_rs256_keys():
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt.algorithms import RSAAlgorithm

    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    jwk = json.loads(RSAAlgorithm.to_jwk(signing_key.public_key()))
    jwk["kid"] = "portal-key-1"
    jwk["alg"] = "RS256"
    jwk["use"] = "sig"

    other_jwk = json.loads(RSAAlgorithm.to_jwk(other_key.public_key()))
    other_jwk["kid"] = "portal-key-1"
    other_jwk["alg"] = "RS256"
    other_jwk["use"] = "sig"

    return signing_key, jwk, other_jwk


@pytest.fixture()
def portal_jwt_env(monkeypatch, portal_rs256_keys):
    _signing_key, jwk, _other_jwk = portal_rs256_keys
    monkeypatch.setattr(router, "PORTAL_JWT_ISSUER", "https://portal.example.test", raising=False)
    monkeypatch.setattr(router, "PORTAL_JWT_AUDIENCE", "mcpfinder-runtime", raising=False)
    monkeypatch.setattr(router, "_fetch_jwks_sync", lambda: [jwk])
    if hasattr(router, "_jwks_cache"):
        router._jwks_cache["keys"] = None
        router._jwks_cache["fetched_at"] = 0.0


def _portal_claims(*, issuer="https://portal.example.test", audience="mcpfinder-runtime", exp=None):
    now = int(time.time())
    payload = {
        "sub": "user-123",
        "user_id": "user-123",
        "tenant_id": "tenant-456",
        "email": "user@example.test",
        "is_admin": False,
        "iat": now,
        "exp": exp if exp is not None else now + 300,
    }
    if issuer is not None:
        payload["iss"] = issuer
    if audience is not None:
        payload["aud"] = audience
    return payload


def _portal_rs256_token(signing_key, *, issuer="https://portal.example.test", audience="mcpfinder-runtime", exp=None):
    import jwt

    return jwt.encode(
        _portal_claims(issuer=issuer, audience=audience, exp=exp),
        signing_key,
        algorithm="RS256",
        headers={"kid": "portal-key-1"},
    )


def _portal_hs256_token(secret, *, issuer="https://portal.example.test", audience="mcpfinder-runtime", exp=None):
    import jwt

    return jwt.encode(_portal_claims(issuer=issuer, audience=audience, exp=exp), secret, algorithm="HS256")


def test_valid_portal_rs256_jwt_is_accepted(portal_jwt_env, portal_rs256_keys):
    signing_key, _jwk, _other_jwk = portal_rs256_keys

    claims = router._validate_user_jwt(_portal_rs256_token(signing_key))

    assert claims is not None
    assert claims["user_id"] == "user-123"
    assert claims["tenant_id"] == "tenant-456"
    assert claims["is_admin"] is False
    assert claims["email"] == "user@example.test"
    assert claims["sub"] == "user-123"
    assert claims["jwt_claims"]["iss"] == "https://portal.example.test"
    assert claims["jwt_claims"]["aud"] == "mcpfinder-runtime"


def test_configured_portal_rs256_public_key_is_accepted_without_jwks_fetch(monkeypatch, portal_rs256_keys):
    from cryptography.hazmat.primitives import serialization

    signing_key, _jwk, _other_jwk = portal_rs256_keys
    public_pem = signing_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    monkeypatch.setattr(router, "PORTAL_JWT_ISSUER", "https://portal.example.test", raising=False)
    monkeypatch.setattr(router, "PORTAL_JWT_AUDIENCE", "mcpfinder-runtime", raising=False)
    monkeypatch.setattr(router, "PORTAL_RS256_PUBLIC_KEY", public_pem, raising=False)
    monkeypatch.setattr(router, "PORTAL_RS256_KEY_ID", "portal-key-1", raising=False)
    monkeypatch.setattr(router, "_portal_public_key_cache", {"pem": None, "keys": None}, raising=False)
    monkeypatch.setattr(router, "_fetch_jwks_sync", lambda: pytest.fail("JWKS fetch should not run when a public key is configured"))

    claims = router._validate_user_jwt(_portal_rs256_token(signing_key))

    assert claims["user_id"] == "user-123"
    assert claims["tenant_id"] == "tenant-456"


def test_portal_rs256_jwt_signed_by_wrong_key_is_rejected(monkeypatch, portal_rs256_keys):
    signing_key, _jwk, other_jwk = portal_rs256_keys
    monkeypatch.setattr(router, "PORTAL_JWT_ISSUER", "https://portal.example.test", raising=False)
    monkeypatch.setattr(router, "PORTAL_JWT_AUDIENCE", "mcpfinder-runtime", raising=False)
    monkeypatch.setattr(router, "PORTAL_RS256_PUBLIC_KEY", "", raising=False)
    monkeypatch.setattr(router, "_fetch_jwks_sync", lambda: [other_jwk])

    assert router._validate_user_jwt(_portal_rs256_token(signing_key)) is None


@pytest.mark.parametrize(
    ("issuer", "audience"),
    [
        ("https://evil.example.test", "mcpfinder-runtime"),
        ("https://portal.example.test", "other-runtime"),
    ],
)
def test_portal_rs256_jwt_wrong_issuer_or_audience_is_rejected(
    portal_jwt_env,
    portal_rs256_keys,
    issuer,
    audience,
):
    signing_key, _jwk, _other_jwk = portal_rs256_keys

    assert router._validate_user_jwt(_portal_rs256_token(signing_key, issuer=issuer, audience=audience)) is None


def test_portal_rs256_jwt_expired_token_is_rejected(portal_jwt_env, portal_rs256_keys):
    signing_key, _jwk, _other_jwk = portal_rs256_keys

    assert router._validate_user_jwt(_portal_rs256_token(signing_key, exp=int(time.time()) - 10)) is None


@pytest.mark.parametrize(
    ("configured_issuer", "configured_audience", "token_issuer", "token_audience"),
    [
        ("", "mcpfinder-runtime", None, "mcpfinder-runtime"),
        ("https://portal.example.test", "", "https://portal.example.test", None),
        ("", "", "https://evil.example.test", "mcpfinder-runtime"),
        ("", "", "https://portal.example.test", "other-runtime"),
    ],
)
def test_portal_rs256_jwt_fails_closed_when_prod_like_issuer_or_audience_config_is_missing(
    monkeypatch,
    portal_rs256_keys,
    configured_issuer,
    configured_audience,
    token_issuer,
    token_audience,
):
    signing_key, jwk, _other_jwk = portal_rs256_keys
    monkeypatch.setenv("MCPFINDER_DEPLOYMENT_ENV", "public-test")
    monkeypatch.setattr(router, "PORTAL_JWT_ISSUER", configured_issuer, raising=False)
    monkeypatch.setattr(router, "PORTAL_JWT_AUDIENCE", configured_audience, raising=False)
    monkeypatch.setattr(router, "PORTAL_RS256_PUBLIC_KEY", "", raising=False)
    monkeypatch.setattr(router, "_fetch_jwks_sync", lambda: [jwk])

    token = _portal_rs256_token(signing_key, issuer=token_issuer, audience=token_audience)

    assert router._validate_user_jwt(token) is None


@pytest.mark.parametrize(
    ("issuer", "audience"),
    [
        ("https://evil.example.test", "mcpfinder-runtime"),
        ("https://portal.example.test", "other-runtime"),
        ("https://portal.example.test", None),
    ],
)
def test_enabled_legacy_portal_hs256_fallback_enforces_configured_issuer_and_audience(
    monkeypatch,
    issuer,
    audience,
):
    secret = "x" * 32
    monkeypatch.setattr(router, "NEXTAUTH_SECRET", secret, raising=False)
    monkeypatch.setattr(router, "PORTAL_JWT_ISSUER", "https://portal.example.test", raising=False)
    monkeypatch.setattr(router, "PORTAL_JWT_AUDIENCE", "mcpfinder-runtime", raising=False)
    monkeypatch.setenv("MCPFINDER_DEPLOYMENT_ENV", "development")
    monkeypatch.setenv("AUTH_ALLOW_LEGACY_PORTAL_HS256", "true")

    valid_claims = router._validate_user_jwt(_portal_hs256_token(secret))
    token = _portal_hs256_token(secret, issuer=issuer, audience=audience)

    assert valid_claims is not None
    assert valid_claims["user_id"] == "user-123"
    assert router._validate_user_jwt(token) is None


def test_portal_hs256_fallback_rejects_valid_legacy_token_by_default(monkeypatch):
    secret = "x" * 32
    monkeypatch.setattr(router, "NEXTAUTH_SECRET", secret, raising=False)
    monkeypatch.setattr(router, "PORTAL_JWT_ISSUER", "https://portal.example.test", raising=False)
    monkeypatch.setattr(router, "PORTAL_JWT_AUDIENCE", "mcpfinder-runtime", raising=False)
    monkeypatch.delenv("AUTH_ALLOW_LEGACY_PORTAL_HS256", raising=False)

    assert router._validate_user_jwt(_portal_hs256_token(secret)) is None


def test_portal_hs256_fallback_accepts_valid_legacy_token_only_with_explicit_non_production_flag(monkeypatch):
    secret = "x" * 32
    monkeypatch.setattr(router, "NEXTAUTH_SECRET", secret, raising=False)
    monkeypatch.setattr(router, "PORTAL_JWT_ISSUER", "https://portal.example.test", raising=False)
    monkeypatch.setattr(router, "PORTAL_JWT_AUDIENCE", "mcpfinder-runtime", raising=False)
    monkeypatch.setenv("MCPFINDER_DEPLOYMENT_ENV", "development")
    monkeypatch.setenv("AUTH_ALLOW_LEGACY_PORTAL_HS256", "true")

    claims = router._validate_user_jwt(_portal_hs256_token(secret))

    assert claims is not None
    assert claims["user_id"] == "user-123"
    assert claims["tenant_id"] == "tenant-456"


def test_portal_hs256_fallback_is_disabled_in_public_test_even_with_legacy_flag(monkeypatch):
    secret = "x" * 32
    monkeypatch.setattr(router, "NEXTAUTH_SECRET", secret, raising=False)
    monkeypatch.setattr(router, "PORTAL_JWT_ISSUER", "https://portal.example.test", raising=False)
    monkeypatch.setattr(router, "PORTAL_JWT_AUDIENCE", "mcpfinder-runtime", raising=False)
    monkeypatch.setenv("MCPFINDER_DEPLOYMENT_ENV", "public-test")
    monkeypatch.setenv("AUTH_ALLOW_LEGACY_PORTAL_HS256", "true")

    assert router._validate_user_jwt(_portal_hs256_token(secret)) is None
