"""Short-lived token minting for scoped access.

Creates time-limited, scope-limited tokens that downstream services
can validate. Ensures least-privilege: each tool call gets a token
scoped to exactly what it needs.

MVP: Simple HMAC-based tokens. Later: JWT with RS256, integration
with identity providers.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("mcpfinder.broker.tokens")

# Signing secret for HMAC tokens. Loaded from env; the dev fallback is allowed
# only outside production-like environments (fail closed in prod).
_SIGNING_SECRET_ENV = "MCPFINDER_TOKEN_SIGNING_SECRET"
_DEV_SIGNING_SECRET = "mcpfinder-dev-secret-change-me"


def _is_production_like_env() -> bool:
    candidates = (
        os.environ.get("MCPFINDER_DEPLOYMENT_ENV"),
        os.environ.get("DEPLOYMENT_ENV"),
        os.environ.get("ENVIRONMENT"),
        os.environ.get("APP_ENV"),
        os.environ.get("NODE_ENV"),
    )
    normalized = {
        str(v).strip().lower().replace("_", "-") for v in candidates if v is not None
    }
    return bool(normalized & {"production", "prod", "public-test"})


def _resolve_signing_secret() -> str:
    secret = os.environ.get(_SIGNING_SECRET_ENV)
    if secret:
        return secret
    if _is_production_like_env():
        raise RuntimeError(
            f"{_SIGNING_SECRET_ENV} is not set in a production-like environment; "
            "refusing the dev signing secret."
        )
    logger.warning(
        "%s not set — using dev signing secret (non-production only)", _SIGNING_SECRET_ENV
    )
    return _DEV_SIGNING_SECRET


@dataclass
class Token:
    """A short-lived access token."""
    token_id: str
    user_id: str
    scope: str
    issued_at: float
    expires_at: float
    signature: str

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    @property
    def ttl_remaining(self) -> float:
        return max(0.0, self.expires_at - time.time())

    def to_bearer(self) -> str:
        """Serialize to a bearer token string."""
        payload = json.dumps({
            "tid": self.token_id,
            "uid": self.user_id,
            "scope": self.scope,
            "iat": self.issued_at,
            "exp": self.expires_at,
            "sig": self.signature,
        }, separators=(",", ":"))
        return payload


class TokenMinter:
    """Mints and validates short-lived access tokens.

    Tokens are scoped to a specific user + action and expire quickly.
    """

    def __init__(self, signing_secret: Optional[str] = None):
        self._secret = (signing_secret or _resolve_signing_secret()).encode()
        self._issued: dict[str, Token] = {}

    def _sign(self, data: str) -> str:
        return hmac.new(self._secret, data.encode(), hashlib.sha256).hexdigest()[:32]

    def mint(
        self,
        user_id: str,
        scope: str,
        ttl_seconds: int = 300,
    ) -> Token:
        """Mint a new short-lived token.

        Args:
            user_id: The user this token is for.
            scope: What this token grants (e.g., "tools:execute:crypto.price_quote").
            ttl_seconds: Time-to-live in seconds (default 5 minutes).

        Returns:
            A Token object.
        """
        now = time.time()
        token_id = hashlib.sha256(
            f"{user_id}:{scope}:{now}".encode()
        ).hexdigest()[:16]

        signature = self._sign(f"{token_id}:{user_id}:{scope}:{now}")

        token = Token(
            token_id=token_id,
            user_id=user_id,
            scope=scope,
            issued_at=now,
            expires_at=now + ttl_seconds,
            signature=signature,
        )

        self._issued[token_id] = token
        logger.info(
            "Token minted: user=%s scope=%s ttl=%ds",
            user_id, scope, ttl_seconds,
        )
        return token

    def validate(self, token_id: str) -> Optional[Token]:
        """Validate a token by ID.

        Returns:
            The Token if valid and not expired, None otherwise.
        """
        token = self._issued.get(token_id)
        if token is None:
            return None

        if token.is_expired:
            del self._issued[token_id]
            return None

        # Verify signature
        expected_sig = self._sign(
            f"{token.token_id}:{token.user_id}:{token.scope}:{token.issued_at}"
        )
        if not hmac.compare_digest(token.signature, expected_sig):
            logger.warning("Invalid token signature: %s", token_id)
            return None

        return token

    def revoke(self, token_id: str) -> bool:
        """Revoke a token before expiry."""
        if token_id in self._issued:
            del self._issued[token_id]
            return True
        return False

    def cleanup_expired(self) -> int:
        """Remove expired tokens. Returns count of removed tokens."""
        expired = [
            tid for tid, t in self._issued.items() if t.is_expired
        ]
        for tid in expired:
            del self._issued[tid]
        return len(expired)


# Singleton
token_minter = TokenMinter()
