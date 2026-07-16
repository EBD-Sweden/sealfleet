# Risk Assessment (TEMPLATE)

**Version:** 0.1 DRAFT · **Owner:** _<security lead>_ · **Review:** annual + on major change

## Methodology
Identify assets/threats → rate **likelihood × impact** (1–5 each → score 1–25) → assign treatment
(mitigate / accept / transfer / avoid) → owner + due date → re-rate residual. Review annually and after
significant changes.

## Risk register (seed — extend)
| ID | Risk | Likelihood | Impact | Score | Treatment | Owner | Status |
|---|---|---|---|---|---|---|---|
| R1 | Plaintext internal service traffic intercepted | 3 | 4 | 12 | Mitigate: internal mTLS (`k8s/tls/MTLS_RUNBOOK.md`) | | Open |
| R2 | Credential/secret leak | 2 | 5 | 10 | Mitigate: encryption+rotation, gitleaks, sealed inputs | | Mitigating |
| R3 | Audit log tampering | 2 | 4 | 8 | Mitigate: hash chain + append-only trigger (`/audit/verify`) | | Mitigated |
| R4 | Data loss / no tested restore | 2 | 5 | 10 | Mitigate: encrypted backups + scheduled restore test | | Partial |
| R5 | Vulnerable dependency / supply chain | 3 | 4 | 12 | Mitigate: pip/cargo-audit, trivy, SBOM, signed images | | Mitigating |
| R6 | Over-privileged access / stale grants | 3 | 3 | 9 | Mitigate: least-privilege + quarterly access review | | Partial |
| R7 | Unmanaged subprocessor risk | 2 | 4 | 8 | Mitigate: vendor management program | | Open |
| R8 | No incident process → slow/uncoordinated response | 2 | 4 | 8 | Mitigate: IR plan + tabletop drills | | Open |
| R9 | DoS / resource exhaustion | 3 | 3 | 9 | Mitigate: rate limits/quotas (expand coverage) | | Partial |
| R10 | Tenant isolation failure | 1 | 5 | 5 | Mitigate: tenant-scoping + tests | | Mitigating |

Re-score residual risk after each treatment; track open items to closure.
