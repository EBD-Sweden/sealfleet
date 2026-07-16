# SOC 2 Evidence Index — Sealfleet

Maps Trust Services Criteria → implementing control (code/process) → evidence artifact → status.
Auditors test *operating effectiveness*, so "evidence" must be reproducible/collectable over the period.

Status: ✅ implemented · 🟡 partial · ❌ gap (technical) · ⬜ organizational (not code)

| TSC | Control | Implementation (code/process) | Evidence artifact | Status |
|---|---|---|---|---|
| CC6.1 | AuthN (SSO/keys) | OIDC/SAML + API keys + JWKS — `runtime/router.py`, `packages/mcpfinder-auth` | IdP config; `GET /enterprise/contract`; auth tests | ✅ |
| CC6.1 | AuthZ least-privilege | per-action gates + `api_keys.action_permissions` — `router.py:_authorize_action` | 403 tests; key permission rows | ✅ |
| CC6.1 | Encryption at rest (secrets) | AES-256/Fernet, BYOK, k8s Secret, **key rotation** (versioned ring) — `runtime/credentials.py` | `test_credentials_key_rotation.py`; rotation runbook | ✅ |
| CC6.7 | Encryption in transit — edge | Traefik Ingress + TLS Secret (cert-manager prod) — `k8s/tls/ingress.yaml` | `curl -kI https://…`; cert details | 🟡 |
| CC6.7 | Encryption in transit — app | opt-in uvicorn TLS — `runtime/entrypoint.sh` | live `curl -k https→200` capture | 🟡 |
| CC6.7 | Encryption in transit — internal mTLS | Linkerd — `k8s/tls/MTLS_RUNBOOK.md` (ready, not applied) | `linkerd viz edges` = SECURED | ❌ |
| CC6.1 | Network segmentation | NetworkPolicies — `k8s/tls/networkpolicy.yaml` | `kubectl get netpol` | 🟡 |
| CC6.2/6.3 | Provisioning/deprovisioning | SCIM lifecycle + session revocation — router SCIM routes | SCIM logs; deactivation tests | 🟡 |
| CC6.3 | Access reviews | quarterly review procedure — `ACCESS_CONTROL_AND_REVIEW.md` | signed review records | ⬜ |
| CC7.2 | Audit logging — tamper-evident | hash chain + append-only trigger + `/audit/verify` — `runtime/router.py`, `db/migrations/010` | `/audit/verify` → `intact:true`; UPDATE/DELETE rejected | ✅ |
| CC7.3–7.5 | Incident response | `INCIDENT_RESPONSE_PLAN.md` | incident tickets; tabletop log | ⬜ |
| CC7.3 | Monitoring/alerting | OTEL hooks — `observability/` (backend wiring TODO) | dashboards/alerts | 🟡 |
| CC8.1 | Change management | PR review + branch protection + CI — `CHANGE_MANAGEMENT.md`, `.github/workflows/` | PR history; required-check config | 🟡 |
| CC8.1 | SAST/deps/secret/container scan | `.github/workflows/security.yml` (bandit/pip-audit/gitleaks/trivy) | CI run logs | ✅ |
| CC8.1 | SBOM + image signing | `.github/workflows/supply-chain.yml` (syft + cosign keyless) | SBOM artifacts; cosign attestations | ✅ |
| CC9 | Vendor/subprocessor mgmt | `VENDOR_MANAGEMENT.md` | subprocessor register + reviews | ⬜ |
| CC3 | Risk assessment | `RISK_ASSESSMENT.md` | risk register | ⬜ |
| A1.2 | Backup/restore + DR | `scripts/backup-postgres.sh` + `k8s/backup-cronjob.yaml`; `BUSINESS_CONTINUITY_DR.md` | backup logs; **restore-test record** | 🟡 |
| C1 | Confidentiality | sealed inputs, tenant isolation, secret redaction | sealed-handle tests | ✅ |
| P-series | Privacy / GDPR | `/privacy/export`, `/privacy/erase`, retention — `router.py`, `DATA_RETENTION_AND_PRIVACY.md` | `test_router_privacy.py`; DSAR records | 🟡 |
| CC1/CC2 | Governance/policies | this compliance pack | ratified, dated, approved policies | ⬜ |

**Biggest remaining items:** internal mTLS (❌, runbook ready); the ⬜ organizational controls (need adoption + recurring operation + a GRC tool + auditor); restore-test evidence; monitoring/alerting backend.
