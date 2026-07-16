from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "db" / "migrations"


def test_public_db_migrations_include_registry_core_tables_before_auth_fk():
    migration_files = sorted(p.name for p in MIGRATIONS.glob("*.sql"))

    assert migration_files[0] == "001_registry_core.sql"

    base_sql = (MIGRATIONS / "001_registry_core.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS servers" in base_sql
    assert "CREATE TABLE IF NOT EXISTS tools" in base_sql
    assert "CREATE TABLE IF NOT EXISTS api_keys" in base_sql
    assert "CREATE TABLE IF NOT EXISTS audit_events" in base_sql
    assert "CREATE TABLE IF NOT EXISTS deployments" in base_sql
    assert "server_id UUID REFERENCES servers(id)" in (MIGRATIONS / "004_auth.sql").read_text()
