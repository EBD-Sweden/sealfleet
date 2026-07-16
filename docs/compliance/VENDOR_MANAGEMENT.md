# Vendor / Subprocessor Management (TEMPLATE)

**Version:** 0.1 DRAFT · **Owner:** _<security lead>_ · **Review:** annual per vendor (or on contract change)

## Policy
Third parties that process or could access customer data ("subprocessors") are inventoried, risk-assessed
before onboarding, and reviewed at least annually. Prefer vendors with a current SOC 2 / ISO 27001 report.

## Onboarding checklist
- [ ] Business purpose + data shared (class per `INFORMATION_SECURITY_POLICY.md`)
- [ ] Security report obtained (SOC 2 Type II / ISO 27001) and reviewed; DPA signed if PII
- [ ] Access scoped least-privilege; credentials stored in a secret manager, rotated
- [ ] Added to the register below; added to the public subprocessor list if customer-facing

## Subprocessor register
| Vendor | Service | Data class | Region | Report on file | Last review | Owner |
|---|---|---|---|---|---|---|
| _<cloud/k8s host>_ | compute/k8s | Restricted | | | | |
| _<managed Postgres or self-host>_ | database | Restricted | | | | |
| _<LLM provider>_ | model inference (planner only; no secrets) | Confidential | | | | |
| _<container registry / GHCR>_ | image hosting | Internal | | | | |
| _<email/IdP>_ | auth/notifications | Confidential | | | | |

> Note: per architecture, the LLM provider receives planning context only — never restricted secrets
> (sealed-input boundary). Document this in the data-flow / DPA.

## Offboarding
Revoke credentials, confirm data deletion/return, update the register.
