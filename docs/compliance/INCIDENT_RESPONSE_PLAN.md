# Incident Response Plan (TEMPLATE)

**Version:** 0.1 DRAFT · **Owner:** _<security lead>_ · **Review:** annual + after each SEV1 · **Drill:** ≥ annual tabletop

## Severity
| Sev | Definition | Example | Target response |
|---|---|---|---|
| SEV1 | Confirmed breach / data exposure / full outage | credential leak, audit-chain break, customer data exposed | immediate, 24/7 |
| SEV2 | Significant degradation / suspected breach | auth bypass suspicion, partial outage | < 1 hour |
| SEV3 | Minor, contained | single failed control, non-prod issue | next business day |

## Roles
- **Incident Commander** — coordinates, declares severity, owns the timeline.
- **Tech lead** — investigation/containment/eradication.
- **Comms** — internal + customer/regulator notifications.
- **Scribe** — maintains the timeline (evidence).

## Process
1. **Detect & report** — anyone reports to the IC channel; IC declares severity.
2. **Contain** — revoke keys/sessions (SCIM revocation), isolate workloads (NetworkPolicies), disable affected paths.
3. **Eradicate & recover** — patch, rotate keys (`reencrypt_platform`), restore from backup if needed (`scripts/backup-postgres.sh`).
4. **Forensics** — preserve audit log; run `GET /audit/verify` to prove/disprove tamper; collect logs/timeline.
5. **Notify** — per contractual/GDPR timelines (GDPR breach notification ≤ 72h where applicable).
6. **Post-mortem** — blameless, within 5 business days; track corrective actions to closure.

## Contacts
| Role | Name | Channel |
|---|---|---|
| IC | _<fill>_ | _<fill>_ |
| Security owner | | |
| Legal/Privacy | | |

## Logs (evidence)
| Date | Sev | Summary | IC | Post-mortem link | Actions closed |
|---|---|---|---|---|---|
| | | | | | |

### Tabletop drills
| Date | Scenario | Participants | Findings |
|---|---|---|---|
| | | | |
