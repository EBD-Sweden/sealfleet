# Registry Backup and Restore

This document describes the current Sealfleet registry/catalog data model and the tenant-safe JSON import/export workflow for operator backups.

## Current data model

Runtime catalog state lives in `runtime/router.py` and is loaded from YAML plus runtime registration endpoints:

- `manifests: dict[str, McpManifest]` — one MCP server entry per name. Fields: `name`, `endpoint`, `publishes`, `subscribes`, `tools`, `transport`, and optional `image`.
- `typed_manifests: dict[str, dict]` — raw typed manifest payloads for MCPs with typed tool input/output metadata. These feed `TypeGraph` and generated pipeline docs.
- `named_pipelines: dict[str, NamedPipeline]` — named pipeline metadata (`name`, `description`, `inputs`, `stages`, `output_stage`, `tags`, `created_at`).
- `_registry_item_tenants: dict[str, str]` — runtime ownership metadata keyed as `manifest:<name>` and `pipeline:<name>` so exports and imports are scoped to the authenticated tenant.

The standalone `registry/` service still owns PostgreSQL discovery rows for `servers`, `tools`, and `audit_events`. The import/export slice here targets the runtime catalog interfaces used by the router/portal catalog path and reuses the existing manifest and pipeline models rather than introducing a second registry schema.

## Security guarantees

- `GET /registry/export` requires the `registry.export` action permission.
- `POST /registry/import` requires the `registry.import` action permission.
- The bundle `tenant_id` must match the authenticated runtime tenant on import.
- Export includes schema metadata: `schema = mcpfinder.registry.export`, `schema_version = 1`, `tenant_id`, and `exported_at`.
- Export recursively redacts sensitive keys such as API keys, tokens, passwords, credentials, sealed values, authorization headers, and encrypted values.
- Import returns per-item validation/apply results and skips invalid items without blocking valid siblings.
- Audit events store only counts/status summaries, never raw bundle contents.

## API smoke

Set an API key that has `registry.export` and `registry.import` permissions for the tenant being backed up.

```bash
export RUNTIME_URL=http://localhost:8040
export MCPFINDER_BACKUP_API_KEY='***'

python scripts/registry_import_export.py \
  --runtime-url "$RUNTIME_URL" \
  --api-key "$MCPFINDER_BACKUP_API_KEY" \
  export --output /tmp/mcpfinder-registry-backup.json

python scripts/registry_import_export.py \
  --runtime-url "$RUNTIME_URL" \
  --api-key "$MCPFINDER_BACKUP_API_KEY" \
  import --input /tmp/mcpfinder-registry-backup.json
```

The import command defaults to dry-run and exits non-zero if validation reports errors.

## Restore/apply

After reviewing the dry-run report, apply the bundle:

```bash
python scripts/registry_import_export.py \
  --runtime-url "$RUNTIME_URL" \
  --api-key "$MCPFINDER_BACKUP_API_KEY" \
  import --input /tmp/mcpfinder-registry-backup.json --apply
```

Then verify the restored catalog through the runtime endpoints:

```bash
curl -fsS -H "X-API-Key: $MCPFINDER_BACKUP_API_KEY" "$RUNTIME_URL/manifests" | jq 'length'
curl -fsS -H "X-API-Key: $MCPFINDER_BACKUP_API_KEY" "$RUNTIME_URL/pipelines" | jq 'length'
curl -fsS -H "X-API-Key: $MCPFINDER_BACKUP_API_KEY" "$RUNTIME_URL/registry/export" \
  | jq '{tenant_id, manifests: (.manifests|length), typed_manifests: (.typed_manifests|length), pipelines: (.pipelines|length)}'
```

## Operational notes

- Do not store backup files in source control.
- Treat backup files as sensitive even though secret-like fields are redacted, because server names/endpoints and pipeline metadata may reveal internal topology.
- For disaster recovery, restore into an empty or tenant-matched runtime first, then run the dry-run again to catch any malformed or stale items before applying to production.
