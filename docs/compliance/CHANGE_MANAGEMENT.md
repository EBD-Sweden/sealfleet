# Change Management (TEMPLATE)

**Version:** 0.1 DRAFT · **Owner:** _<eng lead>_ · **Review:** annual

## Policy
All production changes go through version control + peer review + automated checks; no direct pushes
to protected branches; changes are traceable to an author and reviewer.

## Controls
- **Branch protection** on `main`/`master`: require PR, ≥1 review, passing required checks, no force-push,
  linear history. _(Configure in GitHub repo settings — evidence: settings export/screenshot.)_
- **Required CI checks** before merge:
  - `ci.yml` — tests (Python + portal) + build.
  - `security.yml` — bandit (SAST), pip-audit (deps), gitleaks (secrets), trivy (vuln/misconfig).
  - `cluster-routing-guard.yml` — routing/CLI guard.
  - `supply-chain.yml` — SBOM (syft) + image signing (cosign) on release.
- **Reviewer** verifies: tests added/updated, no secrets, least-privilege, migration safety, rollback plan.
- **Migrations** are additive/idempotent and forward-only (see `db/migrations/`).
- **Releases** are tagged; images are signed (cosign) and have SBOM attestations.

## Rollback
- App: redeploy the prior signed image tag.
- DB: migrations are designed additive; destructive changes require a backout script + a tested backup
  (`scripts/backup-postgres.sh`).

## Emergency changes
Allowed for SEV1 incidents; must still land as a PR retroactively within 1 business day with review + post-mortem link.

## Evidence
PR history (author/reviewer/checks), branch-protection config, CI run logs, signed-image/SBOM attestations.
