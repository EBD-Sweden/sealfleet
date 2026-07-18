# Changelog

All notable changes to Sealfleet are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] — Hosted (scale-to-zero) deployment

### Added
- **Hosted / managed-service deployment** on Cloud Run
  (`deploy/terraform/hosted-cloudrun/`): the platform runs with
  `min_instances=0` (pay ~$0 while idle), new customers are tenants in a shared
  serverless Postgres (Neon/Supabase) — no per-customer infrastructure. See
  `docs/HOSTED.md` for the architecture, economics, and growth path.
- Images are now **Cloud Run-native**: the entrypoints honor the injected
  `$PORT` (falling back to `APP_PORT`), so the same images run on Cloud Run,
  Kubernetes, and Compose unchanged.

## [0.2.1] — License key rotation & revocation

### Added
- License signing-key **rotation**: the verifier accepts a public-key *ring*
  (`SEALFLEET_LICENSE_PUBKEYS`), so a new signing key can be introduced while
  licenses signed by the old key keep verifying until they expire. Tokens carry
  a `kid` naming their signing key.
- License **revocation**: every issued key has an `id`; blocklist individual
  licenses via `SEALFLEET_LICENSE_REVOKED` (or a revocation file) without
  rotating the signing key. `scripts/sealfleet-license.py` now emits the `kid`
  and a per-license `id`. Chart: `licensing.{publicKeys,revokedIds}`.

## [0.2.0] — Open-core licensing

### Added
- **Open-core licensing / entitlements** (`runtime/licensing.py`): the platform
  is free; enterprise features (SSO/OIDC/IdP mapping, multi-user / multi-tenant,
  SCIM, advanced RBAC, audit export) unlock via an Ed25519-signed
  `SEALFLEET_LICENSE_KEY` (offline-verified) or an AWS Marketplace entitlement.
  Bad/expired/tampered keys degrade to the free tier.
- `GET /license` entitlement endpoint; SCIM endpoints return `402` when
  unlicensed; portal SSO login (`upsertSsoUser`, `/api/sso/start`) gated on the
  `sso` feature.
- `scripts/sealfleet-license.py` seller-side key issuer (keygen + mint).
- `LICENSING.md` documents the model, feature matrix, and how to buy/apply a key.

## [0.1.0] — Initial public release

First open-source release of the Sealfleet MCP Agent Platform.

### Platform
- Runtime Router (FastAPI): `/call` tool invoke, typed / named / v2 YAML
  pipelines, async jobs, manifests, channels, scale-to-zero, and the
  `mcpfinder` CLI.
- Registry (discovery), Deploy service (git → Kubernetes), Core Agent
  (LLM natural-language → pipeline execution), Portal (Next.js: catalog,
  test console, deploy UI, pipeline visualization, sealed-input flows).
- Two reference examples: the fake-data demo sandbox and the Weather Trip
  Planner (build a pipeline → visualize results).

### Security & tenancy
- Credentials never reach the LLM: `{{credential:name}}` tokens resolve at
  MCP-call time, tenant-scoped, across k8s Secret / BYOK / platform-AES modes.
- Per-tenant isolation, RBAC with IdP group→role mapping, per-tool grants,
  manifest-declared access gates and PII redaction.
- Tamper-evident audit hash chain with GDPR purpose / lawful-basis tagging,
  DSAR export, right-to-erasure, scheduled retention.
- SSRF-guarded outbound endpoints, path-traversal-safe pipeline deploy,
  authenticated deploy/registry services, CORS allowlist, non-root containers.
- Supply chain: SBOM (CycloneDX + SPDX) and keyless cosign signing per image;
  CI secret/dependency/SAST/IaC scanning.

### Deploy (bring your own cloud)
- Helm chart (`deploy/helm/sealfleet`) for any Kubernetes cluster.
- AWS Terraform (`deploy/terraform/aws`): one `terraform apply` provisions
  VPC + EKS + RDS Postgres + KMS + Secrets Manager + ALB and installs the
  chart. Validated with `terraform plan` against a live account.
- GCP Terraform (`deploy/terraform/gcp`): GKE + Cloud SQL equivalent.
- Docker Compose one-command local quickstart.

[Unreleased]: https://github.com/EBD-Sweden/sealfleet/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/EBD-Sweden/sealfleet/releases/tag/v0.3.0
[0.2.1]: https://github.com/EBD-Sweden/sealfleet/releases/tag/v0.2.1
[0.2.0]: https://github.com/EBD-Sweden/sealfleet/releases/tag/v0.2.0
[0.1.0]: https://github.com/EBD-Sweden/sealfleet/releases/tag/v0.1.0
