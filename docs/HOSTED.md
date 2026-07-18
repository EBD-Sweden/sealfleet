# Running Sealfleet as a hosted service

Sealfleet can be offered as a **managed SaaS** — customers sign up and use it,
you run the infrastructure — without buying a standing (expensive) Kubernetes
cluster before you have customers. This doc explains the cost-efficient
architecture and how it maps to what's already in the platform.

## The problem

A standing EKS/GKE cluster costs **~$150+/month, 24/7, even with zero
customers** (control plane + minimum nodes). Paying that before you have revenue
is the trap.

## The efficient answer: pooled multi-tenancy + scale-to-zero

**1. One shared deployment, many tenants.** Sealfleet is multi-tenant to the
core — every request is scoped by `tenant_id`, with per-tenant OIDC/SSO, RBAC,
IdP group mapping, tenant-scoped credentials, audit, and manifests. So a new
customer is a **tenant row**, not new infrastructure. This is "pooled" SaaS: the
most cost-efficient model.

**2. Scale-to-zero compute.** Instead of always-on nodes, run the platform on
**Cloud Run** with `min_instances=0`: it costs ~$0 while idle and only bills for
actual request time. First request after idle cold-starts in ~1–2s. See
[`deploy/terraform/hosted-cloudrun/`](../deploy/terraform/hosted-cloudrun/).

**3. Serverless database.** Use a Postgres that also scales to zero — **Neon** or
**Supabase** (both have free tiers). No idle DB cost.

Result: **0 customers ≈ $0–5/month.** Cost grows only with real usage.

| Component | Service | Idle | Active |
|---|---|---|---|
| router / portal / registry | Cloud Run (min=0) | $0 | request-time (2M req/mo free) |
| Postgres | Neon / Supabase (serverless) | $0 | tiny |

## Growth path (don't over-build)

1. **0 → first customers:** Cloud Run + Neon, pooled tenants. ~$0 idle.
2. **Traction:** set Cloud Run `min_instances=1` to kill cold starts, or move to
   **GKE Autopilot** (pay per pod, no node management) as sustained traffic makes
   always-on cheaper than per-request.
3. **Scale / enterprise:** full multi-tenant cluster, or **siloed** dedicated
   deployments (per-tenant namespace/DB) for customers who require hard
   isolation — automate with the [BYOF Terraform](DEPLOY.md) per tenant.

## Provisioning models

- **Pooled (shared)** — tenant = DB row. Cheapest; start here.
- **Siloed (dedicated)** — a dedicated instance/DB per customer, provisioned on
  signup. Higher cost/ops; reserve for enterprise customers who demand it.

## Billing

The hosted service is a **direct-billing** product (not the license-key path,
which is for self-hosted). Bill via **Stripe** subscriptions or usage, metered
off the per-tenant `api_key_usage_log` the router already writes. You can run
both revenue lines: free self-host → paid license (BYOF), *and* paid hosted.

## What's built vs. still to build

**Built:** multi-tenant isolation, per-tenant usage metering hook
(`api_key_usage_log`), Cloud Run-native images (honor `$PORT`), and the
[Cloud Run scale-to-zero module](../deploy/terraform/hosted-cloudrun/).

**To build for a full SaaS:** a self-serve **signup → create tenant** flow, and
the **Stripe** subscription + metering integration. The foundation (tenants,
usage log, deploy module) is in place.
