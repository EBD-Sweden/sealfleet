"""Vault integration for secret storage and retrieval.

MVP: In-memory secret store with basic encryption simulation.
Later: HashiCorp Vault, AWS KMS, Azure Key Vault integration.

Key principle: LLM is planner, not secret-holder. Credentials never
pass through the model — they're injected at execution time by the broker.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("mcpfinder.broker.vault")


@dataclass
class SecretMetadata:
    """Metadata about a stored secret (the secret value is never exposed)."""
    secret_id: str
    owner_id: str
    name: str
    secret_type: str  # "api_key", "oauth_token", "private_key", etc.
    created_at: float = 0.0
    expires_at: Optional[float] = None
    last_accessed: float = 0.0
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at


class VaultClient:
    """In-memory secret store (MVP).

    Production implementation would delegate to HashiCorp Vault or
    cloud KMS via their respective SDKs.
    """

    def __init__(self):
        # secret_id -> (metadata, encrypted_value)
        self._secrets: dict[str, tuple[SecretMetadata, str]] = {}

    @staticmethod
    def _generate_id(owner_id: str, name: str) -> str:
        return hashlib.sha256(f"{owner_id}:{name}".encode()).hexdigest()[:16]

    def store_secret(
        self,
        owner_id: str,
        name: str,
        value: str,
        secret_type: str = "api_key",
        ttl_seconds: Optional[int] = None,
        tags: Optional[dict[str, str]] = None,
    ) -> SecretMetadata:
        """Store a secret.

        Args:
            owner_id: User who owns this secret.
            name: Human-readable name (e.g., "binance-api-key").
            value: The secret value.
            secret_type: Classification of the secret.
            ttl_seconds: Optional time-to-live.
            tags: Optional key-value tags.

        Returns:
            Metadata about the stored secret (not the value).
        """
        secret_id = self._generate_id(owner_id, name)
        now = time.time()
        expires_at = (now + ttl_seconds) if ttl_seconds else None

        metadata = SecretMetadata(
            secret_id=secret_id,
            owner_id=owner_id,
            name=name,
            secret_type=secret_type,
            created_at=now,
            expires_at=expires_at,
            tags=tags or {},
        )

        # In production: encrypt with KMS before storing
        self._secrets[secret_id] = (metadata, value)
        logger.info("Secret stored: %s (type=%s, owner=%s)", name, secret_type, owner_id)
        return metadata

    def get_secret(self, secret_id: str, accessor_id: str) -> Optional[str]:
        """Retrieve a secret value.

        Args:
            secret_id: The secret identifier.
            accessor_id: ID of the user/service accessing the secret.

        Returns:
            The secret value, or None if not found or expired.
        """
        entry = self._secrets.get(secret_id)
        if entry is None:
            logger.warning("Secret not found: %s", secret_id)
            return None

        metadata, value = entry

        # Check ownership
        if metadata.owner_id != accessor_id:
            logger.warning(
                "Unauthorized secret access: %s tried to access %s's secret",
                accessor_id,
                metadata.owner_id,
            )
            return None

        # Check expiry
        if metadata.is_expired:
            logger.info("Secret expired: %s", secret_id)
            del self._secrets[secret_id]
            return None

        metadata.last_accessed = time.time()
        return value

    def get_secret_by_name(self, owner_id: str, name: str) -> Optional[str]:
        """Retrieve a secret by owner and name."""
        secret_id = self._generate_id(owner_id, name)
        return self.get_secret(secret_id, owner_id)

    def list_secrets(self, owner_id: str) -> list[SecretMetadata]:
        """List secret metadata for a user (never exposes values)."""
        return [
            meta
            for meta, _ in self._secrets.values()
            if meta.owner_id == owner_id and not meta.is_expired
        ]

    def delete_secret(self, secret_id: str, owner_id: str) -> bool:
        """Delete a secret. Only the owner can delete."""
        entry = self._secrets.get(secret_id)
        if entry is None:
            return False

        metadata, _ = entry
        if metadata.owner_id != owner_id:
            logger.warning("Unauthorized delete attempt on secret %s", secret_id)
            return False

        del self._secrets[secret_id]
        logger.info("Secret deleted: %s", secret_id)
        return True

    def inject_credentials(self, user_id: str, tool_name: str) -> dict[str, str]:
        """Get credentials needed for a tool call.

        This is the main integration point with the gateway. The gateway
        calls this before executing a tool to get the required credentials.

        Args:
            user_id: The authenticated user.
            tool_name: The tool being called.

        Returns:
            Dict of credential key-value pairs for the tool.
        """
        # MVP: Look for secrets tagged with the tool name
        credentials = {}
        for meta, value in self._secrets.values():
            if meta.owner_id != user_id or meta.is_expired:
                continue
            if meta.tags.get("tool") == tool_name or meta.tags.get("tool") == "*":
                credentials[meta.name] = value

        return credentials


# Singleton
vault = VaultClient()
