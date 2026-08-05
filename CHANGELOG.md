# Changelog

All notable changes to Sealfleet are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.7.6] — Hide platform-only admin surfaces from customers

### Fixed
- The **Tenants** admin tab is no longer shown to customers. Every self-serve
  signup receives a tenant-scoped `admin` role, so `is_admin` was true for
  everyone and the nav offered a page whose API (`requirePlatformAdmin`) could
  only ever return 403. Sessions now carry a distinct `is_platform_admin`,
  derived once in the `jwt` callback where every provider converges, and it
  gates both the nav item and the page (`AdminGuard platformOnly`). Roles and
  Users remain available — an org owner manages their own members, whether
  invited directly or provisioned over SSO/SCIM.

## [0.7.5] — Clean image builds

### Fixed
- Hosted portal images are now built from **committed source only**. Locally
  built images 0.7.0–0.7.4 included files that are untracked in this repo,
  because Docker's build context is the directory rather than git, which put
  internal-only pages and API routes into a published image. CI-built images
  were never affected. `scripts/build-hosted-images.sh` uses `git archive`, so
  the build context is an allowlist and uncommitted content cannot ship.

## [0.7.4] — Base-image hardening + image scanning in CI

### Security
- Portal base image `node:20-alpine` → `node:22-alpine`. Node 20 is in
  maintenance and its image (last rebuilt 2026-04-15) still carried 18 fixable
  HIGH/CRITICAL findings, including the Alpine `libssl3`/`libcrypto3`
  CVE-2026-45447.
- Dropped npm from the portal runner stage. The entrypoint is `node server.js`,
  so npm is never invoked at runtime, yet its bundled dependencies (`tar`
  — including CRITICAL CVE-2026-59873 — `sigstore`, `brace-expansion`,
  `picomatch`) were the image's only remaining CVE source. The portal image now
  reports **0** fixable HIGH/CRITICAL vulnerabilities, down from 18.
- Deploy image installs only the pinned, checksum-verified docker **client**
  binary instead of piping `get.docker.com` into a shell, which installed the
  whole engine. `server.py` shells out to `docker build`/`docker push` as a
  client against an external daemon, so `dockerd`/`containerd`/`runc` were never
  executed there but did contribute their embedded Go CVEs (containerd
  CVE-2026-53488/53489, grpc GHSA-hrxh-6v49-42gf). Found by the new image scan.

### Added
- Image vulnerability scanning in the supply-chain workflow, blocking on
  CRITICAL/HIGH, for all five service images and on pull requests. The security
  workflow's trivy run is filesystem-scoped, so base-image vulnerabilities were
  previously invisible to CI: it stayed green while the published image carried
  a CRITICAL.

## [0.7.3] — Portal dependency CVE fixes

### Security
- Resolved three HIGH advisories in portal dependencies: `postcss` 8.4.31 →
  8.5.24 (GHSA-r28c-9q8g-f849 path traversal via `sourceMappingURL`
  auto-loading, CVE-2026-45623) and `sharp` 0.34.5 → 0.35.3 (inherited libvips
  CVE-2026-33327/33328/35590/35591). Next 16.2.11 → 16.2.12 alone does not fix
  either — it still pins `postcss` to exactly 8.4.31 and caps `sharp` at
  `^0.34.5` — so `overrides` force the patched transitive versions.

## [0.7.2] — Public pricing page

### Added
- **Public `/pricing` page**: plans are visible without an account, so prospects
  can compare before signing up. Amounts are read live from Stripe server-side
  (`planDisplays()` in `portal/src/lib/stripe.ts`) with a 10-minute in-memory
  cache, and fall back to plan blurbs if Stripe is unreachable. `/pricing` is
  whitelisted in the portal route policy; checkout still requires an account, so
  every CTA routes to `/signup`.

### Fixed
- The pricing page is rendered per request (`dynamic = "force-dynamic"`). A
  static build-time prerender saw no Stripe configuration and baked an empty
  "plans being finalized" state into the image.

## [0.7.0] — AWS Marketplace self-host license fulfillment

### Changed
- **AWS Marketplace self-host license flow**: fulfillment now records buyer
  contact details, confirms entitlement, and alerts the operator to issue/revoke
  an offline-signed license key instead of creating a hosted tenant or toggling
  tenant API keys. Migration `017_aws_marketplace_selfhost.sql`; optional AWS
  App Runner Terraform lives under `deploy/terraform/hosted-aws/` only for the
  "Deployed on AWS" badge path.

## [0.6.0] — AWS Marketplace SaaS fulfillment

### Added
- **AWS Marketplace fulfillment**: buyers who subscribe on AWS Marketplace land on
  `/api/aws-marketplace` (ResolveCustomer), register at `/aws-marketplace` (creates
  + links a tenant, checks GetEntitlements), and an SNS handler
  (`/api/aws-marketplace/notification`, signature-verified) activates/deactivates
  the tenant on subscription changes. Migration `016_aws_marketplace.sql`;
  `portal/src/lib/aws-marketplace.ts`. Config via `SEALFLEET_AWS_MARKETPLACE_PRODUCT_CODE`
  + AWS creds; hosted Terraform wires them to Secret Manager.

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
  generated per-deployment by the keygen service. Scrubbed a business-specific
  database name from a default DSN and env-drove the deploy registry prefixes.

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

[Unreleased]: https://github.com/EBD-Sweden/sealfleet/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/EBD-Sweden/sealfleet/releases/tag/v0.6.0
[0.5.3]: https://github.com/EBD-Sweden/sealfleet/releases/tag/v0.5.3
[0.5.2]: https://github.com/EBD-Sweden/sealfleet/releases/tag/v0.5.2
[0.5.1]: https://github.com/EBD-Sweden/sealfleet/releases/tag/v0.5.1
[0.5.0]: https://github.com/EBD-Sweden/sealfleet/releases/tag/v0.5.0
[0.4.0]: https://github.com/EBD-Sweden/sealfleet/releases/tag/v0.4.0
[0.3.0]: https://github.com/EBD-Sweden/sealfleet/releases/tag/v0.3.0
[0.2.1]: https://github.com/EBD-Sweden/sealfleet/releases/tag/v0.2.1
[0.2.0]: https://github.com/EBD-Sweden/sealfleet/releases/tag/v0.2.0
[0.1.0]: https://github.com/EBD-Sweden/sealfleet/releases/tag/v0.1.0
