# Sealfleet licensing (open-core)

Sealfleet is **open-core**. The platform is free and source-available under
[Apache-2.0](LICENSE); a set of **enterprise features** ships in the same
codebase but stays locked until an **Enterprise license** unlocks it.

## What's free vs enterprise

| Capability | Free (Community) | Enterprise |
|---|---|---|
| MCP tools, typed/named/v2 pipelines, jobs, scale-to-zero | ✅ | ✅ |
| Portal, test console, deploy UI, pipeline visualization | ✅ | ✅ |
| Sealed inputs / credential brokering (LLM never sees secrets) | ✅ | ✅ |
| Tamper-evident audit log | ✅ | ✅ |
| Local login | ✅ (single user) | ✅ |
| **SSO / OIDC / SAML + IdP group→role mapping** | — | ✅ |
| **Multiple users / multiple tenants** | — | ✅ |
| **SCIM user/group provisioning** | — | ✅ |
| **Advanced RBAC** (per-tool grants, manifest access gates) | — | ✅ |
| **Audit export / long retention (DSAR)** | — | ✅ |

Feature flags: `sso`, `multi_user`, `multi_tenant`, `scim`, `rbac_advanced`,
`audit_export`. The current entitlement is always visible at `GET /license`.

## Applying a license (customer)

Set the key as an environment variable on the runtime + portal:

```bash
SEALFLEET_LICENSE_KEY=<your-signed-license-key>
```

Helm:

```bash
helm upgrade --install sealfleet deploy/helm/sealfleet \
  --set-string licensing.licenseKey=<your-signed-license-key>
```

Or subscribe on **AWS Marketplace** — set `SEALFLEET_AWS_MARKETPLACE_PRODUCT_CODE`
and the platform resolves your entitlement automatically (no key to paste).

Verify it took effect:

```bash
curl -s http://<router>/license
# {"tier":"enterprise","features":["sso","multi_user",...],"seats":50,...}
```

A missing, expired, or tampered key **degrades to the free tier** — it never
breaks the platform.

## Issuing licenses (seller)

`scripts/sealfleet-license.py` mints Ed25519-signed keys. Do this once:

```bash
# 1. create the issuer keypair (keep private.key SECRET; never commit it)
python scripts/sealfleet-license.py keygen --out-dir ./license-keys

# 2. bundle the public key into the app image / deployment
SEALFLEET_LICENSE_PUBKEY=<contents of license-keys/public.b64>
```

Then mint per customer:

```bash
# 1-year, 50-seat enterprise license
python scripts/sealfleet-license.py issue \
  --private ./license-keys/private.key \
  --customer "ACME Corp" --seats 50 --days 365

# feature-metered (SSO only)
python scripts/sealfleet-license.py issue --private ./license-keys/private.key \
  --customer "SSO Only Inc" --features sso --days 365
```

Keys are **offline-verifiable** — the platform checks the signature against the
bundled public key with no phone-home.

## AWS Marketplace

Sealfleet is deployed to the customer's own cluster (bring-your-own-cloud), so
the marketplace fit is a **Container product** with **Helm delivery** and
**contract pricing** (annual corporate license, optionally per-seat). AWS issues
the buyer an entitlement; Sealfleet reads it via the AWS License Manager /
Marketplace Metering entitlement API and unlocks the enterprise tier. See
[docs/DEPLOY.md](docs/DEPLOY.md) for deployment and
`SEALFLEET_AWS_MARKETPLACE_PRODUCT_CODE` for the wiring.

## Key rotation & revocation (seller)

The verifier accepts a **ring** of public keys, so you can rotate the signing
key without invalidating licenses already in the field.

**Routine rotation:**
1. `keygen` a new keypair (it prints the new key's `kid`).
2. Deploy both keys in the ring so old *and* new licenses verify:
   ```
   SEALFLEET_LICENSE_PUBKEYS=["<old-pubkey-b64>","<new-pubkey-b64>"]
   ```
3. Sign all new licenses with the new private key.
4. Once every license signed by the old key has expired, drop the old key from
   the ring.

**Revoking a single license** (e.g. refund, or a leaked key) — no rotation
needed. Every issued key has an `id` (printed by `issue`); blocklist it:
```
SEALFLEET_LICENSE_REVOKED=lic-abc123,lic-def456      # comma list or JSON array
# or a file: SEALFLEET_LICENSE_REVOCATION_FILE=/etc/sealfleet/revoked.json
```
A revoked license immediately degrades to the free tier.

**If the private key is compromised:** add a new key to the ring, re-issue every
legitimate customer a key signed with the new key, then remove the compromised
key from the ring in the next release — the attacker's minted keys die, your
customers' (already re-issued) keep working.

## Terms

- The Sealfleet platform is licensed under **Apache-2.0** — use, modify, and
  self-host freely.
- The **enterprise features** listed above require a paid **Sealfleet Enterprise
  license** for production use; the license key / marketplace entitlement is how
  that entitlement is granted and verified.
- For enterprise licensing and pricing, contact sales.

> The commercial terms for enterprise features are provided under a separate
> agreement; this document describes the model, not the contract.
