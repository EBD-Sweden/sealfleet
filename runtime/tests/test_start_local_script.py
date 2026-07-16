from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
START_LOCAL = REPO_ROOT / "scripts" / "start-local.sh"


def test_start_local_honors_database_url_and_never_uses_fixed_placeholder_assignment():
    script = START_LOCAL.read_text()

    assert 'DB_URL="${DATABASE_URL:-' in script
    assert 'DATABASE_URL="$DB_URL"' in script
    assert "DB_URL=\"postgresql://admin:" not in script


def test_start_local_allows_port_overrides_for_clean_e2e_on_busy_hosts():
    script = START_LOCAL.read_text()

    assert 'REGISTRY_PORT="${REGISTRY_PORT:-8010}"' in script
    assert 'AUTH_ALLOW_EPHEMERAL_KEYS="${AUTH_ALLOW_EPHEMERAL_KEYS:-1}"' in script
    assert 'PYTHONPATH="$BASE:${PYTHONPATH:-}"' in script
    assert 'code=$(curl -s -o /dev/null -w "%{http_code}"' in script
    assert '[[ "$code" =~ ^[0-9]{3}$ ]]' in script
    assert 'DEPLOY_PORT="${DEPLOY_PORT:-8030}"' in script
    assert 'ROUTER_PORT="${ROUTER_PORT:-8040}"' in script
    assert 'PORTAL_PORT="${PORTAL_PORT:-3000}"' in script
    assert '"registry|${BASE}/registry|server:app|${REGISTRY_PORT}|python"' in script
