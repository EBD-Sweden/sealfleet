"""Sealfleet licensing / entitlement resolver (open-core model).

The core platform is free (Apache-2.0). Enterprise features — SSO/OIDC/IdP
group mapping, multi-user / multi-tenant, SCIM, advanced RBAC, audit export —
are present in the code but stay locked until an entitlement unlocks them.

Entitlement sources, first match wins:
  1. SEALFLEET_LICENSE_KEY  — an Ed25519-signed offline license token (direct
     sales / self-serve). Verified against a bundled public key; no phone-home.
  2. AWS Marketplace        — an active subscription resolved via the AWS
     License Manager / Marketplace Metering entitlement API (adapter below).
  3. Default                — the free tier (single user, local login).

Everything here is import-safe with no hard dependency on AWS; the marketplace
adapter is only invoked when explicitly enabled.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field

log = logging.getLogger("sealfleet.licensing")

# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------
# Free tier gets the empty set (core platform only). Enterprise unlocks these.
FEATURE_SSO = "sso"                     # OIDC/SAML login + IdP group->role mapping
FEATURE_MULTI_USER = "multi_user"      # more than one active user
FEATURE_MULTI_TENANT = "multi_tenant"  # more than one tenant
FEATURE_SCIM = "scim"                  # SCIM user/group provisioning
FEATURE_RBAC_ADVANCED = "rbac_advanced"  # per-tool grants, manifest access gates
FEATURE_AUDIT_EXPORT = "audit_export"  # DSAR export + long audit retention

ENTERPRISE_FEATURES = frozenset({
    FEATURE_SSO, FEATURE_MULTI_USER, FEATURE_MULTI_TENANT,
    FEATURE_SCIM, FEATURE_RBAC_ADVANCED, FEATURE_AUDIT_EXPORT,
})

TIER_FREE = "free"
TIER_ENTERPRISE = "enterprise"

# Free tier is capped to a single user (the bootstrap admin).
FREE_TIER_SEATS = 1

# Bundled Sealfleet license public key (Ed25519, base64). The matching private
# key is held by the Sealfleet license issuer and NEVER ships. Overridable via
# SEALFLEET_LICENSE_PUBKEY for self-hosted issuing / testing.
_DEFAULT_LICENSE_PUBKEY_B64 = os.environ.get("SEALFLEET_LICENSE_PUBKEY", "")


@dataclass(frozen=True)
class Entitlement:
    tier: str = TIER_FREE
    features: frozenset = field(default_factory=frozenset)
    seats: int = FREE_TIER_SEATS
    customer: str = ""
    expires_at: int = 0          # unix seconds; 0 = perpetual
    source: str = "default"      # license_key | aws_marketplace | default
    reason: str = ""             # why free / why a key was rejected
    license_id: str = ""         # license jti (for revocation), if present
    signing_kid: str = ""        # id of the key that signed this license

    def has(self, feature: str) -> bool:
        return feature in self.features

    @property
    def is_enterprise(self) -> bool:
        return self.tier == TIER_ENTERPRISE

    def to_public_dict(self) -> dict:
        return {
            "tier": self.tier,
            "features": sorted(self.features),
            "seats": self.seats,
            "customer": self.customer,
            "expires_at": self.expires_at,
            "source": self.source,
        }


def _free(reason: str = "") -> Entitlement:
    return Entitlement(tier=TIER_FREE, features=frozenset(), seats=FREE_TIER_SEATS,
                       source="default", reason=reason)


# ---------------------------------------------------------------------------
# Signed license key (Ed25519)
# ---------------------------------------------------------------------------
# Token format: base64url(payload_json) + "." + base64url(signature)
# payload = {"customer","tier","features":[...],"seats",int,"iat","exp"}

def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def kid_for_pubkey(pubkey_b64: str) -> str:
    """Deterministic key id = first 12 hex of sha256(raw public key).

    Both the issuer and the verifier derive the same id from the public key, so
    tokens can name their signing key (`kid`) without managing id strings.
    """
    return hashlib.sha256(base64.b64decode(pubkey_b64)).hexdigest()[:12]


def _public_keyring(explicit_b64: str | None = None) -> dict:
    """Return {kid: Ed25519PublicKey} the verifier will accept.

    A ring lets you rotate: keep the old public key alongside the new one so
    licenses signed by either still verify until the old ones expire. Sources
    (all optional, additive):
      * explicit_b64            — a single key (test/override); ring = just it.
      * SEALFLEET_LICENSE_PUBKEYS — JSON list/object of base64 public keys.
      * SEALFLEET_LICENSE_PUBKEY / bundled default — the single current key.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    b64_keys: list[str] = []
    if explicit_b64:
        b64_keys.append(explicit_b64)
    else:
        raw_ring = os.environ.get("SEALFLEET_LICENSE_PUBKEYS")
        if raw_ring:
            try:
                parsed = json.loads(raw_ring)
                if isinstance(parsed, dict):
                    b64_keys.extend(str(v) for v in parsed.values())
                elif isinstance(parsed, list):
                    b64_keys.extend(str(v) for v in parsed)
            except Exception:
                log.warning("SEALFLEET_LICENSE_PUBKEYS is set but not valid JSON")
        single = _DEFAULT_LICENSE_PUBKEY_B64 or os.environ.get("SEALFLEET_LICENSE_PUBKEY", "")
        if single:
            b64_keys.append(single)

    ring: dict = {}
    for b64 in b64_keys:
        try:
            raw = base64.b64decode(b64)
            ring[hashlib.sha256(raw).hexdigest()[:12]] = Ed25519PublicKey.from_public_bytes(raw)
        except Exception:
            continue
    return ring


def _load_revoked_ids() -> set:
    """License ids to reject even with a valid signature (compromise / refunds).

    Sources: SEALFLEET_LICENSE_REVOKED (comma list or JSON array) and an optional
    SEALFLEET_LICENSE_REVOCATION_FILE (JSON array or {"revoked": [...]}).
    """
    ids: set = set()
    raw = os.environ.get("SEALFLEET_LICENSE_REVOKED", "")
    if raw:
        try:
            parsed = json.loads(raw)
            ids |= {str(x) for x in (parsed if isinstance(parsed, list) else [])}
            if not isinstance(parsed, list):
                raise ValueError
        except Exception:
            ids |= {x.strip() for x in raw.split(",") if x.strip()}
    path = os.environ.get("SEALFLEET_LICENSE_REVOCATION_FILE")
    if path:
        try:
            data = json.loads(open(path).read())
            src = data if isinstance(data, list) else data.get("revoked", [])
            ids |= {str(x) for x in src}
        except Exception:
            log.warning("could not read SEALFLEET_LICENSE_REVOCATION_FILE=%s", path)
    return ids


def verify_license_key(token: str, *, pubkey_b64: str | None = None,
                       now: int | None = None) -> Entitlement:
    """Verify a signed license token and return the resulting Entitlement.

    Any failure (bad signature, expired, malformed, no public key) falls back to
    the free tier with a reason — it never raises, so a bad key degrades to free
    rather than breaking the platform.
    """
    now = int(now if now is not None else time.time())
    if not token:
        return _free("no license key")
    ring = _public_keyring(pubkey_b64)
    if not ring:
        return _free("no license public key configured")
    try:
        payload_b64, sig_b64 = token.strip().split(".", 1)
        payload_bytes = _b64url_decode(payload_b64)
        signature = _b64url_decode(sig_b64)
    except Exception:
        return _free("malformed license key")

    # Read the (unverified) kid to try the right key first; the signature check
    # below is what actually gates. A token with no/unknown kid tries every key.
    try:
        peek = json.loads(payload_bytes)
    except Exception:
        peek = {}
    kid = str(peek.get("kid", "")) if isinstance(peek, dict) else ""

    # Try the named key first, then the rest of the ring (rotation-safe).
    matched_kid = ""
    verified = False
    for kid_id in ([kid] if kid in ring else []) + [i for i in ring if i != kid]:
        try:
            ring[kid_id].verify(signature, payload_bytes)
            verified, matched_kid = True, kid_id
            break
        except Exception:
            continue
    if not verified:
        return _free("license signature invalid")

    try:
        payload = json.loads(payload_bytes)
    except Exception:
        return _free("license payload not JSON")

    exp = int(payload.get("exp", 0) or 0)
    if exp and now > exp:
        return _free(f"license expired at {exp}")

    lic_id = str(payload.get("id", ""))
    if lic_id and lic_id in _load_revoked_ids():
        return _free(f"license revoked ({lic_id})")

    tier = str(payload.get("tier", TIER_ENTERPRISE))
    if tier == TIER_ENTERPRISE:
        # An enterprise key grants all enterprise features unless it explicitly
        # narrows the set (feature-metered licensing).
        feats = payload.get("features")
        features = frozenset(feats) if feats else ENTERPRISE_FEATURES
        seats = int(payload.get("seats", 0) or 0) or 2**31
    else:
        features, seats = frozenset(), FREE_TIER_SEATS

    return Entitlement(
        tier=tier, features=features, seats=seats,
        customer=str(payload.get("customer", "")),
        expires_at=exp, source="license_key",
        license_id=lic_id, signing_kid=matched_kid,
    )


# ---------------------------------------------------------------------------
# AWS Marketplace entitlement adapter (opt-in)
# ---------------------------------------------------------------------------

def resolve_aws_marketplace_entitlement() -> Entitlement | None:
    """Resolve an entitlement from AWS Marketplace (contract pricing).

    Enabled by setting SEALFLEET_AWS_MARKETPLACE_PRODUCT_CODE. Uses the
    marketplace metering `get_entitlements` API; an active entitlement maps to
    the enterprise tier. Returns None when disabled or on any error (so it
    never blocks startup) — the resolver then falls through to the next source.
    """
    product_code = os.environ.get("SEALFLEET_AWS_MARKETPLACE_PRODUCT_CODE")
    if not product_code:
        return None
    try:
        import boto3  # optional dependency, only needed on the marketplace path
        client = boto3.client("marketplace-entitlement",
                              region_name=os.environ.get("AWS_REGION", "us-east-1"))
        resp = client.get_entitlements(ProductCode=product_code)
        ents = resp.get("Entitlements", [])
        now = int(time.time())
        active = [e for e in ents if not e.get("ExpirationDate")
                  or e["ExpirationDate"].timestamp() > now]
        if not active:
            return None
        seats = 0
        for e in active:
            v = e.get("Value", {})
            seats += int(v.get("IntegerValue", 0) or 0)
        return Entitlement(
            tier=TIER_ENTERPRISE, features=ENTERPRISE_FEATURES,
            seats=seats or 2**31,
            customer=active[0].get("CustomerIdentifier", ""),
            source="aws_marketplace",
        )
    except Exception as exc:  # pragma: no cover - network/optional dep
        log.warning("AWS Marketplace entitlement check failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Resolver + cache
# ---------------------------------------------------------------------------
_CACHE: dict = {"ent": None, "at": 0.0}
_CACHE_TTL = int(os.environ.get("SEALFLEET_LICENSE_CACHE_TTL", "300"))


def resolve_entitlement(*, force: bool = False) -> Entitlement:
    """Return the current entitlement, cached for _CACHE_TTL seconds."""
    now = time.time()
    if not force and _CACHE["ent"] is not None and (now - _CACHE["at"]) < _CACHE_TTL:
        return _CACHE["ent"]

    ent = verify_license_key(os.environ.get("SEALFLEET_LICENSE_KEY", ""))
    if not ent.is_enterprise:
        aws = resolve_aws_marketplace_entitlement()
        if aws is not None:
            ent = aws
    _CACHE["ent"] = ent
    _CACHE["at"] = now
    if ent.is_enterprise:
        log.info("Sealfleet licensed: %s tier=%s source=%s seats=%s",
                 ent.customer or "(unnamed)", ent.tier, ent.source, ent.seats)
    else:
        log.info("Sealfleet running on the free tier (%s)", ent.reason or "no license")
    return ent


def feature_enabled(feature: str) -> bool:
    return resolve_entitlement().has(feature)
