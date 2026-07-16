"""
Sealfleet Credential Resolution Engine

Three deployment modes:
  1. K8s-native: credentials live in k8s Secrets, never in Sealfleet DB
  2. BYOK: user-provided key, Sealfleet encrypts/decrypts but never holds plaintext key
  3. Sealfleet-managed: AES-256-GCM with platform KMS key (default/cloud tier)

The LLM NEVER sees plaintext credentials. Tokens like {{credential:db_password}}
are resolved at MCP call time by this module.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
from typing import Optional

log = logging.getLogger("mcpfinder.credentials")

# ---------------------------------------------------------------------------
# Token pattern: {{credential:name}} or {{cred:name}}
# ---------------------------------------------------------------------------
CRED_TOKEN_RE = re.compile(r'\{\{(?:credential|cred):([a-zA-Z0-9_\-\.]+)\}\}')


# ---------------------------------------------------------------------------
# Mode 1: K8s Secrets reader
# ---------------------------------------------------------------------------

def read_k8s_secret(secret_name: str, key: str, namespace: str = "default") -> Optional[str]:
    """
    Read a value from a k8s Secret.
    Works inside a pod (uses service account token) or with kubeconfig outside.
    """
    try:
        from kubernetes import client, config
        try:
            config.load_incluster_config()
        except Exception:
            config.load_kube_config()
        v1 = client.CoreV1Api()
        secret = v1.read_namespaced_secret(name=secret_name, namespace=namespace)
        if secret.data and key in secret.data:
            return base64.b64decode(secret.data[key]).decode("utf-8")
        log.warning("K8s secret %s/%s key %s not found", namespace, secret_name, key)
        return None
    except ImportError:
        log.warning("kubernetes package not installed — K8s secrets mode unavailable")
        return None
    except Exception as e:
        log.error("K8s secret read error: %s", e)
        return None


def _sanitize_tenant_for_secret(tenant_id: str) -> str:
    """RFC1123-safe suffix for a per-tenant k8s Secret name."""
    s = re.sub(r"[^a-z0-9-]", "-", str(tenant_id).lower()).strip("-")
    return s[:200] or "unknown"


def read_k8s_secret_by_cred_name(cred_name: str, tenant_id: str) -> Optional[str]:
    """
    Resolve a credential name to a per-tenant k8s Secret.

    Tenant isolation: each tenant's credentials live in a dedicated Secret named
    "<base>-<tenant>" (base from MCPFINDER_K8S_SECRET_NAME, default
    "mcpfinder-creds"), so tenant B can never read tenant A's Secret by guessing
    a credential name. `tenant_id` is required.
    """
    if not tenant_id:
        log.warning("k8s credential read refused: no tenant_id")
        return None
    base = os.environ.get("MCPFINDER_K8S_SECRET_NAME", "mcpfinder-creds")
    secret_name = f"{base}-{_sanitize_tenant_for_secret(tenant_id)}"
    namespace = os.environ.get("MCPFINDER_K8S_NAMESPACE", "default")
    return read_k8s_secret(secret_name, cred_name, namespace)


# ---------------------------------------------------------------------------
# Mode 2: BYOK (Bring Your Own Key)
# ---------------------------------------------------------------------------

def get_byok_fernet(user_key: Optional[str] = None):
    """
    Get Fernet instance using the user-provided key.
    Key can come from: explicit arg, env BYOK_KEY, or a k8s secret.
    """
    from cryptography.fernet import Fernet
    key = user_key or os.environ.get("BYOK_KEY") or os.environ.get("MCPFINDER_BYOK_KEY")
    if not key:
        return None
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as e:
        log.error("BYOK key invalid: %s", e)
        return None


def encrypt_byok(plaintext: str, user_key: str) -> str:
    """Encrypt a credential value with the user's own key."""
    f = get_byok_fernet(user_key)
    if not f:
        raise ValueError("Invalid BYOK key")
    return f.encrypt(plaintext.encode()).decode()


def decrypt_byok(ciphertext: str, user_key: str) -> str:
    """Decrypt a BYOK-encrypted credential."""
    f = get_byok_fernet(user_key)
    if not f:
        raise ValueError("Invalid BYOK key")
    return f.decrypt(ciphertext.encode()).decode()


# ---------------------------------------------------------------------------
# Mode 3: Sealfleet-managed (AES-256 via Fernet + KMS key from k8s Secret)
# ---------------------------------------------------------------------------

def _is_production_like_env() -> bool:
    """True when any deployment-env signal looks production/public-facing.

    Mirrors runtime.router._is_production_like_auth_env so the credential layer
    fails closed in the same environments the router treats as production.
    """
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


# Versioned-key ciphertext envelope.
#   "mcpfk1:<key_id>:<fernet_token>"
# Records which platform key encrypted the value so decryption can pick the
# matching key during/after a rotation. Legacy ciphertext (no prefix) is a bare
# Fernet token encrypted with the single env key and decrypts as key id "v1".
_PLATFORM_ENVELOPE_PREFIX = "mcpfk1"
_LEGACY_KEY_ID = "v1"


def _dev_platform_key() -> str:
    """Deterministic local/dev key — NOT for production."""
    return base64.urlsafe_b64encode(
        hashlib.sha256(b"mcpfinder-dev-key-change-in-prod").digest()
    ).decode()


def _load_platform_keyring() -> tuple[str, dict[str, str]]:
    """Resolve the platform key ring as (current_key_id, {key_id: key_material}).

    Sources (all optional, additive):
      * ENCRYPTION_KEY / MCPFINDER_ENCRYPTION_KEY — the single "current" key.
        Registered under MCPFINDER_ENCRYPTION_KEY_ID (default "v1") so the
        existing single-key env path keeps working unchanged (back-compat).
      * MCPFINDER_ENCRYPTION_KEYS — JSON object {key_id: fernet_key, ...} of
        historical/active keys for zero-downtime rotation. Any key listed here
        can decrypt; only the current key encrypts.
      * MCPFINDER_ENCRYPTION_KEY_ID — id of the current (encrypting) key. Must
        resolve to a key present in the ring.

    Fails closed in production-like environments when no key material is
    configured; in dev it falls back to the deterministic dev key as "v1".
    """
    keyring: dict[str, str] = {}

    raw_keys = os.environ.get("MCPFINDER_ENCRYPTION_KEYS")
    if raw_keys:
        try:
            parsed = json.loads(raw_keys)
            if not isinstance(parsed, dict):
                raise ValueError("must be a JSON object of {key_id: key}")
            for kid, material in parsed.items():
                if not kid or not material:
                    raise ValueError("empty key id or material")
                keyring[str(kid)] = str(material)
        except Exception as exc:
            raise RuntimeError(
                f"MCPFINDER_ENCRYPTION_KEYS is set but invalid: {exc}"
            ) from exc

    single = os.environ.get("ENCRYPTION_KEY") or os.environ.get("MCPFINDER_ENCRYPTION_KEY")
    single_id = os.environ.get("MCPFINDER_ENCRYPTION_KEY_ID") or _LEGACY_KEY_ID
    if single:
        # Don't clobber an explicit ring entry of the same id with the env single
        # key unless they actually differ; if they differ, the explicit ring wins
        # only when no current id is set. Keep it simple: env single key registers
        # under single_id, and is the default current key.
        keyring.setdefault(single_id, single)
        if keyring.get(single_id) != single and single_id not in (os.environ.get("MCPFINDER_ENCRYPTION_KEY_ID") or ""):
            # ring already had this id from JSON; prefer the JSON value.
            pass

    if not keyring:
        if _is_production_like_env():
            raise RuntimeError(
                "No platform encryption key configured in a production-like "
                "environment. Provide ENCRYPTION_KEY (k8s Secret "
                "mcpfinder-encryption) or MCPFINDER_ENCRYPTION_KEYS; refusing "
                "the dev fallback key."
            )
        log.warning(
            "No platform encryption key set — using dev key. "
            "Set ENCRYPTION_KEY via k8s Secret mcpfinder-encryption"
        )
        keyring[_LEGACY_KEY_ID] = _dev_platform_key()

    current_id = os.environ.get("MCPFINDER_ENCRYPTION_KEY_ID")
    if current_id:
        if current_id not in keyring:
            raise RuntimeError(
                f"MCPFINDER_ENCRYPTION_KEY_ID={current_id!r} is not present in "
                "the configured key ring (ENCRYPTION_KEY / MCPFINDER_ENCRYPTION_KEYS)."
            )
    elif single:
        current_id = single_id
    else:
        # Deterministic: prefer the legacy id, else the sole/first ring entry.
        current_id = _LEGACY_KEY_ID if _LEGACY_KEY_ID in keyring else sorted(keyring)[0]

    return current_id, keyring


def _fernet_for_key(key_material: str):
    from cryptography.fernet import Fernet
    return Fernet(key_material.encode() if isinstance(key_material, str) else key_material)


def get_platform_fernet():
    """Fernet instance for the *current* platform key (used for encryption).

    Back-compat: with only ENCRYPTION_KEY set, this returns a Fernet over that
    single key exactly as before. Fails closed in production-like environments
    when no key is configured.
    """
    current_id, keyring = _load_platform_keyring()
    return _fernet_for_key(keyring[current_id])


def platform_current_key_id() -> str:
    """Return the id of the key currently used for encryption."""
    current_id, _ = _load_platform_keyring()
    return current_id


def encrypt_platform(plaintext: str) -> str:
    """Encrypt with the current platform key, tagging the ciphertext with its key id."""
    current_id, keyring = _load_platform_keyring()
    token = _fernet_for_key(keyring[current_id]).encrypt(plaintext.encode()).decode()
    return f"{_PLATFORM_ENVELOPE_PREFIX}:{current_id}:{token}"


def _parse_platform_envelope(ciphertext: str) -> tuple[Optional[str], str]:
    """Split a stored ciphertext into (key_id, fernet_token).

    Returns (None, ciphertext) for legacy bare Fernet tokens (no envelope).
    """
    if ciphertext.startswith(_PLATFORM_ENVELOPE_PREFIX + ":"):
        parts = ciphertext.split(":", 2)
        if len(parts) == 3:
            return parts[1], parts[2]
    return None, ciphertext


def decrypt_platform(ciphertext: str) -> str:
    """Decrypt platform ciphertext, selecting the key that encrypted it.

    Handles both the versioned envelope ("mcpfk1:<key_id>:<token>") and legacy
    bare Fernet tokens. For the envelope, tries the tagged key first; for legacy
    tokens (or if the tagged key is missing), tries every key in the ring so an
    old ciphertext still decrypts after a rotation.
    """
    from cryptography.fernet import InvalidToken

    _current_id, keyring = _load_platform_keyring()
    key_id, token = _parse_platform_envelope(ciphertext)

    # Preferred order: the tagged key first, then everything else (newest-ish
    # first via reverse-sorted ids) so rotation never strands ciphertext.
    ordered_ids: list[str] = []
    if key_id and key_id in keyring:
        ordered_ids.append(key_id)
    for kid in sorted(keyring, reverse=True):
        if kid not in ordered_ids:
            ordered_ids.append(kid)

    last_err: Exception | None = None
    for kid in ordered_ids:
        try:
            return _fernet_for_key(keyring[kid]).decrypt(token.encode()).decode()
        except InvalidToken as exc:
            last_err = exc
            continue
    raise last_err or InvalidToken()


def reencrypt_platform(ciphertext: str) -> str:
    """Decrypt with whichever key applies, then re-encrypt under the current key.

    Used to migrate stored credentials onto a new current key after rotation.
    Returns the new envelope ciphertext.
    """
    return encrypt_platform(decrypt_platform(ciphertext))


# ---------------------------------------------------------------------------
# Unified resolver: {{credential:name}} → plaintext value
# ---------------------------------------------------------------------------

# Resolution order per credential:
#   1. Check if stored with mode tag in DB (k8s | byok | platform)
#   2. Try k8s Secret directly (always available in k8s deployments)
#   3. Fall back to platform-managed DB lookup

def resolve_credential_token(
    cred_name: str,
    db_conn=None,
    byok_key: Optional[str] = None,
    *,
    tenant_id: Optional[str] = None,
) -> Optional[str]:
    """
    Resolve a single credential name to its plaintext value, scoped to a tenant.

    SECURITY: `tenant_id` is REQUIRED. Every mode (k8s Secret, BYOK, platform)
    resolves only credentials owned by the calling tenant, so one tenant can
    never read another tenant's secret by referencing its name. A missing
    tenant_id fails closed (returns None).
    """
    if not tenant_id:
        log.warning("credential resolution refused for %s: no tenant_id", cred_name)
        return None

    # Mode 1: K8s Secret (per-tenant Secret; always try first if running in k8s)
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        val = read_k8s_secret_by_cred_name(cred_name, tenant_id)
        if val:
            log.debug("Resolved credential %s via k8s secret (tenant %s)", cred_name, tenant_id)
            return val

    # Mode 2: BYOK — if user key is in env or provided
    if byok_key or os.environ.get("BYOK_KEY"):
        f = get_byok_fernet(byok_key)
        if f and db_conn:
            import psycopg2.extras
            cur = db_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                "SELECT encrypted_value, storage_mode FROM credentials "
                "WHERE name = %s AND tenant_id = %s AND is_active = TRUE LIMIT 1",
                (cred_name, tenant_id),
            )
            row = cur.fetchone(); cur.close()
            if row and row.get("storage_mode") == "byok":
                try:
                    val = f.decrypt(row["encrypted_value"].encode()).decode()
                    log.debug("Resolved credential %s via BYOK", cred_name)
                    return val
                except Exception:
                    log.warning("BYOK decryption failed for %s", cred_name)

    # Mode 3: Platform-managed (default)
    if db_conn:
        import psycopg2.extras
        cur = db_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT encrypted_value, storage_mode FROM credentials "
            "WHERE name = %s AND tenant_id = %s AND is_active = TRUE LIMIT 1",
            (cred_name, tenant_id),
        )
        row = cur.fetchone(); cur.close()
        if row:
            try:
                val = decrypt_platform(row["encrypted_value"])
                log.debug("Resolved credential %s via platform key", cred_name)
                # Update last_used_at (tenant-scoped)
                try:
                    cur2 = db_conn.cursor()
                    cur2.execute(
                        "UPDATE credentials SET last_used_at = NOW() "
                        "WHERE name = %s AND tenant_id = %s",
                        (cred_name, tenant_id),
                    )
                    db_conn.commit(); cur2.close()
                except Exception:
                    pass
                return val
            except Exception:
                log.warning("Platform decryption failed for %s", cred_name)

    log.warning("Could not resolve credential %s for tenant %s", cred_name, tenant_id)
    return None


def resolve_credential_tokens(value, db_conn=None, byok_key: Optional[str] = None, *, tenant_id: Optional[str] = None):
    """
    Recursively resolve {{credential:X}} tokens in any value (string, dict, list).
    Called at MCP execution time — LLM context is never modified.

    SECURITY: `tenant_id` is required and scopes every credential lookup to the
    calling tenant (see resolve_credential_token).

    Input:  {"Authorization": "Bearer {{credential:stripe_key}}"}
    Output: {"Authorization": "Bearer sk-live-..."}
    """
    if isinstance(value, str):
        def replace_token(m):
            cred_name = m.group(1)
            resolved = resolve_credential_token(cred_name, db_conn, byok_key, tenant_id=tenant_id)
            if resolved is None:
                log.error("Unresolvable credential token: %s", cred_name)
                return m.group(0)  # leave token in place if unresolvable
            return resolved
        return CRED_TOKEN_RE.sub(replace_token, value)

    elif isinstance(value, dict):
        return {k: resolve_credential_tokens(v, db_conn, byok_key, tenant_id=tenant_id) for k, v in value.items()}

    elif isinstance(value, list):
        return [resolve_credential_tokens(v, db_conn, byok_key, tenant_id=tenant_id) for v in value]

    return value


def has_credential_tokens(value) -> bool:
    """Check if a value contains any {{credential:X}} tokens."""
    if isinstance(value, str):
        return bool(CRED_TOKEN_RE.search(value))
    elif isinstance(value, dict):
        return any(has_credential_tokens(v) for v in value.values())
    elif isinstance(value, list):
        return any(has_credential_tokens(v) for v in value)
    return False
