"""Crypto transaction signing service (stub).

Handles signing of blockchain transactions without exposing private keys
to the LLM or any upstream component. Keys are managed in the vault and
signing happens entirely within the broker boundary.

MVP: Simulated signing. Later: HSM-backed signing, multi-sig support.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from broker.vault import vault

logger = logging.getLogger("mcpfinder.broker.signer")


@dataclass
class SignedTransaction:
    """A signed transaction ready for broadcast."""
    tx_id: str
    chain: str
    user_id: str
    transaction: dict[str, Any]
    signature: str
    signed_at: float
    status: str = "signed"  # signed, broadcast, confirmed, failed

    def to_dict(self) -> dict:
        return {
            "tx_id": self.tx_id,
            "chain": self.chain,
            "user_id": self.user_id,
            "transaction": self.transaction,
            "signature": self.signature,
            "signed_at": self.signed_at,
            "status": self.status,
        }


class TransactionSigner:
    """Signs crypto transactions using keys from the vault.

    The signer never exposes private keys. It retrieves them from
    the vault, signs the transaction, and returns only the signature.
    """

    def __init__(self):
        self._tx_log: list[SignedTransaction] = []

    def sign_transaction(
        self,
        user_id: str,
        chain: str,
        transaction: dict[str, Any],
    ) -> SignedTransaction:
        """Sign a transaction.

        Args:
            user_id: The user initiating the transaction.
            chain: Blockchain identifier (e.g., "ethereum", "bitcoin").
            transaction: The transaction data to sign.

        Returns:
            SignedTransaction with the signature.

        Raises:
            ValueError: If signing key not found for user/chain.
        """
        # Look up the signing key from vault
        key_name = f"{chain}-signing-key"
        signing_key = vault.get_secret_by_name(user_id, key_name)

        if signing_key is None:
            # MVP: Use a simulated key if none stored
            logger.warning(
                "No signing key found for user=%s chain=%s, using simulated signing",
                user_id,
                chain,
            )
            signing_key = f"simulated-key-{user_id}-{chain}"

        # Simulate transaction signing
        tx_data = json.dumps(transaction, sort_keys=True)
        signature = hashlib.sha256(
            f"{signing_key}:{tx_data}".encode()
        ).hexdigest()

        tx_id = hashlib.sha256(
            f"{user_id}:{chain}:{tx_data}:{time.time()}".encode()
        ).hexdigest()[:16]

        signed_tx = SignedTransaction(
            tx_id=tx_id,
            chain=chain,
            user_id=user_id,
            transaction=transaction,
            signature=signature,
            signed_at=time.time(),
        )

        self._tx_log.append(signed_tx)
        logger.info(
            "Transaction signed: tx_id=%s chain=%s user=%s",
            tx_id,
            chain,
            user_id,
        )
        return signed_tx

    def get_transaction(self, tx_id: str) -> Optional[SignedTransaction]:
        """Look up a signed transaction by ID."""
        for tx in self._tx_log:
            if tx.tx_id == tx_id:
                return tx
        return None

    def get_user_transactions(
        self,
        user_id: str,
        limit: int = 50,
    ) -> list[SignedTransaction]:
        """Get recent transactions for a user."""
        user_txs = [tx for tx in self._tx_log if tx.user_id == user_id]
        return user_txs[-limit:]


# Singleton
signer = TransactionSigner()
