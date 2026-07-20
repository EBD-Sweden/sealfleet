# Changelog

All notable changes to Sealfleet are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.3] — Product-neutral public kit (scrub internal project names)

### Fixed
- Removed all internal EBD project names from the public platform kit: the CLI
  cross-project guard is now env-driven (`MCPFINDER_CROSS_PROJECT_MARKERS`, empty
  default) with generic messages; the agents-page subtitle, docs/test-console
  example names, and the k8s anti-bleed guard list are product-neutral; dropped a
  stale `.gitignore` entry. No functional change; the public repo no longer
  references any internal service.

## [0.5.2] — Security hardening (hardcoded-value audit)

### Fixed
- Quickstart no longer ships a hardcoded, delegation-capable dev API key — it's
  generated per-deployment by the keygen service. Scrubbed an `investdb` business
  string from a default DSN and env-drove the deploy registry prefixes.

## [0.5.1] — Portal landing page

### Added
- Public **product landing page** at the portal root for logged-out visitors —
  hero + "Deploy your way" (managed cloud · self-host · BYOF AWS · BYOF GCP) +
  enterprise/open-core + GitHub/docs/signup CTAs. Authenticated users still get
  the dashboard.

### Fixed
- AppShell now treats `/signup` as public — logged-out visitors were being
  bounced to `/login` before they could reach the signup page.

## [0.5.0] — Billing plans + metered usage

### Added
- **Multiple hosted plans** so customers can self-serve start any way: Monthly,
  Annual, and **Usage-only** (metered). `/billing` shows the configured plans;
  `POST /api/billing/checkout {"plan":...}` starts the right Checkout.
- **Metered usage billing, wired end-to-end**: `POST /api/billing/report-usage`
  (cron, secret-gated) aggregates each metered tenant's `api_key_usage_log`
  since a per-tenant watermark and pushes a Stripe **meter event**
  (`sealfleet_api_calls`), billing each call once. Hosted Terraform creates a
  **Cloud Scheduler** job to drive it.
- `scripts/stripe-setup.py`: idempotent creation of the full Stripe catalog
  (Hosted monthly/annual/usage/overage + Self-Hosted License annual/monthly +
  the usage meter).
- Hosted Terraform vars for the plan price IDs, meter name, and a generated
  `BILLING_CRON_SECRET`. Migration `015_usage_watermark.sql`.

## [0.4.0] — Self-serve signup + Stripe billing (hosted)

### Added
- **Self-serve signup** (`/signup`, `POST /api/signup`): creates a tenant +
  admin user + first API key in one transaction — no admin needed. Turn off
  with `DISABLE_SELF_SIGNUP=true`.
- **Stripe billing** for the hosted service (`docs/BILLING.md`): Checkout,
  webhook (signature-verified, no SDK — `fetch` + Node `crypto`), Billing
  Portal, and a `/billing` page. A tenant's access is enforced by tying its API
  keys' `is_active` to Stripe subscription status.
- **Usage metering feed**: migration `014_billing.sql` finally creates the
  `api_key_usage_log` table (the router already wrote to it) plus
  `api_keys.request_count/last_used_at` and a `subscriptions` table.
- Bundled the production Sealfleet license **public** key so released images
  verify Enterprise license tokens out of the box.

### Fixed
- Router usage logging + per-key counters silently no-op'd because no migration
  created their table/columns; `014_billing.sql` adds them.

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

[Unreleased]: https://github.com/EBD-Sweden/sealfleet/compare/v0.5.3...HEAD
[0.5.3]: https://github.com/EBD-Sweden/sealfleet/releases/tag/v0.5.3
[0.5.2]: https://github.com/EBD-Sweden/sealfleet/releases/tag/v0.5.2
[0.5.1]: https://github.com/EBD-Sweden/sealfleet/releases/tag/v0.5.1
[0.5.0]: https://github.com/EBD-Sweden/sealfleet/releases/tag/v0.5.0
[0.4.0]: https://github.com/EBD-Sweden/sealfleet/releases/tag/v0.4.0
[0.3.0]: https://github.com/EBD-Sweden/sealfleet/releases/tag/v0.3.0
[0.2.1]: https://github.com/EBD-Sweden/sealfleet/releases/tag/v0.2.1
[0.2.0]: https://github.com/EBD-Sweden/sealfleet/releases/tag/v0.2.0
[0.1.0]: https://github.com/EBD-Sweden/sealfleet/releases/tag/v0.1.0
