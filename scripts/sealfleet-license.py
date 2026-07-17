#!/usr/bin/env python3
"""Sealfleet license issuer (SELLER-side tool — keep the private key secret).

Generate the Ed25519 signing keypair once, publish the public key (bake it into
the app image / set SEALFLEET_LICENSE_PUBKEY), and mint signed license keys for
enterprise customers. Customers set the key as SEALFLEET_LICENSE_KEY.

    # one-time: create the issuer keypair
    python scripts/sealfleet-license.py keygen --out-dir ./license-keys
    # -> license-keys/private.key (KEEP SECRET), license-keys/public.b64 (bundle)

    # mint a 1-year enterprise license for a customer
    python scripts/sealfleet-license.py issue \
        --private ./license-keys/private.key \
        --customer "ACME Corp" --seats 50 --days 365

    # a feature-metered key (SSO only)
    python scripts/sealfleet-license.py issue --private ./license-keys/private.key \
        --customer "SSO Only Inc" --features sso --days 365
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def cmd_keygen(args: argparse.Namespace) -> int:
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    priv = Ed25519PrivateKey.generate()
    priv_raw = priv.private_bytes_raw()
    pub_b64 = base64.b64encode(priv.public_key().public_bytes_raw()).decode()
    (out / "private.key").write_text(base64.b64encode(priv_raw).decode() + "\n")
    (out / "private.key").chmod(0o600)
    (out / "public.b64").write_text(pub_b64 + "\n")
    print(f"private key -> {out/'private.key'}  (KEEP SECRET — never commit)")
    print(f"public key  -> {out/'public.b64'}")
    print("\nBundle the public key into the app:")
    print(f"  SEALFLEET_LICENSE_PUBKEY={pub_b64}")
    return 0


def cmd_issue(args: argparse.Namespace) -> int:
    priv_raw = base64.b64decode(Path(args.private).read_text().strip())
    priv = Ed25519PrivateKey.from_private_bytes(priv_raw)
    now = int(time.time())
    payload = {
        "customer": args.customer,
        "tier": "enterprise",
        "seats": args.seats,
        "iat": now,
        "exp": now + args.days * 86400 if args.days else 0,
    }
    if args.features:
        payload["features"] = args.features
    body = json.dumps(payload, separators=(",", ":")).encode()
    token = f"{_b64url(body)}.{_b64url(priv.sign(body))}"
    print(token)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Sealfleet license issuer")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("keygen", help="generate the issuer keypair")
    g.add_argument("--out-dir", default="./license-keys")
    g.set_defaults(func=cmd_keygen)

    i = sub.add_parser("issue", help="mint a signed enterprise license key")
    i.add_argument("--private", required=True, help="path to the base64 private key")
    i.add_argument("--customer", required=True)
    i.add_argument("--seats", type=int, default=0, help="0 = unlimited")
    i.add_argument("--days", type=int, default=365, help="0 = perpetual")
    i.add_argument("--features", nargs="*", help="limit to specific features (default: all enterprise)")
    i.set_defaults(func=cmd_issue)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
