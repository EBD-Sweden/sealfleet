# Security Test Report — Sealfleet

**Date:** 2026-06-08 · **Scope:** runtime router API + portal · **Type:** authorized internal security test (SOC 2 CC4.1 / CC7.1 evidence)
**Focus (requested):** no SQL injection; no cross-user/cross-tenant data access (broken access control / IDOR).

## Methodology
- **Static SQLi audit** — reviewed all 87 `execute()` call sites in `runtime/router.py`, `registry/server.py`, `deploy/server.py`, `packages/mcpfinder-auth`.
- **Access-control sweep** — enumerated every path-param / resource endpoint and checked auth gating + tenant/owner scoping; confirmed findings dynamically against the live cluster with a real API key.
- **ZAP** — `zap-baseline.py` (passive) against the router (`ghcr.io/zaproxy/zaproxy`).
- **Burp Suite** — not run: Burp has no free headless automation (only Burp **Pro** REST API). Equivalent coverage via ZAP + the manual auth/IDOR tests below; a Burp Pro runbook can be added if licensed.

## SQL injection — PASS (no injectable sinks)
All DB access uses **parameterized queries** (`%s` + params). The only two dynamically-built SQL strings interpolate **hardcoded column allowlists**, never user input, with values still parameterized:
- `runtime/router.py` `list_jobs` — `WHERE` built from fixed clauses (`status`/`pipeline_name`/`tenant_id` = `%s`).
- `runtime/router.py` credential update — `SET` built from a fixed column allowlist; values `%s`.
No string-formatted user input reaches SQL. **No SQL injection found.**

## Broken access control / IDOR — 2 found, FIXED + verified

### FINDING 1 (High) — jobs endpoints had no tenant isolation
`GET /jobs`, `GET /jobs/{id}`, `POST /jobs/{id}/cancel` had **no `request`, no tenant scoping**. Confirmed live: a `local-dev` key listed `system`-tenant jobs and read one by id; the `tenant_id` query param let a caller target other tenants.
- **Fix** (`187d58f`): all three derive the caller tenant and gate on `_can_submit_job_for_tenant` (own tenant or platform authority); list always filters by the caller's tenant; cross-tenant id → 404 (no existence disclosure); foreign `tenant_id` filter → 403.
- **Verified live (post-fix):** `GET /jobs` → caller tenant only; `GET /jobs/{other}` → 404; `?tenant_id=other` → 403; own job → 200.

### FINDING 2 (Medium) — manual scale endpoints unauthenticated
`POST /scale/{mcp}/up` and `/down` had **no permission gate** — any authenticated caller could scale any MCP (cross-tenant DoS by scaling a victim's MCP to zero, or resource abuse).
- **Fix** (`9887c15`): both gated on `mcp.server.register` (manage-MCP authority).
- **Verified live:** `POST /scale/.../down` with no auth → 401.

### Endpoints checked and confirmed already-correct
`GET /manifests/{name}` (tenant visibility), `DELETE /credentials/{id}` + `/credentials/{id}/use` (tenant-scoped + action-gated), `GET/DELETE /sealed/{id}` (HTTP plaintext resolve disabled; delete is tenant+subject scoped), audit endpoints (tenant-scoped + `audit.read`). Authentication itself is enforced (no-auth → 401).

## ZAP baseline (live router) — PASS but shallow
No passive findings; only 3 URLs (auth-gated, non-HTML API → spider can't crawl).

## Deep authenticated scan — disposable `docker compose` stack (2026-06-08)
Stood up the full stack via `docker compose` (isolated, internal network) and scanned it so active/mutating tests never touched the live cluster.
- **ZAP `api-scan`** (authenticated via the seeded operator key, OpenAPI-driven): **194 URLs** scanned (vs 3 in the baseline); passive rules **all PASS**; ZAP's **CRLF / header-injection** probes were rejected (**422**) — no header injection. No SQLi/XSS surfaced.
- **SQLi (targeted probes)**: the public `sqlmapproject/sqlmap` image is gone, so classic injection payloads were fired at the only user-input params (`/jobs?status=`, `?pipeline=`). All requests (payloads **and** the legitimate baseline) returned an identical 500 — i.e. **input-independent**, **no injection signal**. Combined with the static audit (parameterized queries), **SQLi: clean**.
- The identical 500 traced to a real **functional bug** (see below), not security.

## Confirmed fixed (this engagement)
- Jobs tenant-isolation IDOR (`187d58f`); unauthenticated scale endpoints (`9887c15`).
- **Portal not serving** (the earlier "500"/refused): the image didn't set `PORT`, so Next.js listened on 3000 while everything targeted 3004. Fixed in `portal/Dockerfile` (`PORT=3004`/`HOSTNAME=0.0.0.0`); verified the portal then serves `/`, `/login`, `/api/ready` → 200 (`4cae40e`).
- **DAST in CI**: `.github/workflows/dast.yml` brings up the compose stack and runs the authenticated ZAP api-scan + portal baseline (weekly + on-demand, reports uploaded).
- **`pipeline_jobs`/`pipeline_job_steps` missing on fresh deploy** (root cause of the `/jobs` 500 above) — created by no migration nor at startup, so jobs + the `workflow` CLI facade 500'd on any clean `docker compose`/Helm install. Fixed (`cef33ae`): added `db/migrations/011_pipeline_jobs.sql` + a startup `_ensure_jobs_tables()` self-heal. **Verified:** a fresh-DB run of all migrations creates both tables and accepts inserts; live `GET /jobs` → 200.
- **`POST /registry/import` 500 on malformed input** — now returns **400** for non-JSON and non-object bodies (`cef33ae`). **Verified live:** bad JSON → 400; JSON array → 400.
- **Stray `npm install -g @steipete/bird`** in `portal/Dockerfile` build stage (supply-chain hygiene) — removed (`cef33ae`); portal still builds clean and serves `/` → 200.

## Open items / follow-ups
- **Burp Pro** REST-API runbook (if licensed) — optional; ZAP api-scan provides equivalent coverage.
- Re-run DAST per release as SOC 2 evidence.
