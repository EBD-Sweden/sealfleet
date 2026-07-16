# Information Security Policy (TEMPLATE)

**Version:** 0.1 DRAFT · **Owner:** _<security lead>_ · **Approved by:** _<name/date>_ · **Review:** annual

## 1. Purpose & scope
Defines how Sealfleet protects the confidentiality, integrity, and availability of customer
and company data across the platform (runtime router, deploy, registry, broker, policy,
observability, portal, `packages/mcpfinder-auth`) and supporting infrastructure (k8s, Postgres, CI).

## 2. Roles
- **Security/Compliance owner:** maintains this program, evidence, and the risk register.
- **Engineers:** follow change-management, least-privilege, secure-coding.
- **Admins:** manage access grants; subject to access reviews.

## 3. Data classification
| Class | Examples | Handling |
|---|---|---|
| Restricted | credentials, sealed inputs, signing keys, customer PII | encrypted at rest + in transit; never in logs/LLM; access logged |
| Confidential | tenant config, audit logs, internal docs | least-privilege; tenant-isolated |
| Internal | source, manifests | repo access control |
| Public | published docs, OSS code | n/a |

LLM boundary (architecture principle): the model is a planner, never a secret-holder; secrets
flow via sealed handles only.

## 4. Core controls (see `EVIDENCE_INDEX.md`)
- Least-privilege RBAC/ABAC with per-action gates; fail-closed in production.
- Encryption: at rest (AES-256, key rotation), in transit (edge/app TLS; internal mTLS roadmap).
- Tamper-evident, append-only audit logging.
- Secure SDLC: peer review, CI security scanning, SBOM + signed images.
- Backups encrypted + retained; restores tested.
- Vendor risk review; incident response; annual risk assessment.

## 5. Acceptable use & secure coding
No shared accounts; MFA on all admin/IdP/CI/cloud; no secrets in code (enforced by gitleaks/trivy);
dependencies scanned (pip-audit/cargo-audit); least-privilege service credentials.

## 6. Enforcement
Violations are handled per HR/contract terms. Exceptions require documented risk acceptance by the owner.
