"""Acceptance tests for the Sealfleet MCP server CLI command-line contract."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_CLI = ROOT / "scripts" / "mcpfinder_cli.py"


def _run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    run_env = None
    if env is not None:
        run_env = {**os.environ, **env}
    return subprocess.run(
        [sys.executable, "-m", "runtime.cli", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=run_env,
    )


def _run_script_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_CLI), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_module_entrypoint_global_json_before_invoke_accepts_structured_payload_in_dry_run():
    result = _run_cli(
        "--json",
        "invoke",
        "--mcp",
        "demo-sandbox-mcp",
        "--tool",
        "get_demo_customer",
        "--payload",
        '{"customer_id":"cust_123"}',
        "--dry-run",
    )

    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["command"] == "invoke"
    assert body["dry_run"] is True
    assert body["request"] == {
        "mcp": "demo-sandbox-mcp",
        "tool": "get_demo_customer",
        "inputs": {"customer_id": "cust_123"},
    }


def test_script_wrapper_delegates_to_canonical_runtime_cli_entrypoint():
    result = _run_script_cli("--json", "contract")

    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["contract"]["entrypoint"] == "python -m runtime.cli"


def test_control_plane_export_fails_honestly_when_backend_unavailable():
    result = _run_cli(
        "--json",
        "registry",
        "export",
        "--runtime-url",
        "http://127.0.0.1:9",
        "--api-key",
        "test-key",
    )

    assert result.returncode == 2
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "backend_unavailable"
    assert "registry/export" in body["error"]["target"]


def test_validate_accepts_real_mcpfinder_config_and_redacts_secret_fields(tmp_path: Path):
    config = tmp_path / "mcpfinder-cli.json"
    config.write_text(
        json.dumps(
            {
                "schema": "mcpfinder.cli.config/v1",
                "product": "mcpfinder",
                "runtime_url": "http://localhost:8040",
                "allowed_scopes": ["runtime", "registry", "control-plane"],
                "api_token": "super-secret-token",
            }
        )
    )

    result = _run_cli("--json", "validate", "--config", str(config))

    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["command"] == "validate"
    assert body["redacted"] is True
    assert "super-secret-token" not in result.stdout


def test_validate_rejects_cross_project_cli_config(tmp_path: Path):
    config = tmp_path / "other-product-cli.json"
    config.write_text(
        json.dumps(
            {
                "schema": "mcpfinder.cli.config/v1",
                "product": "example-other-product",
                "runtime_url": "http://localhost:8040",
                "allowed_scopes": ["runtime"],
            }
        )
    )

    result = _run_cli("--json", "validate", "--config", str(config))

    assert result.returncode == 2
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "project_scope_violation"


def test_manifest_register_dry_run_redacts_secret_fields(tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "name": "demo-sandbox-mcp",
                "endpoint": "http://demo-sandbox-mcp:8080",
                "publishes": ["demo.customer"],
                "subscribes": [],
                "tools": ["get_demo_customer"],
                "metadata": {"api_key": "should-not-print"},
            }
        )
    )

    result = _run_cli("--json", "manifest", "register", "--file", str(manifest), "--dry-run")

    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["dry_run"] is True
    assert body["manifest"]["metadata"]["api_key"] == "[REDACTED]"
    assert "should-not-print" not in result.stdout


def test_smoke_local_demo_dry_run_maps_to_real_runtime_operations_without_backend_success():
    result = _run_cli("--json", "smoke", "local-demo", "--dry-run")

    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["command"] == "smoke local-demo"
    assert body["dry_run"] is True
    assert body["checks"] == [
        {"method": "GET", "path": "/health"},
        {"method": "GET", "path": "/ready"},
        {"method": "GET", "path": "/manifests"},
        {"method": "POST", "path": "/call", "body": {"mcp": "demo-sandbox-mcp", "tool": "get_demo_customer", "inputs": {"customer_id": "cust_123"}}},
    ]


def test_status_fails_nonzero_when_health_backend_unavailable():
    result = _run_cli("--json", "status", "--runtime-url", "http://127.0.0.1:9")

    assert result.returncode == 2
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "backend_unavailable"
    assert body["error"]["target"].endswith("/health")


def test_contract_surface_documents_control_registry_invoke_smoke_and_agent_contract():
    result = _run_cli("--json", "contract")

    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["ok"] is True
    contract = body["contract"]
    assert contract["schema"] == "mcpfinder.cli.contract/v1"
    assert contract["product"] == "mcpfinder"
    assert contract["entrypoint"] == "python -m runtime.cli"
    assert {"contract", "validate", "status", "invoke", "registry", "manifest", "smoke"}.issubset(contract["commands"])
    assert "agent_contract" in contract
    assert contract["agent_contract"]["secrets"] == "never pass raw secrets in prompts or payload logs"


def test_contract_and_help_use_cli_not_cri_wording():
    contract_result = _run_cli("--json", "contract")
    assert contract_result.returncode == 0, contract_result.stderr
    contract_text = contract_result.stdout.lower()
    assert "command line interface" in contract_text
    assert not re.search(r"\bcri\b", contract_text)

    help_result = _run_cli("--help")
    assert help_result.returncode == 0, help_result.stderr
    help_text = help_result.stdout.lower()
    assert "command line interface" in help_text
    assert not re.search(r"\bcri\b", help_text)


# ---------------------------------------------------------------------------
# Cluster lifecycle
# ---------------------------------------------------------------------------


def test_cluster_create_k3d_dry_run_lists_real_commands():
    result = _run_cli("--json", "cluster", "create", "--mode", "k3d", "--dry-run")
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["dry_run"] is True
    assert body["mode"] == "k3d"
    assert ["k3d", "cluster", "create", "mcpfinder"] in body["commands"]
    assert any(c[:2] == ["kubectl", "apply"] for c in body["commands"])


def test_cluster_create_k3d_missing_tooling_fails_dependency_missing():
    # Empty PATH so k3d/kubectl/docker cannot be resolved; python runs via absolute sys.executable.
    result = _run_cli("--json", "cluster", "create", "--mode", "k3d", env={"PATH": "/nonexistent"})
    assert result.returncode == 2
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "dependency_missing"


def test_cluster_status_backend_unavailable_fails_honestly():
    result = _run_cli("--json", "cluster", "status", "--runtime-url", "http://127.0.0.1:9", "--deploy-url", "http://127.0.0.1:9")
    assert result.returncode == 2
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "backend_unavailable"
    assert body["error"]["target"].endswith("/health")


def test_cluster_down_requires_confirmation():
    result = _run_cli("--json", "cluster", "down", "--mode", "local")
    assert result.returncode == 2
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "confirmation_required"


def test_cluster_down_refuses_unscoped_k3d_name_without_force():
    result = _run_cli("--json", "cluster", "down", "--mode", "k3d", "--name", "production", "--yes", "--dry-run")
    assert result.returncode == 2
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "scope_violation"


# ---------------------------------------------------------------------------
# MCP deploy via the separate deploy service (:8030)
# ---------------------------------------------------------------------------


def test_mcp_deploy_dry_run_redacts_env_and_targets_deploy_service():
    result = _run_cli(
        "--json", "mcp", "deploy",
        "--repo-url", "https://github.com/acme/mcp",
        "--name", "acme-mcp",
        "--env", "API_KEY=topsecret",
        "--env", "REGION=eu",
        "--dry-run",
    )
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["dry_run"] is True
    # env_vars are operator secrets: every value is masked on echo, keys stay visible.
    assert set(body["request"]["env_vars"].keys()) == {"API_KEY", "REGION"}
    assert body["request"]["env_vars"]["API_KEY"] == "[REDACTED]"
    assert body["request"]["env_vars"]["REGION"] == "[REDACTED]"
    assert "topsecret" not in result.stdout
    # Targets the deploy service (:8030), never the router (:8040).
    assert body["target"].endswith(":8030/deploy")


def test_mcp_deploy_requires_api_key_when_live():
    result = _run_cli(
        "--json", "mcp", "deploy",
        "--repo-url", "https://github.com/acme/mcp",
        "--name", "acme-mcp",
        "--deploy-url", "http://127.0.0.1:9",
        env={"MCPFINDER_API_KEY": ""},
    )
    assert result.returncode == 2
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "auth_missing"


def test_mcp_list_backend_unavailable_targets_deployments():
    result = _run_cli("--json", "mcp", "list", "--deploy-url", "http://127.0.0.1:9")
    assert result.returncode == 2
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "backend_unavailable"
    assert "/deployments" in body["error"]["target"]


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------


def test_pipeline_run_v2_dry_run_shape():
    result = _run_cli("--json", "pipeline", "run", "--name", "credit_batch", "--inputs", '{"sni_code":"62010"}', "--dry-run")
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["engine"] == "v2"
    assert body["target"].endswith("/v2/pipelines/run")
    assert body["request"] == {"pipeline": "credit_batch", "inputs": {"sni_code": "62010"}}


def test_pipeline_run_v1_dry_run_targets_named_run():
    result = _run_cli("--json", "pipeline", "run", "--name", "my_pipeline", "--engine", "v1", "--dry-run")
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["engine"] == "v1"
    assert body["target"].endswith("/pipelines/my_pipeline/run")


def test_pipeline_deploy_dry_run_redacts_secret_fields(tmp_path: Path):
    pipeline = tmp_path / "p.json"
    pipeline.write_text(json.dumps({"name": "p", "version": 2, "steps": [{"id": "s1", "mcp": "m", "tool": "t"}], "metadata": {"api_key": "leak"}}))
    result = _run_cli("--json", "pipeline", "deploy", "--file", str(pipeline), "--dry-run")
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["pipeline"]["metadata"]["api_key"] == "[REDACTED]"
    assert "leak" not in result.stdout
    assert body["target"].endswith("/v2/pipelines/deploy")


def test_pipeline_run_requires_api_key_when_live():
    result = _run_cli("--json", "pipeline", "run", "--name", "credit_batch", "--runtime-url", "http://127.0.0.1:9", env={"MCPFINDER_API_KEY": ""})
    assert result.returncode == 2
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "auth_missing"


# ---------------------------------------------------------------------------
# Workflow facade (pipelines + jobs)
# ---------------------------------------------------------------------------


def test_workflow_create_defaults_v1_named_pipeline_no_network(tmp_path: Path):
    # workflow run uses POST /jobs which resolves only v1 named pipelines, so
    # workflow create defaults to v1 (stages) for an end-to-end-coherent facade.
    out = tmp_path / "wf.json"
    result = _run_cli("--json", "workflow", "create", "--name", "wf", "--step", "demo-sandbox-mcp.score_fake_vendor", "--output", str(out))
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["engine"] == "v1"
    written = json.loads(out.read_text())
    assert written["name"] == "wf"
    assert written["stages"][0]["mcp"] == "demo-sandbox-mcp"
    assert written["stages"][0]["tool"] == "score_fake_vendor"
    assert written["output_stage"] == "stage_1"


def test_workflow_create_v2_still_available_via_flag(tmp_path: Path):
    out = tmp_path / "wf2.json"
    result = _run_cli("--json", "workflow", "create", "--name", "wf2", "--engine", "v2", "--step", "m.t", "--output", str(out))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["engine"] == "v2"
    assert json.loads(out.read_text())["steps"][0]["mcp"] == "m"


def test_workflow_run_maps_to_jobs_async():
    result = _run_cli("--json", "workflow", "run", "--name", "wf", "--inputs", '{"x":1}', "--dry-run")
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["target"].endswith("/jobs")
    assert body["request"]["pipeline"] == "wf"
    assert body["request"]["inputs"] == {"x": 1}


def test_workflow_status_dry_run_maps_to_jobs_get():
    result = _run_cli("--json", "workflow", "status", "--job-id", "job-42", "--dry-run")
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["target"].endswith("/jobs/job-42")


def test_workflow_cancel_dry_run_maps_to_jobs_cancel():
    result = _run_cli("--json", "workflow", "cancel", "--job-id", "job-42", "--dry-run")
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["target"].endswith("/jobs/job-42/cancel")


# ---------------------------------------------------------------------------
# Zero-to-hero smoke + extended contract/config
# ---------------------------------------------------------------------------


def test_smoke_zero_to_hero_dry_run_spans_deploy_and_runtime():
    result = _run_cli("--json", "smoke", "zero-to-hero", "--dry-run")
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["dry_run"] is True
    services = {c["service"] for c in body["checks"]}
    assert services == {"deploy", "runtime"}
    assert {"method": "GET", "path": "/deployments", "service": "deploy"} in body["checks"]
    assert {"method": "GET", "path": "/v2/pipelines", "service": "runtime"} in body["checks"]


def test_contract_documents_cluster_mcp_pipeline_workflow():
    result = _run_cli("--json", "contract")
    assert result.returncode == 0, result.stderr
    contract = json.loads(result.stdout)["contract"]
    assert {"cluster", "mcp", "pipeline", "workflow"}.issubset(contract["commands"])
    # Existing invariants preserved.
    assert contract["schema"] == "mcpfinder.cli.contract/v1"
    assert contract["product"] == "mcpfinder"
    assert contract["entrypoint"] == "python -m runtime.cli"
    assert contract["agent_contract"]["secrets"] == "never pass raw secrets in prompts or payload logs"
    # New contract sections honest about the workflow-vs-pipeline mapping + deploy split.
    assert "workflow_model" in contract
    assert "jobs" in contract["workflow_model"]["run"].lower() or "/jobs" in contract["workflow_model"]["run"]
    assert "deploy_api" in contract


def test_validate_accepts_extended_config_with_deploy_and_cluster_keys(tmp_path: Path):
    config = tmp_path / "cli.json"
    config.write_text(json.dumps({
        "schema": "mcpfinder.cli.config/v1",
        "product": "mcpfinder",
        "runtime_url": "http://localhost:8040",
        "deploy_url": "http://localhost:8030",
        "kube_context": "k3d-mcpfinder",
        "cluster_mode": "k3d",
        "allowed_scopes": ["runtime", "deploy", "cluster"],
    }))
    result = _run_cli("--json", "validate", "--config", str(config))
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert body["config"]["deploy_url"] == "http://localhost:8030"
    assert body["config"]["cluster_mode"] == "k3d"


def test_validate_rejects_bad_deploy_url(tmp_path: Path):
    config = tmp_path / "cli.json"
    config.write_text(json.dumps({
        "schema": "mcpfinder.cli.config/v1",
        "product": "mcpfinder",
        "runtime_url": "http://localhost:8040",
        "deploy_url": "ftp://nope",
        "allowed_scopes": ["runtime"],
    }))
    result = _run_cli("--json", "validate", "--config", str(config))
    assert result.returncode == 2
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "config_invalid"


# ---------------------------------------------------------------------------
# SSE consumption + output redaction (in-process unit tests, no live backend)
# ---------------------------------------------------------------------------


def test_sse_request_accumulates_events_and_stops_on_done(monkeypatch):
    import io
    import urllib.request

    from runtime import cli

    class _FakeResp:
        def __init__(self, lines: list[bytes]):
            self._buf = io.BytesIO(b"".join(lines))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            return iter(self._buf.readlines())

    stream = [
        b'data: {"step": "clone", "message": "cloning"}\n',
        b'data: {"step": "build", "api_key": "should-not-survive"}\n',
        b'data: {"step": "done", "endpoint": "http://acme-mcp:8000", "server_id": "srv_1"}\n',
        b'data: {"step": "after-done"}\n',
    ]

    def _fake_urlopen(req, timeout=0):  # noqa: ARG001
        return _FakeResp(stream)

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    out = cli._sse_request("http://localhost:8030/deploy", {"name": "acme-mcp"}, api_key="k")
    # Stops at the first done event; the trailing event is not consumed.
    assert out["event_count"] == 3
    assert out["errored"] is False
    assert out["final"]["step"] == "done"
    assert out["final"]["server_id"] == "srv_1"
    # Secret-looking fields in events are redacted.
    assert out["events"][1]["api_key"] == "[REDACTED]"


def test_sse_request_marks_intermediate_error_status_terminal(monkeypatch):
    import io
    import urllib.request

    from runtime import cli

    class _FakeResp:
        def __init__(self, lines):
            self._buf = io.BytesIO(b"".join(lines))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            return iter(self._buf.readlines())

    # Deploy service emits a fatal {step:build, status:error} then ends the stream.
    stream = [
        b'data: {"step": "clone", "status": "done", "msg": "ok"}\n',
        b'data: {"step": "build", "status": "error", "msg": "Docker build failed"}\n',
    ]
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: _FakeResp(stream))
    out = cli._sse_request("http://localhost:8030/deploy", {"name": "x"})
    assert out["errored"] is True
    assert out["final"]["step"] == "build"


def test_mcp_deploy_reports_failure_when_stream_errors(monkeypatch):
    import io
    import urllib.request

    from runtime import cli

    class _FakeResp:
        def __init__(self, lines):
            self._buf = io.BytesIO(b"".join(lines))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            return iter(self._buf.readlines())

    stream = [b'data: {"step": "build", "status": "error", "msg": "Docker build failed"}\n']
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: _FakeResp(stream))
    monkeypatch.setenv("MCPFINDER_API_KEY", "k")
    # A failed deploy must NOT report success — it raises CliError -> non-zero exit.
    rc = cli.main([
        "--json", "mcp", "deploy",
        "--repo-url", "https://github.com/acme/mcp",
        "--name", "acme-mcp",
    ])
    assert rc == 2


def test_redact_text_masks_secret_lines():
    from runtime import cli

    masked = cli._redact_text("PGPASSWORD=hunter2\nREGION=eu\ntoken: abc123")
    assert "hunter2" not in masked
    assert "abc123" not in masked
    assert "REGION=eu" in masked
