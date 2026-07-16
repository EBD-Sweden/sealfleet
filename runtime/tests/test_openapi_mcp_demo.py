import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_demo_openapi_assets_generate_manifest_schema_and_catalog_entry(tmp_path):
    import router
    from openapi_demo import create_demo_openapi_mcp

    router.manifests.clear()
    router.typed_manifests.clear()
    router.types_registry.clear()
    router.type_graph = router.TypeGraph()

    receipt = create_demo_openapi_mcp(
        {
            "mode": "public_demo",
            "tenant_id": "demo-sandbox",
            "workspace_id": "demo-external-evaluation",
            "spec_ref": "checked-in:fake-crm-openapi",
            "deploy_action": "dry_run",
            "output_dir": str(tmp_path),
        },
        register_runtime=router.register_generated_openapi_demo,
    )

    assert receipt["status"] == "created"
    assert receipt["trace_id"].startswith("demo-openapi-")
    assert receipt["safety"] == {
        "mode": "public_demo",
        "tenant_id": "demo-sandbox",
        "workspace_id": "demo-external-evaluation",
        "deploy_action": "dry_run",
        "data_classification": "fake-demo-only",
    }
    assert receipt["catalog_entry"]["name"] == "demo-fake-crm-mcp"
    assert receipt["catalog_entry"]["tenant_id"] == "demo-sandbox"
    assert receipt["catalog_entry"]["workspace_id"] == "demo-external-evaluation"
    assert receipt["catalog_entry"]["registered"] is True
    assert receipt["manifest"]["tools"][0]["name"] == "get_demo_customer"
    assert receipt["manifest"]["tools"][0]["inputSchema"]["required"] == ["customer_id"]
    assert receipt["manifest"]["tools"][0]["inputs"]["customer_id"]["type"] == "String"
    assert (tmp_path / "demo-fake-crm-mcp" / "mcp.yaml").exists()
    assert (tmp_path / "demo-fake-crm-mcp" / "wrapper.py").exists()

    assert "demo-fake-crm-mcp" in router.manifests
    assert "demo-fake-crm-mcp" in router.typed_manifests
    assert router.type_graph.tool_inputs[("demo-fake-crm-mcp", "get_demo_customer")]["customer_id"]["type"] == "String"


def test_demo_cli_default_writes_to_ignored_scratch_dir_without_tracked_fixture_changes():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "demo-openapi-to-mcp.py"), "--invoke"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    receipt = json.loads(result.stdout)

    assert receipt["artifact_dir"] == str(ROOT / "runtime" / ".generated" / "demo-fake-crm-mcp")
    assert receipt["invocation"]["classification"] == "fake-demo-only"

    status = subprocess.run(
        ["git", "status", "--short", "--", "runtime/generated/demo-fake-crm-mcp"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""


def test_generated_demo_tool_invocation_is_deterministic(tmp_path):
    from openapi_demo import create_demo_openapi_mcp, invoke_generated_demo_tool

    receipt = create_demo_openapi_mcp(
        {
            "mode": "public_demo",
            "tenant_id": "demo-sandbox",
            "workspace_id": "demo-external-evaluation",
            "spec_ref": "checked-in:fake-crm-openapi",
            "deploy_action": "dry_run",
            "output_dir": str(tmp_path),
        }
    )

    result = invoke_generated_demo_tool(
        receipt["artifact_dir"],
        "get_demo_customer",
        {"customer_id": "CUST-DEMO-001"},
    )

    assert result == {
        "classification": "fake-demo-only",
        "customer_id": "CUST-DEMO-001",
        "name": "Northwind Demo Supplies",
        "tier": "demo-gold",
        "open_invoices": 2,
    }


def test_public_demo_openapi_creation_denies_unsafe_inputs(tmp_path):
    from openapi_demo import DemoOpenAPIError, create_demo_openapi_mcp

    base = {
        "mode": "public_demo",
        "tenant_id": "demo-sandbox",
        "workspace_id": "demo-external-evaluation",
        "spec_ref": "checked-in:fake-crm-openapi",
        "deploy_action": "dry_run",
        "output_dir": str(tmp_path),
    }

    unsafe_cases = [
        ({"tenant_id": "prod-tenant"}, "demo_openapi_tenant_forbidden"),
        ({"workspace_id": "prod-workspace"}, "demo_openapi_workspace_forbidden"),
        ({"spec_ref": "https://example.com/openapi.yaml"}, "demo_openapi_external_spec_forbidden"),
        ({"deploy_action": "kubectl_apply"}, "demo_openapi_privileged_deploy_forbidden"),
        ({"secrets": {"api_key": "raw-secret-value"}}, "demo_openapi_raw_secrets_forbidden"),
    ]

    for override, code in unsafe_cases:
        request = {**base, **override}
        with pytest.raises(DemoOpenAPIError) as exc:
            create_demo_openapi_mcp(request)
        assert exc.value.code == code


def test_fake_openapi_spec_is_checked_in_and_fake_only():
    spec_path = ROOT / "runtime" / "openapi_demo" / "fake_crm_openapi.yaml"
    spec = yaml.safe_load(spec_path.read_text())

    assert spec["info"]["title"] == "Fake CRM Demo API"
    assert spec["servers"] == [{"url": "https://example.invalid/fake-crm"}]
    assert "/customers/{customer_id}" in spec["paths"]
    assert spec["x-mcpfinder-demo"]["data_classification"] == "fake-demo-only"
    assert spec["x-mcpfinder-demo"]["network"] == "disabled"
