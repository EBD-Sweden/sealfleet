"""Safe public-demo OpenAPI-to-MCP generator.

This module intentionally supports only the checked-in fake CRM OpenAPI spec.
It never fetches specs from the network, never accepts raw credentials, and
never performs a real deploy in public_demo mode unless an operator explicitly
uses a non-demo mode in future code.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import uuid
from pathlib import Path
from typing import Callable

import yaml

DEMO_TENANT = "demo-sandbox"
DEMO_WORKSPACE = "demo-external-evaluation"
DEMO_SPEC_REF = "checked-in:fake-crm-openapi"
DEMO_MCP_NAME = "demo-fake-crm-mcp"
DEMO_TOOL_NAME = "get_demo_customer"
SPEC_PATH = Path(__file__).resolve().parent / "openapi_demo" / "fake_crm_openapi.yaml"
DEFAULT_ARTIFACT_DIR = Path(__file__).resolve().parent / ".generated" / DEMO_MCP_NAME


class DemoOpenAPIError(ValueError):
    """Validation failure for the public demo OpenAPI creation flow."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail

    def to_dict(self) -> dict:
        return {"error": self.code, "detail": self.detail}


def _deny(code: str, detail: str) -> None:
    raise DemoOpenAPIError(code, detail)


def _validate_public_demo_request(request: dict) -> None:
    if request.get("mode") != "public_demo":
        _deny("demo_openapi_mode_required", "OpenAPI-to-MCP demo creation requires mode=public_demo")
    if request.get("tenant_id") != DEMO_TENANT:
        _deny("demo_openapi_tenant_forbidden", "public_demo OpenAPI creation is restricted to the demo-sandbox tenant")
    if request.get("workspace_id") != DEMO_WORKSPACE:
        _deny(
            "demo_openapi_workspace_forbidden",
            "public_demo OpenAPI creation is restricted to the demo-external-evaluation workspace",
        )

    spec_ref = str(request.get("spec_ref") or "")
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", spec_ref):
        _deny("demo_openapi_external_spec_forbidden", "public_demo mode never fetches OpenAPI specs from URLs")
    if spec_ref != DEMO_SPEC_REF:
        _deny("demo_openapi_spec_forbidden", "public_demo mode only permits the checked-in fake CRM OpenAPI spec")

    if request.get("secrets") or request.get("env_vars") or request.get("credentials"):
        _deny("demo_openapi_raw_secrets_forbidden", "public_demo OpenAPI creation accepts no raw secrets or credentials")

    deploy_action = request.get("deploy_action", "dry_run")
    if deploy_action != "dry_run" and not bool(request.get("allow_real_deploy")):
        _deny(
            "demo_openapi_privileged_deploy_forbidden",
            "public_demo OpenAPI creation is dry-run only unless an operator explicitly allows a real deploy",
        )


def _load_demo_spec() -> dict:
    spec = yaml.safe_load(SPEC_PATH.read_text())
    demo_meta = spec.get("x-mcpfinder-demo") or {}
    if demo_meta.get("data_classification") != "fake-demo-only":
        _deny("demo_openapi_spec_invalid", "checked-in demo spec must be classified fake-demo-only")
    if demo_meta.get("network") != "disabled" or demo_meta.get("credentials") != "disabled":
        _deny("demo_openapi_spec_invalid", "checked-in demo spec must disable network and credentials")
    return spec


def _schema_type(openapi_schema: dict) -> str:
    typ = str(openapi_schema.get("type") or "string")
    return {"string": "String", "integer": "Integer", "number": "Float", "boolean": "Boolean"}.get(typ, "String")


def _build_manifest(spec: dict) -> dict:
    operation = spec["paths"]["/customers/{customer_id}"]["get"]
    parameters = operation.get("parameters", [])
    required = [p["name"] for p in parameters if p.get("required")]
    properties = {
        p["name"]: {"type": p.get("schema", {}).get("type", "string"), "description": p.get("description", "")}
        for p in parameters
    }
    typed_inputs = {
        p["name"]: {
            "type": _schema_type(p.get("schema", {})),
            "required": bool(p.get("required")),
            "description": p.get("description", ""),
        }
        for p in parameters
    }
    output_props = operation["responses"]["200"]["content"]["application/json"]["schema"]["properties"]
    typed_outputs = {name: {"type": _schema_type(schema), "required": True} for name, schema in output_props.items()}
    return {
        "id": DEMO_MCP_NAME,
        "name": DEMO_MCP_NAME,
        "endpoint": "stdio://demo-generated-openapi/fake-crm",
        "transport": "stdio",
        "publishes": ["DemoCustomer"],
        "subscribes": [],
        "metadata": {
            "tenant": DEMO_TENANT,
            "workspace": DEMO_WORKSPACE,
            "data_classification": "fake-demo-only",
            "source": DEMO_SPEC_REF,
            "network": "disabled",
            "credentials": "disabled",
        },
        "tools": [
            {
                "name": operation["operationId"],
                "description": operation.get("description") or operation.get("summary", ""),
                "inputSchema": {
                    "type": "object",
                    "required": required,
                    "properties": properties,
                    "additionalProperties": False,
                },
                "inputs": typed_inputs,
                "outputs": typed_outputs,
                "tags": ["demo", "openapi", "fake-data"],
            }
        ],
    }


def _wrapper_source() -> str:
    return '''"""Generated fake CRM MCP wrapper for public demo use only."""

FAKE_CUSTOMERS = {
    "CUST-DEMO-001": {
        "classification": "fake-demo-only",
        "customer_id": "CUST-DEMO-001",
        "name": "Northwind Demo Supplies",
        "tier": "demo-gold",
        "open_invoices": 2,
    },
    "CUST-DEMO-002": {
        "classification": "fake-demo-only",
        "customer_id": "CUST-DEMO-002",
        "name": "Contoso Demo Manufacturing",
        "tier": "demo-silver",
        "open_invoices": 0,
    },
}


def get_demo_customer(customer_id: str) -> dict:
    if customer_id in FAKE_CUSTOMERS:
        return dict(FAKE_CUSTOMERS[customer_id])
    return {
        "classification": "fake-demo-only",
        "customer_id": customer_id,
        "name": "Unknown Fake Demo Customer",
        "tier": "demo-review",
        "open_invoices": 0,
    }
'''


def _write_artifacts(manifest: dict, output_dir: Path) -> dict:
    artifact_dir = output_dir / DEMO_MCP_NAME
    artifact_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = artifact_dir / "mcp.yaml"
    wrapper_path = artifact_dir / "wrapper.py"
    catalog_path = artifact_dir / "catalog_entry.json"
    manifest_path.write_text(yaml.dump(manifest, sort_keys=False))
    wrapper_path.write_text(_wrapper_source())
    return {
        "artifact_dir": str(artifact_dir),
        "manifest_path": str(manifest_path),
        "wrapper_path": str(wrapper_path),
        "catalog_path": str(catalog_path),
    }


def _catalog_entry(manifest: dict, artifact_paths: dict, trace_id: str) -> dict:
    return {
        "name": manifest["name"],
        "tenant_id": DEMO_TENANT,
        "workspace_id": DEMO_WORKSPACE,
        "description": "Generated from checked-in fake CRM OpenAPI spec (public demo only).",
        "tools": [tool["name"] for tool in manifest["tools"]],
        "data_classification": "fake-demo-only",
        "registered": True,
        "deploy": "dry_run",
        "trace_id": trace_id,
        "artifact_dir": artifact_paths["artifact_dir"],
    }


def create_demo_openapi_mcp(request: dict, *, register_runtime: Callable[[dict], dict] | None = None) -> dict:
    """Create demo MCP artifacts from the checked-in fake OpenAPI spec.

    Returns a traceable receipt with generated manifest, catalog metadata, and paths.
    """
    _validate_public_demo_request(request)
    spec = _load_demo_spec()
    manifest = _build_manifest(spec)
    output_dir = Path(request.get("output_dir") or DEFAULT_ARTIFACT_DIR.parent)
    artifact_paths = _write_artifacts(manifest, output_dir)
    spec_hash = hashlib.sha256(SPEC_PATH.read_bytes()).hexdigest()
    trace_id = f"demo-openapi-{uuid.uuid4().hex[:12]}"
    catalog_entry = _catalog_entry(manifest, artifact_paths, trace_id)
    Path(artifact_paths["catalog_path"]).write_text(json.dumps(catalog_entry, indent=2, sort_keys=True))
    runtime_registration = register_runtime(manifest) if register_runtime else {"registered": False, "reason": "no_runtime_callback"}
    return {
        "status": "created",
        "trace_id": trace_id,
        "spec_ref": DEMO_SPEC_REF,
        "spec_sha256": spec_hash,
        "artifact_dir": artifact_paths["artifact_dir"],
        "manifest": manifest,
        "catalog_entry": catalog_entry,
        "runtime_registration": runtime_registration,
        "audit_receipt": {
            "action": "openapi_demo.create_mcp",
            "result": "dry_run_registered",
            "trace_id": trace_id,
            "tenant_id": DEMO_TENANT,
            "workspace_id": DEMO_WORKSPACE,
        },
        "safety": {
            "mode": "public_demo",
            "tenant_id": DEMO_TENANT,
            "workspace_id": DEMO_WORKSPACE,
            "deploy_action": "dry_run",
            "data_classification": "fake-demo-only",
        },
    }


def invoke_generated_demo_tool(artifact_dir: str, tool_name: str, arguments: dict) -> dict:
    """Invoke a generated fake-data wrapper deterministically, without network."""
    if tool_name != DEMO_TOOL_NAME:
        _deny("demo_openapi_tool_not_found", f"Unknown generated demo tool: {tool_name}")
    wrapper_path = Path(artifact_dir) / "wrapper.py"
    if not wrapper_path.exists():
        _deny("demo_openapi_artifact_missing", "Generated wrapper.py is missing")
    module_spec = importlib.util.spec_from_file_location("generated_demo_fake_crm", wrapper_path)
    if module_spec is None or module_spec.loader is None:
        _deny("demo_openapi_artifact_invalid", "Generated wrapper.py cannot be imported")
    assert module_spec is not None and module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module.get_demo_customer(str(arguments.get("customer_id") or ""))
