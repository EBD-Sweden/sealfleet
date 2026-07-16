"""JWKS fetching, caching, and RS256 token verification."""
import time, json, base64, logging
from typing import Any
import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

logger = logging.getLogger(__name__)

_cache: dict[str, Any] = {}  # keyed by jwks_url

async def get_jwks_keys(jwks_url: str, ttl: int = 300) -> list[dict]:
    """Fetch and cache JWK keys from url with TTL seconds."""
    entry = _cache.get(jwks_url)
    now = time.monotonic()
    if entry and (now - entry["fetched_at"]) < ttl:
        return entry["keys"]
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(jwks_url)
        resp.raise_for_status()
    keys = resp.json().get("keys", [])
    _cache[jwks_url] = {"keys": keys, "fetched_at": now}
    return keys

def _decode_header(token: str) -> dict:
    header_b64 = token.split(".")[0]
    header_b64 += "=" * (-len(header_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(header_b64))

async def verify_token(
    token: str,
    jwks_url: str,
    *,
    audience: str | None = None,   # if set, validates aud claim
    issuer: str | None = None,     # if set, validates iss claim
    algorithms: list[str] | None = None,
) -> dict:
    """
    Verify a JWT against JWKS. Returns decoded payload.
    Raises jwt.exceptions.InvalidTokenError on failure.
    """
    algorithms = algorithms or ["RS256"]
    header = _decode_header(token)
    kid = header.get("kid")

    keys = await get_jwks_keys(jwks_url)
    # Find matching key by kid; fall back to trying all keys
    candidates = [k for k in keys if k.get("kid") == kid] if kid else keys
    if not candidates:
        candidates = keys

    last_exc = None
    for key_data in candidates:
        try:
            public_key = RSAAlgorithm.from_jwk(json.dumps(key_data))
            options = {"verify_aud": bool(audience)}
            payload = jwt.decode(
                token,
                public_key,
                algorithms=algorithms,
                audience=audience,
                issuer=issuer,
                options=options,
            )
            return payload
        except Exception as e:
            last_exc = e
            continue

    raise last_exc or jwt.exceptions.InvalidTokenError("No valid JWKS key found")
