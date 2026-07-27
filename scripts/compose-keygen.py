#!/usr/bin/env python3
"""Generate the persistent local-dev auth material used by docker-compose.yml.

The Compose quickstart shares these files between containers that run as different
non-root users (runtime appuser uid 10001 and portal node uid 1000). The files are
therefore readable by those containers, but the volume is mounted only into the
local dev stack and read-only for consumers.
"""

from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

KEY_DIR = Path(os.getenv("MCPFINDER_KEY_DIR", "/keys"))
KEY_MODE = 0o644


def _write_once(path: Path, data: bytes) -> None:
    if not path.exists():
        path.write_bytes(data)
    path.chmod(KEY_MODE)


def _gen_rsa(path: Path) -> None:
    if path.exists():
        path.chmod(KEY_MODE)
        return
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    _write_once(path, pem)


def _gen_secret(path: Path) -> None:
    if path.exists():
        path.chmod(KEY_MODE)
        return
    secret = base64.urlsafe_b64encode(secrets.token_bytes(32))
    _write_once(path, secret)


def main() -> None:
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    _gen_rsa(KEY_DIR / "portal-rs256.pem")
    _gen_rsa(KEY_DIR / "router-rs256.pem")
    _gen_secret(KEY_DIR / "nextauth-secret")
    _gen_secret(KEY_DIR / "encryption-key")
    _gen_secret(KEY_DIR / "runtime-api-key")
    print(f"auth keys ready in {KEY_DIR}")


if __name__ == "__main__":
    main()
