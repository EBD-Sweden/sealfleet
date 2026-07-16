from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]


def test_demo_sandbox_assets_are_bounded_and_fake_only():
    manifest_path = ROOT / "runtime" / "manifests" / "demo-sandbox-mcp.yaml"
    pipeline_path = ROOT / "runtime" / "pipelines" / "v2" / "demo_sandbox_invoice_review.yaml"
    seed_path = ROOT / "db" / "seeds" / "010_demo_sandbox.sql"
    doc_path = ROOT / "docs" / "EXTERNAL_DEMO_QUICKSTART.md"
    network_policy_path = ROOT / "k8s" / "demo-sandbox-mcp.yaml"

    for path in (manifest_path, pipeline_path, seed_path, doc_path, network_policy_path):
        assert path.exists(), f"missing demo sandbox asset: {path.relative_to(ROOT)}"

    manifest = yaml.safe_load(manifest_path.read_text())
    pipeline = yaml.safe_load(pipeline_path.read_text())
    seed_sql = seed_path.read_text()
    quickstart = doc_path.read_text()
    network_policy = network_policy_path.read_text()

    assert manifest["id"] == "demo-sandbox-mcp"
    assert manifest["metadata"]["tenant"] == "demo-sandbox"
    assert manifest["metadata"]["workspace"] == "demo-external-evaluation"
    assert manifest["metadata"]["data_classification"] == "fake-demo-only"
    assert manifest["metadata"]["network_policy"] == "egress-deny"
    assert {tool["name"] for tool in manifest["tools"]} == {"summarize_fake_invoice", "score_fake_vendor"}

    assert pipeline["name"] == "demo_sandbox_invoice_review"
    assert isinstance(pipeline["inputs"], dict)
    assert pipeline["inputs"]["workspace"]["default"] == "demo-external-evaluation"
    assert pipeline["safety"]["tenant_scope"] == "demo-sandbox"
    assert pipeline["safety"]["workspace_scope"] == "demo-external-evaluation"
    assert pipeline["safety"]["sealed_inputs"] == "disabled-for-demo"
    assert pipeline["expected_output"]["classification"] == "fake-demo-only"
    assert all(step.get("mcp") == "demo-sandbox-mcp" and step.get("tool") for step in pipeline["steps"])

    assert "demo-sandbox" in seed_sql
    assert "demo.viewer@mcpfinder.dev" in seed_sql
    assert "fake-demo-only" in seed_sql
    assert "workspace_scope=demo-external-evaluation" in seed_sql
    assert "password_hash" not in seed_sql.lower()

    assert "kind: NetworkPolicy" in network_policy
    assert "demo-sandbox-mcp" in network_policy
    assert "policyTypes" in network_policy and "Egress" in network_policy

    for required in (
        "Sandbox safety boundaries",
        "Expected output",
        "Cleanup",
        "Quotas and rate limits",
        "curl -fsS http://localhost:3004/api/health",
        "X-Workspace-ID: demo-external-evaluation",
        "enforced by runtime auth metadata",
    ):
        assert required in quickstart


def test_demo_pipeline_is_registered_as_executable_v2_definition():
    import router

    router.v2_pipelines.clear()
    router._load_yaml_pipeline_v2()

    assert "demo_sandbox_invoice_review" in router.v2_pipelines
    pipeline = router.v2_pipelines["demo_sandbox_invoice_review"]
    assert isinstance(pipeline["inputs"], dict)
    assert all(step.get("mcp") == "demo-sandbox-mcp" and step.get("tool") for step in pipeline["steps"])


@pytest.mark.asyncio
async def test_demo_v2_pipeline_runs_with_sandbox_context(monkeypatch):
    import router

    pipeline = yaml.safe_load((ROOT / "runtime" / "pipelines" / "v2" / "demo_sandbox_invoice_review.yaml").read_text())
    router._demo_sandbox_run_timestamps.clear()

    async def fake_call_mcp(client, mcp_name, tool, inputs, **kwargs):
        assert mcp_name == "demo-sandbox-mcp"
        if tool == "summarize_fake_invoice":
            return {"invoice_id": inputs["invoice_id"], "status": "review_required"}
        if tool == "score_fake_vendor":
            return {"vendor_name": inputs["vendor_name"], "score": 72, "tier": "demo-medium"}
        raise AssertionError(f"unexpected tool {tool}")

    monkeypatch.setattr(router, "_call_mcp", fake_call_mcp)

    result = await router._run_v2_pipeline(
        pipeline,
        {"workspace": "demo-external-evaluation"},
        tenant_id="demo-sandbox",
        workspace_id="demo-external-evaluation",
        body_size_bytes=512,
    )

    assert "error" not in result
    assert result["output"]["classification"] == "fake-demo-only"
    assert result["output"]["invoice_summary"]["status"] == "review_required"
    assert result["output"]["vendor_score"]["tier"] == "demo-medium"


def test_demo_v2_pipeline_run_endpoint_executes_and_enforces_sandbox_context(monkeypatch):
    import router

    router.v2_pipelines.clear()
    router._demo_sandbox_run_timestamps.clear()
    router._load_yaml_pipeline_v2()
    monkeypatch.setattr(router, "REQUIRE_AUTH", False)
    monkeypatch.setattr(router, "get_tenant_id", lambda request: request.headers.get("X-Tenant-ID", "system"))
    monkeypatch.setattr(router, "get_workspace_id", lambda request: request.headers.get("X-Workspace-ID", ""))

    async def fake_call_mcp(client, mcp_name, tool, inputs, **kwargs):
        assert mcp_name == "demo-sandbox-mcp"
        if tool == "summarize_fake_invoice":
            return {"classification": "fake-demo-only", "invoice_id": inputs["invoice_id"], "status": "review_required"}
        if tool == "score_fake_vendor":
            return {"classification": "fake-demo-only", "vendor_name": inputs["vendor_name"], "score": 72, "tier": "demo-medium"}
        raise AssertionError(f"unexpected tool {tool}")

    monkeypatch.setattr(router, "_call_mcp", fake_call_mcp)
    client = TestClient(router.app)
    payload = {"pipeline": "demo_sandbox_invoice_review", "inputs": {"workspace": "demo-external-evaluation"}}

    ok = client.post(
        "/v2/pipelines/run",
        json=payload,
        headers={"X-Tenant-ID": "demo-sandbox", "X-Workspace-ID": "demo-external-evaluation"},
    )
    assert ok.status_code == 200
    assert ok.json()["output"]["classification"] == "fake-demo-only"

    wrong_tenant = client.post(
        "/v2/pipelines/run",
        json=payload,
        headers={"X-Tenant-ID": "other", "X-Workspace-ID": "demo-external-evaluation"},
    )
    assert wrong_tenant.status_code == 200
    assert wrong_tenant.json()["error"] == "demo_sandbox_tenant_forbidden"


def test_demo_v2_pipeline_enforces_tenant_workspace_body_and_run_quota():
    import router

    pipeline = yaml.safe_load((ROOT / "runtime" / "pipelines" / "v2" / "demo_sandbox_invoice_review.yaml").read_text())

    wrong_tenant = router._enforce_demo_sandbox_boundary(
        pipeline,
        {"workspace": "demo-external-evaluation"},
        tenant_id="other-tenant",
        body_size_bytes=512,
    )
    assert wrong_tenant and wrong_tenant["error"] == "demo_sandbox_tenant_forbidden"

    wrong_workspace = router._enforce_demo_sandbox_boundary(
        pipeline,
        {"workspace": "production"},
        tenant_id="demo-sandbox",
        body_size_bytes=512,
    )
    assert wrong_workspace and wrong_workspace["error"] == "demo_sandbox_workspace_forbidden"

    oversized = router._enforce_demo_sandbox_boundary(
        pipeline,
        {"workspace": "demo-external-evaluation"},
        tenant_id="demo-sandbox",
        body_size_bytes=65537,
    )
    assert oversized and oversized["error"] == "demo_sandbox_body_too_large"

    router._demo_sandbox_run_timestamps.clear()
    assert router._enforce_demo_sandbox_boundary(
        pipeline,
        {"workspace": "demo-external-evaluation"},
        tenant_id="demo-sandbox",
        body_size_bytes=512,
    ) is None
    rate_limited = None
    for _ in range(10):
        rate_limited = router._enforce_demo_sandbox_boundary(
            pipeline,
            {"workspace": "demo-external-evaluation"},
            tenant_id="demo-sandbox",
            body_size_bytes=512,
        )
    assert rate_limited and rate_limited["error"] == "demo_sandbox_rate_limited"


def test_k8s_smoke_script_cleanup_is_demo_scoped_and_dry_run_by_default():
    script_path = ROOT / "scripts" / "k8s-demo-smoke.sh"
    script = script_path.read_text()

    assert "DRY_RUN=\"${DRY_RUN:-1}\"" in script
    assert "NAMESPACE=\"${NAMESPACE:-demo-sandbox}\"" in script
    assert "SELECTOR=\"${SELECTOR:-app.kubernetes.io/part-of=mcpfinder-demo-sandbox}\"" in script
    assert "Refusing cleanup outside demo namespace" in script
    assert "Refusing cleanup without demo selector" in script
    assert "delete pod" in script
    assert "delete job" in script
    assert "-l" in script and "$SELECTOR" in script
    assert "ImagePullBackOff" in script
    assert "ContainerStatusUnknown" in script
