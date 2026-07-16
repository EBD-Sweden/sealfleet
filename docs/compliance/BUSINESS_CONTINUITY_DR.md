# Business Continuity & Disaster Recovery (TEMPLATE)

**Version:** 0.1 DRAFT · **Owner:** _<eng/ops lead>_ · **Review:** annual · **DR test:** ≥ annual (restore test)

## Objectives
| Metric | Target | Notes |
|---|---|---|
| RTO (recovery time) | _< 4h_ | redeploy signed images + restore DB |
| RPO (data loss) | _< 24h_ | daily encrypted backups (tighten with WAL/PITR if needed) |

## Backups
- **What:** platform Postgres (all databases). **How:** `scripts/backup-postgres.sh` — `pg_dump -Fc` →
  AES-256 encrypted → checksum → rotate (`RETENTION_DAYS`, default 30) → optional offsite (`BACKUP_S3_URI`).
- **Schedule:** `k8s/backup-cronjob.yaml` (daily 02:00 UTC) with a dedicated PVC; offsite copy recommended.
- **Key:** `BACKUP_ENCRYPTION_KEY` from a Secret/KMS — never committed.

## Restore (run deliberately; never automated)
```bash
openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY -in <file>.enc \
  | pg_restore --clean --if-exists --dbname="$DATABASE_URL"
```

## DR test (evidence — REQUIRED periodically)
Restore the latest backup into a scratch database, run smoke checks, and record below. A backup with no
tested restore is not evidence of recoverability.

| Date | Backup tested | Restore target | RTO observed | Result | Tester |
|---|---|---|---|---|---|
| | | | | | |

## Continuity
- Infra reproducible from `k8s/` manifests + signed images in the registry.
- Cluster recovery procedures: see `../../[memory] cluster-infra-recovery` notes (docker data-root on SSD,
  image-preload playbook, CoreDNS). Keep a current runbook for node/cluster rebuild.
- Single-region today; document multi-region/HA as a roadmap item if availability commitments require it.
