# Access Control & Review (TEMPLATE)

**Version:** 0.1 DRAFT · **Owner:** _<security lead>_ · **Review:** quarterly

## Policy
- **Least privilege**, role-based. Platform access is gated per-action (`api_keys.action_permissions`,
  `router.py:_authorize_action`); protected actions fail closed when permission metadata is missing.
- **SSO required** for human access where available (OIDC/SAML); MFA enforced at the IdP.
- **Provisioning/deprovisioning** via SCIM (create/update/deactivate + session revocation). Offboarding
  must revoke access **same business day**.
- **Service identities/API keys** are scoped to the minimum action set and rotated; no shared keys.
- **Admin/break-glass** access is exceptional, time-bound, and logged (audit events).

## Quarterly access review (procedure → evidence)
1. Export current human users + roles (IdP/SCIM) and active API keys + `action_permissions` (DB).
2. The owner + each resource owner confirm every grant is still required (job role unchanged).
3. Revoke unneeded grants; deactivate stale keys/users (`is_active=false`).
4. Record the review (table below), sign, and store as evidence.

### Review log
| Date | Reviewer | Users reviewed | Keys reviewed | Revocations | Notes |
|---|---|---|---|---|---|
| _YYYY-Qn_ | | | | | |

## Key/credential rotation
- Platform encryption keys: versioned ring + `reencrypt_platform()` (`runtime/credentials.py`); rotate on schedule/compromise.
- API keys/service creds: rotate ≥ annually and on suspected compromise; document in the log.
