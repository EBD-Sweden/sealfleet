#!/usr/bin/env bash
#
# Encrypted, rotated logical backup of the Sealfleet platform Postgres.
# SOC2 A1.2 (availability — backup/restore). Pairs with k8s/backup-cronjob.yaml.
#
# Produces an AES-256 encrypted pg_dump (custom format) on a target directory,
# prunes backups older than RETENTION_DAYS, and (optionally) uploads to object
# storage. Restore: see the "RESTORE" note at the bottom.
#
# Required env:
#   DATABASE_URL            postgres connection string (or PG* vars)
#   BACKUP_ENCRYPTION_KEY   passphrase for AES-256 encryption (from a Secret/KMS)
# Optional env:
#   BACKUP_DIR              default /backups
#   RETENTION_DAYS          default 30
#   BACKUP_S3_URI           if set (e.g. s3://bucket/prefix), upload via aws cli
#
set -euo pipefail

: "${BACKUP_ENCRYPTION_KEY:?BACKUP_ENCRYPTION_KEY is required (do not hardcode)}"
DB_URL="${DATABASE_URL:?DATABASE_URL is required}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

mkdir -p "$BACKUP_DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
base="mcpfinder-${stamp}.dump"
enc="${BACKUP_DIR}/${base}.enc"

echo "[backup] dumping database -> ${enc}"
# pg_dump -Fc (custom, compressed) | AES-256 encrypt with a key derived via PBKDF2.
pg_dump --dbname="$DB_URL" -Fc \
  | openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_ENCRYPTION_KEY -out "$enc"

sha256sum "$enc" > "${enc}.sha256"
echo "[backup] wrote $(du -h "$enc" | cut -f1) + checksum"

if [ -n "${BACKUP_S3_URI:-}" ]; then
  echo "[backup] uploading to ${BACKUP_S3_URI}"
  aws s3 cp "$enc" "${BACKUP_S3_URI%/}/${base}.enc"
  aws s3 cp "${enc}.sha256" "${BACKUP_S3_URI%/}/${base}.enc.sha256"
fi

echo "[backup] pruning backups older than ${RETENTION_DAYS} days"
find "$BACKUP_DIR" -name 'mcpfinder-*.dump.enc*' -type f -mtime "+${RETENTION_DAYS}" -delete

echo "[backup] done: ${enc}"

# RESTORE (run deliberately, never automatically):
#   openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY -in <file>.enc \
#     | pg_restore --clean --if-exists --dbname="$DATABASE_URL"
# A restore test MUST be run periodically (SOC2 evidence) — see docs/REGISTRY_BACKUP_RESTORE.md.
