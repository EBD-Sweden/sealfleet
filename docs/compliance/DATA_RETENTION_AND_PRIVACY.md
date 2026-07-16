# Data Retention & Privacy

**Version:** 0.2 · **Owner:** _<privacy/security lead>_ · **Review:** annual

## Retention schedule
| Data | Store | Retention | Mechanism |
|---|---|---|---|
| Audit events | Postgres `audit_events` (append-only, tamper-evident) | ≥ 1 year (`AUDIT_RETENTION_DAYS`, default 365); archive, never in-place delete | archival/export only; immutability trigger blocks DELETE; scheduled cycle reports rows past retention (`retention.prune` audit event) |
| Operational/session data | Postgres `user_sessions` etc. | 90 days (`PRIVACY_OPERATIONAL_RETENTION_DAYS`) | **scheduled** `prune_operational_data()` — runs every `MCPFINDER_RETENTION_INTERVAL_HOURS` (default 24h) unless `MCPFINDER_RETENTION_SCHEDULE=false` |
| Backups | encrypted dumps | 30 days (`RETENTION_DAYS`) | `scripts/backup-postgres.sh` rotation |
| Credentials/sealed inputs | encrypted | life of resource + sealed-handle TTL | `runtime/credentials.py`, sealed handles |

> Audit data is intentionally exempt from erasure/pruning (integrity > minimization for the security log);
> the erase flow records `audit_events_preserved: true`.

## Processing purpose & lawful basis (GDPR Art. 30)

Every audit event carries a `purpose` and `lawful_basis` column (migration
`db/migrations/012_gdpr_audit_purpose.sql`). The router derives defaults from the
action when the caller does not supply them (`_default_audit_purpose`):

| Action prefix | Purpose | Lawful basis |
|---|---|---|
| `privacy.*`, `audit*`, `retention.*` | compliance | legal_obligation |
| `auth*`, `token*`, `policy_*`, `key.*`, `credential*` | security | legitimate_interest |
| everything else (tool calls, pipelines) | service_delivery | contract |

New events include both fields in the tamper-evident hash chain; rows written
before the migration verify with the original field set (NULL columns are
excluded from the recomputed hash).

## PII in tool outputs (data minimization)

Operators declare PII output fields directly in the MCP manifest YAML — per tool
or MCP-wide — and the runtime redacts them at the execution boundary before
results reach callers, downstream pipeline steps, or logs:

```yaml
tools:
  - name: get_customer
    pii_fields:            # dot paths into the tool result
      - customer.email
      - customer.ssn
      - orders.contact     # lists are traversed element-wise
```

Enforcement is always on (`ManifestPiiGuard` in `runtime/policy_hooks.py`) and
independent of the optional `runtime_hooks` config. Redactions are audited by
field NAME only, never value. Self-registration cannot drop YAML-declared
`pii_fields` (seeded declarations are re-applied on re-register).

## Privacy / GDPR (DSAR)
- **Right of access / portability:** `GET /privacy/export` (gated `privacy.export`) returns the subject's
  user record, roles, session metadata, **and audit trail** (events attributed to the
  subject; payloads secret-redacted at write time; capped at 1000 most recent with a
  `audit_trail_truncated` flag).
- **Right to erasure:** `POST /privacy/erase` (gated `privacy.erase`) anonymizes/deactivates a subject's
  non-audit personal data + revokes sessions; the action itself is audited.
- **Lawful basis / data map:** every audit event is tagged (see table above); maintain the
  record of processing activities for stores outside the audit log.
- **Breach notification:** ≤ 72h to the supervisory authority where applicable (see IR plan).

### DSAR log (evidence)
| Date | Subject | Request type | Fulfilled by | Date closed |
|---|---|---|---|---|
| | | export / erase | | |

## Data minimization
Collect only what's needed; keep restricted data out of logs (CLI/deploy redaction,
manifest `pii_fields` redaction) and the LLM (sealed inputs).
