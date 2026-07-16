"""Shared fixtures for runtime tests."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("AUTH_ALLOW_EPHEMERAL_KEYS", "true")
os.environ.setdefault("MCPFINDER_DEPLOYMENT_ENV", "development")

import pytest
import pytest_asyncio
import yaml

# Ensure the runtime package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def test_app():
    """Return a FastAPI app with DB patched out (all DB calls are no-ops).

    We patch _get_db before importing so the lifespan startup doesn't
    attempt real DB connections.  We also patch the file-loading helpers
    so they don't require YAML files on disk.
    """
    with patch("router._get_db", return_value=None), \
         patch("router._ensure_audit_table"), \
         patch("router._ensure_audit_events_table"), \
         patch("router._ensure_sealed_table"):
        import router
        yield router.app
        # Clean up global state between tests.  Some globals existed in older
        # router builds and have since been removed, so guard each cleanup to
        # keep the fixture focused on state isolation instead of API shape.
        for attr in (
            "channels",
            "manifests",
            "typed_manifests",
            "messages",
            "types_registry",
            "named_pipelines",
            "v2_pipelines",
            "external_agents",
            "_external_agent_rate_limits",
            "_demo_sandbox_run_timestamps",
            "_registry_item_tenants",
        ):
            value = getattr(router, attr, None)
            clear = getattr(value, "clear", None)
            if callable(clear):
                clear()
        router.type_graph = router.TypeGraph()


@pytest_asyncio.fixture()
async def client(test_app):
    """Async HTTP test client bound to the test app."""
    import httpx
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest.fixture()
def policy_engine_with_rules(tmp_path):
    """A PolicyEngine instance loaded from a temp YAML with known rules."""
    import router

    rules_yaml = {
        "version": "1",
        "rules": [
            {
                "id": "block-delete",
                "match": {"tool_pattern": "delete_*"},
                "action": "deny",
                "reason": "Destructive tools blocked",
            },
            {
                "id": "confirm-execute",
                "match": {"tool_pattern": "execute_*"},
                "action": "require_confirm",
                "reason": "Execution requires confirmation",
            },
            {
                "id": "default-allow",
                "match": {"tool_pattern": "*"},
                "action": "allow",
            },
        ],
    }

    policy_file = tmp_path / "policies" / "default.yaml"
    policy_file.parent.mkdir(parents=True, exist_ok=True)
    policy_file.write_text(yaml.dump(rules_yaml))

    engine = router.PolicyEngine.__new__(router.PolicyEngine)
    engine.rules = []

    with open(policy_file) as f:
        data = yaml.safe_load(f)
    engine.rules = data.get("rules", [])

    return engine
