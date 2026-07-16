"""Regression tests for production/public-test router auth-key hardening."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import router


def test_router_refuses_missing_rs256_key_in_production_like_env():
    env = {
        "MCPFINDER_DEPLOYMENT_ENV": "public-test",
        "AUTH_ALLOW_EPHEMERAL_KEYS": "true",
    }

    assert router._is_production_like_auth_env(env) is True
    assert router._router_ephemeral_keys_allowed(env) is False
    with pytest.raises(RuntimeError, match="ROUTER_RS256_PRIVATE_KEY is required"):
        router._assert_router_key_configured(env)


def test_router_import_fails_without_rs256_key_in_production_like_env():
    env = os.environ.copy()
    env.pop("AUTH_ALLOW_EPHEMERAL_KEYS", None)
    env.pop("ROUTER_RS256_PRIVATE_KEY", None)
    env["MCPFINDER_DEPLOYMENT_ENV"] = "production"

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, 'runtime'); import router; print(router._ROUTER_KID)",
        ],
        cwd=str(router._REPO_ROOT),
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
    )

    assert proc.returncode != 0
    assert "ROUTER_RS256_PRIVATE_KEY is required" in (proc.stderr + proc.stdout)


def test_router_allows_ephemeral_rs256_key_only_with_explicit_dev_flag():
    assert router._router_ephemeral_keys_allowed(
        {"MCPFINDER_DEPLOYMENT_ENV": "development", "AUTH_ALLOW_EPHEMERAL_KEYS": "true"}
    ) is True
    assert router._router_ephemeral_keys_allowed(
        {"MCPFINDER_DEPLOYMENT_ENV": "development"}
    ) is False


def test_k3d_router_manifest_declares_safe_rs256_key_policy():
    manifest_path = Path(__file__).resolve().parents[2] / "k8s" / "mcp-router.yaml"
    docs = list(yaml.safe_load_all(manifest_path.read_text()))
    deployment = next(doc for doc in docs if doc.get("kind") == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env_entries = container["env"]
    env = {entry["name"]: entry for entry in env_entries}
    env_from_secret_names = {
        entry.get("secretRef", {}).get("name")
        for entry in container.get("envFrom", [])
        if entry.get("secretRef", {}).get("name")
    }

    has_private_key_secret = bool(env.get("ROUTER_RS256_PRIVATE_KEY", {}).get("valueFrom", {}).get("secretKeyRef"))
    has_runtime_auth_secret_bundle = "mcpfinder-runtime-auth" in env_from_secret_names
    explicitly_local_dev = env.get("MCPFINDER_DEPLOYMENT_ENV", {}).get("value") == "development"
    allows_ephemeral_in_dev = env.get("AUTH_ALLOW_EPHEMERAL_KEYS", {}).get("value") == "true"

    assert has_private_key_secret or has_runtime_auth_secret_bundle or (explicitly_local_dev and allows_ephemeral_in_dev)


def test_api_key_manager_loads_durable_identity_delegation_metadata(monkeypatch):
    class FakeCursor:
        def __init__(self):
            self.sql = ""

        def execute(self, sql):
            self.sql = sql

        def fetchall(self):
            return [
                (
                    "portal-key",
                    "service-tenant",
                    "portal-shared-key",
                    ["sealed_handle.create"],
                    True,
                    {"portal_identity_delegation": True},
                )
            ]

        def close(self):
            pass

    class FakeConn:
        def __init__(self):
            self.cursor_obj = FakeCursor()

        def cursor(self):
            return self.cursor_obj

    conn = FakeConn()
    monkeypatch.setattr(router, "_get_db", lambda: conn)
    manager = router.ApiKeyManager()

    manager.load_keys()

    assert "allow_identity_delegation" in conn.cursor_obj.sql
    assert "metadata" in conn.cursor_obj.sql
    assert manager.keys["portal-key"] == {
        "tenant_id": "service-tenant",
        "name": "portal-shared-key",
        "permissions": ["sealed_handle.create"],
        "allow_identity_delegation": True,
        "metadata": {"portal_identity_delegation": True},
    }
    assert router._api_key_allows_identity_delegation("portal-key", manager.keys["portal-key"]) is True


def test_k3d_router_manifest_explicitly_opts_into_public_test_persistent_keys():
    manifest_path = router._REPO_ROOT / "k8s" / "mcp-router.yaml"
    docs = list(yaml.safe_load_all(manifest_path.read_text()))
    deployment = next(doc for doc in docs if doc and doc.get("kind") == "Deployment" and doc["metadata"]["name"] == "mcp-router")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env = {
        entry["name"]: entry.get("value")
        for entry in container["env"]
        if "name" in entry
    }
    env_from_secret_names = {
        entry.get("secretRef", {}).get("name")
        for entry in container.get("envFrom", [])
        if entry.get("secretRef", {}).get("name")
    }

    assert env["MCPFINDER_DEPLOYMENT_ENV"] == "public-test"
    assert env["AUTH_ALLOW_EPHEMERAL_KEYS"] == "false"
    assert "mcpfinder-runtime-auth" in env_from_secret_names
