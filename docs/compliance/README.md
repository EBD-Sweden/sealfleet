# Sealfleet Compliance Pack (SOC 2 starter)

**Status: TEMPLATES / DRAFTS.** This pack jump-starts the *organizational* half of SOC 2 —
the policies, procedures, and evidence index an auditor expects. **It is not, by itself,
SOC 2 readiness.** Code controls (in the repo) are necessary but not sufficient; SOC 2 is an
attestation by a licensed CPA firm that documented controls **operate effectively over a period**.

> Honest framing: no code change can "complete" SOC 2. Reaching a report requires a named
> owner, adoption of these policies, a GRC/continuous-monitoring tool (Vanta/Drata/Secureframe),
> recurring operation of the procedures (access reviews, change mgmt, IR drills, vendor reviews),
> a penetration test, and an auditor engagement (Type I → Type II observation window).

## What's here
| File | Purpose | TSC |
|---|---|---|
| `INFORMATION_SECURITY_POLICY.md` | Top-level ISP / acceptable use / data classification | CC1, CC2 |
| `ACCESS_CONTROL_AND_REVIEW.md` | Access policy + quarterly access-review procedure & template | CC6.1–6.3 |
| `CHANGE_MANAGEMENT.md` | PR review, branch protection, CI gates, release/rollback | CC8.1 |
| `INCIDENT_RESPONSE_PLAN.md` | Severity, roles, comms, runbook, post-mortem, drill log | CC7.3–7.5 |
| `VENDOR_MANAGEMENT.md` | Subprocessor inventory + risk review cadence | CC9 |
| `DATA_RETENTION_AND_PRIVACY.md` | Retention schedule, GDPR DSAR/erasure, data map | C1, P-series |
| `RISK_ASSESSMENT.md` | Risk register + methodology | CC3 |
| `BUSINESS_CONTINUITY_DR.md` | RTO/RPO, backup/restore, DR test log | A1.2 |
| `EVIDENCE_INDEX.md` | Control → implementation → evidence artifact mapping | all |

## Readiness roadmap
1. **Assign an owner** (security/compliance lead) and a target report type (start Type I).
2. **Adopt + ratify** these policies (date, version, approver); store signed copies.
3. **Onboard a GRC tool**; connect cloud, repo, IdP, ticketing for continuous evidence.
4. **Operate the procedures** for the observation window: quarterly access reviews, change
   management on every release, IR tabletop, vendor reviews, backup-restore tests.
5. **Remediate the remaining technical P0/P1** in `../SOC2_GAP_ANALYSIS.md` (esp. internal mTLS).
6. **Pen test** + remediate.
7. **Type I readiness assessment → remediation → Type II observation (3–12 mo) → CPA exam.**

Realistic timeline to a Type II report: **6–12 months** after the program is stood up.
