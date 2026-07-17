"""Sealfleet Secure Runtime Router.

The kernel layer between agents and MCP tools. Every message goes through
named channels with policy enforcement and audit logging.

Includes type-based tool resolution: tools declare typed inputs/outputs,
the runtime builds a directed type graph and resolves chains automatically.
"""

from __future__ import annotations

import asyncio
import base64
from cryptography.fernet import Fernet as _Fernet
import fnmatch
import hashlib
import ipaddress
import json
import os
import re
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
from pathlib import Path
from typing import Mapping

import httpx
import yaml
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel

_REPO_ROOT = Path(__file__).resolve().parent.parent
_AUTH_PACKAGE_SRC = _REPO_ROOT / "packages" / "mcpfinder-auth" / "src"
if _AUTH_PACKAGE_SRC.exists() and str(_AUTH_PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(_AUTH_PACKAGE_SRC))

try:
    from mcpfinder_auth.enterprise import enterprise_contract_v1 as _enterprise_contract_v1  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - deployment packaging guard
    _enterprise_contract_v1 = None

from policy_hooks import (  # noqa: E402
    RuntimeHookContext,
    build_runtime_hook_manager,
)
import licensing  # noqa: E402 — open-core entitlement / feature flags

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class McpAccessPolicy(BaseModel):
    """Declarative access gate carried in the MCP manifest.

    When either list is non-empty, user-identity callers (portal JWT or
    delegated API-key identity) must hold one of the named platform roles or
    present one of the named IdP group claims. API keys without a delegated
    user identity are scoped by action_permissions instead and are not
    subject to manifest role gating.
    """

    allowed_roles: list[str] = []
    allowed_groups: list[str] = []

    @property
    def restricted(self) -> bool:
        return bool(self.allowed_roles or self.allowed_groups)


class McpManifest(BaseModel):
    name: str
    endpoint: str
    publishes: list[str]
    subscribes: list[str]
    tools: list[str]
    transport: str = "http"          # "http" or "stdio"
    image: str | None = None         # required when transport=stdio
    access: McpAccessPolicy | None = None
    # tool name (or "*" for MCP-wide) -> output dot paths declared as PII;
    # the runtime redacts these from every result at the execution boundary.
    pii_fields: dict[str, list[str]] = {}



class PipelineStep(BaseModel):
    mcp: str
    tool: str
    inputs: dict


class PipelineRequest(BaseModel):
    steps: list[PipelineStep]



# ---------------------------------------------------------------------------
# Named Pipeline models
# ---------------------------------------------------------------------------

class PipelineStageSchema(BaseModel):
    name: str
    mcp: str
    tool: str
    input_channel: str | None = None   # reads from this channel
    output_channel: str | None = None  # publishes to this channel
    input_type: str | None = None      # typed input (e.g. "String")
    output_type: str | None = None     # typed output (e.g. "WeatherData")


class NamedPipeline(BaseModel):
    name: str                           # e.g. "my_pipeline"
    description: str
    inputs: dict[str, str | dict]      # param_name -> type string or {type, description}
    stages: list[PipelineStageSchema]
    output_stage: str                  # name of the stage whose result is the final output
    tags: list[str] = []
    created_at: str = ""


class RegisterPipelineRequest(BaseModel):
    pipeline: NamedPipeline


class RunNamedPipelineRequest(BaseModel):
    inputs: dict                        # e.g. {"location": "Stockholm"}


class CallPipelineToolRequest(BaseModel):
    name: str
    arguments: dict


class SealedInputRequest(BaseModel):
    label: str
    value: str
    expires_in_seconds: int = 300


class ExternalAgentAuth(BaseModel):
    """Sealed auth reference for tenant-owned external agents.

    Raw tokens are resolved only inside the runtime boundary during invocation.
    """

    type: str = "bearer"
    sealed_handle: str


class ExternalAgentRegistrationRequest(BaseModel):
    name: str
    description: str = ""
    endpoint: str
    protocol: str = "json_rpc"
    auth: ExternalAgentAuth | None = None
    timeout_ms: int = 1000


# ---------------------------------------------------------------------------
# Type Graph
# ---------------------------------------------------------------------------

class TypeGraph:
    """
    Directed graph: type -> list of (mcp_name, tool_name) that produce it.
    Built from registered manifests at startup and on manifest registration.
    """
    def __init__(self):
        self.producers: dict[str, list[tuple[str, str]]] = {}  # type -> [(mcp, tool)]
        self.consumers: dict[str, list[tuple[str, str]]] = {}  # type -> [(mcp, tool)]
        self.tool_inputs: dict[tuple, dict] = {}   # (mcp, tool) -> {param: TypeRef}
        self.tool_outputs: dict[tuple, dict] = {}  # (mcp, tool) -> {param: TypeRef}

    def register_manifest(self, manifest: dict):
        """Parse a typed manifest and update the graph."""
        mcp_name = manifest["name"]
        for tool in manifest.get("tools", []):
            if isinstance(tool, str):
                continue  # skip untyped tool entries
            tool_name = tool["name"]
            key = (mcp_name, tool_name)
            # Register outputs as producers
            for param, typedef in tool.get("outputs", {}).items():
                t = typedef["type"]
                self.producers.setdefault(t, []).append(key)
            self.tool_outputs[key] = tool.get("outputs", {})
            # Register inputs as consumers
            for param, typedef in tool.get("inputs", {}).items():
                t = typedef["type"]
                self.consumers.setdefault(t, []).append(key)
            self.tool_inputs[key] = tool.get("inputs", {})

    def resolve(self, output_type: str, raw_inputs: dict, types_reg: dict,
                *, strict: bool = True) -> list[dict]:
        """
        Given desired output_type and raw_inputs (primitive values),
        return ordered list of steps to execute.

        Algorithm:
        1. Find which (mcp, tool) produces output_type
        2. For each of its inputs:
           - If primitive type and value in raw_inputs: bind directly
           - If domain type: recursively resolve producer
        3. Build ordered step list (topological order)
        4. Raise ValueError if chain is broken or ambiguous

        When strict=False, missing required primitive inputs are skipped
        (used for doc generation / chain discovery).
        """
        return self._resolve_type(output_type, raw_inputs, types_reg,
                                  visited=set(), strict=strict)

    def _resolve_type(self, type_name: str, raw_inputs: dict,
                      types_reg: dict, visited: set,
                      strict: bool = True) -> list[dict]:
        if type_name in visited:
            raise ValueError(f"Circular dependency detected for type: {type_name}")

        # Primitive types don't need a producer
        type_def = types_reg.get(type_name, {})
        if type_def.get("primitive"):
            return []

        producers = self.producers.get(type_name, [])
        if not producers:
            raise ValueError(
                f"No tool produces type '{type_name}'. "
                "Register a manifest that outputs this type."
            )
        # First registered wins (future: scoring/preference system)
        mcp_name, tool_name = producers[0]
        visited = visited | {type_name}

        steps = []
        tool_inputs = self.tool_inputs.get((mcp_name, tool_name), {})
        bound_inputs: dict = {}

        for param, typedef in tool_inputs.items():
            param_type = typedef["type"]
            is_primitive = types_reg.get(param_type, {}).get("primitive", False)
            is_optional = not typedef.get("required", True)
            from_channel = typedef.get("source") == "channel"

            if is_primitive:
                if param in raw_inputs:
                    bound_inputs[param] = raw_inputs[param]
                elif not is_optional and strict:
                    raise ValueError(
                        f"Required input '{param}' (type {param_type}) "
                        f"not provided for {mcp_name}.{tool_name}"
                    )
            elif from_channel:
                # Channel-sourced: resolve the producer of this type so
                # the chain includes the upstream step.
                sub_steps = self._resolve_type(param_type, raw_inputs, types_reg, visited, strict)
                steps.extend(sub_steps)
                bound_inputs[f"_channel_{param}"] = typedef.get("channel")
            else:
                # Domain type: resolve recursively
                sub_steps = self._resolve_type(param_type, raw_inputs, types_reg, visited, strict)
                steps.extend(sub_steps)
                bound_inputs[f"_resolved_{param}"] = param_type

        steps.append({
            "mcp": mcp_name,
            "tool": tool_name,
            "inputs": bound_inputs,
            "output_type": type_name,
        })
        return steps

    def validate_all_manifests(self, all_manifests: dict, types_reg: dict) -> list[str]:
        """
        Validate all registered manifests against the type registry.
        Returns list of warnings/errors.
        """
        issues = []
        for (mcp, tool), outputs in self.tool_outputs.items():
            for param, typedef in outputs.items():
                t = typedef["type"]
                if t not in types_reg:
                    issues.append(f"WARN: {mcp}.{tool} outputs unknown type '{t}'")
        for (mcp, tool), inputs in self.tool_inputs.items():
            for param, typedef in inputs.items():
                t = typedef["type"]
                if t not in types_reg:
                    issues.append(f"WARN: {mcp}.{tool} input '{param}' references unknown type '{t}'")
        return issues


# ---------------------------------------------------------------------------
# Pipeline doc generation
# ---------------------------------------------------------------------------

def _get_required_inputs(chain: list[dict], type_graph: TypeGraph,
                         types_reg: dict) -> dict[str, str]:
    """Collect all primitive inputs required across the chain."""
    required = {}
    for step in chain:
        key = (step["mcp"], step["tool"])
        tool_inputs = type_graph.tool_inputs.get(key, {})
        for param, typedef in tool_inputs.items():
            param_type = typedef["type"]
            if types_reg.get(param_type, {}).get("primitive"):
                if typedef.get("required", True):
                    required[param] = param_type
    return required


def generate_pipeline_doc(output_type: str, chain: list[dict],
                          types_reg: dict, type_graph: TypeGraph) -> str:
    """Generate a PIPELINE.md for a resolvable output type."""
    type_def = types_reg.get(output_type, {})
    description = type_def.get("description", output_type)
    required = _get_required_inputs(chain, type_graph, types_reg)

    # Build type flow diagram
    flow_parts = []
    for param_name, param_type in required.items():
        flow_parts.append(f"{param_type}({param_name})")
    flow_line_top = " ──► ".join(flow_parts + [step["output_type"] for step in chain])
    flow_line_bot = "".join(
        f"{'':>{len(p) + 5}}{step['tool']}\n{'':>{len(p) + 5}}({step['mcp']})"
        for p, step in zip(flow_parts, chain)
    ) if flow_parts else ""

    # Required inputs table
    input_rows = ""
    for param_name, param_type in required.items():
        # Try to find description from tool inputs
        desc = param_name
        for step in chain:
            key = (step["mcp"], step["tool"])
            tool_inputs = type_graph.tool_inputs.get(key, {})
            if param_name in tool_inputs:
                desc = tool_inputs[param_name].get("description", param_name)
                break
        input_rows += f"| {param_name} | {param_type} | {desc} |\n"

    # Chain steps
    steps_text = ""
    for i, step in enumerate(chain, 1):
        key = (step["mcp"], step["tool"])
        tool_outputs = type_graph.tool_outputs.get(key, {})
        tool_inputs = type_graph.tool_inputs.get(key, {})
        steps_text += f"{i}. **{step['mcp']}.{step['tool']}** → produces `{step['output_type']}`\n"
        # Check for channel_out/channel_in
        for param, typedef in tool_inputs.items():
            if typedef.get("source") == "channel":
                steps_text += f"   - Reads from channel: `{typedef['channel']}`\n"

    # Output schema
    fields = type_def.get("fields", {})
    schema_lines = {}
    for fname, fdef in fields.items():
        ftype = fdef.get("type", "string").lower()
        if fdef.get("array"):
            schema_lines[fname] = f'["{ftype}"]'
        else:
            schema_lines[fname] = f'"{ftype}"'
    schema_json = "{\n" + ",\n".join(
        f'  "{k}": {v}' for k, v in schema_lines.items()
    ) + "\n}"

    # Example curl inputs
    example_inputs = {}
    for pname, ptype in required.items():
        if ptype == "String":
            example_inputs[pname] = "Stockholm"
        elif ptype == "Integer":
            example_inputs[pname] = 30
        elif ptype == "Float":
            example_inputs[pname] = 0.0
    curl_body = json.dumps({"output_type": output_type, "inputs": example_inputs})

    doc = f"""# Pipeline: {output_type}
Auto-generated — do not edit manually. Re-generated on runtime startup.

## What it produces
{description}.

## Type Flow
```
{flow_line_top}
```

## Required Inputs
| Name | Type | Description |
|------|------|-------------|
{input_rows}
## Chain Steps
{steps_text}
## Call this pipeline
```bash
curl -X POST http://localhost:8040/resolve \\
  -H "Content-Type: application/json" \\
  -d '{curl_body}'
```

## Output Schema
```json
{schema_json}
```
"""
    return doc


def generate_pipeline_yaml(output_type: str, chain: list[dict],
                           type_graph: TypeGraph, types_reg: dict) -> dict:
    """Generate machine-readable pipeline.yaml for an output type."""
    required = _get_required_inputs(chain, type_graph, types_reg)
    steps = []
    for step in chain:
        entry: dict = {
            "mcp": step["mcp"],
            "tool": step["tool"],
            "output_type": step["output_type"],
        }
        # Find channel info from the raw tool definition
        key = (step["mcp"], step["tool"])
        tool_inputs = type_graph.tool_inputs.get(key, {})
        for param, typedef in tool_inputs.items():
            if typedef.get("source") == "channel":
                entry["channel_in"] = typedef["channel"]
        # Check tool outputs for channel_out from the original manifest
        # We look at typed_manifests for channel_out
        for name, raw in typed_manifests.items():
            if name == step["mcp"]:
                for t in raw.get("tools", []):
                    if isinstance(t, dict) and t.get("name") == step["tool"]:
                        if "channel_out" in t:
                            entry["channel_out"] = t["channel_out"]
        steps.append(entry)

    return {
        "output_type": output_type,
        "required_inputs": {k: v for k, v in required.items()},
        "chain": steps,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def write_pipeline_docs(type_graph: TypeGraph, types_reg: dict):
    """Generate PIPELINE.md and pipeline.yaml for all resolvable output types."""
    pipelines_dir = Path(__file__).parent / "pipelines"
    count = 0
    for type_name in type_graph.producers:
        type_def = types_reg.get(type_name, {})
        if type_def.get("primitive"):
            continue
        try:
            chain = type_graph.resolve(type_name, {}, types_reg, strict=False)
        except ValueError:
            continue
        if not chain:
            continue

        out_dir = pipelines_dir / type_name
        out_dir.mkdir(parents=True, exist_ok=True)

        md = generate_pipeline_doc(type_name, chain, types_reg, type_graph)
        (out_dir / "PIPELINE.md").write_text(md)

        yml = generate_pipeline_yaml(type_name, chain, type_graph, types_reg)
        (out_dir / "pipeline.yaml").write_text(
            yaml.dump(yml, default_flow_style=False, sort_keys=False)
        )
        count += 1
    print(f"[router] generated pipeline docs for {count} output types")


# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------

manifests: dict[str, McpManifest] = {}
typed_manifests: dict[str, dict] = {}       # raw manifest dicts for typed tools
types_registry: dict[str, dict] = {}
type_graph = TypeGraph()
_yaml_seeded_endpoints: dict[str, str] = {}  # mcp-name -> endpoint from YAML (authoritative)
_yaml_seeded_access: dict[str, "McpAccessPolicy"] = {}  # mcp-name -> access gate from YAML (authoritative)
_yaml_seeded_pii_fields: dict[str, dict[str, list[str]]] = {}  # mcp-name -> pii declarations from YAML


# ---------------------------------------------------------------------------
# SSRF guard for caller-supplied MCP / external-agent endpoints
# ---------------------------------------------------------------------------
# Optional strict allowlist: comma-separated host names / suffixes. When set,
# ONLY endpoints whose host matches are permitted (production lock-down). When
# unset, the block-list below applies (block loopback, link-local/cloud
# metadata, reserved/multicast) while allowing normal in-cluster targets.
_ENDPOINT_HOST_ALLOWLIST = [
    h.strip().lower() for h in os.getenv("MCPFINDER_ENDPOINT_HOST_ALLOWLIST", "").split(",") if h.strip()
]


def _endpoint_host_allowed(host: str) -> bool:
    host = host.lower()
    for entry in _ENDPOINT_HOST_ALLOWLIST:
        if host == entry or host.endswith("." + entry.lstrip(".")) or (entry.startswith(".") and host.endswith(entry)):
            return True
    return False


def _ip_is_blocked(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    # Block loopback, link-local (169.254.0.0/16 + fe80::/10 — the cloud
    # metadata range), unspecified, multicast, and reserved. Private RFC1918 /
    # cluster IPs stay allowed: the router's job is calling in-cluster MCPs.
    return (
        addr.is_loopback
        or addr.is_link_local
        or addr.is_unspecified
        or addr.is_multicast
        or addr.is_reserved
    )


def _assert_safe_endpoint(url: str, *, resolve: bool = True) -> None:
    """Reject SSRF-dangerous MCP/agent endpoints. Raises HTTPException(400).

    Always enforced: scheme must be http/https; host must be present; no
    loopback / link-local / cloud-metadata / reserved targets. With
    MCPFINDER_ENDPOINT_HOST_ALLOWLIST set, the host must additionally match the
    allowlist. `resolve=True` also checks every DNS-resolved IP (best-effort;
    unresolvable hosts are allowed at registration and re-checked at fetch).
    """
    from urllib.parse import urlparse

    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(400, "endpoint scheme must be http or https")
    host = parsed.hostname
    if not host:
        raise HTTPException(400, "endpoint must include a host")

    if _ENDPOINT_HOST_ALLOWLIST and not _endpoint_host_allowed(host):
        raise HTTPException(400, f"endpoint host '{host}' is not in the allowlist")

    # Literal-IP host: block directly.
    try:
        ipaddress.ip_address(host)
        if _ip_is_blocked(host):
            raise HTTPException(400, f"endpoint host '{host}' targets a blocked address range")
        return
    except ValueError:
        pass  # hostname, not a literal IP

    if resolve:
        import socket
        try:
            infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
        except OSError:
            return  # unresolvable now — allowed at registration, re-checked at fetch
        for info in infos:
            ip = info[4][0]
            if _ip_is_blocked(ip):
                raise HTTPException(400, f"endpoint host '{host}' resolves to a blocked address ({ip})")


def _parse_manifest_access(raw: object) -> "McpAccessPolicy | None":
    """Parse an optional manifest `access:` block into an McpAccessPolicy."""
    if not isinstance(raw, dict):
        return None
    roles = raw.get("allowed_roles") or []
    groups = raw.get("allowed_groups") or []
    if not isinstance(roles, list):
        roles = [roles]
    if not isinstance(groups, list):
        groups = [groups]
    policy = McpAccessPolicy(
        allowed_roles=[str(r).strip() for r in roles if str(r).strip()],
        allowed_groups=[str(g).strip() for g in groups if str(g).strip()],
    )
    return policy if policy.restricted else None


def _parse_manifest_pii_fields(data: dict) -> dict[str, list[str]]:
    """Collect PII output-field declarations from a raw manifest dict.

    Supported forms:
      pii_fields: [customer.email]          # MCP-wide, stored under "*"
      tools:
        - name: get_customer
          pii_fields: [customer.ssn]        # per-tool
    """
    declared: dict[str, list[str]] = {}
    mcp_level = data.get("pii_fields") or []
    if isinstance(mcp_level, (list, tuple)):
        paths = [str(p).strip() for p in mcp_level if str(p).strip()]
        if paths:
            declared["*"] = paths
    for t in data.get("tools", []):
        if isinstance(t, dict) and t.get("pii_fields"):
            paths = [str(p).strip() for p in (t.get("pii_fields") or []) if str(p).strip()]
            if paths:
                declared[str(t.get("name", ""))] = paths
    declared.pop("", None)
    return declared


def _merge_seeded_manifest_guards(manifest: "McpManifest") -> None:
    """Re-apply YAML-seeded access/PII gates onto a (re-)registered manifest.

    Self-registering MCPs may ADD declarations but can never drop or weaken
    what the operator declared in the seeded YAML.
    """
    seeded_access = _yaml_seeded_access.get(manifest.name)
    if seeded_access is not None:
        if manifest.access is None:
            manifest.access = seeded_access
        else:
            manifest.access = McpAccessPolicy(
                allowed_roles=sorted(set(seeded_access.allowed_roles) & set(manifest.access.allowed_roles))
                or seeded_access.allowed_roles,
                allowed_groups=sorted(set(seeded_access.allowed_groups) & set(manifest.access.allowed_groups))
                or seeded_access.allowed_groups,
            )
    seeded_pii = _yaml_seeded_pii_fields.get(manifest.name)
    if seeded_pii:
        merged = {k: list(v) for k, v in manifest.pii_fields.items()}
        for tool, paths in seeded_pii.items():
            merged[tool] = sorted(set(merged.get(tool, [])) | set(paths))
        manifest.pii_fields = merged


named_pipelines: dict[str, NamedPipeline] = {}
# Extra pipeline listing entries contributed by optional extensions (see
# _load_internal_extensions at the bottom of this module) — same dict shape
# as the entries built in list_pipelines.
EXTRA_PIPELINE_LISTINGS: list[dict] = []
_registry_item_tenants: dict[str, str] = {}
external_agents: dict[str, dict] = {}
_external_agent_rate_limits: dict[tuple[str, str], list[float]] = {}


# ---------------------------------------------------------------------------
# Typed I/O Mismatch Checking
# ---------------------------------------------------------------------------

def _get_tool_schemas(mcp_name: str, tool_name: str) -> tuple[dict | None, dict | None]:
    """Return (input_schema, output_schema) for a tool from the type graph."""
    key = (mcp_name, tool_name)
    inp = type_graph.tool_inputs.get(key)
    out = type_graph.tool_outputs.get(key)
    return (inp, out)


def _compare_schemas(out_schema: dict | None, inp_schema: dict | None) -> list[dict]:
    """Compare output schema of stage N with input schema of stage N+1.

    Returns list of {field, severity, message}.
    """
    if out_schema is None or inp_schema is None:
        return []

    mismatches: list[dict] = []
    out_fields = {k: v for k, v in out_schema.items()}
    inp_fields = {k: v for k, v in inp_schema.items()}

    for field, typedef in inp_fields.items():
        required = typedef.get("required", True)
        if field not in out_fields:
            if required:
                mismatches.append({
                    "field": field,
                    "severity": "error",
                    "message": f"Required input '{field}' (type {typedef.get('type', '?')}) missing from upstream output",
                })
            else:
                mismatches.append({
                    "field": field,
                    "severity": "warning",
                    "message": f"Optional input '{field}' not provided by upstream output",
                })
        else:
            out_type = out_fields[field].get("type", "")
            inp_type = typedef.get("type", "")
            if out_type and inp_type and out_type != inp_type:
                mismatches.append({
                    "field": field,
                    "severity": "warning",
                    "message": f"Type mismatch: upstream outputs '{out_type}' but downstream expects '{inp_type}'",
                })

    return mismatches


def _validate_pipeline_types(pipeline: NamedPipeline) -> list[dict]:
    """Validate type compatibility between consecutive pipeline stages."""
    warnings: list[dict] = []
    stages = pipeline.stages

    for i in range(len(stages) - 1):
        s_from = stages[i]
        s_to = stages[i + 1]

        _, out_schema = _get_tool_schemas(s_from.mcp, s_from.tool)
        inp_schema, _ = _get_tool_schemas(s_to.mcp, s_to.tool)

        mismatches = _compare_schemas(out_schema, inp_schema)
        has_errors = any(m["severity"] == "error" for m in mismatches)
        has_warnings = any(m["severity"] == "warning" for m in mismatches)

        warnings.append({
            "stage_from": s_from.name,
            "stage_to": s_to.name,
            "mismatches": mismatches,
            "has_errors": has_errors,
            "has_warnings": has_warnings,
        })

    return warnings

# ---------------------------------------------------------------------------
# Policy Engine
# ---------------------------------------------------------------------------

class PolicyEngine:
    """Simple YAML rules engine for tool call authorization."""

    def __init__(self):
        self.rules: list[dict] = []
        self._load()

    def _load(self):
        path = Path(__file__).parent / "policies" / "default.yaml"
        if not path.exists():
            print("[policy] no policy file found, default-allow")
            return
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
            self.rules = data.get("rules", [])
            print(f"[policy] loaded {len(self.rules)} rules from {path}")
        except Exception as e:
            print(f"[policy] failed to load: {e}")

    def check(self, *, mcp: str, tool: str, user_id: str = "system") -> dict:
        """
        Evaluate policy for a tool call.
        Returns: {"action": "allow"|"deny"|"require_confirm", "rule_id": str, "reason": str}
        """
        for rule in self.rules:
            match = rule.get("match", {})
            tool_pattern = match.get("tool_pattern", "*")
            mcp_pattern = match.get("mcp_pattern", "*")

            if (fnmatch.fnmatch(tool, tool_pattern) and
                    fnmatch.fnmatch(mcp, mcp_pattern)):
                return {
                    "action": rule.get("action", "allow"),
                    "rule_id": rule.get("id", "unknown"),
                    "reason": rule.get("reason", ""),
                }
        return {"action": "allow", "rule_id": "default", "reason": "no matching rule"}


policy_engine = PolicyEngine()


# ---------------------------------------------------------------------------
# Scale-to-zero manager
# ---------------------------------------------------------------------------

# Mapping from MCP manifest name → k8s deployment name (used by scale-to-zero).
# Add entries when your MCP's manifest name differs from its k8s deployment name.
MCP_DEPLOYMENT_MAP: dict[str, str] = {}


class ScaleManager:
    """Manages scale-to-zero for MCP deployments via the k8s API."""

    def __init__(self):
        self.enabled = os.getenv("K8S_SCALE_TO_ZERO", "false").lower() == "true"
        self.idle_timeout = int(os.getenv("MCP_IDLE_TIMEOUT", "300"))
        self.namespace = os.getenv("K8S_NAMESPACE", "default")
        self.last_call: dict[str, float] = {}   # deployment_name → timestamp
        self._apps_v1 = None
        self._core_v1 = None

    def _init_k8s(self):
        """Lazily initialise the kubernetes client (in-cluster config)."""
        if self._apps_v1 is not None:
            return
        try:
            from kubernetes import client, config as k8s_config
            k8s_config.load_incluster_config()
            self._apps_v1 = client.AppsV1Api()
            self._core_v1 = client.CoreV1Api()
            print("[scale-manager] k8s client initialised (in-cluster)")
        except Exception as e:
            print(f"[scale-manager] k8s init failed: {e} — scale-to-zero disabled")
            self.enabled = False

    def get_deployment_name(self, mcp_name: str) -> str | None:
        """Map MCP manifest name to k8s deployment name."""
        return MCP_DEPLOYMENT_MAP.get(mcp_name)

    async def ensure_running(self, mcp_name: str) -> bool:
        """
        Ensure the MCP's deployment has replicas >= 1.
        Returns True if the MCP is ready, False if could not start.
        Blocks until ready (max 30s).
        """
        if not self.enabled:
            return True

        deploy_name = self.get_deployment_name(mcp_name)
        if not deploy_name:
            return True  # Not managed — assume always running

        self._init_k8s()
        if not self.enabled:
            return True

        try:
            deploy = await asyncio.to_thread(
                self._apps_v1.read_namespaced_deployment,
                name=deploy_name, namespace=self.namespace,
            )
            current_replicas = deploy.spec.replicas or 0

            if current_replicas == 0:
                print(f"[scale-manager] scaling up {deploy_name} from 0")
                await asyncio.to_thread(
                    self._apps_v1.patch_namespaced_deployment_scale,
                    name=deploy_name,
                    namespace=self.namespace,
                    body={"spec": {"replicas": 1}},
                )
                await self._wait_for_ready(deploy_name, timeout=30)

            self.last_call[deploy_name] = time.time()
            return True

        except Exception as e:
            print(f"[scale-manager] ensure_running error for {deploy_name}: {e}")
            return True  # Don't block the call on scale error

    async def _wait_for_ready(self, deploy_name: str, timeout: int = 30):
        """Poll until this deployment has a ready Service endpoint or ready non-terminating pod."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                endpoints = await asyncio.to_thread(
                    self._core_v1.read_namespaced_endpoints,
                    name=deploy_name,
                    namespace=self.namespace,
                )
                for subset in endpoints.subsets or []:
                    if subset.addresses:
                        print(f"[scale-manager] {deploy_name} service endpoint is ready")
                        return
            except Exception:
                pass

            try:
                pods = await asyncio.to_thread(
                    self._core_v1.list_namespaced_pod,
                    namespace=self.namespace,
                    label_selector=f"app={deploy_name}",
                )
                for pod in pods.items:
                    if pod.metadata and pod.metadata.deletion_timestamp:
                        continue
                    if pod.status.phase == "Running":
                        conditions = pod.status.conditions or []
                        ready = any(
                            c.type == "Ready" and c.status == "True"
                            for c in conditions
                        )
                        if ready:
                            print(f"[scale-manager] {deploy_name} pod is ready")
                            return
            except Exception:
                pass
            await asyncio.sleep(1)
        print(f"[scale-manager] {deploy_name} did not become ready in {timeout}s")

    async def record_call(self, mcp_name: str):
        """Update the last-call timestamp after a successful MCP call."""
        deploy_name = self.get_deployment_name(mcp_name)
        if deploy_name:
            self.last_call[deploy_name] = time.time()

    async def scale_down_idle(self):
        """Scale down deployments that have been idle > idle_timeout seconds."""
        if not self.enabled:
            return
        self._init_k8s()
        if not self.enabled:
            return

        now = time.time()
        for deploy_name, last in list(self.last_call.items()):
            if now - last > self.idle_timeout:
                try:
                    deploy = await asyncio.to_thread(
                        self._apps_v1.read_namespaced_deployment,
                        name=deploy_name, namespace=self.namespace,
                    )
                    if (deploy.spec.replicas or 0) > 0:
                        print(f"[scale-manager] scaling down idle {deploy_name}")
                        await asyncio.to_thread(
                            self._apps_v1.patch_namespaced_deployment_scale,
                            name=deploy_name,
                            namespace=self.namespace,
                            body={"spec": {"replicas": 0}},
                        )
                except Exception as e:
                    print(f"[scale-manager] scale-down error for {deploy_name}: {e}")


# Global instance
scale_manager = ScaleManager()


# ---------------------------------------------------------------------------
# Docker stdio transport
# ---------------------------------------------------------------------------

DOCKER_STDIO_ENABLED = os.getenv("DOCKER_STDIO_ENABLED", "false").lower() == "true"


async def run_docker_stdio(image: str, tool: str, inputs: dict) -> dict:
    """Run an MCP tool via docker stdio transport.

    Spawns: docker run --rm -i <image>
    Writes: {"tool": tool, "inputs": inputs} to stdin
    Reads:  {"result": ...} or {"error": ...} from stdout
    Returns the result dict or raises RuntimeError.
    """
    payload = json.dumps({"tool": tool, "inputs": inputs})
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "run", "--rm", "-i", image,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(payload.encode()),
            timeout=30.0,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"docker run failed (exit {proc.returncode}): {stderr.decode()[:500]}"
            )
        return json.loads(stdout.decode())
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"docker stdio timeout for {image}:{tool}")


# ---------------------------------------------------------------------------
# Database (optional — graceful degradation if unavailable)
# ---------------------------------------------------------------------------

DB_DSN = os.getenv("DATABASE_URL", "postgresql://admin:admin@localhost:54323/mcpfinder")
_db_conn = None


def _get_db():
    global _db_conn
    if _db_conn is not None:
        return _db_conn
    try:
        import psycopg2
        _db_conn = psycopg2.connect(DB_DSN)
        _db_conn.autocommit = True
        return _db_conn
    except Exception:
        return None


def _ensure_audit_table():
    conn = _get_db()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS runtime_messages (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                channel TEXT NOT NULL,
                publisher TEXT,
                subscriber TEXT,
                action TEXT,
                payload JSONB,
                message_id TEXT,
                created_at TIMESTAMPTZ DEFAULT now()
            );
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_runtime_channel
            ON runtime_messages(channel);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_runtime_created
            ON runtime_messages(created_at DESC);
        """)
        cur.close()
    except Exception as e:
        print(f"[router] audit table setup warning: {e}")


def _audit_log(*, channel: str, publisher: str = "", subscriber: str = "",
               action: str, payload: dict | None = None, message_id: str = ""):
    conn = _get_db()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO runtime_messages
               (channel, publisher, subscriber, action, payload, message_id)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (channel, publisher, subscriber, action,
             json.dumps(payload) if payload else None, message_id),
        )
        cur.close()
    except Exception as e:
        print(f"[router] audit log warning: {e}")


# ---------------------------------------------------------------------------
# Audit events (structured events for the portal)
# ---------------------------------------------------------------------------

def _ensure_audit_events_table():
    conn = _get_db()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS audit_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id TEXT NOT NULL DEFAULT 'system',
                user_id TEXT DEFAULT 'system',
                action TEXT NOT NULL,
                resource TEXT,
                server_name TEXT,
                result TEXT DEFAULT 'ok',
                trace_id TEXT,
                duration_ms INTEGER,
                payload JSONB,
                created_at TIMESTAMPTZ DEFAULT now()
            );
        """)
        cur.execute("""
            ALTER TABLE audit_events
            ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'system';
        """)
        # Self-heal: older deployments created this table with a `metadata`
        # column; the writer uses `payload`. Without this, audit writes fail
        # silently (caught below), breaking the audit trail (SOC2 CC7.2).
        cur.execute("ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS payload JSONB;")
        # Drop the brittle result CHECK (allowed only success/denied/error) — the
        # writer also emits 'ok'/'rate_limited', so it silently dropped events.
        # An audit trail must not lose events to an over-strict enum.
        cur.execute("ALTER TABLE audit_events DROP CONSTRAINT IF EXISTS audit_events_result_check;")
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_created
            ON audit_events(created_at DESC);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_tenant_created
            ON audit_events(tenant_id, created_at DESC);
        """)
        # Tamper-evidence (SOC2 CC7.2): hash chain + append-only trigger.
        # Mirrors db/migrations/010 so the controls exist even without migrations.
        cur.execute("ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS seq BIGSERIAL;")
        cur.execute("ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS prev_hash TEXT;")
        cur.execute("ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS entry_hash TEXT;")
        cur.execute("ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS audit_hash_version TEXT;")
        # GDPR Art. 30 processing metadata (mirrors db/migrations/012).
        cur.execute("ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS purpose TEXT;")
        cur.execute("ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS lawful_basis TEXT;")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_seq ON audit_events(seq);")
        cur.execute("""
            CREATE OR REPLACE FUNCTION audit_events_block_mutation() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'audit_events is append-only (SOC2 CC7.2); % is not permitted', TG_OP;
            END;
            $$ LANGUAGE plpgsql;
        """)
        cur.execute("DROP TRIGGER IF EXISTS trg_audit_events_no_update ON audit_events;")
        cur.execute("""
            CREATE TRIGGER trg_audit_events_no_update
                BEFORE UPDATE OR DELETE ON audit_events
                FOR EACH ROW EXECUTE FUNCTION audit_events_block_mutation();
        """)
        cur.close()
    except Exception as e:
        print(f"[router] audit_events table setup warning: {e}")


# Advisory-lock key that serializes audit writes so the hash chain stays linear.
_AUDIT_CHAIN_LOCK_KEY = 778020114
_AUDIT_HASH_VERSION_CANONICAL_PAYLOAD = "canonical-payload-v1"
_AUDIT_HASH_VERSION_LEGACY_JSON_ORDER = "legacy-json-payload-order"


def _audit_entry_hash(prev_hash: str, fields: dict) -> str:
    """Deterministic SHA-256 over the previous hash + canonical event fields."""
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{prev_hash}\n{canonical}".encode()).hexdigest()


# GDPR Art. 30 processing metadata defaults, derived from the audit action.
# Callers can override per event; the derivation keeps every event tagged.
_AUDIT_PURPOSE_RULES: tuple[tuple[tuple[str, ...], tuple[str, str]], ...] = (
    (("privacy.", "audit", "retention."), ("compliance", "legal_obligation")),
    (("auth", "login", "token", "policy_", "key.", "credential"), ("security", "legitimate_interest")),
)


def _default_audit_purpose(action: str) -> tuple[str, str]:
    """Return (purpose, lawful_basis) defaults for an audit action."""
    normalized = (action or "").lower()
    for prefixes, tags in _AUDIT_PURPOSE_RULES:
        if any(normalized.startswith(p) or p.rstrip(".") == normalized for p in prefixes):
            return tags
    return ("service_delivery", "contract")


def _audit_hash_fields(
    *, tenant_id, user_id, action, resource, server_name, result,
    trace_id, duration_ms, payload_json, purpose=None, lawful_basis=None,
) -> dict:
    """Canonical hash-input fields for one audit entry.

    purpose/lawful_basis are included only when set, so rows written before
    the GDPR columns existed (NULL) still verify with the original 9 fields.
    """
    fields = {
        "tenant_id": tenant_id, "user_id": user_id, "action": action,
        "resource": resource, "server_name": server_name, "result": result,
        "trace_id": trace_id, "duration_ms": duration_ms, "payload": payload_json,
    }
    if purpose:
        fields["purpose"] = purpose
    if lawful_basis:
        fields["lawful_basis"] = lawful_basis
    return fields


def _audit_payload_hash_is_explicitly_legacy(
    *, payload, purpose, lawful_basis, audit_hash_version: str | None
) -> bool:
    """Return true only for rows whose payload hash bytes are unrecoverable.

    A multi-key dict mismatch is not enough: tampered canonical rows have the
    same shape after Postgres JSONB round-trip. Current writers tag rows as
    canonical; intentionally backfilled pre-canonical rows can carry the legacy
    marker. Untagged rows are excused only when they also predate GDPR purpose
    tagging.
    """
    if not isinstance(payload, dict) or len(payload) <= 1:
        return False
    if audit_hash_version == _AUDIT_HASH_VERSION_LEGACY_JSON_ORDER:
        return True
    if audit_hash_version == _AUDIT_HASH_VERSION_CANONICAL_PAYLOAD:
        return False
    return purpose is None and lawful_basis is None


def _log_api_usage(*, key_id: str, tenant_id: str, tool: str,
                   status_code: int = 200, response_time_ms: int = 0,
                   ip_address: str = "", error_msg: str = ""):
    """Log a pipeline/tool call to api_key_usage_log for billing/analytics."""
    conn = _get_db()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO api_key_usage_log
                (key_id, tenant_id, tool, status_code, response_time_ms, ip_address, error_msg)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (key_id, tenant_id, tool, status_code, response_time_ms, ip_address, error_msg or None))
        # Increment request_count on the key
        cur.execute("UPDATE api_keys SET request_count = request_count + 1, last_used_at = NOW() WHERE api_key = %s", (key_id,))
        cur.close()
    except Exception as e:
        pass  # usage logging is non-fatal


def _contains_secret_marker(key: object, markers: tuple[str, ...]) -> bool:
    """Return true when a field name matches secret markers across common separators."""
    normalized = str(key).lower().replace("-", "_")
    return any(marker in normalized for marker in markers)


def _redact_audit_payload(value):
    """Return a JSON-safe audit payload with likely secret fields redacted."""
    secret_markers = (
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "password",
        "credential",
        "private_key",
        "access_key",
    )
    if isinstance(value, dict):
        redacted = {}
        for key, nested in value.items():
            if _contains_secret_marker(key, secret_markers):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = _redact_audit_payload(nested)
        return redacted
    if isinstance(value, list):
        return [_redact_audit_payload(item) for item in value]
    return value


def _new_trace_id() -> str:
    return uuid.uuid4().hex[:16]


def _request_trace_id(request: Request | None = None) -> str:
    if request is not None:
        header_trace = (request.headers.get("X-Trace-Id") or request.headers.get("Traceparent") or "").strip()
        if header_trace:
            return header_trace[:128]
    return _new_trace_id()


def _write_audit_event(*, action: str, resource: str = "", server_name: str = "",
                       result: str = "ok", trace_id: str = "", duration_ms: int = 0,
                       payload: dict | None = None, user_id: str = "system",
                       tenant_id: str = "system", transport: str = "",
                       pipeline_name: str = "", purpose: str | None = None,
                       lawful_basis: str | None = None):
    conn = _get_db()
    if not conn:
        return
    try:
        cur = conn.cursor()
        safe_payload = _redact_audit_payload(payload) if payload else None
        # Canonical serialization (sorted keys, compact separators): Postgres
        # JSONB normalizes key order, so hashing the as-written insertion order
        # made multi-key payload rows unverifiable after a DB round-trip.
        payload_json = (
            json.dumps(safe_payload, sort_keys=True, separators=(",", ":"), default=str)
            if safe_payload else None
        )
        # GDPR Art. 30: every event carries its processing purpose + lawful basis.
        if not purpose or not lawful_basis:
            default_purpose, default_basis = _default_audit_purpose(action)
            purpose = purpose or default_purpose
            lawful_basis = lawful_basis or default_basis
        # Serialize chain writes (autocommit conn -> session advisory lock).
        cur.execute("SELECT pg_advisory_lock(%s)", (_AUDIT_CHAIN_LOCK_KEY,))
        try:
            cur.execute("SELECT entry_hash FROM audit_events ORDER BY seq DESC LIMIT 1")
            row = cur.fetchone()
            prev_hash = (row[0] if row and row[0] else "") or ""
            entry_hash = _audit_entry_hash(prev_hash, _audit_hash_fields(
                tenant_id=tenant_id, user_id=user_id, action=action,
                resource=resource, server_name=server_name, result=result,
                trace_id=trace_id, duration_ms=duration_ms, payload_json=payload_json,
                purpose=purpose, lawful_basis=lawful_basis,
            ))
            cur.execute(
                """INSERT INTO audit_events
                   (tenant_id, user_id, action, resource, server_name, result, trace_id,
                    duration_ms, payload, prev_hash, entry_hash, purpose, lawful_basis,
                    audit_hash_version)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (tenant_id, user_id, action, resource, server_name, result, trace_id,
                 duration_ms, payload_json, prev_hash, entry_hash, purpose, lawful_basis,
                 _AUDIT_HASH_VERSION_CANONICAL_PAYLOAD),
            )
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_AUDIT_CHAIN_LOCK_KEY,))
        cur.close()
    except Exception as e:
        print(f"[router] audit event warning: {e}")


def _load_runtime_hook_config() -> dict:
    """Load runtime hook config; disabled by default, fail-closed on invalid enabled config."""
    raw_json = os.environ.get("MCPFINDER_RUNTIME_HOOKS_JSON")
    explicit_file = os.environ.get("MCPFINDER_RUNTIME_HOOKS_FILE")
    default_file = Path(__file__).resolve().parent / "hooks.yaml"

    if raw_json:
        try:
            parsed = json.loads(raw_json)
            build_runtime_hook_manager(parsed)
            return parsed
        except Exception as exc:
            raise RuntimeError(f"Invalid enabled runtime hook config from MCPFINDER_RUNTIME_HOOKS_JSON: {exc}") from exc

    config_path = Path(explicit_file) if explicit_file else default_file
    if config_path.exists():
        try:
            with config_path.open() as f:
                parsed = yaml.safe_load(f) or {}
            build_runtime_hook_manager(parsed)
            return parsed
        except Exception as exc:
            source = "MCPFINDER_RUNTIME_HOOKS_FILE" if explicit_file else str(default_file)
            raise RuntimeError(f"Invalid enabled runtime hook config from {source}: {exc}") from exc

    return {"runtime_hooks": {"enabled": False}}


def _write_runtime_hook_audit_events(events: list[dict]) -> None:
    """Persist redacted runtime hook events through the structured audit table."""
    for event in events:
        tenant_id = str(event.get("tenant") or "system")
        subject_id = str(event.get("subject") or tenant_id)
        mcp = str(event.get("mcp") or "")
        tool = str(event.get("tool") or "")
        payload = {
            "hook": event.get("hook_name", ""),
            "action": event.get("action", ""),
            "result": event.get("result", ""),
            "tenant": tenant_id,
            "subject": subject_id,
            "mcp": mcp,
            "tool": tool,
            "transport": event.get("transport", ""),
            "pipeline_name": event.get("pipeline_name", ""),
            "reason": event.get("reason", ""),
        }
        _write_audit_event(
            action="runtime_hook",
            resource=f"{mcp}/{tool}" if mcp or tool else "runtime_hook",
            server_name=mcp,
            result=str(event.get("result") or "ok"),
            trace_id=str(event.get("trace_id") or ""),
            payload=payload,
            user_id=subject_id,
            tenant_id=tenant_id,
            transport=str(event.get("transport") or ""),
            pipeline_name=str(event.get("pipeline_name") or ""),
        )


runtime_hook_manager = build_runtime_hook_manager(_load_runtime_hook_config())


def _list_audit_events(
    *,
    limit: int = 100,
    server: str = "",
    tenant_id: str = "system",
    include_all_tenants: bool = False,
) -> list[dict]:
    conn = _get_db()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        if include_all_tenants and server:
            cur.execute(
                """SELECT id, tenant_id, user_id, action, resource, server_name, result,
                          trace_id, duration_ms, created_at
                   FROM audit_events
                   WHERE server_name = %s
                   ORDER BY created_at DESC LIMIT %s""",
                (server, limit),
            )
        elif include_all_tenants:
            cur.execute(
                """SELECT id, tenant_id, user_id, action, resource, server_name, result,
                          trace_id, duration_ms, created_at
                   FROM audit_events
                   ORDER BY created_at DESC LIMIT %s""",
                (limit,),
            )
        elif server:
            cur.execute(
                """SELECT id, tenant_id, user_id, action, resource, server_name, result,
                          trace_id, duration_ms, created_at
                   FROM audit_events
                   WHERE tenant_id = %s AND server_name = %s
                   ORDER BY created_at DESC LIMIT %s""",
                (tenant_id, server, limit),
            )
        else:
            cur.execute(
                """SELECT id, tenant_id, user_id, action, resource, server_name, result,
                          trace_id, duration_ms, created_at
                   FROM audit_events
                   WHERE tenant_id = %s
                   ORDER BY created_at DESC LIMIT %s""",
                (tenant_id, limit),
            )
        rows = cur.fetchall()
        cur.close()
        return [
            {
                "event_id": str(r[0]),
                "tenant_id": r[1],
                "user_id": r[2],
                "action": r[3],
                "resource": r[4],
                "server_name": r[5],
                "result": r[6],
                "trace_id": r[7],
                "duration_ms": r[8],
                "created_at": r[9].isoformat() if r[9] else None,
            }
            for r in rows
        ]
    except Exception as e:
        print(f"[router] audit events list warning: {e}")
    return []


# ---------------------------------------------------------------------------
# Sealed handle storage (sensitive inputs never exposed to LLM)
# ---------------------------------------------------------------------------

def _ensure_sealed_table():
    conn = _get_db()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sealed_handles (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                label TEXT NOT NULL,
                encrypted_value TEXT NOT NULL,
                tenant_id TEXT NOT NULL DEFAULT 'system',
                subject_id TEXT,
                created_at TIMESTAMPTZ DEFAULT now(),
                expires_at TIMESTAMPTZ,
                used_at TIMESTAMPTZ
            );
        """)
        cur.execute("ALTER TABLE sealed_handles ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'system'")
        cur.execute("ALTER TABLE sealed_handles ADD COLUMN IF NOT EXISTS subject_id TEXT")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sealed_handles_owner ON sealed_handles (tenant_id, subject_id, created_at DESC)")
        cur.close()
    except Exception as e:
        print(f"[router] sealed table setup warning: {e}")


def _get_fernet() -> _Fernet:
    """Get Fernet using an injected KMS/Vault/k8s secret; fail closed in production."""
    key = os.environ.get("ENCRYPTION_KEY") or os.environ.get("MCPFINDER_ENCRYPTION_KEY")
    if not key:
        if _is_production_like_auth_env():
            raise RuntimeError(
                "ENCRYPTION_KEY or MCPFINDER_ENCRYPTION_KEY is required in production; "
                "inject it from KMS/Vault/k8s Secret instead of using the development fallback"
            )
        # Development-only fallback. Production-like environments fail closed above.
        key = base64.urlsafe_b64encode(hashlib.sha256(b"mcpfinder-dev-key-change-in-prod").digest())
        print("[router] WARNING: ENCRYPTION_KEY not set — using insecure dev key. Set via KMS/Vault/k8s Secret")
    return _Fernet(key.encode() if isinstance(key, str) else key)


def _create_sealed_handle(
    label: str,
    value: str,
    expires_in_seconds: int = 300,
    *,
    tenant_id: str = "system",
    subject_id: str | None = None,
) -> dict | None:
    """Store a sealed handle owned by tenant/subject. Value is encrypted at rest."""
    conn = _get_db()
    if not conn:
        return None
    try:
        encoded = _get_fernet().encrypt(value.encode()).decode()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO sealed_handles (label, encrypted_value, tenant_id, subject_id, expires_at)
               VALUES (%s, %s, %s, %s, now() + (%s * interval '1 second'))
               RETURNING id, label, created_at, expires_at""",
            (label, encoded, tenant_id, subject_id, expires_in_seconds),
        )
        row = cur.fetchone()
        cur.close()
        if row:
            return {
                "handle_id": str(row[0]),
                "label": row[1],
                "created_at": row[2].isoformat() if row[2] else None,
                "expires_at": row[3].isoformat() if row[3] else None,
            }
    except Exception as e:
        print(f"[router] sealed create warning: {e}")
    return None


def _resolve_handle_from_db(
    handle_id: str,
    *,
    tenant_id: str = "system",
    subject_id: str | None = None,
) -> dict | None:
    """Resolve exactly once, scoped to tenant/subject, and mark used atomically."""
    conn = _get_db()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute(
            """UPDATE sealed_handles SET used_at = now()
               WHERE id = %s
                 AND tenant_id = %s
                 AND subject_id = %s
                 AND used_at IS NULL
                 AND (expires_at IS NULL OR expires_at > now())
               RETURNING label, encrypted_value, created_at, expires_at""",
            (handle_id, tenant_id, subject_id),
        )
        row = cur.fetchone()
        cur.close()
        if not row:
            return None
        decrypted = _get_fernet().decrypt(row[1].encode()).decode()
        return {
            "label": row[0],
            "value": decrypted,
            "created_at": row[2].isoformat() if row[2] else None,
            "expires_at": row[3].isoformat() if row[3] else None,
        }
    except Exception as e:
        print(f"[router] sealed resolve warning: {e}")
    return None


def _list_sealed_handles(*, tenant_id: str = "system", subject_id: str | None = None) -> list[dict]:
    """List active handles owned by the caller; plaintext values are never returned."""
    conn = _get_db()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, label, created_at, expires_at
               FROM sealed_handles
               WHERE tenant_id = %s
                 AND subject_id = %s
                 AND (expires_at IS NULL OR expires_at > now())
                 AND used_at IS NULL
               ORDER BY created_at DESC""",
            (tenant_id, subject_id),
        )
        rows = cur.fetchall()
        cur.close()
        return [
            {
                "handle_id": str(r[0]),
                "label": r[1],
                "created_at": r[2].isoformat() if r[2] else None,
                "expires_at": r[3].isoformat() if r[3] else None,
            }
            for r in rows
        ]
    except Exception as e:
        print(f"[router] sealed list warning: {e}")
    return []


def _delete_sealed_handle(handle_id: str, *, tenant_id: str = "system", subject_id: str | None = None) -> bool:
    """Invalidate a handle only if it belongs to the caller's tenant/subject."""
    conn = _get_db()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            """UPDATE sealed_handles SET expires_at = now()
               WHERE id = %s
                 AND tenant_id = %s
                 AND subject_id = %s
                 AND used_at IS NULL
               RETURNING id""",
            (handle_id, tenant_id, subject_id),
        )
        row = cur.fetchone()
        cur.close()
        return bool(row)
    except Exception as e:
        print(f"[router] sealed delete warning: {e}")
    return False


async def resolve_sealed_inputs(inputs: dict, *, tenant_id: str | None = None, subject_id: str | None = None, trace_id: str = "") -> dict:
    """
    Resolve two types of credential references at MCP call time:
    1. {__handle: uuid} — sealed handles (short-lived, single-use, tenant/subject scoped)
    2. {{credential:name}} — new persistent credential tokens (LLM never sees plaintext)
    """
    from credentials import resolve_credential_tokens, has_credential_tokens

    resolved = {}
    audit_tenant_id = tenant_id or "system"
    audit_subject_id = subject_id or ""
    for k, v in inputs.items():
        # Sealed handles require an authenticated tenant + subject owner scope.
        if isinstance(v, dict) and "__handle" in v:
            handle_id = str(v["__handle"])
            if not tenant_id or not subject_id:
                _write_audit_event(
                    action="sealed_handle.resolve",
                    resource=f"sealed:{handle_id}",
                    result="denied",
                    trace_id=trace_id,
                    payload={"tenant_id": audit_tenant_id, "reason": "missing_owner_scope"},
                    user_id=audit_subject_id,
                    tenant_id=audit_tenant_id,
                )
                resolved[k] = v
                continue

            result = _resolve_handle_from_db(handle_id, tenant_id=tenant_id, subject_id=subject_id)
            if result:
                _write_audit_event(
                    action="sealed_handle.resolve",
                    resource=f"sealed:{handle_id}",
                    result="ok",
                    trace_id=trace_id,
                    payload={"tenant_id": tenant_id, "label": result.get("label", "")},
                    user_id=subject_id,
                    tenant_id=tenant_id,
                )
                resolved[k] = result["value"]
            else:
                _write_audit_event(
                    action="sealed_handle.resolve",
                    resource=f"sealed:{handle_id}",
                    result="denied",
                    trace_id=trace_id,
                    payload={"tenant_id": tenant_id, "reason": "not_found_or_not_owned_or_expired"},
                    user_id=subject_id,
                    tenant_id=tenant_id,
                )
                resolved[k] = v
        # New: {{credential:name}} tokens — resolve recursively.
        # SECURITY: credentials are tenant-scoped; an authenticated tenant is
        # required. Without one, tokens are left unresolved (fail closed) so a
        # caller can never read another tenant's (or the platform's) secrets.
        elif has_credential_tokens(v):
            if not tenant_id:
                _write_audit_event(
                    action="credential.resolve",
                    resource=f"{k}",
                    result="denied",
                    trace_id=trace_id,
                    payload={"tenant_id": audit_tenant_id, "reason": "missing_tenant_scope"},
                    user_id=audit_subject_id,
                    tenant_id=audit_tenant_id,
                )
                resolved[k] = v
            else:
                resolved[k] = resolve_credential_tokens(v, db_conn=_get_db(), tenant_id=tenant_id)
        else:
            resolved[k] = v
    return resolved


# ---------------------------------------------------------------------------
# Seed from YAML files
# ---------------------------------------------------------------------------

def _load_types_yaml():
    path = Path(__file__).parent / "types.yaml"
    if not path.exists():
        print("[router] types.yaml not found, type system disabled")
        return
    data = yaml.safe_load(path.read_text())
    for name, typedef in data.get("types", {}).items():
        types_registry[name] = typedef
    print(f"[router] loaded {len(types_registry)} types from types.yaml")



def _load_manifests_dir():
    mdir = Path(__file__).parent / "manifests"
    if not mdir.exists():
        return

    # Allow k8s deployments to override manifest endpoints via env var.
    # Format: JSON map of {"mcp-name": "http://k8s-service:port"}
    # Example: ENDPOINT_OVERRIDES='{"weather-trip-mcp":"http://weather-trip-mcp:8080"}'
    endpoint_overrides: dict = {}
    raw_overrides = os.getenv("ENDPOINT_OVERRIDES", "")
    if raw_overrides:
        try:
            endpoint_overrides = json.loads(raw_overrides)
            print(f"[router] endpoint overrides: {list(endpoint_overrides.keys())}")
        except Exception as e:
            print(f"[router] WARNING: could not parse ENDPOINT_OVERRIDES: {e}")

    for f in sorted(mdir.glob("*.yaml")):
        data = yaml.safe_load(f.read_text())
        raw_tools = data.get("tools", [])

        # Apply k8s endpoint override if present
        mcp_name = data.get("name", "")
        if mcp_name in endpoint_overrides:
            data["endpoint"] = endpoint_overrides[mcp_name]

        # Extract tool names for the McpManifest (backward compat)
        tool_names = []
        is_typed = False
        for t in raw_tools:
            if isinstance(t, dict):
                tool_names.append(t["name"])
                is_typed = True
            else:
                tool_names.append(t)

        manifest = McpManifest(
            name=data["name"],
            endpoint=data.get("endpoint", ""),
            publishes=data.get("publishes", []),
            subscribes=data.get("subscribes", []),
            tools=tool_names,
            transport=data.get("transport", "http"),
            image=data.get("image"),
            access=_parse_manifest_access(data.get("access")),
            pii_fields=_parse_manifest_pii_fields(data),
        )
        manifests[manifest.name] = manifest
        _registry_item_tenants[f"manifest:{manifest.name}"] = "system"

        # Remember YAML endpoint so self-registration cannot override it
        if manifest.endpoint:
            _yaml_seeded_endpoints[manifest.name] = manifest.endpoint
        # Remember YAML access/PII declarations so self-registration cannot
        # weaken operator-declared gates (restriction is authoritative).
        if manifest.access is not None:
            _yaml_seeded_access[manifest.name] = manifest.access
        if manifest.pii_fields:
            _yaml_seeded_pii_fields[manifest.name] = manifest.pii_fields

        # Register typed manifests into the type graph
        if is_typed:
            typed_manifests[data["name"]] = data
            _registry_item_tenants[f"typed_manifest:{data['name']}"] = "system"
            type_graph.register_manifest(data)

    print(f"[router] loaded {len(manifests)} manifests")
    if typed_manifests:
        issues = type_graph.validate_all_manifests(typed_manifests, types_registry)
        for issue in issues:
            print(f"[router] {issue}")
        n_producers = sum(len(v) for v in type_graph.producers.values())
        print(f"[router] Type graph: {len(types_registry)} types, {n_producers} producers registered")


# ---------------------------------------------------------------------------
# Named Pipeline loader
# ---------------------------------------------------------------------------

def _load_named_pipelines():
    """Scan runtime/pipelines/ for named_pipeline.yaml files and load them."""
    pipelines_dir = Path(__file__).parent / "pipelines"
    if not pipelines_dir.exists():
        return
    count = 0
    for yaml_file in sorted(pipelines_dir.glob("**/named_pipeline.yaml")):
        try:
            data = yaml.safe_load(yaml_file.read_text())
            # Normalize inputs: accept both "type: String" and plain "String"
            pipeline = NamedPipeline(
                name=data["name"],
                description=data.get("description", ""),
                inputs=data.get("inputs", {}),
                stages=[PipelineStageSchema(**s) for s in data.get("stages", [])],
                output_stage=data.get("output_stage", ""),
                tags=data.get("tags", []),
                created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
            )
            named_pipelines[pipeline.name] = pipeline
            count += 1
            print(f"[router] loaded named pipeline: {pipeline.name} from {yaml_file}")
        except Exception as e:
            print(f"[router] WARN: failed to load {yaml_file}: {e}")
    print(f"[router] loaded {count} named pipelines")


# ---------------------------------------------------------------------------
# API Key Authentication
# ---------------------------------------------------------------------------

REQUIRE_AUTH = os.getenv("REQUIRE_AUTH", "true").lower() == "true"
NEXTAUTH_SECRET = os.getenv("NEXTAUTH_SECRET", "")
API_KEY_REFRESH_INTERVAL = 300  # 5 minutes
TOKEN_EXCHANGE_RATE_LIMIT_MAX = int(os.getenv("TOKEN_EXCHANGE_RATE_LIMIT_MAX", "60"))
TOKEN_EXCHANGE_RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("TOKEN_EXCHANGE_RATE_LIMIT_WINDOW_SECONDS", "60"))
TOKEN_EXCHANGE_RATE_LIMIT_MAX_BUCKETS = int(os.getenv("TOKEN_EXCHANGE_RATE_LIMIT_MAX_BUCKETS", "4096"))
_token_exchange_rate_limits: dict[str, list[float]] = {}
TRUSTED_PROXY_CIDRS = [
    item.strip()
    for item in os.getenv("TRUSTED_PROXY_CIDRS", os.getenv("TRUSTED_PROXY_IPS", "")).split(",")
    if item.strip()
]
# Default fits local-from-source runs; docker-compose and k8s deployments must
# set PORTAL_URL explicitly (compose: http://portal:3004).
PORTAL_URL = os.getenv("PORTAL_URL", "http://localhost:3004")
JWKS_URL = os.getenv("JWKS_URL", f"{PORTAL_URL}/api/.well-known/jwks.json")
ROUTER_ISSUER = os.getenv("ROUTER_ISSUER", "https://sealfleet.io/router")
PORTAL_JWT_ISSUER = os.getenv("PORTAL_JWT_ISSUER", os.getenv("NEXTAUTH_ISSUER", ""))
PORTAL_JWT_AUDIENCE = os.getenv("PORTAL_JWT_AUDIENCE", os.getenv("NEXTAUTH_AUDIENCE", ""))
PORTAL_RS256_PUBLIC_KEY = os.getenv("PORTAL_RS256_PUBLIC_KEY", os.getenv("PORTAL_JWT_PUBLIC_KEY", ""))
PORTAL_RS256_KEY_ID = os.getenv("PORTAL_RS256_KEY_ID", os.getenv("PORTAL_JWT_KEY_ID", ""))
PORTAL_DELEGATION_API_KEY_SHA256S = {
    item.strip().lower()
    for item in os.getenv("PORTAL_DELEGATION_API_KEY_SHA256S", "").split(",")
    if item.strip()
}
_JWKS_CACHE_TTL = 300
_jwks_cache: dict = {"keys": None, "fetched_at": 0.0}
_portal_public_key_cache: dict = {"pem": None, "keys": None}


def _env_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _metadata_truthy(value) -> bool:
    """Return True only for explicit boolean-ish privilege values."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _normalize_metadata(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _api_key_sha256(api_key: str | None) -> str:
    return hashlib.sha256((api_key or "").encode("utf-8")).hexdigest()


def _api_key_allows_identity_delegation(api_key: str | None, key_info: Mapping | None) -> bool:
    """Whether an API key may trust X-Sealfleet-* delegated identity headers.

    Deliberately does not trust key names. Production/public-test delegation must
    be granted by durable DB fields/metadata loaded with the key or by an
    operator-configured SHA-256 fingerprint of the key material.
    """
    if not key_info:
        return False
    if _metadata_truthy(key_info.get("allow_identity_delegation")):
        return True
    metadata = _normalize_metadata(key_info.get("metadata"))
    if _metadata_truthy(metadata.get("allow_identity_delegation")):
        return True
    if _metadata_truthy(metadata.get("portal_identity_delegation")):
        return True
    return bool(api_key and _api_key_sha256(api_key) in PORTAL_DELEGATION_API_KEY_SHA256S)


def _is_production_like_auth_env(env: Mapping[str, str | None] | None = None) -> bool:
    env_map = os.environ if env is None else env
    candidates = [
        env_map.get("MCPFINDER_DEPLOYMENT_ENV"),
        env_map.get("DEPLOYMENT_ENV"),
        env_map.get("ENVIRONMENT"),
        env_map.get("APP_ENV"),
        env_map.get("NODE_ENV"),
    ]
    normalized = {
        str(value).strip().lower().replace("_", "-")
        for value in candidates
        if value is not None
    }
    return bool(normalized & {"production", "prod", "public-test"})


def _router_ephemeral_keys_allowed(env: Mapping[str, str | None] | None = None) -> bool:
    env_map = os.environ if env is None else env
    return _env_truthy(env_map.get("AUTH_ALLOW_EPHEMERAL_KEYS")) and not _is_production_like_auth_env(env_map)


def _legacy_portal_hs256_allowed(env: Mapping[str, str | None] | None = None) -> bool:
    """Permit legacy portal HS256 JWT migration only by explicit non-production opt-in."""
    env_map = os.environ if env is None else env
    return _env_truthy(env_map.get("AUTH_ALLOW_LEGACY_PORTAL_HS256")) and not _is_production_like_auth_env(env_map)


def _assert_router_key_configured(env: Mapping[str, str | None] | None = None) -> None:
    env_map = os.environ if env is None else env
    pem = env_map.get("ROUTER_RS256_PRIVATE_KEY")
    if pem and pem.strip():
        return
    if _router_ephemeral_keys_allowed(env_map):
        return
    raise RuntimeError(
        "ROUTER_RS256_PRIVATE_KEY is required unless AUTH_ALLOW_EPHEMERAL_KEYS=true in a non-production development environment"
    )


# ---------------------------------------------------------------------------
# User JWT Validation (co-exists with API key auth)
# ---------------------------------------------------------------------------

def _configured_portal_public_keys() -> list[dict]:
    """Return JWKS-shaped keys derived from a configured portal public PEM."""
    pem = PORTAL_RS256_PUBLIC_KEY.strip()
    if not pem:
        return []
    if _portal_public_key_cache["pem"] == pem and _portal_public_key_cache["keys"] is not None:
        return _portal_public_key_cache["keys"]

    from jwt.algorithms import RSAAlgorithm

    public_key = serialization.load_pem_public_key(pem.encode("utf-8"), backend=default_backend())
    jwk = json.loads(RSAAlgorithm.to_jwk(public_key))
    if PORTAL_RS256_KEY_ID.strip():
        jwk["kid"] = PORTAL_RS256_KEY_ID.strip()
    jwk["alg"] = "RS256"
    jwk["use"] = "sig"
    _portal_public_key_cache["pem"] = pem
    _portal_public_key_cache["keys"] = [jwk]
    return _portal_public_key_cache["keys"]


def _portal_jwt_decode_kwargs(algorithms: list[str] | None = None) -> dict:
    """Build PyJWT verifier kwargs, fail-closing production-like iss/aud config."""
    issuer = PORTAL_JWT_ISSUER.strip()
    audience = PORTAL_JWT_AUDIENCE.strip()
    if _is_production_like_auth_env() and (not issuer or not audience):
        raise ValueError(
            "PORTAL_JWT_ISSUER and PORTAL_JWT_AUDIENCE are required in production/public-test environments"
        )
    required_claims = ["exp"]
    if issuer:
        required_claims.append("iss")
    if audience:
        required_claims.append("aud")
    kwargs: dict = {
        "algorithms": algorithms or ["RS256"],
        "options": {
            "require": required_claims,
            "verify_iss": bool(issuer),
            "verify_aud": bool(audience),
        },
    }
    if issuer:
        kwargs["issuer"] = issuer
    if audience:
        kwargs["audience"] = audience
    return kwargs


def _fetch_jwks_sync() -> list[dict]:
    """Fetch and cache portal JWKS for RS256 portal session JWT validation."""
    now = time.monotonic()
    if _jwks_cache["keys"] is not None and (now - _jwks_cache["fetched_at"]) < _JWKS_CACHE_TTL:
        return _jwks_cache["keys"]
    with httpx.Client(timeout=5.0) as client:
        resp = client.get(JWKS_URL)
        resp.raise_for_status()
        data = resp.json()
    _jwks_cache["keys"] = data.get("keys", [])
    _jwks_cache["fetched_at"] = now
    return _jwks_cache["keys"]


def _decode_jwt_header(token: str) -> dict:
    header_b64 = token.split(".")[0]
    header_b64 += "=" * (-len(header_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(header_b64))


def _validate_user_jwt(token: str) -> dict | None:
    """Validate a portal session JWT, never a router-issued MCP access token.

    Primary path: portal RS256 via JWKS. Legacy migration fallback: portal
    HS256 via NEXTAUTH_SECRET only when AUTH_ALLOW_LEGACY_PORTAL_HS256=true
    in a non-production environment. Router-issued RS256 MCP tokens return None so
    they cannot authenticate general runtime/router APIs.
    """
    try:
        import jwt  # PyJWT

        header = _decode_jwt_header(token)
        alg = header.get("alg", "")
        kid = header.get("kid")
        try:
            unverified = jwt.decode(token, options={"verify_signature": False})
        except Exception:
            unverified = {}
        token_iss = unverified.get("iss") if isinstance(unverified, dict) else None
        if alg == "RS256" and token_iss == ROUTER_ISSUER:
            return None

        if alg == "RS256":
            from jwt.algorithms import RSAAlgorithm

            keys = _configured_portal_public_keys() or _fetch_jwks_sync()
            if not keys:
                raise ValueError("JWKS returned no keys")
            key_data = next((k for k in keys if k.get("kid") == kid), None)
            if key_data is None and kid is None:
                key_data = keys[0]
            if key_data is None:
                raise ValueError(f"No matching JWKS key for kid={kid!r}")
            public_key = RSAAlgorithm.from_jwk(json.dumps(key_data))
            payload = jwt.decode(token, public_key, **_portal_jwt_decode_kwargs())
        elif alg == "HS256" and NEXTAUTH_SECRET and _legacy_portal_hs256_allowed():
            hs256_kwargs = _portal_jwt_decode_kwargs()
            hs256_kwargs["algorithms"] = ["HS256"]
            payload = jwt.decode(token, NEXTAUTH_SECRET, **hs256_kwargs)
        else:
            raise ValueError(f"Unsupported JWT algorithm: {alg!r}")

        user_id = payload.get("user_id") or payload.get("sub")
        tenant_id = payload.get("tenant_id")
        email = payload.get("email", "")
        is_admin = payload.get("is_admin", False)
        sub = payload.get("sub")
        if not user_id or not tenant_id:
            return None
        claims = {
            "user_id": str(user_id),
            "tenant_id": str(tenant_id),
            "workspace_id": str(payload.get("workspace_id") or payload.get("workspace") or ""),
            "is_admin": bool(is_admin),
            "email": str(email),
            "sub": str(sub) if sub is not None else None,
            "jwt_claims": payload,
        }

        for claim_key in ("permissions", "actions", "scopes", "scope", "groups", "group_ids"):
            if claim_key in payload:
                claims[claim_key] = payload.get(claim_key)
        return claims
    except Exception as e:
        if os.getenv("DEBUG", "").lower() in ("1", "true"):
            print(f"[auth] JWT validation failed: {e}")
        return None

# ---------------------------------------------------------------------------
# MCP Permission Check (user-based requests)
# ---------------------------------------------------------------------------

def _allowed_tools_clause(tool: str | None) -> tuple[str, list]:
    """SQL fragment enforcing mcp_permissions.allowed_tools for a given tool.

    NULL/empty allowed_tools = whole-MCP grant. When a tool is named, the
    grant must either be unrestricted or list that tool.
    """
    if tool is None:
        return "", []
    return (
        " AND (allowed_tools IS NULL OR cardinality(allowed_tools) = 0 OR %s = ANY(allowed_tools))",
        [tool],
    )


def _check_user_mcp_permission(
    tenant_id: str,
    user_id: str,
    server_name_or_id: str,
    *,
    tool: str | None = None,
    groups: list[str] | None = None,
) -> bool:
    """Check if a user has mcp_permissions for the given server (and tool).

    Grant resolution order: platform admin flag -> direct user grant ->
    role grant (via user_roles) -> IdP group claim grant (via
    scim_group_role_mappings and sso_role_mappings -> roles). Grants with a
    non-empty allowed_tools list only cover the listed tools.
    Returns True if any valid (non-expired) permission covers the call.
    """
    conn = _get_db()
    if not conn:
        return False
    try:
        cur = conn.cursor()

        # Platform admins bypass per-MCP grants. This also covers delegated
        # API-key identities, whose JWT is_admin claim never reaches us.
        cur.execute(
            "SELECT 1 FROM users WHERE id::text = %s AND is_admin = TRUE AND is_active = TRUE LIMIT 1",
            (user_id,),
        )
        if cur.fetchone():
            cur.close()
            return True

        # Resolve server_id from name or id
        cur.execute(
            "SELECT id FROM servers WHERE name = %s OR id::text = %s LIMIT 1",
            (server_name_or_id, server_name_or_id),
        )
        row = cur.fetchone()
        if not row:
            cur.close()
            return False
        server_id = row[0]
        tools_sql, tools_params = _allowed_tools_clause(tool)

        # Check direct user permission
        cur.execute(  # nosec B608 — tools_sql is a constant fragment; values parameterized
            f"""SELECT 1 FROM mcp_permissions
               WHERE grantee_type = 'user'
                 AND grantee_id = %s
                 AND server_id = %s
                 AND (expires_at IS NULL OR expires_at > NOW()){tools_sql}
               LIMIT 1""",  # nosec B608 — tools_sql is a constant fragment; values parameterized
            [user_id, server_id, *tools_params],
        )
        if cur.fetchone():
            cur.close()
            return True

        # Check role-based permissions
        cur.execute(  # nosec B608 — tools_sql is a constant fragment; values parameterized
            f"""SELECT 1 FROM mcp_permissions mp
               JOIN user_roles ur ON ur.role_id = mp.grantee_id
               WHERE mp.grantee_type = 'role'
                 AND ur.user_id = %s
                 AND mp.server_id = %s
                 AND (mp.expires_at IS NULL OR mp.expires_at > NOW()){tools_sql.replace('allowed_tools', 'mp.allowed_tools')}
               LIMIT 1""",  # nosec B608 — tools_sql is a constant fragment; values parameterized
            [user_id, server_id, *tools_params],
        )
        if cur.fetchone():
            cur.close()
            return True

        # Check IdP group claim grants: JWT groups -> mapped roles -> mcp grant.
        # Both mapping tables are honored so a mapping configured in the portal
        # (sso_role_mappings) or via SCIM (scim_group_role_mappings) grants MCP
        # access at request time, before/without login-time materialization.
        if groups:
            cur.execute(  # nosec B608 — tools_sql is a constant fragment; values parameterized
                f"""SELECT 1 FROM mcp_permissions mp
                   WHERE mp.grantee_type = 'role'
                     AND (mp.expires_at IS NULL OR mp.expires_at > NOW())
                     AND mp.server_id = %s
                     AND mp.grantee_id IN (
                        SELECT r.id
                        FROM scim_group_role_mappings sgrm
                        JOIN roles r ON r.name = ANY(sgrm.role_names)
                                    AND r.tenant_id::text = sgrm.tenant_id::text
                        WHERE sgrm.tenant_id::text = %s
                          AND sgrm.external_group_id = ANY(%s)
                        UNION
                        SELECT srm.role_id
                        FROM sso_role_mappings srm
                        WHERE srm.tenant_id::text = %s
                          AND srm.idp_claim_key IN ('groups', 'roles')
                          AND srm.idp_claim_value = ANY(%s)
                     ){tools_sql.replace('allowed_tools', 'mp.allowed_tools')}
                   LIMIT 1""",  # nosec B608 — tools_sql is a constant fragment; values parameterized
                [server_id, tenant_id, groups, tenant_id, groups, *tools_params],
            )
            if cur.fetchone():
                cur.close()
                return True

        cur.close()
        return False
    except Exception as e:
        print(f"[auth] permission check error: {e}")
        return False


def _user_role_names(tenant_id: str, user_id: str, groups: list[str] | None = None) -> set[str]:
    """Resolve a user's effective platform role NAMES (assigned + group-mapped)."""
    conn = _get_db()
    if not conn:
        return set()
    names: set[str] = set()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT r.name FROM user_roles ur
               JOIN roles r ON r.id = ur.role_id
               WHERE ur.user_id::text = %s""",
            (user_id,),
        )
        names.update(str(r[0]) for r in cur.fetchall())
        if groups:
            cur.execute(
                """SELECT r.name
                   FROM scim_group_role_mappings sgrm
                   JOIN roles r ON r.name = ANY(sgrm.role_names)
                               AND r.tenant_id::text = sgrm.tenant_id::text
                   WHERE sgrm.tenant_id::text = %s
                     AND sgrm.external_group_id = ANY(%s)
                   UNION
                   SELECT r.name
                   FROM sso_role_mappings srm
                   JOIN roles r ON r.id = srm.role_id
                   WHERE srm.tenant_id::text = %s
                     AND srm.idp_claim_key IN ('groups', 'roles')
                     AND srm.idp_claim_value = ANY(%s)""",
                (tenant_id, groups, tenant_id, groups),
            )
            names.update(str(r[0]) for r in cur.fetchall())
        cur.close()
    except Exception as e:
        if os.getenv("DEBUG", "").lower() in ("1", "true"):
            print(f"[auth] role name resolution error: {e}")
    return names


def _manifest_access_allows(
    manifest: "McpManifest",
    *,
    tenant_id: str,
    user_id: str,
    groups: list[str] | None,
    is_admin: bool = False,
) -> bool:
    """Evaluate a manifest's declarative access gate for a user identity."""
    access = getattr(manifest, "access", None)
    if access is None or not access.restricted:
        return True
    if is_admin:
        return True
    caller_groups = set(groups or [])
    if caller_groups & set(access.allowed_groups):
        return True
    if access.allowed_roles:
        if _user_role_names(tenant_id, user_id, list(caller_groups)) & set(access.allowed_roles):
            return True
    return False


def _manifest_owner(mcp_name: str) -> str:
    """Owning tenant of a registered manifest; 'system' = shared platform MCP."""
    return _registry_item_tenants.get(f"manifest:{mcp_name}", "system")


def _assert_manifest_tenant_visibility(request: Request, mcp_name: str) -> None:
    """403 unless the caller's tenant owns this MCP or it's a shared/system MCP.

    Applies to EVERY authenticated caller — including plain service API keys,
    which otherwise skip per-MCP checks. Stops a tenant from invoking a manifest
    another tenant registered (the pentest cross-tenant /call path).
    """
    auth_type = getattr(request.state, "auth_type", "")
    if auth_type == "none":  # REQUIRE_AUTH=false / system context
        return
    if bool(getattr(request.state, "is_admin", False)):
        return
    owner = _manifest_owner(mcp_name)
    if owner in ("system", ""):  # shared platform MCPs (YAML-seeded)
        return
    if owner != get_tenant_id(request):
        raise HTTPException(403, f"Forbidden: MCP '{mcp_name}' belongs to another tenant")


def _enforce_user_mcp_access(request: Request, mcp_name: str, tool: str | None = None) -> None:
    """403 unless the request's identity may call this MCP (and tool).

    Two layers: (1) tenant visibility of the target manifest — enforced for ALL
    authenticated callers including plain service keys; (2) for user identities
    (portal JWTs and delegated API keys), per-MCP/tool grants + manifest
    role/group gate.
    """
    # Layer 1 — manifest tenant ownership (covers service keys too).
    _assert_manifest_tenant_visibility(request, mcp_name)

    auth_type = getattr(request.state, "auth_type", "")
    user_id = str(getattr(request.state, "user_id", "") or "")
    if auth_type not in ("user_jwt", "api_key") or not user_id:
        return
    if auth_type == "api_key" and "delegated_from" not in (getattr(request.state, "identity", None) or {}):
        return

    tenant_id = get_tenant_id(request)
    is_admin = bool(getattr(request.state, "is_admin", False))
    identity = getattr(request.state, "identity", None) or {}
    _, groups = _extract_action_permissions(identity)

    manifest = manifests.get(mcp_name)
    if manifest is not None and not _manifest_access_allows(
        manifest, tenant_id=tenant_id, user_id=user_id, groups=groups, is_admin=is_admin
    ):
        raise HTTPException(403, f"Forbidden: MCP '{mcp_name}' requires one of roles/groups declared in its manifest")

    if is_admin:
        return
    if not _check_user_mcp_permission(tenant_id, user_id, mcp_name, tool=tool, groups=groups):
        detail = f"Forbidden: no MCP access to '{mcp_name}'"
        if tool:
            detail += f" (tool '{tool}')"
        raise HTTPException(403, detail)


class ApiKeyManager:
    """Manages API key authentication with in-memory caching."""

    def __init__(self):
        self.keys: dict[str, dict] = {}  # api_key -> {tenant_id, name, is_active}
        self.last_refresh: float = 0

    def _ensure_table(self):
        """Create api_keys table if it doesn't exist."""
        conn = _get_db()
        if not conn:
            return
        try:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    api_key TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    is_active BOOLEAN DEFAULT true,
                    action_permissions TEXT[],
                    allow_identity_delegation BOOLEAN NOT NULL DEFAULT false,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                );
            """)
            cur.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS allow_identity_delegation BOOLEAN NOT NULL DEFAULT false")
            cur.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb")
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_api_keys_tenant
                ON api_keys(tenant_id);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_api_keys_active
                ON api_keys(is_active) WHERE is_active = true;
            """)
            cur.execute("""
                ALTER TABLE api_keys
                ADD COLUMN IF NOT EXISTS action_permissions TEXT[];
            """)
            cur.close()
        except Exception as e:
            print(f"[auth] api_keys table setup warning: {e}")

    def load_keys(self):
        """Load all active API keys from the database."""
        conn = _get_db()
        if not conn:
            print("[auth] no database connection, auth disabled")
            return

        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT api_key, tenant_id, name, action_permissions, allow_identity_delegation, metadata
                FROM api_keys
                WHERE is_active = true
            """)
            rows = cur.fetchall()
            cur.close()

            self.keys = {}
            for row in rows:
                permissions = list(row[3]) if len(row) > 3 and row[3] is not None else []
                allow_identity_delegation = bool(row[4]) if len(row) > 4 else False
                metadata = _normalize_metadata(row[5]) if len(row) > 5 else {}
                self.keys[row[0]] = {
                    "tenant_id": row[1],
                    "name": row[2],
                    "permissions": permissions,
                }
                if allow_identity_delegation:
                    self.keys[row[0]]["allow_identity_delegation"] = True
                if metadata:
                    self.keys[row[0]]["metadata"] = metadata
            self.last_refresh = time.time()
            print(f"[auth] loaded {len(self.keys)} active API keys")
        except Exception as e:
            print(f"[auth] failed to load API keys: {e}")

    def refresh_if_needed(self):
        """Refresh keys from DB if cache is stale."""
        if time.time() - self.last_refresh > API_KEY_REFRESH_INTERVAL:
            self.load_keys()

    def validate(self, api_key: str | None) -> dict | None:
        """Validate an API key. Returns key info if valid, None otherwise."""
        if not api_key:
            return None
        self.refresh_if_needed()
        return self.keys.get(api_key)


api_key_manager = ApiKeyManager()


async def api_key_auth_middleware(request: Request, call_next):
    """Middleware to enforce API key or user JWT authentication.

    Auth priority:
    1. X-API-Key header → API key auth
    2. Authorization: Bearer → try API key first, then user JWT
    3. If REQUIRE_AUTH=false, default to tenant_id='system'
    """
    # Skip auth for public discovery endpoints and token exchange. /token validates the subject_token itself.
    if (
        request.url.path == "/health"
        or request.url.path == "/ready"
        or request.url.path == "/.well-known/jwks.json"
        or request.url.path == "/.well-known/oauth-protected-resource"
        or request.url.path == "/enterprise/contract"
        or request.url.path == "/license"
        or request.url.path == "/token"
        or request.method == "OPTIONS"
    ):
        return await call_next(request)

    # Skip auth if REQUIRE_AUTH is false
    if not REQUIRE_AUTH:
        request.state.tenant_id = "system"
        request.state.workspace_id = request.headers.get("X-Workspace-ID", "")
        request.state.auth_type = "none"
        return await call_next(request)

    from fastapi.responses import JSONResponse

    # Extract credentials from headers
    x_api_key = request.headers.get("X-API-Key")
    auth_header = request.headers.get("Authorization", "")
    bearer_token = auth_header[7:] if auth_header.startswith("Bearer ") else None

    # --- Try API key auth first ---
    api_key = x_api_key or bearer_token
    key_info = api_key_manager.validate(api_key) if api_key else None

    if key_info:
        # API key is valid. X-Sealfleet-* identity headers are trusted only
        # for keys explicitly opted in via DB metadata/flag or configured SHA-256 fingerprint.
        effective_tenant_id = str(key_info["tenant_id"])
        effective_user_id: str | None = None
        effective_identity = dict(key_info)
        if _api_key_allows_identity_delegation(api_key, key_info):
            delegated_tenant_id = (request.headers.get("X-Sealfleet-Tenant-Id") or "").strip()
            delegated_user_id = (request.headers.get("X-Sealfleet-User-Id") or "").strip()
            if delegated_tenant_id and delegated_user_id:
                effective_tenant_id = delegated_tenant_id
                effective_user_id = delegated_user_id
                effective_identity.update(
                    {
                        "tenant_id": effective_tenant_id,
                        "user_id": effective_user_id,
                        "delegated_from": {
                            "api_key_tenant_id": str(key_info.get("tenant_id", "")),
                            "api_key_name": str(key_info.get("name", "")),
                            "api_key_sha256_prefix": _api_key_sha256(api_key)[:12],
                        },
                    }
                )
                # Optional: the delegating caller (portal) forwards the user's
                # IdP group claims so group->role mappings apply at request time.
                delegated_groups = (request.headers.get("X-Sealfleet-Groups") or "").strip()
                if delegated_groups:
                    effective_identity["groups"] = [
                        g.strip() for g in delegated_groups.split(",") if g.strip()
                    ]

        request.state.tenant_id = effective_tenant_id
        request.state.workspace_id = request.headers.get("X-Workspace-ID", "")
        request.state.api_key = api_key
        request.state.api_key_id = _api_key_subject_id(api_key)
        request.state.identity = effective_identity
        request.state.auth_type = "api_key"
        if effective_user_id:
            request.state.user_id = effective_user_id

        import time as _time
        _t0 = _time.monotonic()
        response = await call_next(request)
        _elapsed = int((_time.monotonic() - _t0) * 1000)

        _log_api_usage(
            key_id=api_key,
            tenant_id=key_info["tenant_id"],
            tool=request.url.path,
            status_code=response.status_code,
            response_time_ms=_elapsed,
            ip_address=request.client.host if request.client else "",
        )
        return response

    # --- Try user JWT auth (Bearer token that wasn't a valid API key) ---
    if bearer_token:
        jwt_claims = _validate_user_jwt(bearer_token)
        if jwt_claims:
            request.state.tenant_id = jwt_claims["tenant_id"]
            request.state.workspace_id = request.headers.get("X-Workspace-ID") or jwt_claims.get("workspace_id", "")
            request.state.user_id = jwt_claims["user_id"]
            request.state.is_admin = jwt_claims["is_admin"]
            request.state.email = jwt_claims["email"]
            request.state.identity = jwt_claims
            request.state.auth_type = "user_jwt"
            return await call_next(request)

    # --- Neither API key nor JWT valid ---
    return JSONResponse(
        status_code=401,
        content={"error": "Unauthorized"},
    )


def get_tenant_id(request: Request) -> str:
    """Get tenant_id from request state, defaulting to 'system'."""
    return getattr(request.state, "tenant_id", "system")


def get_workspace_id(request: Request) -> str:
    """Get workspace_id from auth metadata/header, defaulting to empty string."""
    return getattr(request.state, "workspace_id", "")


def _api_key_subject_id(api_key: str | None) -> str:
    """Return a non-secret audit subject identifier for an API key."""
    return f"api_key:{_api_key_sha256(api_key or '')[:12]}"


def get_subject_id(request: Request) -> str:
    """Get the authenticated subject that owns caller-scoped resources."""
    return (
        getattr(request.state, "user_id", None)
        or getattr(request.state, "api_key_id", None)
        or (
            _api_key_subject_id(getattr(request.state, "api_key", None))
            if getattr(request.state, "api_key", None)
            else None
        )
        or "system"
    )


def _extract_action_permissions(identity: Mapping | None) -> tuple[set[str] | None, list[str]]:
    """Extract explicit action permissions/scopes and groups from auth metadata.

    Returns (permissions, groups). permissions=None means the identity did not
    include action metadata (legacy API key behavior); permissions=set() means
    explicit deny-all unless DB grants apply.
    """
    if not identity:
        return None, []
    raw_permissions = None
    for key in ("permissions", "actions", "scopes", "scope"):
        if key in identity:
            raw_permissions = identity.get(key)
            break
    permissions: set[str] | None
    if raw_permissions is None:
        permissions = None
    elif isinstance(raw_permissions, str):
        permissions = {p.strip() for p in re.split(r"[\s,]+", raw_permissions) if p.strip()}
    elif isinstance(raw_permissions, (list, tuple, set)):
        permissions = {str(p).strip() for p in raw_permissions if str(p).strip()}
    else:
        permissions = set()

    raw_groups = identity.get("groups") or identity.get("group_ids") or []
    if isinstance(raw_groups, str):
        groups = [g.strip() for g in re.split(r"[\s,]+", raw_groups) if g.strip()]
    elif isinstance(raw_groups, (list, tuple, set)):
        groups = [str(g).strip() for g in raw_groups if str(g).strip()]
    else:
        groups = []
    return permissions, groups


def _permission_set_allows(permissions: set[str] | None, action: str) -> bool:
    if permissions is None:
        return False
    if "*" in permissions or action in permissions:
        return True
    prefix = action.split(".", 1)[0]
    return f"{prefix}.*" in permissions


def _db_has_action_permission(tenant_id: str, user_id: str, groups: list[str], action: str) -> bool:
    """Check direct, role, and SCIM/IdP group-derived action grants."""
    conn = _get_db()
    if not conn or not user_id:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 1
            FROM action_permissions ap
            WHERE ap.tenant_id::text = %s
              AND ap.grantee_type = 'user'
              AND ap.grantee_id::text = %s
              AND (%s = ANY(ap.actions) OR '*' = ANY(ap.actions))
              AND (ap.expires_at IS NULL OR ap.expires_at > NOW())
            LIMIT 1
            """,
            (tenant_id, user_id, action),
        )
        if cur.fetchone():
            cur.close()
            return True

        cur.execute(
            """
            SELECT 1
            FROM action_permissions ap
            JOIN user_roles ur ON ur.role_id = ap.grantee_id
            WHERE ap.tenant_id::text = %s
              AND ap.grantee_type = 'role'
              AND ur.user_id::text = %s
              AND (%s = ANY(ap.actions) OR '*' = ANY(ap.actions))
              AND (ap.expires_at IS NULL OR ap.expires_at > NOW())
            LIMIT 1
            """,
            (tenant_id, user_id, action),
        )
        if cur.fetchone():
            cur.close()
            return True

        if groups:
            cur.execute(
                """
                SELECT 1
                FROM scim_group_role_mappings sgrm
                JOIN roles r ON r.name = ANY(sgrm.role_names) AND r.tenant_id::text = sgrm.tenant_id::text
                JOIN action_permissions ap ON ap.grantee_type = 'role' AND ap.grantee_id = r.id
                WHERE sgrm.tenant_id::text = %s
                  AND sgrm.external_group_id = ANY(%s)
                  AND (%s = ANY(ap.actions) OR '*' = ANY(ap.actions))
                  AND (ap.expires_at IS NULL OR ap.expires_at > NOW())
                LIMIT 1
                """,
                (tenant_id, groups, action),
            )
            if cur.fetchone():
                cur.close()
                return True
        cur.close()
        return False
    except Exception as e:
        if os.getenv("DEBUG", "").lower() in ("1", "true"):
            print(f"[auth] action permission check error: {e}")
        return False


def _remember_authorized_action(request: Request, action: str) -> None:
    """Record actions successfully authorized during this request for background replay."""
    remembered = getattr(request.state, "authorized_actions", None)
    if remembered is None:
        remembered = set()
        request.state.authorized_actions = remembered
    remembered.add(action)


def _authorize_action(request: Request, action: str) -> None:
    """Raise 403 unless the authenticated principal may perform action."""
    auth_type = getattr(request.state, "auth_type", "")
    if auth_type == "none" or getattr(request.state, "is_admin", False):
        _remember_authorized_action(request, action)
        return

    identity = getattr(request.state, "identity", None)
    permissions, groups = _extract_action_permissions(identity)

    # Protected enterprise actions fail closed when the authenticated identity
    # lacks explicit action metadata. Explicit permissions=[] is deny-all.
    if _permission_set_allows(permissions, action):
        _remember_authorized_action(request, action)
        return

    tenant_id = get_tenant_id(request)
    user_id = getattr(request.state, "user_id", "") or (identity or {}).get("user_id", "")
    if _db_has_action_permission(tenant_id, str(user_id), groups, action):
        _remember_authorized_action(request, action)
        return
    raise HTTPException(status_code=403, detail=f"Forbidden: missing permission {action}")


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

async def _idle_scaler_loop():
    """Background task: check for idle MCPs every 60 seconds and scale them down."""
    while True:
        await asyncio.sleep(60)
        try:
            await scale_manager.scale_down_idle()
        except Exception as e:
            print(f"[idle-scaler] error: {e}")


@asynccontextmanager
def _ensure_jobs_tables():
    """Create pipeline_jobs / pipeline_job_steps if missing (mirrors migration 011).

    Self-heal so async jobs / the `workflow` CLI facade work on a fresh deploy
    even if migrations weren't run.
    """
    conn = _get_db()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_jobs (
                job_id TEXT PRIMARY KEY, name TEXT, pipeline_name TEXT,
                status TEXT NOT NULL DEFAULT 'queued', inputs JSONB, result JSONB,
                error TEXT, tenant_id TEXT NOT NULL DEFAULT 'system',
                parent_job_id TEXT, created_at TIMESTAMPTZ DEFAULT now(),
                started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_tenant_created ON pipeline_jobs(tenant_id, created_at DESC);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_parent ON pipeline_jobs(parent_job_id);")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_job_steps (
                step_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, step_name TEXT,
                status TEXT NOT NULL DEFAULT 'queued', inputs JSONB, result JSONB,
                error TEXT, sequence_order INT, created_at TIMESTAMPTZ DEFAULT now(),
                completed_at TIMESTAMPTZ
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_job_steps_job ON pipeline_job_steps(job_id, sequence_order);")
        cur.close()
    except Exception as e:
        print(f"[router] pipeline_jobs table setup warning: {e}")


async def lifespan(app: FastAPI):
    _load_types_yaml()
    _load_manifests_dir()
    _ensure_audit_table()
    _ensure_audit_events_table()
    _ensure_sealed_table()
    _ensure_jobs_tables()
    # Initialize API key authentication
    api_key_manager._ensure_table()
    api_key_manager.load_keys()
    write_pipeline_docs(type_graph, types_registry)
    _load_named_pipelines()
    _load_yaml_pipeline_v2()
    if scale_manager.enabled:
        asyncio.create_task(_idle_scaler_loop())
        print(f"[router] scale-to-zero enabled (idle timeout: {scale_manager.idle_timeout}s)")
    if RETENTION_SCHEDULE_ENABLED:
        asyncio.create_task(_retention_loop())
        print(f"[router] scheduled retention enabled (every {RETENTION_INTERVAL_HOURS}h, "
              f"operational={PRIVACY_OPERATIONAL_RETENTION_DAYS}d, audit archival due after {AUDIT_RETENTION_DAYS}d)")
    auth_status = "enabled" if REQUIRE_AUTH else "disabled (set REQUIRE_AUTH=true to enable)"
    print(f"[router] API key auth: {auth_status}")
    print(f"[router] Runtime Router ready on port 8040")
    yield


app = FastAPI(title="Sealfleet Runtime Router", version="0.2.0", lifespan=lifespan)

# CORS: default-deny cross-origin. `allow_origins=["*"]` with
# `allow_credentials=True` reflects any origin (pentest M2) — replaced with an
# env-driven allowlist (MCPFINDER_CORS_ALLOW_ORIGINS, comma-separated, e.g. the
# portal origin). Unset ⇒ no cross-origin access. Credentials are only allowed
# when an explicit allowlist is configured (never with a wildcard).
_CORS_ALLOW_ORIGINS = [
    o.strip() for o in os.getenv("MCPFINDER_CORS_ALLOW_ORIGINS", "").split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ALLOW_ORIGINS,
    allow_credentials=bool(_CORS_ALLOW_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)

# API key authentication middleware (runs after CORS)
app.add_middleware(BaseHTTPMiddleware, dispatch=api_key_auth_middleware)


def _external_agent_public_record(agent: dict) -> dict:
    record = {
        "mcp": agent["mcp"],
        "tool": "invoke",
        "name": agent["name"],
        "description": agent.get("description", ""),
        "endpoint": agent["endpoint"],
        "protocol": agent["protocol"],
        "timeout_ms": agent["timeout_ms"],
    }
    auth = agent.get("auth") or {}
    if auth:
        record["auth"] = {"type": auth.get("type", "bearer"), "sealed_handle": auth.get("sealed_handle")}
    return record


def register_generated_openapi_demo(data: dict) -> dict:
    """Register the checked-in public-demo OpenAPI MCP manifest in-memory."""
    name = data.get("name")
    if not name:
        raise ValueError("generated OpenAPI demo manifest requires name")
    tool_names = [t.get("name") if isinstance(t, dict) else t for t in data.get("tools", [])]
    manifest = McpManifest(
        name=name,
        endpoint=data.get("endpoint", ""),
        publishes=data.get("publishes", []),
        subscribes=data.get("subscribes", []),
        tools=[str(t) for t in tool_names if t],
        transport=data.get("transport", "stdio"),
        image=data.get("image"),
    )
    manifests[name] = manifest
    typed_manifests[name] = data
    type_graph.register_manifest(data)
    return {"registered": True, "name": name, "tools": manifest.tools}


def _check_external_agent_rate_limit(tenant_id: str, mcp_name: str) -> bool:
    """Return True when the tenant/MCP is still under the in-process quota."""
    key = (tenant_id, mcp_name)
    now = time.time()
    window_start = now - 60
    recent = [ts for ts in _external_agent_rate_limits.get(key, []) if ts >= window_start]
    if len(recent) >= 60:
        _external_agent_rate_limits[key] = recent
        return False
    recent.append(now)
    _external_agent_rate_limits[key] = recent
    return True


async def _invoke_external_agent(
    *,
    request: Request | None,
    agent: dict,
    tool: str,
    inputs: dict,
    tenant_id: str,
    subject_id: str,
    trace_id: str,
    authorized_actions: set[str] | None = None,
) -> dict:
    """Invoke a tenant-owned external agent through a dedicated runtime boundary."""
    mcp_name = agent["mcp"]
    start_time = time.time()

    def audit(result: str, payload: dict | None = None) -> None:
        _write_audit_event(
            action="external_agent.invoke",
            resource=f"{mcp_name}/{tool}",
            server_name=mcp_name,
            result=result,
            trace_id=trace_id,
            duration_ms=round((time.time() - start_time) * 1000),
            payload=payload,
            user_id=subject_id,
            tenant_id=tenant_id,
        )

    if request is not None:
        _authorize_action(request, "agent.invoke")
    elif not _permission_set_allows(authorized_actions, "agent.invoke"):
        audit("denied", {"reason": "missing_agent_invoke_authorization"})
        raise HTTPException(status_code=403, detail="Forbidden: missing permission agent.invoke")

    if agent.get("tenant_id") != tenant_id:
        audit("denied", {"reason": "wrong_tenant"})
        raise HTTPException(403, "Forbidden: external agent belongs to another tenant")
    if tool != "invoke":
        audit("denied", {"reason": "unknown_tool"})
        raise HTTPException(400, "external agents expose only the invoke tool")

    policy_result = policy_engine.check(mcp=mcp_name, tool=tool, user_id=tenant_id)
    if policy_result["action"] == "deny":
        audit("denied", {"rule_id": policy_result.get("rule_id"), "reason": policy_result.get("reason")})
        raise HTTPException(403, f"Policy denied: {policy_result['reason']}")
    if policy_result["action"] == "require_confirm":
        audit("denied", {"rule_id": policy_result.get("rule_id"), "reason": policy_result.get("reason"), "policy_action": "require_confirm"})
        raise HTTPException(409, {"message": _policy_confirmation_required_message(policy_result), "trace_id": trace_id})

    if not _check_external_agent_rate_limit(tenant_id, mcp_name):
        audit("rate_limited", {"reason": "rate_limit_exceeded"})
        raise HTTPException(429, "External agent rate limit exceeded")

    headers = {"Content-Type": "application/json"}
    auth = agent.get("auth") or {}
    if auth.get("type") == "bearer" and auth.get("sealed_handle"):
        resolved = _resolve_handle_from_db(auth["sealed_handle"], tenant_id=tenant_id, subject_id=subject_id)
        if not resolved or not resolved.get("value"):
            audit("denied", {"reason": "sealed_auth_unavailable"})
            raise HTTPException(403, "External agent auth handle could not be resolved")
        headers["Authorization"] = f"Bearer {resolved['value']}"

    timeout = max(0.05, min(float(agent.get("timeout_ms", 1000)) / 1000.0, 10.0))
    payload = {"jsonrpc": "2.0", "method": "invoke", "params": inputs, "id": trace_id}
    _assert_safe_endpoint(agent["endpoint"], resolve=False)  # fetch-time SSRF re-check
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(agent["endpoint"], json=payload, headers=headers)
        if resp.status_code >= 400:
            audit("error", {"status_code": resp.status_code})
            raise HTTPException(502, "External agent call failed")
        data = resp.json()
        result = data.get("result", data) if isinstance(data, dict) else data
        audit("ok")
        return result
    except httpx.TimeoutException:
        audit("timeout", {"reason": "timeout"})
        raise HTTPException(504, "External agent timeout")
    except HTTPException:
        raise
    except Exception as e:
        audit("error", {"error": type(e).__name__})
        raise HTTPException(502, f"External agent call failed: {type(e).__name__}")


@app.post("/external-agents", status_code=201)
async def register_external_agent(req: ExternalAgentRegistrationRequest, request: Request):
    _authorize_action(request, "agent.register")
    tenant_id = get_tenant_id(request)
    if req.protocol != "json_rpc":
        raise HTTPException(400, "Only json_rpc external agents are supported")
    _assert_safe_endpoint(req.endpoint)
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}$", req.name):
        raise HTTPException(400, "name must be alphanumeric with optional _ or -")

    mcp_name = f"agent:{req.name}"
    existing_owner = _registry_item_tenants.get(f"manifest:{mcp_name}")
    if existing_owner and existing_owner not in {"system", tenant_id}:
        raise HTTPException(409, "external agent name is already registered by another tenant")
    auth = req.auth.model_dump() if req.auth else None
    agent = {
        "name": req.name,
        "mcp": mcp_name,
        "description": req.description,
        "endpoint": req.endpoint,
        "protocol": req.protocol,
        "auth": auth,
        "timeout_ms": max(50, min(req.timeout_ms, 10_000)),
        "tenant_id": tenant_id,
    }
    external_agents[mcp_name] = agent
    manifests[mcp_name] = McpManifest(
        name=mcp_name,
        endpoint=req.endpoint,
        publishes=[],
        subscribes=[],
        tools=["invoke"],
        transport="external_agent",
    )
    _registry_item_tenants[f"manifest:{mcp_name}"] = tenant_id
    _registry_item_tenants[f"external_agent:{mcp_name}"] = tenant_id
    return _external_agent_public_record(agent)



# ---------------------------------------------------------------------------
# Manifest Management
# ---------------------------------------------------------------------------

@app.post("/manifests", status_code=201)
async def register_manifest(manifest: McpManifest, request: Request):
    _authorize_action(request, "mcp.server.register")
    incoming_ep = manifest.endpoint
    # If this MCP was seeded from YAML, keep the YAML endpoint (authoritative)
    # so that self-registering MCPs cannot override the local routing.
    if manifest.name in _yaml_seeded_endpoints:
        manifest.endpoint = _yaml_seeded_endpoints[manifest.name]
        print(f"[router] POST /manifests: {manifest.name} tried {incoming_ep}, forced to {manifest.endpoint}")
    else:
        # Caller-supplied endpoint: SSRF guard (seeded endpoints are trusted).
        _assert_safe_endpoint(manifest.endpoint)
        print(f"[router] POST /manifests: {manifest.name} -> {manifest.endpoint}")
    _merge_seeded_manifest_guards(manifest)
    manifests[manifest.name] = manifest
    _registry_item_tenants[f"manifest:{manifest.name}"] = get_tenant_id(request)
    return {"status": "registered", "mcp": manifest.name}


@app.post("/manifests/typed", status_code=201)
async def register_typed_manifest(request: Request):
    _authorize_action(request, "mcp.server.register")
    """Register a manifest with typed inputs/outputs. Validates types on register."""
    data = await request.json()
    name = data.get("name")
    if not name:
        raise HTTPException(400, "manifest must have a 'name' field")

    # Extract tool names for backward-compat McpManifest
    tool_names = []
    for t in data.get("tools", []):
        if isinstance(t, dict):
            tool_names.append(t["name"])
        else:
            tool_names.append(t)

    # If this MCP was seeded from YAML, keep the YAML endpoint (authoritative);
    # otherwise SSRF-guard the caller-supplied endpoint.
    if name in _yaml_seeded_endpoints:
        effective_endpoint = _yaml_seeded_endpoints[name]
    else:
        effective_endpoint = data.get("endpoint", "")
        if effective_endpoint and data.get("transport", "http") != "stdio":
            _assert_safe_endpoint(effective_endpoint)

    manifest = McpManifest(
        name=name,
        endpoint=effective_endpoint,
        publishes=data.get("publishes", []),
        subscribes=data.get("subscribes", []),
        tools=tool_names,
        transport=data.get("transport", "http"),
        image=data.get("image"),
        access=_parse_manifest_access(data.get("access")),
        pii_fields=_parse_manifest_pii_fields(data),
    )
    _merge_seeded_manifest_guards(manifest)
    manifests[name] = manifest
    _registry_item_tenants[f"manifest:{name}"] = get_tenant_id(request)
    data["endpoint"] = effective_endpoint  # keep typed_manifests consistent
    typed_manifests[name] = data
    _registry_item_tenants[f"typed_manifest:{name}"] = get_tenant_id(request)
    type_graph.register_manifest(data)

    issues = type_graph.validate_all_manifests(typed_manifests, types_registry)
    write_pipeline_docs(type_graph, types_registry)

    return {"registered": name, "warnings": issues}


@app.get("/manifests")
async def list_manifests(request: Request):
    tenant_id = get_tenant_id(request)
    return [
        m.model_dump()
        for name, m in manifests.items()
        if _registry_item_visible("manifest", name, tenant_id)
    ]


@app.get("/manifests/{name}")
async def get_manifest(name: str, request: Request):
    tenant_id = get_tenant_id(request)
    if name not in manifests or not _registry_item_visible("manifest", name, tenant_id):
        raise HTTPException(404, f"manifest '{name}' not found")
    # Return typed manifest if available, otherwise basic
    if name in typed_manifests and (
        _registry_item_visible("typed_manifest", name, tenant_id)
        or _registry_item_visible("manifest", name, tenant_id)
    ):
        return typed_manifests[name]
    return manifests[name].model_dump()


# ---------------------------------------------------------------------------
# Registry Import/Export
# ---------------------------------------------------------------------------

REGISTRY_EXPORT_SCHEMA = "mcpfinder.registry.export"
REGISTRY_EXPORT_SCHEMA_VERSION = 1


def _registry_redact(value):
    """Recursively redact secret-like registry export keys."""
    secret_markers = (
        "secret",
        "sealed",
        "auth",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "password",
        "credential",
        "private_key",
        "access_key",
        "encrypted",
    )
    if isinstance(value, dict):
        redacted = {}
        for key, nested in value.items():
            if _contains_secret_marker(key, secret_markers):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = _registry_redact(nested)
        return redacted
    if isinstance(value, list):
        return [_registry_redact(item) for item in value]
    return value


def _registry_item_owned(kind: str, name: str, tenant_id: str) -> bool:
    return _registry_item_tenants.get(f"{kind}:{name}", "system") == tenant_id


def _registry_item_visible(kind: str, name: str, tenant_id: str) -> bool:
    owner = _registry_item_tenants.get(f"{kind}:{name}", "system")
    return owner in {"system", tenant_id}


def _registry_item_result(kind: str, item: object, *, dry_run: bool, tenant_id: str) -> dict:
    name = item.get("name", "") if isinstance(item, dict) else getattr(item, "name", "")
    result = {"kind": kind, "name": name or "<missing>", "status": "validated" if dry_run else "applied"}
    try:
        if kind == "manifest":
            manifest = McpManifest(**item)
            if not dry_run:
                manifests[manifest.name] = manifest
                _registry_item_tenants[f"manifest:{manifest.name}"] = tenant_id
        elif kind == "typed_manifest":
            if not isinstance(item, dict):
                raise ValueError("typed manifest must be an object")
            if not name:
                raise ValueError("typed manifest must have a 'name' field")
            tool_names = []
            for tool in item.get("tools", []):
                tool_names.append(tool.get("name") if isinstance(tool, dict) else tool)
            manifest = McpManifest(
                name=name,
                endpoint=item.get("endpoint", ""),
                publishes=item.get("publishes", []),
                subscribes=item.get("subscribes", []),
                tools=tool_names,
                transport=item.get("transport", "http"),
                image=item.get("image"),
            )
            if not dry_run:
                manifests[manifest.name] = manifest
                typed_manifests[name] = dict(item)
                type_graph.register_manifest(item)
                _registry_item_tenants[f"manifest:{manifest.name}"] = tenant_id
                _registry_item_tenants[f"typed_manifest:{name}"] = tenant_id
        elif kind == "pipeline":
            pipeline = NamedPipeline(**item)
            if not dry_run:
                named_pipelines[pipeline.name] = pipeline
                _registry_item_tenants[f"pipeline:{pipeline.name}"] = tenant_id
        else:
            raise ValueError(f"unsupported registry item kind: {kind}")
    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
    return result


@app.get("/registry/export")
async def export_registry(request: Request):
    _authorize_action(request, "registry.export")
    tenant_id = get_tenant_id(request)
    manifest_items = [
        _registry_redact(manifest.model_dump())
        for name, manifest in manifests.items()
        if _registry_item_owned("manifest", name, tenant_id)
    ]
    typed_items = [
        _registry_redact(manifest)
        for name, manifest in typed_manifests.items()
        if _registry_item_owned("typed_manifest", name, tenant_id)
        or _registry_item_owned("manifest", name, tenant_id)
    ]
    pipeline_items = [
        _registry_redact(pipeline.model_dump())
        for name, pipeline in named_pipelines.items()
        if _registry_item_owned("pipeline", name, tenant_id)
    ]
    _write_audit_event(
        action="registry.export",
        resource="registry",
        result="ok",
        trace_id=_request_trace_id(request),
        payload={
            "tenant_id": tenant_id,
            "manifests": len(manifest_items),
            "typed_manifests": len(typed_items),
            "pipelines": len(pipeline_items),
        },
        user_id=get_subject_id(request),
        tenant_id=tenant_id,
    )
    return {
        "schema": REGISTRY_EXPORT_SCHEMA,
        "schema_version": REGISTRY_EXPORT_SCHEMA_VERSION,
        "tenant_id": tenant_id,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "manifests": manifest_items,
        "typed_manifests": typed_items,
        "pipelines": pipeline_items,
    }


@app.post("/registry/import")
async def import_registry(request: Request, dry_run: bool = True):
    _authorize_action(request, "registry.import")
    tenant_id = get_tenant_id(request)
    try:
        bundle = await request.json()
    except Exception:
        raise HTTPException(400, "request body must be valid JSON")
    if not isinstance(bundle, dict):
        raise HTTPException(400, "request body must be a JSON object")
    if bundle.get("schema") != REGISTRY_EXPORT_SCHEMA:
        raise HTTPException(400, "invalid registry export schema")
    if bundle.get("schema_version") != REGISTRY_EXPORT_SCHEMA_VERSION:
        raise HTTPException(400, "invalid registry export schema_version")
    if bundle.get("tenant_id") != tenant_id:
        raise HTTPException(400, "bundle tenant_id must match authenticated tenant_id")

    items = []
    for kind, field in (
        ("manifest", "manifests"),
        ("typed_manifest", "typed_manifests"),
        ("pipeline", "pipelines"),
    ):
        for item in bundle.get(field, []) or []:
            items.append(_registry_item_result(kind, item, dry_run=dry_run, tenant_id=tenant_id))

    applied_count = sum(1 for item in items if item["status"] == "applied")
    validated_count = sum(1 for item in items if item["status"] in {"validated", "applied"})
    summary = {
        "applied": applied_count,
        "validated": validated_count,
        "errors": sum(1 for item in items if item["status"] == "error"),
    }
    _write_audit_event(
        action="registry.import",
        resource="registry",
        result="ok" if summary["errors"] == 0 else "partial",
        trace_id=_request_trace_id(request),
        payload={"tenant_id": tenant_id, "dry_run": dry_run, "summary": summary},
        user_id=get_subject_id(request),
        tenant_id=tenant_id,
    )
    return {"dry_run": dry_run, "summary": summary, "items": items}


# ---------------------------------------------------------------------------
# Type System Endpoints
# ---------------------------------------------------------------------------

@app.get("/types")
async def list_types():
    """List the type registry."""
    return {"types": types_registry}


@app.get("/capabilities")
async def list_capabilities():
    """Returns all producible types with their resolved chains."""
    capabilities = {}
    for type_name, producers in type_graph.producers.items():
        type_def = types_registry.get(type_name, {})
        if type_def.get("primitive"):
            continue
        try:
            chain = type_graph.resolve(type_name, {}, types_registry, strict=False)
            required = _get_required_inputs(chain, type_graph, types_registry)
            capabilities[type_name] = {
                "chain": [f"{s['mcp']}.{s['tool']}" for s in chain],
                "required_inputs": required,
            }
        except ValueError:
            pass
    return {"capabilities": capabilities}




# ---------------------------------------------------------------------------
# Pipeline Orchestration (original — still works)
# ---------------------------------------------------------------------------

def _policy_confirmation_required_message(policy_result: Mapping) -> str:
    reason = policy_result.get("reason") or "confirmation required"
    return f"Policy confirmation required: {reason}"


def _audit_policy_confirmation_required(
    *,
    mcp: str,
    tool: str,
    policy_result: Mapping,
    user_id: str,
    tenant_id: str,
    trace_id: str = "",
) -> None:
    _write_audit_event(
        action="policy_confirm_required",
        resource=f"{mcp}/{tool}",
        server_name=mcp,
        result="denied",
        trace_id=trace_id,
        payload={"rule_id": policy_result.get("rule_id"), "reason": policy_result.get("reason")},
        user_id=user_id,
        tenant_id=tenant_id,
    )


async def _execute_mcp_tool(
    client: httpx.AsyncClient,
    *,
    manifest: McpManifest,
    mcp_name: str,
    tool: str,
    inputs: dict,
    trace_id: str,
    tenant_id: str,
    subject_id: str | None = None,
    pipeline_name: str = "",
) -> tuple[object, str | None]:
    """Shared MCP execution boundary: hooks wrap both HTTP and Docker stdio transports."""
    transport = "stdio" if manifest.transport == "stdio" and manifest.image else "http"
    audit_subject = subject_id or tenant_id
    declared_pii = getattr(manifest, "pii_fields", None) or {}
    pii_paths = tuple(dict.fromkeys(
        list(declared_pii.get("*", [])) + list(declared_pii.get(tool, []))
    ))
    ctx = RuntimeHookContext(
        trace_id=trace_id,
        tenant_id=tenant_id,
        subject_id=audit_subject,
        mcp=mcp_name,
        tool=tool,
        transport=transport,
        pipeline_name=pipeline_name,
        pii_fields=pii_paths,
    )
    audit_start = len(runtime_hook_manager.audit_events)
    try:
        call_inputs = await runtime_hook_manager.run_pre_call(ctx, inputs)
        if transport == "stdio":
            try:
                result = await run_docker_stdio(manifest.image or "", tool, call_inputs)
                error = None
            except RuntimeError as exc:
                result = {"error": str(exc)}
                error = str(exc)
        else:
            # Fetch-time SSRF re-check (cheap, no per-call DNS): registration
            # already did the resolving check; this catches literal-IP/scheme.
            if manifest.name not in _yaml_seeded_endpoints:
                _assert_safe_endpoint(manifest.endpoint, resolve=False)
            await scale_manager.ensure_running(mcp_name)
            try:
                resp = await client.post(
                    f"{manifest.endpoint}/call",
                    json={"tool": tool, "inputs": call_inputs},
                )
                resp.raise_for_status()
                result = resp.json()
                error = None
            except httpx.HTTPStatusError as exc:
                result = {"error": f"HTTP {exc.response.status_code}", "detail": exc.response.text}
                error = str(exc)
            except Exception as exc:
                result = {"error": str(exc)}
                error = str(exc)
            await scale_manager.record_call(mcp_name)
        if error is None:
            result = await runtime_hook_manager.run_post_call(ctx, result)
        return result, error
    finally:
        _write_runtime_hook_audit_events(runtime_hook_manager.audit_events[audit_start:])


@app.post("/pipeline")
async def run_pipeline(req: PipelineRequest, request: Request):
    tenant_id = get_tenant_id(request)
    subject_id = get_subject_id(request)
    trace_id = _request_trace_id(request)
    results = []
    trace_entries = []
    start_time = time.time()

    # Pre-check MCP permissions for user identities (JWT or delegated API key)
    for step in req.steps:
        _enforce_user_mcp_access(request, step.mcp, step.tool)

    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, step in enumerate(req.steps):
            step_start = time.time()

            # Resolve MCP endpoint
            manifest = manifests.get(step.mcp)
            if not manifest:
                raise HTTPException(400, f"unknown MCP: '{step.mcp}'")
            if step.tool not in manifest.tools:
                raise HTTPException(400, f"tool '{step.tool}' not in manifest for '{step.mcp}'")

            # Policy check
            policy_result = policy_engine.check(mcp=step.mcp, tool=step.tool, user_id=tenant_id)
            if policy_result["action"] == "deny":
                _write_audit_event(
                    action="policy_deny",
                    resource=f"{step.mcp}/{step.tool}",
                    server_name=step.mcp,
                    result="denied",
                    trace_id=trace_id,
                    payload={"rule_id": policy_result["rule_id"], "reason": policy_result["reason"]},
                    user_id=subject_id,
                    tenant_id=tenant_id,
                )
                raise HTTPException(403, f"Policy denied: {policy_result['reason']}")
            if policy_result["action"] == "require_confirm":
                _audit_policy_confirmation_required(
                    mcp=step.mcp,
                    tool=step.tool,
                    policy_result=policy_result,
                    user_id=subject_id,
                    tenant_id=tenant_id,
                    trace_id=trace_id,
                )
                raise HTTPException(
                    409,
                    {
                        "message": _policy_confirmation_required_message(policy_result),
                        "trace_id": trace_id,
                    },
                )

            # Resolve sealed handles in inputs using caller ownership scope.
            resolved_inputs = await resolve_sealed_inputs(
                step.inputs,
                tenant_id=tenant_id,
                subject_id=subject_id,
                trace_id=trace_id,
            )

            step_error = None
            if manifest.transport == "external_agent":
                agent = external_agents.get(step.mcp)
                if not agent:
                    step_result = {"error": f"External agent '{step.mcp}' not found"}
                    step_error = step_result["error"]
                else:
                    try:
                        step_result = await _invoke_external_agent(
                            request=request,
                            agent=agent,
                            tool=step.tool,
                            inputs=resolved_inputs,
                            tenant_id=tenant_id,
                            subject_id=subject_id,
                            trace_id=trace_id,
                        )
                    except HTTPException as e:
                        step_result = {"error": str(e.detail)}
                        step_error = str(e.detail)
            else:
                try:
                    step_result, step_error = await _execute_mcp_tool(
                        client,
                        manifest=manifest,
                        mcp_name=step.mcp,
                        tool=step.tool,
                        inputs=resolved_inputs,
                        trace_id=trace_id,
                        tenant_id=tenant_id,
                        subject_id=subject_id,
                        pipeline_name="pipeline",
                    )
                except PermissionError as exc:
                    raise HTTPException(403, f"Runtime hook denied: {exc}") from exc

            step_duration = time.time() - step_start
            duration_ms = round(step_duration * 1000)

            results.append({
                "step": i,
                "mcp": step.mcp,
                "tool": step.tool,
                "result": step_result,
                "duration_ms": duration_ms,
            })

            trace_entries.append({
                "step": i,
                "mcp": step.mcp,
                "tool": step.tool,
                "channel": manifest.publishes[0] if manifest.publishes else None,
                "action": "pipeline_call",
                "timestamp": time.time(),
                "duration_ms": duration_ms,
            })

            # Audit (legacy channel log)
            _audit_log(
                channel=manifest.publishes[0] if manifest.publishes else "pipeline",
                publisher=step.mcp,
                action="pipeline",
                payload=step_result,
                message_id=trace_id,
            )
            # Structured audit event
            _write_audit_event(
                action="tool_call",
                resource=f"{step.mcp}/{step.tool}",
                server_name=step.mcp,
                result="error" if step_error else "ok",
                trace_id=trace_id,
                duration_ms=duration_ms,
                payload={"error": step_error} if step_error else None,
                user_id=subject_id,
                tenant_id=tenant_id,
            )

    total_duration = time.time() - start_time

    return {
        "trace_id": trace_id,
        "total_duration_ms": round(total_duration * 1000),
        "steps": results,
        "trace": trace_entries,
    }


# ---------------------------------------------------------------------------
# Named Pipeline Endpoints
# ---------------------------------------------------------------------------

def _pipeline_input_schema(pipeline: NamedPipeline) -> dict:
    """Build a JSON Schema-style inputSchema from pipeline inputs."""
    properties = {}
    required = []
    for param_name, param_def in pipeline.inputs.items():
        if isinstance(param_def, dict):
            type_str = param_def.get("type", "string")
            desc = param_def.get("description", param_name)
        else:
            type_str = param_def
            desc = param_name
        json_type = "string" if type_str in ("String", "string") else (
            "number" if type_str in ("Float", "float", "Integer", "integer", "Number") else "string"
        )
        properties[param_name] = {"type": json_type, "description": desc}
        required.append(param_name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


@app.post("/pipelines/register", status_code=201)
async def register_pipeline(req: RegisterPipelineRequest, request: Request):
    """Register a named pipeline. Validates stages reference registered MCPs."""
    _authorize_action(request, "mcp.server.register")
    pipeline = req.pipeline
    # Validate stages
    for stage in pipeline.stages:
        if stage.mcp not in manifests:
            raise HTTPException(400, f"Stage '{stage.name}' references unknown MCP '{stage.mcp}'. "
                                     f"Registered: {list(manifests.keys())}")
        manifest = manifests[stage.mcp]
        if stage.tool not in manifest.tools:
            raise HTTPException(400, f"Stage '{stage.name}' references unknown tool '{stage.tool}' "
                                     f"in MCP '{stage.mcp}'. Available: {manifest.tools}")
    # Validate output_stage exists
    stage_names = [s.name for s in pipeline.stages]
    if pipeline.output_stage not in stage_names:
        raise HTTPException(400, f"output_stage '{pipeline.output_stage}' not found in stages: {stage_names}")

    if not pipeline.created_at:
        pipeline.created_at = datetime.now(timezone.utc).isoformat()

    named_pipelines[pipeline.name] = pipeline
    _registry_item_tenants[f"pipeline:{pipeline.name}"] = get_tenant_id(request)
    return {"status": "registered", "name": pipeline.name}




@app.get("/pipelines")
async def list_pipelines():
    """List all named pipelines."""
    result = [
        {
            "name": p.name,
            "description": p.description,
            "inputs": _pipeline_input_schema(p),
            "tags": p.tags,
            "stages": [{"name": s.name, "description": getattr(s, "description", s.name)} for s in p.stages],
            "output_stage": p.output_stage,
        }
        for p in named_pipelines.values()
    ]
    result.extend(EXTRA_PIPELINE_LISTINGS)
    return result


@app.get("/pipelines/tools")
async def pipelines_as_tools():
    """Expose named pipelines as MCP-style tools list."""
    tools = []
    for p in named_pipelines.values():
        tools.append({
            "name": p.name,
            "description": p.description,
            "inputSchema": _pipeline_input_schema(p),
        })
    return {"tools": tools}


@app.post("/pipelines/tools/call")
async def call_pipeline_tool(req: CallPipelineToolRequest, request: Request):
    """Call a named pipeline as an MCP tool."""
    _authorize_action(request, "pipeline.invoke")
    tenant_id = get_tenant_id(request)
    if req.name not in named_pipelines:
        raise HTTPException(404, f"Pipeline '{req.name}' not found")
    pipeline = named_pipelines[req.name]
    result = await _run_named_pipeline(
        pipeline,
        req.arguments,
        tenant_id=tenant_id,
        subject_id=get_subject_id(request),
        request=request,
    )
    return {
        "content": [
            {"type": "text", "text": json.dumps(result.get("final", result))}
        ],
        "isError": "error" in result,
    }


@app.post("/call")
async def call_mcp_tool(request: Request):
    """Generic MCP tool proxy. Body: {mcp, tool, inputs}"""
    tenant_id = get_tenant_id(request)
    subject_id = get_subject_id(request)
    trace_id = _request_trace_id(request)
    start_time = time.time()
    body = await request.json()
    mcp_name = body.get("mcp")
    tool = body.get("tool")
    # Accept "args" as an alias for "inputs" — callers commonly send either,
    # and silently defaulting to {} produced opaque downstream tool errors.
    inputs = body.get("inputs", body.get("args", {}))

    if not mcp_name or not tool:
        raise HTTPException(400, "mcp and tool are required")

    manifest = manifests.get(mcp_name)
    if not manifest:
        raise HTTPException(404, f"MCP '{mcp_name}' not found. Registered: {list(manifests.keys())}")
    if tool not in manifest.tools:
        raise HTTPException(400, f"tool '{tool}' not in manifest for '{mcp_name}'")
    if manifest.transport == "external_agent":
        agent = external_agents.get(mcp_name)
        if not agent:
            raise HTTPException(404, f"External agent '{mcp_name}' not found")
        return await _invoke_external_agent(
            request=request,
            agent=agent,
            tool=tool,
            inputs=inputs,
            tenant_id=tenant_id,
            subject_id=subject_id,
            trace_id=trace_id,
        )

    # Permission check for user identities (portal JWTs and delegated API-key
    # identities). Enforces per-MCP grants, per-tool allowed_tools, IdP group
    # claim mappings, and manifest-declared access gates.
    _enforce_user_mcp_access(request, mcp_name, tool)

    policy_result = policy_engine.check(mcp=mcp_name, tool=tool, user_id=tenant_id)
    if policy_result["action"] == "deny":
        _write_audit_event(
            action="policy_deny",
            resource=f"{mcp_name}/{tool}",
            server_name=mcp_name,
            result="denied",
            trace_id=trace_id,
            payload={"rule_id": policy_result["rule_id"], "reason": policy_result["reason"]},
            user_id=subject_id,
            tenant_id=tenant_id,
        )
        raise HTTPException(403, f"Policy denied: {policy_result['reason']}")
    if policy_result["action"] == "require_confirm":
        _audit_policy_confirmation_required(
            mcp=mcp_name,
            tool=tool,
            policy_result=policy_result,
            user_id=subject_id,
            tenant_id=tenant_id,
            trace_id=trace_id,
        )
        raise HTTPException(
            409,
            {"message": _policy_confirmation_required_message(policy_result), "trace_id": trace_id},
        )

    resolved_inputs = await resolve_sealed_inputs(
        inputs,
        tenant_id=tenant_id,
        subject_id=subject_id,
        trace_id=trace_id,
    )

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            result, step_error = await _execute_mcp_tool(
                client,
                manifest=manifest,
                mcp_name=mcp_name,
                tool=tool,
                inputs=resolved_inputs,
                trace_id=trace_id,
                tenant_id=tenant_id,
                subject_id=subject_id,
                pipeline_name="direct_call",
            )
        except PermissionError as exc:
            raise HTTPException(403, f"Runtime hook denied: {exc}") from exc

    duration_ms = round((time.time() - start_time) * 1000)
    _write_audit_event(
        action="tool_call",
        resource=f"{mcp_name}/{tool}",
        server_name=mcp_name,
        result="error" if step_error else "ok",
        trace_id=trace_id,
        duration_ms=duration_ms,
        payload={"error": step_error} if step_error else None,
        user_id=subject_id,
        tenant_id=tenant_id,
    )
    if step_error:
        raise HTTPException(502, f"MCP call failed: {step_error}")
    if isinstance(result, dict):
        return {**result, "trace_id": trace_id}
    return {"result": result, "trace_id": trace_id}


@app.get("/pipelines/{name}/type-check")
async def type_check_pipeline(name: str):
    """Run type compatibility check on a named pipeline."""
    if name not in named_pipelines:
        raise HTTPException(404, f"Pipeline '{name}' not found")
    p = named_pipelines[name]
    tw = _validate_pipeline_types(p)
    has_errors = any(w["has_errors"] for w in tw)
    has_warnings = any(w["has_warnings"] for w in tw)
    status = "error" if has_errors else ("warning" if has_warnings else "ok")
    return {
        "pipeline": name,
        "status": status,
        "type_warnings": tw,
        "stage_count": len(p.stages),
        "checked_connections": len(tw),
    }


@app.get("/pipelines/{name}")
async def get_pipeline(name: str):
    """Get a single named pipeline definition."""
    if name not in named_pipelines:
        raise HTTPException(404, f"Pipeline '{name}' not found")
    p = named_pipelines[name]
    data = p.model_dump()
    data["type_warnings"] = _validate_pipeline_types(p)
    return data


@app.post("/pipelines/{name}/run")
async def run_named_pipeline_endpoint(name: str, req: RunNamedPipelineRequest, request: Request):
    """Run a named pipeline by name."""
    _authorize_action(request, "pipeline.invoke")
    tenant_id = get_tenant_id(request)
    if name not in named_pipelines:
        raise HTTPException(404, f"Pipeline '{name}' not found")
    pipeline = named_pipelines[name]

    # Pre-check MCP permissions for user identities (JWT or delegated API key)
    for stage in pipeline.stages:
        _enforce_user_mcp_access(request, stage.mcp, stage.tool)

    # Validate required inputs
    for param_name in pipeline.inputs:
        if param_name not in req.inputs:
            raise HTTPException(400, f"Missing required input: '{param_name}'")

    result = await _run_named_pipeline(
        pipeline,
        req.inputs,
        tenant_id=tenant_id,
        subject_id=get_subject_id(request),
        request=request,
    )
    result["pipeline_name"] = name
    return result


async def _run_named_pipeline(
    pipeline: NamedPipeline,
    inputs: dict,
    tenant_id: str = "system",
    subject_id: str | None = None,
    request: Request | None = None,
    authorized_actions: set[str] | None = None,
) -> dict:
    """Execute a named pipeline's stages in order."""
    trace_id = uuid.uuid4().hex[:16]
    audit_subject_id = subject_id or tenant_id
    results = []
    trace_entries = []
    start_time = time.time()
    last_result = None

    for i, stage in enumerate(pipeline.stages):
        step_start = time.time()

        manifest = manifests.get(stage.mcp)
        if not manifest:
            return {"error": f"Unknown MCP: '{stage.mcp}'", "trace_id": trace_id}

        # Policy check
        policy_result = policy_engine.check(mcp=stage.mcp, tool=stage.tool, user_id=tenant_id)
        if policy_result["action"] == "deny":
            _write_audit_event(
                action="policy_deny",
                resource=f"{stage.mcp}/{stage.tool}",
                server_name=stage.mcp,
                result="denied",
                trace_id=trace_id,
                payload={"rule_id": policy_result["rule_id"], "reason": policy_result["reason"]},
                user_id=audit_subject_id,
                tenant_id=tenant_id,
            )
            return {"error": f"Policy denied: {policy_result['reason']}", "trace_id": trace_id}
        if policy_result["action"] == "require_confirm":
            _audit_policy_confirmation_required(
                mcp=stage.mcp,
                tool=stage.tool,
                policy_result=policy_result,
                user_id=audit_subject_id,
                tenant_id=tenant_id,
                trace_id=trace_id,
            )
            return {
                "error": _policy_confirmation_required_message(policy_result),
                "trace_id": trace_id,
                "policy_action": "require_confirm",
                "rule_id": policy_result.get("rule_id"),
            }

        # Build call inputs
        call_inputs = {}

        # If stage reads from a channel (input_channel), inject last result
        if stage.input_channel and last_result:
            call_inputs.update(last_result)
        else:
            # Pass raw inputs for the first stage or non-channel stages
            call_inputs.update(inputs)

        # Also pass raw inputs (primitives) so tools can access them
        for k, v in inputs.items():
            if k not in call_inputs:
                call_inputs[k] = v

        # Resolve sealed handles in inputs using caller ownership scope.
        call_inputs = await resolve_sealed_inputs(
            call_inputs,
            tenant_id=tenant_id,
            subject_id=audit_subject_id,
            trace_id=trace_id,
        )

        # Dispatch by transport mode
        step_error = None
        if manifest.transport == "external_agent":
            agent = external_agents.get(stage.mcp)
            if not agent:
                step_result = {"error": f"External agent '{stage.mcp}' not found"}
                step_error = step_result["error"]
            else:
                try:
                    step_result = await _invoke_external_agent(
                        request=request,
                        agent=agent,
                        tool=stage.tool,
                        inputs=call_inputs,
                        tenant_id=tenant_id,
                        subject_id=audit_subject_id,
                        trace_id=trace_id,
                        authorized_actions=authorized_actions,
                    )
                except HTTPException as e:
                    if e.status_code in (401, 403):
                        raise
                    step_result = {"error": str(e.detail)}
                    step_error = str(e.detail)
        else:
            try:
                async with httpx.AsyncClient(timeout=300.0) as client:
                    step_result, step_error = await _execute_mcp_tool(
                        client,
                        manifest=manifest,
                        mcp_name=stage.mcp,
                        tool=stage.tool,
                        inputs=call_inputs,
                        trace_id=trace_id,
                        tenant_id=tenant_id,
                        subject_id=audit_subject_id,
                        pipeline_name=pipeline.name,
                    )
            except PermissionError as exc:
                return {"error": f"Runtime hook denied: {exc}", "trace_id": trace_id}

        step_duration = time.time() - step_start
        duration_ms = round(step_duration * 1000)
        last_result = step_result

        results.append({
            "step": i,
            "stage": stage.name,
            "mcp": stage.mcp,
            "tool": stage.tool,
            "result": step_result,
            "duration_ms": duration_ms,
        })

        trace_entries.append({
            "step": i,
            "stage": stage.name,
            "mcp": stage.mcp,
            "tool": stage.tool,
            "channel": stage.output_channel,
            "action": "named_pipeline",
            "timestamp": time.time(),
            "duration_ms": duration_ms,
        })

        _audit_log(
            channel=stage.output_channel or "pipeline",
            publisher=stage.mcp,
            action="named_pipeline",
            payload=step_result,
            message_id=trace_id,
        )
        _write_audit_event(
            action="tool_call",
            resource=f"{stage.mcp}/{stage.tool}",
            server_name=stage.mcp,
            result="error" if step_error else "ok",
            trace_id=trace_id,
            duration_ms=duration_ms,
            payload={"error": step_error} if step_error else None,
            user_id=audit_subject_id,
            tenant_id=tenant_id,
        )

    total_duration = time.time() - start_time
    return {
        "trace_id": trace_id,
        "total_duration_ms": round(total_duration * 1000),
        "steps": results,
        "trace": trace_entries,
        "final": last_result,
    }


# ---------------------------------------------------------------------------
# Scale-to-zero admin endpoints
# ---------------------------------------------------------------------------

@app.get("/scale/status")
async def scale_status():
    """Get current scale status of all managed MCPs."""
    if not scale_manager.enabled:
        return {"enabled": False}

    scale_manager._init_k8s()
    if not scale_manager.enabled:
        return {"enabled": False, "error": "k8s init failed"}

    status = {}
    for mcp_name, deploy_name in MCP_DEPLOYMENT_MAP.items():
        try:
            deploy = await asyncio.to_thread(
                scale_manager._apps_v1.read_namespaced_deployment,
                name=deploy_name, namespace=scale_manager.namespace,
            )
            last = scale_manager.last_call.get(deploy_name)
            idle_secs = int(time.time() - last) if last else None
            status[mcp_name] = {
                "deployment": deploy_name,
                "replicas": deploy.spec.replicas or 0,
                "ready_replicas": deploy.status.ready_replicas or 0,
                "last_call_ago_secs": idle_secs,
                "idle_timeout_secs": scale_manager.idle_timeout,
            }
        except Exception as e:
            status[mcp_name] = {"error": str(e)}

    return {"enabled": True, "mcps": status}


@app.post("/scale/{mcp_name}/up")
async def scale_up(mcp_name: str, request: Request):
    """Manually scale up an MCP (privileged: manage-MCP authority)."""
    _authorize_action(request, "mcp.server.register")
    if not scale_manager.enabled:
        raise HTTPException(400, "Scale-to-zero is not enabled")
    await scale_manager.ensure_running(mcp_name)
    return {"status": "scaling_up", "mcp": mcp_name}


@app.post("/scale/{mcp_name}/down")
async def scale_down_manual(mcp_name: str, request: Request):
    """Manually scale down an MCP to 0 (privileged: manage-MCP authority)."""
    _authorize_action(request, "mcp.server.register")
    if not scale_manager.enabled:
        raise HTTPException(400, "Scale-to-zero is not enabled")
    deploy_name = scale_manager.get_deployment_name(mcp_name)
    if not deploy_name:
        raise HTTPException(404, f"MCP '{mcp_name}' not in scale map")
    scale_manager._init_k8s()
    if not scale_manager._apps_v1:
        raise HTTPException(500, "k8s client not available")
    await asyncio.to_thread(
        scale_manager._apps_v1.patch_namespaced_deployment_scale,
        name=deploy_name,
        namespace=scale_manager.namespace,
        body={"spec": {"replicas": 0}},
    )
    if deploy_name in scale_manager.last_call:
        del scale_manager.last_call[deploy_name]
    return {"status": "scaled_down", "deployment": deploy_name}



# ---------------------------------------------------------------------------
# Policy Endpoints
# ---------------------------------------------------------------------------

@app.get("/policy/rules")
async def get_policy_rules(request: Request):
    """Return the current policy rules."""
    _authorize_action(request, "policy.admin")
    return {"rules": policy_engine.rules, "count": len(policy_engine.rules)}


@app.post("/policy/reload")
async def reload_policy(request: Request):
    """Reload policy rules from disk."""
    _authorize_action(request, "policy.admin")
    policy_engine._load()
    return {"status": "reloaded", "count": len(policy_engine.rules)}


@app.post("/policy/check")
async def check_policy(body: dict, request: Request):
    """Ad-hoc policy check: {"mcp": "weather-mcp", "tool": "get_weather"}"""
    _authorize_action(request, "policy.admin")
    tenant_id = get_tenant_id(request)
    result = policy_engine.check(mcp=body.get("mcp", ""), tool=body.get("tool", ""), user_id=tenant_id)
    return result


# ---------------------------------------------------------------------------
# Audit Events Endpoint
# ---------------------------------------------------------------------------

def _can_read_all_audit_events(request: Request) -> bool:
    """Return True only for explicit platform/global audit readers."""
    identity = getattr(request.state, "identity", None) or {}
    permissions, _groups = _extract_action_permissions(identity)
    roles = identity.get("roles") or identity.get("role") or []
    if isinstance(roles, str):
        roles = [roles]
    return bool(
        identity.get("platform_admin")
        or "platform_admin" in roles
        or _permission_set_allows(permissions, "audit.read.global")
        or _permission_set_allows(permissions, "platform.audit.read")
    )


@app.get("/audit/events")
async def get_audit_events(request: Request, limit: int = 100, server: str = ""):
    """Return structured audit events for the portal."""
    _authorize_action(request, "audit.read")
    tenant_id = get_tenant_id(request)
    events = _list_audit_events(
        limit=limit,
        server=server,
        tenant_id=tenant_id,
        include_all_tenants=_can_read_all_audit_events(request),
    )
    return {"events": events}


@app.get("/audit/verify")
async def verify_audit_chain(request: Request, limit: int = 100000):
    """Recompute the audit hash chain and report integrity (SOC2 CC7.2 evidence).

    Walks events in seq order, recomputing entry_hash from the stored prev_hash +
    canonical fields. Any mismatch or broken prev->entry link indicates tampering
    (edit/delete/reorder) and is reported with the offending seq.
    """
    _authorize_action(request, "audit.read")
    conn = _get_db()
    if not conn:
        raise HTTPException(503, "audit store unavailable")
    cur = conn.cursor()
    cur.execute(
        """SELECT seq, tenant_id, user_id, action, resource, server_name, result,
                  trace_id, duration_ms, payload, prev_hash, entry_hash,
                  purpose, lawful_basis, audit_hash_version
           FROM audit_events ORDER BY seq ASC LIMIT %s""",
        (limit,),
    )
    rows = cur.fetchall()
    cur.close()
    expected_prev = ""
    checked = 0
    first_break = None
    legacy_unverifiable: list[int] = []
    for r in rows:
        (seq, tenant_id, user_id, action, resource, server_name, result,
         trace_id, duration_ms, payload, prev_hash, entry_hash,
         purpose, lawful_basis, audit_hash_version) = r
        # Legacy rows written before chaining have no hashes; skip until the chain starts.
        if entry_hash is None:
            continue
        # Chain linkage first: a prev_hash mismatch means delete/reorder/insert.
        if (prev_hash or "") != expected_prev:
            first_break = {"seq": seq, "reason": "prev_hash mismatch"}
            break
        # Content hash: current rows serialize payloads canonically (sorted
        # keys, compact separators). Rows written before canonicalization were
        # hashed over the as-written insertion order, which Postgres JSONB does
        # not preserve — try the canonical form first, then the old default
        # format that matches single-key/lucky legacy rows.
        candidates = []
        if isinstance(payload, (dict, list)):
            candidates.append(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str))
            candidates.append(json.dumps(payload))
        else:
            candidates.append(payload)
        content_ok = any(
            _audit_entry_hash(prev_hash or "", _audit_hash_fields(
                tenant_id=tenant_id, user_id=user_id, action=action,
                resource=resource, server_name=server_name, result=result,
                trace_id=trace_id, duration_ms=duration_ms, payload_json=candidate,
                purpose=purpose, lawful_basis=lawful_basis,
            )) == entry_hash
            for candidate in candidates
        )
        if not content_ok:
            if _audit_payload_hash_is_explicitly_legacy(
                payload=payload,
                purpose=purpose,
                lawful_basis=lawful_basis,
                audit_hash_version=audit_hash_version,
            ):
                # Explicit pre-canonicalization multi-key payload: the hash
                # covered as-written insertion order, which JSONB does not
                # preserve. Linkage above still holds; record honestly and
                # continue the chain walk. Unmarked/tagged canonical rows fail
                # closed because the same mismatch shape could be tampering.
                legacy_unverifiable.append(seq)
            else:
                first_break = {"seq": seq, "reason": "entry_hash mismatch"}
                break
        expected_prev = entry_hash
        checked += 1
    return {
        "intact": first_break is None,
        "events_checked": checked,
        "first_break": first_break,
        "legacy_unverifiable_payload_seqs": legacy_unverifiable,
    }


# ---------------------------------------------------------------------------
# Sealed Handle Endpoints
# ---------------------------------------------------------------------------

@app.post("/sealed")
async def create_sealed_handle(req: SealedInputRequest, request: Request):
    """Create a sealed handle for a sensitive value owned by the caller."""
    _authorize_action(request, "sealed_handle.create")
    tenant_id = get_tenant_id(request)
    subject_id = get_subject_id(request)
    result = _create_sealed_handle(
        req.label,
        req.value,
        req.expires_in_seconds,
        tenant_id=tenant_id,
        subject_id=subject_id,
    )
    audit_payload: dict = {"tenant_id": tenant_id, "label": req.label}
    delegated_from = (getattr(request.state, "identity", {}) or {}).get("delegated_from")
    if isinstance(delegated_from, dict):
        audit_payload["delegated_from"] = {
            "api_key_tenant_id": delegated_from.get("api_key_tenant_id", ""),
            "api_key_name": delegated_from.get("api_key_name", ""),
        }
    if not result:
        _write_audit_event(
            action="sealed_handle.create",
            resource="sealed",
            result="denied",
            payload=audit_payload,
            user_id=subject_id,
            tenant_id=tenant_id,
        )
        raise HTTPException(500, "Failed to create sealed handle (database unavailable)")
    _write_audit_event(
        action="sealed_handle.create",
        resource=f"sealed:{result.get('handle_id', '')}",
        result="ok",
        payload=audit_payload,
        user_id=subject_id,
        tenant_id=tenant_id,
    )
    return result


@app.get("/sealed")
async def list_sealed_handles(request: Request):
    """List active (non-expired, non-used) sealed handles for the caller."""
    _authorize_action(request, "sealed_handle.resolve")
    return _list_sealed_handles(tenant_id=get_tenant_id(request), subject_id=get_subject_id(request))


@app.get("/sealed/{handle_id}")
async def resolve_sealed_handle(handle_id: str, request: Request):
    """Reject public HTTP plaintext handle resolution; pipeline internals resolve handles in-process."""
    _authorize_action(request, "sealed_handle.resolve")
    _write_audit_event(
        action="sealed_handle.resolve",
        resource=f"sealed:{handle_id}",
        result="denied",
        payload={"tenant_id": get_tenant_id(request), "reason": "http_plaintext_resolve_disabled"},
        user_id=get_subject_id(request),
        tenant_id=get_tenant_id(request),
    )
    raise HTTPException(status_code=403, detail="Forbidden: HTTP sealed handle resolution is disabled")


@app.delete("/sealed/{handle_id}")
async def delete_sealed_handle(handle_id: str, request: Request):
    """Invalidate a sealed handle owned by the caller."""
    _authorize_action(request, "sealed_handle.delete")
    tenant_id = get_tenant_id(request)
    subject_id = get_subject_id(request)
    if not _delete_sealed_handle(handle_id, tenant_id=tenant_id, subject_id=subject_id):
        _write_audit_event(
            action="sealed_handle.delete",
            resource=f"sealed:{handle_id}",
            result="denied",
            payload={"tenant_id": tenant_id},
            user_id=subject_id,
            tenant_id=tenant_id,
        )
        raise HTTPException(404, "Handle not found or expired")
    _write_audit_event(
        action="sealed_handle.delete",
        resource=f"sealed:{handle_id}",
        result="ok",
        payload={"tenant_id": tenant_id},
        user_id=subject_id,
        tenant_id=tenant_id,
    )
    return {"status": "invalidated", "handle_id": handle_id}


async def _call_mcp(
    client: httpx.AsyncClient,
    mcp_name: str,
    tool: str,
    inputs: dict,
    *,
    trace_id: str = "",
    tenant_id: str = "system",
    subject_id: str | None = None,
    pipeline_name: str = "",
) -> dict:
    """Thin wrapper: call an MCP tool, return result dict (never raises).

    Routes through the shared execution boundary so runtime hooks (incl.
    manifest-declared PII redaction) and per-call audit apply to v2 pipeline
    steps exactly as they do to /call and /pipeline.
    """
    manifest = manifests.get(mcp_name)
    if not manifest:
        return {"error": f"{mcp_name} not registered"}
    try:
        result, error = await _execute_mcp_tool(
            client,
            manifest=manifest,
            mcp_name=mcp_name,
            tool=tool,
            inputs=inputs,
            trace_id=trace_id or _new_trace_id(),
            tenant_id=tenant_id,
            subject_id=subject_id,
            pipeline_name=pipeline_name,
        )
    except PermissionError as e:
        return {"error": f"runtime hook denied: {e}"}
    except Exception as e:
        return {"error": str(e)}
    if error and not isinstance(result, dict):
        return {"error": error}
    return result if isinstance(result, dict) else {"result": result}


# ---------------------------------------------------------------------------
# Task Graph / Jobs API
# ---------------------------------------------------------------------------

class CreateJobRequest(BaseModel):
    pipeline: str
    inputs: dict = {}
    name: str | None = None
    tenant_id: str = "system"


class BatchJobRequest(BaseModel):
    pipeline: str
    items: list[dict]
    name: str | None = None
    tenant_id: str = "system"


# ---- helpers ----

def _job_db_write(sql: str, params: tuple = ()):
    conn = _get_db()
    if not conn:
        raise HTTPException(500, "Database unavailable")
    cur = conn.cursor()
    cur.execute(sql, params)
    cur.close()


def _job_db_read(sql: str, params: tuple = ()) -> list[dict]:
    conn = _get_db()
    if not conn:
        raise HTTPException(500, "Database unavailable")
    import psycopg2.extras
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    return [dict(r) for r in rows]


def _job_db_read_one(sql: str, params: tuple = ()) -> dict | None:
    rows = _job_db_read(sql, params)
    return rows[0] if rows else None


def _serialize_job(row: dict) -> dict:
    """Make a job row JSON-safe (timestamps → ISO strings)."""
    out = dict(row)
    for k in ("created_at", "started_at", "completed_at"):
        if out.get(k):
            out[k] = out[k].isoformat()
    return out


def _pipeline_uses_external_agent(pipeline: NamedPipeline) -> bool:
    """Return True when any named-pipeline stage dispatches to an external agent."""
    return any(manifests.get(stage.mcp) and manifests[stage.mcp].transport == "external_agent" for stage in pipeline.stages)


def _authorized_actions_for_background(request: Request) -> set[str]:
    """Capture request action grants to replay inside background job execution."""
    if getattr(request.state, "auth_type", "") == "none" or getattr(request.state, "is_admin", False):
        return {"*"}
    permissions, _groups = _extract_action_permissions(getattr(request.state, "identity", None))
    captured = set(permissions or set())
    captured.update(getattr(request.state, "authorized_actions", set()) or set())
    return captured


def _can_submit_job_for_tenant(request: Request, tenant_id: str) -> bool:
    """Only platform/global authorities may submit jobs for another tenant."""
    if tenant_id == get_tenant_id(request):
        return True
    identity = getattr(request.state, "identity", None) or {}
    permissions, _groups = _extract_action_permissions(identity)
    roles = identity.get("roles") or identity.get("role") or []
    if isinstance(roles, str):
        roles = [roles]
    return bool(
        getattr(request.state, "is_admin", False)
        or identity.get("platform_admin")
        or "platform_admin" in roles
        or _permission_set_allows(permissions, "jobs.submit.global")
        or _permission_set_allows(permissions, "platform.jobs.submit")
    )


def _job_tenant_id(request: Request, requested_tenant_id: str) -> str:
    """Bind jobs to the authenticated tenant unless a platform/global authority overrides it."""
    auth_tenant_id = get_tenant_id(request)
    requested = (requested_tenant_id or auth_tenant_id).strip() or auth_tenant_id
    return requested if _can_submit_job_for_tenant(request, requested) else auth_tenant_id


# ---- background runner ----


_running_jobs: dict[str, asyncio.Task] = {}  # job_id → asyncio.Task


async def _execute_job(
    job_id: str,
    pipeline_name: str,
    inputs: dict,
    tenant_id: str,
    subject_id: str | None = None,
    authorized_actions: set[str] | None = None,
):
    """Run a single pipeline job in the background, recording steps."""

    try:
        _job_db_write(
            "UPDATE pipeline_jobs SET status='running', started_at=NOW() WHERE job_id=%s",
            (job_id,),
        )

        pipeline = named_pipelines.get(pipeline_name)
        if not pipeline:
            _job_db_write(
                "UPDATE pipeline_jobs SET status='failed', error=%s, completed_at=NOW() WHERE job_id=%s",
                (f"Unknown pipeline: {pipeline_name}", job_id),
            )
            return

        # Pre-create step rows
        for i, stage in enumerate(pipeline.stages):
            step_id = f"{job_id}_s{i}"
            _job_db_write(
                """INSERT INTO pipeline_job_steps
                   (step_id, job_id, step_name, status, inputs, sequence_order)
                   VALUES (%s, %s, %s, 'queued', %s, %s)""",
                (step_id, job_id, stage.name, json.dumps({}), i),
            )

        # Actually run the pipeline
        result = await _run_named_pipeline(
            pipeline,
            inputs,
            tenant_id=tenant_id,
            subject_id=subject_id or tenant_id,
            authorized_actions=authorized_actions,
        )

        # Update step rows from trace
        steps_data = result.get("steps", [])
        for i, step_data in enumerate(steps_data):
            step_id = f"{job_id}_s{i}"
            step_result = step_data.get("result", {})
            step_error = step_result.get("error") if isinstance(step_result, dict) else None
            step_status = "failed" if step_error else "completed"
            _job_db_write(
                """UPDATE pipeline_job_steps
                   SET status=%s, result=%s, error=%s, completed_at=NOW()
                   WHERE step_id=%s""",
                (step_status, json.dumps(step_result), step_error, step_id),
            )

        # Determine overall status
        has_error = result.get("error") or any(
            isinstance(s.get("result"), dict) and s["result"].get("error")
            for s in steps_data
        )
        final_status = "failed" if has_error else "completed"
        error_msg = result.get("error")

        _job_db_write(
            "UPDATE pipeline_jobs SET status=%s, result=%s, error=%s, completed_at=NOW() WHERE job_id=%s",
            (final_status, json.dumps(result), error_msg, job_id),
        )

    except Exception as exc:
        _job_db_write(
            "UPDATE pipeline_jobs SET status='failed', error=%s, completed_at=NOW() WHERE job_id=%s",
            (str(exc), job_id),
        )
    finally:
        _running_jobs.pop(job_id, None)


async def _execute_batch(
    parent_id: str,
    pipeline_name: str,
    items: list[dict],
    tenant_id: str,
    subject_id: str | None = None,
    authorized_actions: set[str] | None = None,
):
    """Run a batch of child jobs with bounded concurrency."""
    try:
        _job_db_write(
            "UPDATE pipeline_jobs SET status='running', started_at=NOW() WHERE job_id=%s",
            (parent_id,),
        )

        sem = asyncio.Semaphore(5)

        async def _run_child(child_id: str, child_inputs: dict):
            async with sem:
                await _execute_job(
                    child_id,
                    pipeline_name,
                    child_inputs,
                    tenant_id,
                    subject_id=subject_id,
                    authorized_actions=authorized_actions,
                )

        # Create child jobs
        child_ids = []
        for i, item_inputs in enumerate(items):
            child_id = f"{parent_id}_c{i}"
            child_ids.append(child_id)
            _job_db_write(
                """INSERT INTO pipeline_jobs
                   (job_id, name, pipeline_name, status, inputs, tenant_id, parent_job_id)
                   VALUES (%s, %s, %s, 'queued', %s, %s, %s)""",
                (child_id, f"batch-child-{i}", pipeline_name,
                 json.dumps(item_inputs), tenant_id, parent_id),
            )

        # Run all children concurrently (max 5)
        await asyncio.gather(
            *[_run_child(cid, inp) for cid, inp in zip(child_ids, items)],
            return_exceptions=True,
        )

        # Aggregate results
        children = _job_db_read(
            "SELECT job_id, status, result, error FROM pipeline_jobs WHERE parent_job_id=%s ORDER BY job_id",
            (parent_id,),
        )
        failed = sum(1 for c in children if c["status"] == "failed")
        completed = sum(1 for c in children if c["status"] == "completed")
        batch_status = "completed" if failed == 0 else ("failed" if completed == 0 else "completed")
        batch_result = {
            "total": len(children),
            "completed": completed,
            "failed": failed,
            "children": [{"job_id": c["job_id"], "status": c["status"]} for c in children],
        }

        _job_db_write(
            "UPDATE pipeline_jobs SET status=%s, result=%s, completed_at=NOW() WHERE job_id=%s",
            (batch_status, json.dumps(batch_result), parent_id),
        )
    except Exception as exc:
        _job_db_write(
            "UPDATE pipeline_jobs SET status='failed', error=%s, completed_at=NOW() WHERE job_id=%s",
            (str(exc), parent_id),
        )
    finally:
        _running_jobs.pop(parent_id, None)


# ---- endpoints ----

@app.post("/jobs")
async def create_job(req: CreateJobRequest, request: Request):
    """Create a single async pipeline job."""
    _authorize_action(request, "pipeline.invoke")
    job_id = uuid.uuid4().hex[:16]
    name = req.name or f"{req.pipeline}_{job_id[:8]}"

    if req.pipeline not in named_pipelines:
        raise HTTPException(404, f"Pipeline '{req.pipeline}' not found")
    pipeline = named_pipelines[req.pipeline]
    if _pipeline_uses_external_agent(pipeline):
        _authorize_action(request, "agent.invoke")
    tenant_id = _job_tenant_id(request, req.tenant_id)
    subject_id = get_subject_id(request)
    authorized_actions = _authorized_actions_for_background(request)

    _job_db_write(
        """INSERT INTO pipeline_jobs
           (job_id, name, pipeline_name, status, inputs, tenant_id)
           VALUES (%s, %s, %s, 'queued', %s, %s)""",
        (job_id, name, req.pipeline, json.dumps(req.inputs), tenant_id),
    )

    task = asyncio.create_task(
        _execute_job(
            job_id,
            req.pipeline,
            req.inputs,
            tenant_id,
            subject_id=subject_id,
            authorized_actions=authorized_actions,
        )
    )
    _running_jobs[job_id] = task

    return {"job_id": job_id, "status": "queued", "name": name}


@app.get("/jobs")
async def list_jobs(request: Request, status: str = "", pipeline: str = "", tenant_id: str = "", limit: int = 50):
    """List jobs, ALWAYS scoped to the caller's tenant.

    A caller may only see its own tenant's jobs; a foreign `tenant_id` filter
    requires platform/global authority (otherwise 403). This prevents the
    cross-tenant job leak (broken access control).
    """
    caller_tenant = get_tenant_id(request)
    if tenant_id and tenant_id != caller_tenant and not _can_submit_job_for_tenant(request, tenant_id):
        raise HTTPException(403, "Forbidden: cannot access another tenant's jobs")
    scope_tenant = tenant_id or caller_tenant
    clauses = ["tenant_id = %s"]
    params: list = [scope_tenant]
    if status:
        clauses.append("status = %s")
        params.append(status)
    if pipeline:
        clauses.append("pipeline_name = %s")
        params.append(pipeline)

    where = " WHERE " + " AND ".join(clauses)
    params.append(limit)
    rows = _job_db_read(
        f"SELECT * FROM pipeline_jobs {where} ORDER BY created_at DESC LIMIT %s",
        tuple(params),
    )
    return {"jobs": [_serialize_job(r) for r in rows], "count": len(rows)}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request):
    """Get job details (scoped to the caller's tenant)."""
    job = _job_db_read_one("SELECT * FROM pipeline_jobs WHERE job_id = %s", (job_id,))
    if not job:
        raise HTTPException(404, "Job not found")
    # Tenant isolation: only the owning tenant (or platform authority) may read it.
    # Return 404 (not 403) so job existence isn't disclosed across tenants.
    if not _can_submit_job_for_tenant(request, job.get("tenant_id") or "system"):
        raise HTTPException(404, "Job not found")

    steps = _job_db_read(
        "SELECT * FROM pipeline_job_steps WHERE job_id = %s ORDER BY sequence_order",
        (job_id,),
    )

    children = _job_db_read(
        "SELECT job_id, name, status, error, created_at, completed_at FROM pipeline_jobs WHERE parent_job_id = %s ORDER BY job_id",
        (job_id,),
    )

    out = _serialize_job(job)
    out["steps"] = [_serialize_job(s) for s in steps]
    if children:
        out["children"] = [_serialize_job(c) for c in children]
    return out


@app.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request):
    """Cancel a running job (scoped to the caller's tenant)."""
    job = _job_db_read_one("SELECT status, tenant_id FROM pipeline_jobs WHERE job_id = %s", (job_id,))
    if not job:
        raise HTTPException(404, "Job not found")
    # Tenant isolation: only the owning tenant (or platform authority) may cancel.
    if not _can_submit_job_for_tenant(request, job.get("tenant_id") or "system"):
        raise HTTPException(404, "Job not found")
    if job["status"] not in ("queued", "running"):
        return {"job_id": job_id, "status": job["status"], "message": "Job already finished"}

    # Cancel the asyncio task if it exists
    task = _running_jobs.pop(job_id, None)
    if task and not task.done():
        task.cancel()

    _job_db_write(
        "UPDATE pipeline_jobs SET status='failed', error='Cancelled by user', completed_at=NOW() WHERE job_id=%s",
        (job_id,),
    )
    return {"job_id": job_id, "status": "cancelled"}


@app.post("/jobs/batch")
async def create_batch_job(req: BatchJobRequest, request: Request):
    """Create a batch job: one parent + N child jobs."""
    _authorize_action(request, "pipeline.invoke")
    if req.pipeline not in named_pipelines:
        raise HTTPException(404, f"Pipeline '{req.pipeline}' not found")
    if not req.items:
        raise HTTPException(400, "items list is empty")
    pipeline = named_pipelines[req.pipeline]
    if _pipeline_uses_external_agent(pipeline):
        _authorize_action(request, "agent.invoke")
    tenant_id = _job_tenant_id(request, req.tenant_id)
    subject_id = get_subject_id(request)
    authorized_actions = _authorized_actions_for_background(request)

    parent_id = uuid.uuid4().hex[:16]
    name = req.name or f"batch_{req.pipeline}_{parent_id[:8]}"

    _job_db_write(
        """INSERT INTO pipeline_jobs
           (job_id, name, pipeline_name, status, inputs, tenant_id)
           VALUES (%s, %s, %s, 'queued', %s, %s)""",
        (parent_id, name, req.pipeline,
         json.dumps({"_batch": True, "item_count": len(req.items)}), tenant_id),
    )

    task = asyncio.create_task(
        _execute_batch(
            parent_id,
            req.pipeline,
            req.items,
            tenant_id,
            subject_id=subject_id,
            authorized_actions=authorized_actions,
        )
    )
    _running_jobs[parent_id] = task

    return {
        "job_id": parent_id,
        "status": "queued",
        "name": name,
        "children_count": len(req.items),
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# V2 YAML Pipeline Engine (Phase 1)
# ---------------------------------------------------------------------------

v2_pipelines: dict[str, dict] = {}   # name -> parsed YAML dict
_demo_sandbox_run_timestamps: dict[str, list[float]] = {}


def _resolve_template(value, context: dict):
    """Resolve {{path.to.key}} templates against a context dict.

    - Non-string values pass through unchanged.
    - Returns None (not error) if the dotted path is missing.
    """
    if not isinstance(value, str):
        return value
    pattern = re.compile(r"\{\{(.+?)\}\}")
    # If the entire string is one placeholder, return the raw value (preserves type)
    m = pattern.fullmatch(value.strip())
    if m:
        return _resolve_dot_path(m.group(1).strip(), context)
    # Otherwise do string interpolation
    def _replacer(match):
        resolved = _resolve_dot_path(match.group(1).strip(), context)
        if resolved is None:
            return ""
        return str(resolved)
    return pattern.sub(_replacer, value)


def _resolve_dot_path(path: str, context: dict):
    """Walk a dotted path like 'steps.landscape.output.landscape' into context."""
    parts = path.split(".")
    current = context
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def _resolve_inputs_recursive(obj, context: dict):
    """Recursively resolve templates in a dict/list/scalar."""
    if isinstance(obj, dict):
        return {k: _resolve_inputs_recursive(v, context) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_inputs_recursive(v, context) for v in obj]
    return _resolve_template(obj, context)


def _validate_v2_pipeline(data: dict) -> list[str]:
    """Validate a v2 pipeline definition. Returns list of error strings (empty = valid)."""
    errors = []
    if not data.get("name"):
        errors.append("missing 'name'")
    if not data.get("steps"):
        errors.append("missing 'steps' list")
    if not isinstance(data.get("inputs", {}), dict):
        errors.append("'inputs' must be a mapping of input name to spec")
    for i, step in enumerate(data.get("steps", [])):
        if not step.get("id"):
            errors.append(f"step {i}: missing 'id'")
        if not step.get("mcp"):
            errors.append(f"step {i}: missing 'mcp'")
        if not step.get("tool") and not step.get("pipeline"):
            errors.append(f"step {i}: must have 'tool' or 'pipeline'")
    return errors


def _is_demo_sandbox_pipeline(pipeline_def: dict) -> bool:
    safety = pipeline_def.get("safety") or {}
    return (
        pipeline_def.get("name") == "demo_sandbox_invoice_review"
        or safety.get("tenant_scope") == "demo-sandbox"
    )


def _enforce_demo_sandbox_boundary(
    pipeline_def: dict,
    resolved_inputs: dict,
    *,
    tenant_id: str,
    workspace_id: str = "",
    body_size_bytes: int = 0,
) -> dict | None:
    """Fail closed for externally exposed demo sandbox pipeline boundaries."""
    if not _is_demo_sandbox_pipeline(pipeline_def):
        return None

    safety = pipeline_def.get("safety") or {}
    quota = safety.get("quota") or {}
    required_tenant = safety.get("tenant_scope", "demo-sandbox")
    required_workspace = safety.get("workspace_scope", "demo-external-evaluation")
    effective_workspace = workspace_id or str(resolved_inputs.get("workspace") or "")

    if tenant_id != required_tenant:
        return {
            "error": "demo_sandbox_tenant_forbidden",
            "detail": "demo sandbox pipeline requires authenticated demo-sandbox tenant context",
        }
    if effective_workspace != required_workspace:
        return {
            "error": "demo_sandbox_workspace_forbidden",
            "detail": "demo sandbox pipeline requires demo-external-evaluation workspace context",
        }

    max_payload_bytes = int(quota.get("max_payload_bytes", 65536))
    if body_size_bytes and body_size_bytes > max_payload_bytes:
        return {
            "error": "demo_sandbox_body_too_large",
            "detail": f"demo sandbox request body exceeds {max_payload_bytes} bytes",
        }

    limit = int(quota.get("pipeline_runs_per_hour", 10))
    now = time.time()
    bucket_key = f"{tenant_id}:{effective_workspace}:{pipeline_def.get('name')}"
    recent = [ts for ts in _demo_sandbox_run_timestamps.get(bucket_key, []) if now - ts < 3600]
    if len(recent) >= limit:
        _demo_sandbox_run_timestamps[bucket_key] = recent
        return {
            "error": "demo_sandbox_rate_limited",
            "detail": f"demo sandbox pipeline is limited to {limit} runs/hour",
        }
    recent.append(now)
    _demo_sandbox_run_timestamps[bucket_key] = recent
    return None


def _load_yaml_pipeline_v2():
    """Scan runtime/pipelines/v2/ for YAML pipeline definitions and load them."""
    v2_dir = Path(__file__).parent / "pipelines" / "v2"
    if not v2_dir.exists():
        return
    count = 0
    for yaml_file in sorted(v2_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(yaml_file.read_text())
            if not data or not isinstance(data, dict):
                continue
            errs = _validate_v2_pipeline(data)
            if errs:
                print(f"[router] WARN: v2 pipeline {yaml_file.name} invalid: {errs}")
                continue
            v2_pipelines[data["name"]] = data
            count += 1
            print(f"[router] loaded v2 pipeline: {data['name']} from {yaml_file}")
        except Exception as e:
            print(f"[router] WARN: failed to load v2 pipeline {yaml_file}: {e}")
    print(f"[router] loaded {count} v2 pipelines")


async def _run_v2_pipeline(
    pipeline_def: dict,
    inputs: dict,
    *,
    tenant_id: str = "system",
    workspace_id: str = "",
    body_size_bytes: int = 0,
    auth_headers: dict | None = None,
    subject_id: str | None = None,
) -> dict:
    """Execute a v2 YAML pipeline definition."""
    trace_id = uuid.uuid4().hex[:16]
    start_time = time.time()

    # Build context with defaults applied
    resolved_inputs = {}
    for param_name, param_spec in pipeline_def.get("inputs", {}).items():
        if isinstance(param_spec, dict):
            if param_name in inputs:
                resolved_inputs[param_name] = inputs[param_name]
            elif "default" in param_spec:
                resolved_inputs[param_name] = param_spec["default"]
            elif param_spec.get("required", False):
                return {"error": f"Missing required input: {param_name}", "trace_id": trace_id}
        else:
            resolved_inputs[param_name] = inputs.get(param_name)

    context: dict = {"inputs": resolved_inputs, "steps": {}}
    boundary_error = _enforce_demo_sandbox_boundary(
        pipeline_def,
        resolved_inputs,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        body_size_bytes=body_size_bytes,
    )
    if boundary_error:
        return {**boundary_error, "trace_id": trace_id}

    step_results: dict = {}

    async with httpx.AsyncClient(timeout=300.0) as client:
        for step in pipeline_def.get("steps", []):
            step_id = step["id"]
            step_start = time.time()

            # Resolve inputs for this step
            step_inputs = _resolve_inputs_recursive(step.get("inputs", {}), context)

            # Check if this step calls another pipeline (pipeline key)
            is_pipeline_call = bool(step.get("pipeline"))

            # Check if this is a foreach step
            foreach_expr = step.get("foreach")
            if foreach_expr:
                items = _resolve_template(foreach_expr, context)
                if not isinstance(items, list):
                    items = []

                concurrency = step.get("concurrency", 5)
                on_error = step.get("on_error", "abort")
                semaphore = asyncio.Semaphore(concurrency)
                results_list: list = [None] * len(items)

                async def _run_foreach_item(idx: int, item_val):
                    item_context = {**context, "item": item_val}
                    item_inputs = _resolve_inputs_recursive(step.get("inputs", {}), item_context)
                    async with semaphore:
                        try:
                            if is_pipeline_call:
                                result = await _v2_call_pipeline(client, step["pipeline"], item_inputs, auth_headers)
                            else:
                                result = await _call_mcp(
                                    client, step["mcp"], step["tool"], item_inputs,
                                    trace_id=trace_id, tenant_id=tenant_id,
                                    subject_id=subject_id,
                                    pipeline_name=str(pipeline_def.get("name", "")),
                                )
                            results_list[idx] = result
                        except Exception as e:
                            if on_error == "skip":
                                results_list[idx] = {"error": str(e)}
                            else:
                                raise

                tasks = [_run_foreach_item(i, item) for i, item in enumerate(items)]
                await asyncio.gather(*tasks, return_exceptions=(step.get("on_error") == "skip"))
                step_output = results_list
            else:
                # Single execution
                try:
                    if is_pipeline_call:
                        step_output = await _v2_call_pipeline(client, step["pipeline"], step_inputs, auth_headers)
                    else:
                        step_output = await _call_mcp(
                            client, step["mcp"], step["tool"], step_inputs,
                            trace_id=trace_id, tenant_id=tenant_id,
                            subject_id=subject_id,
                            pipeline_name=str(pipeline_def.get("name", "")),
                        )
                except Exception as e:
                    step_output = {"error": str(e)}

            step_results[step_id] = {
                "output": step_output,
                "duration_ms": int((time.time() - step_start) * 1000),
            }
            context["steps"][step_id] = {"output": step_output}

    duration_ms = int((time.time() - start_time) * 1000)
    final_output = None
    if pipeline_def.get("output"):
        final_output = _resolve_inputs_recursive(pipeline_def["output"], context)
    response = {
        "results": step_results,
        "trace_id": trace_id,
        "duration_ms": duration_ms,
    }
    if final_output is not None:
        response["output"] = final_output
    return response


async def _v2_call_pipeline(
    client: httpx.AsyncClient,
    pipeline_name: str,
    inputs: dict,
    auth_headers: dict | None = None,
) -> dict:
    """Call an existing named pipeline by posting to /pipelines/{name}/run.

    Forwards the original caller's auth header so this internal self-call passes
    REQUIRE_AUTH with the same identity/permissions; without it the nested call
    is rejected with 401.
    """
    try:
        resp = await client.post(
            f"http://localhost:8040/pipelines/{pipeline_name}/run",
            json={"inputs": inputs},
            headers=auth_headers or {},
            timeout=300.0,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": f"pipeline call '{pipeline_name}' failed: {str(e)}"}


# ---- V2 Pipeline Endpoints ------------------------------------------------

@app.get("/v2/pipelines")
async def list_v2_pipelines():
    """List all loaded v2 pipelines."""
    return {
        "pipelines": [
            {
                "name": p["name"],
                "version": p.get("version", 2),
                "description": p.get("description", ""),
                "inputs": list(p.get("inputs", {}).keys()),
                "steps": len(p.get("steps", [])),
            }
            for p in v2_pipelines.values()
        ]
    }


@app.post("/v2/pipelines/run")
async def run_v2_pipeline(request: Request):
    """Run a v2 pipeline by name.

    Body: {"pipeline": "credit_batch", "inputs": {...}}
    """
    body = await request.json()
    pipeline_name = body.get("pipeline")
    if not pipeline_name or pipeline_name not in v2_pipelines:
        available = list(v2_pipelines.keys())
        raise HTTPException(status_code=404, detail=f"V2 pipeline '{pipeline_name}' not found. Available: {available}")
    inputs = body.get("inputs", {})
    body_size_bytes = int(request.headers.get("content-length") or 0)

    # Pre-check MCP permissions for user identities (JWT or delegated API key).
    # Nested pipeline-call steps re-enter through their own run endpoint, which
    # performs the same enforcement with the forwarded credential.
    for step in v2_pipelines[pipeline_name].get("steps", []):
        if step.get("mcp"):
            _enforce_user_mcp_access(request, step["mcp"], step.get("tool"))

    # Forward the caller's credential to any nested named-pipeline self-calls so
    # they pass REQUIRE_AUTH with the same identity/permissions.
    auth_headers: dict = {}
    if request.headers.get("x-api-key"):
        auth_headers["X-API-Key"] = request.headers["x-api-key"]
    if request.headers.get("authorization"):
        auth_headers["Authorization"] = request.headers["authorization"]
    result = await _run_v2_pipeline(
        v2_pipelines[pipeline_name],
        inputs,
        tenant_id=get_tenant_id(request),
        workspace_id=get_workspace_id(request) or str(body.get("workspace") or inputs.get("workspace") or ""),
        body_size_bytes=body_size_bytes,
        auth_headers=auth_headers,
        subject_id=get_subject_id(request),
    )
    return result


_PIPELINE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@app.post("/v2/pipelines/deploy")
async def deploy_v2_pipeline(request: Request):
    """Accept a YAML body, validate, save to runtime/pipelines/v2/, register in memory.

    Body: raw YAML string in 'yaml' field, or full JSON pipeline def in 'pipeline' field.
    """
    # Deploying a pipeline writes to the server filesystem and registers it in
    # the shared pipeline set — a privileged operation, not open to any key.
    _authorize_action(request, "pipeline.deploy")
    body = await request.json()
    if "yaml" in body:
        try:
            data = yaml.safe_load(body["yaml"])
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")
    elif "pipeline" in body:
        data = body["pipeline"]
    else:
        raise HTTPException(status_code=400, detail="Provide 'yaml' (string) or 'pipeline' (object)")

    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Pipeline definition must be a mapping")

    errs = _validate_v2_pipeline(data)
    if errs:
        raise HTTPException(status_code=400, detail=f"Validation errors: {errs}")

    name = data["name"]
    # Reject path-traversal / unsafe names before they reach the filesystem.
    if not isinstance(name, str) or not _PIPELINE_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="pipeline name must match ^[A-Za-z0-9_-]{1,128}$")

    v2_dir = (Path(__file__).parent / "pipelines" / "v2").resolve()
    v2_dir.mkdir(parents=True, exist_ok=True)
    dest = (v2_dir / f"{name}.yaml").resolve()
    # Defense in depth: the resolved path must stay inside the pipelines dir.
    if dest.parent != v2_dir:
        raise HTTPException(status_code=400, detail="invalid pipeline name")
    dest.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))

    # Register in memory
    v2_pipelines[name] = data

    return {"status": "deployed", "name": name, "path": str(dest)}


@app.get("/v2/pipelines/{name}")
async def get_v2_pipeline(name: str):
    """Get a v2 pipeline definition by name."""
    if name not in v2_pipelines:
        raise HTTPException(status_code=404, detail=f"V2 pipeline '{name}' not found")
    return v2_pipelines[name]


# ---- Hot-reload endpoint ---------------------------------------------------

@app.post("/pipelines/reload")
async def reload_all_pipelines():
    """Re-scan runtime/pipelines/ and runtime/pipelines/v2/, reload all without restart."""
    # Reload v1 named pipelines
    named_pipelines.clear()
    _load_named_pipelines()

    # Reload v2 pipelines
    v2_pipelines.clear()
    _load_yaml_pipeline_v2()

    return {
        "status": "reloaded",
        "named_pipelines": len(named_pipelines),
        "v2_pipelines": len(v2_pipelines),
    }



# ---------------------------------------------------------------------------
# Credential Management API
# ---------------------------------------------------------------------------

class CredentialCreate(BaseModel):
    name: str
    description: str = ""
    secret_type: str = "api_key"        # api_key | oauth_token | basic_auth | custom
    storage_mode: str = "platform"      # platform | byok | k8s
    value: str = ""                     # raw secret (platform/byok modes)
    byok_key: str = ""                  # user's encryption key (byok mode only)
    k8s_secret_name: str = ""           # k8s Secret name (k8s mode)
    k8s_secret_key: str = ""            # k8s Secret key (k8s mode)
    assigned_mcp: str = ""


class CredentialUpdate(BaseModel):
    description: str | None = None
    value: str | None = None        # if provided, re-encrypt and update
    assigned_mcp: str | None = None
    is_active: bool | None = None


@app.get("/credentials")
async def list_credentials(request: Request):
    """List all credentials (metadata only — never returns values)."""
    _authorize_action(request, "credential.read")
    import psycopg2.extras
    tenant_id = get_tenant_id(request)
    db = _get_db()
    if not db:
        raise HTTPException(502, "DB unavailable")
    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT id, name, description, secret_type, assigned_mcp,
               tenant_id, created_by, is_active, last_used_at, created_at, updated_at
        FROM credentials WHERE tenant_id = %s ORDER BY created_at DESC
    """, (tenant_id,))
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    # Serialize datetimes
    for r in rows:
        for k in ("last_used_at", "created_at", "updated_at"):
            if r[k]:
                r[k] = r[k].isoformat()
    return {"credentials": rows}


@app.post("/credentials", status_code=201)
async def create_credential(body: CredentialCreate, request: Request):
    """Store a new credential (value encrypted with Fernet AES)."""
    _authorize_action(request, "credential.create")
    import psycopg2.extras
    tenant_id = get_tenant_id(request)
    db = _get_db()
    if not db:
        raise HTTPException(502, "DB unavailable")
    # Encrypt based on storage mode
    from credentials import encrypt_platform, encrypt_byok
    if body.storage_mode == "platform":
        if not body.value:
            raise HTTPException(400, "value required for platform storage mode")
        encrypted = encrypt_platform(body.value)
    elif body.storage_mode == "byok":
        if not body.value or not body.byok_key:
            raise HTTPException(400, "value and byok_key required for BYOK mode")
        encrypted = encrypt_byok(body.value, body.byok_key)
    elif body.storage_mode == "k8s":
        encrypted = ""  # k8s mode: no value stored in DB, metadata only
    else:
        raise HTTPException(400, f"Invalid storage_mode: {body.storage_mode}")

    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            INSERT INTO credentials
                (name, description, secret_type, encrypted_value, storage_mode,
                 k8s_secret_name, k8s_secret_key, assigned_mcp, tenant_id, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'admin')
            RETURNING id, name, description, secret_type, storage_mode, assigned_mcp, is_active, created_at
        """, (body.name, body.description, body.secret_type, encrypted, body.storage_mode,
              body.k8s_secret_name or None, body.k8s_secret_key or None,
              body.assigned_mcp or None, tenant_id))
        row = dict(cur.fetchone())
        db.commit()
        cur.close()
        if row.get("created_at"):
            row["created_at"] = row["created_at"].isoformat()
        return row
    except Exception as e:
        db.rollback()
        cur.close()
        raise HTTPException(400, f"Failed to create credential: {e}")


@app.patch("/credentials/{cred_id}")
async def update_credential(cred_id: str, body: CredentialUpdate, request: Request):
    """Update credential metadata or rotate the value."""
    _authorize_action(request, "credential.create")
    tenant_id = get_tenant_id(request)
    db = _get_db()
    if not db:
        raise HTTPException(502, "DB unavailable")
    cur = db.cursor()
    updates, params = [], []
    if body.description is not None:
        updates.append("description = %s")
        params.append(body.description)
    if body.value is not None:
        from credentials import encrypt_platform
        encrypted = encrypt_platform(body.value)
        updates.append("encrypted_value = %s")
        params.append(encrypted)
    if body.assigned_mcp is not None:
        updates.append("assigned_mcp = %s")
        params.append(body.assigned_mcp or None)
    if body.is_active is not None:
        updates.append("is_active = %s")
        params.append(body.is_active)
    if not updates:
        raise HTTPException(400, "No fields to update")
    updates.append("updated_at = NOW()")
    params.extend([cred_id, tenant_id])
    cur.execute(  # nosec B608 — column list built from fixed literals above; values parameterized
        f"UPDATE credentials SET {', '.join(updates)} WHERE id = %s AND tenant_id = %s", params)  # nosec B608 — column list from fixed literals; values parameterized
    if cur.rowcount == 0:
        db.rollback()
        cur.close()
        raise HTTPException(404, "Credential not found")
    db.commit()
    cur.close()
    return {"status": "updated", "id": cred_id}


@app.delete("/credentials/{cred_id}", status_code=204)
async def delete_credential(cred_id: str, request: Request):
    """Delete a credential."""
    _authorize_action(request, "credential.create")
    tenant_id = get_tenant_id(request)
    db = _get_db()
    if not db:
        raise HTTPException(502, "DB unavailable")
    cur = db.cursor()
    cur.execute("DELETE FROM credentials WHERE id = %s AND tenant_id = %s", (cred_id, tenant_id))
    if cur.rowcount == 0:
        db.rollback()
        cur.close()
        raise HTTPException(404, "Credential not found")
    db.commit()
    cur.close()


@app.post("/credentials/{cred_id}/use")
async def use_credential(cred_id: str, request: Request):
    """Retrieve credential value (for MCP runtime use — authenticated calls only)."""
    _authorize_action(request, "credential.use")
    tenant_id = get_tenant_id(request)
    db = _get_db()
    if not db:
        raise HTTPException(502, "DB unavailable")
    import psycopg2.extras
    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT encrypted_value, name FROM credentials
        WHERE id = %s AND tenant_id = %s AND is_active = TRUE
    """, (cred_id, tenant_id))
    row = cur.fetchone()
    if not row:
        cur.close()
        raise HTTPException(404, "Credential not found or inactive")
    from credentials import decrypt_platform
    value = decrypt_platform(row["encrypted_value"])
    cur.execute("UPDATE credentials SET last_used_at = NOW() WHERE id = %s", (cred_id,))
    db.commit()
    cur.close()
    return {"value": value, "name": row["name"]}


# ---------------------------------------------------------------------------
# Privacy / GDPR — data-subject export, right-to-erasure, retention
#
# Scope: operational, non-audit personal data only. The append-only,
# tamper-evident `audit_events` table is NEVER modified or deleted here —
# retention there is archival (see docs/SOC2_GAP_ANALYSIS.md). Erasure
# anonymizes the subject's PII in `users` and revokes `user_sessions`, leaving
# referential integrity (and audit history) intact.
# ---------------------------------------------------------------------------

# Retention window for non-audit operational data (days). Pruning is opt-in;
# nothing is deleted unless an operator explicitly invokes the prune routine.
PRIVACY_OPERATIONAL_RETENTION_DAYS = int(
    os.environ.get("MCPFINDER_OPERATIONAL_RETENTION_DAYS", "90")
)
# Documented retention for the immutable audit log (archival only — see doc).
AUDIT_RETENTION_DAYS = int(os.environ.get("MCPFINDER_AUDIT_RETENTION_DAYS", "365"))


class PrivacyEraseRequest(BaseModel):
    subject: str  # user_id (UUID) or email
    mode: str = "anonymize"  # "anonymize" (default) | "deactivate"


def _resolve_privacy_subject(cur, tenant_id: str, subject: str) -> dict | None:
    """Resolve a subject (UUID or email) to a user row within the tenant."""
    import psycopg2.extras  # noqa: F401  (cursor factory set by caller)

    # Try UUID match first, then email; always tenant-scoped.
    cur.execute(
        """SELECT id, tenant_id, email, name, avatar_url, auth_provider,
                  is_active, is_admin, last_login_at, created_at, updated_at
           FROM users
           WHERE tenant_id = %s AND (id::text = %s OR email = %s)
           LIMIT 1""",
        (tenant_id, subject, subject),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _serialize_user_row(row: dict) -> dict:
    out = dict(row)
    out["id"] = str(out.get("id")) if out.get("id") is not None else None
    out["tenant_id"] = str(out.get("tenant_id")) if out.get("tenant_id") is not None else None
    for k in ("last_login_at", "created_at", "updated_at"):
        if out.get(k):
            out[k] = out[k].isoformat()
    return out


@app.get("/privacy/export")
async def privacy_export(request: Request, subject: str):
    """Export a data subject's non-audit personal data (DSAR).

    Gated by the privileged `privacy.export` action. Returns the user record,
    role assignments, and session metadata (no secrets) for the subject within
    the caller's tenant. Writes an audit event for the access.
    """
    _authorize_action(request, "privacy.export")
    tenant_id = get_tenant_id(request)
    db = _get_db()
    if not db:
        raise HTTPException(502, "DB unavailable")
    import psycopg2.extras

    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    user = _resolve_privacy_subject(cur, tenant_id, subject)
    if not user:
        cur.close()
        raise HTTPException(404, "Subject not found in tenant")

    user_id = user["id"]
    cur.execute(
        """SELECT role_id, assignment_source, created_at
           FROM user_roles WHERE user_id = %s""",
        (user_id,),
    )
    roles = [dict(r) for r in cur.fetchall()]
    for r in roles:
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()
        r["role_id"] = str(r["role_id"]) if r.get("role_id") is not None else None

    cur.execute(
        """SELECT id, created_at, expires_at, revoked_at
           FROM user_sessions WHERE user_id = %s ORDER BY created_at DESC""",
        (user_id,),
    )
    sessions = [dict(s) for s in cur.fetchall()]
    for s in sessions:
        s["id"] = str(s["id"]) if s.get("id") is not None else None
        for k in ("created_at", "expires_at", "revoked_at"):
            if s.get(k):
                s[k] = s[k].isoformat()

    # GDPR Art. 15: include the subject's audit trail (events performed by /
    # attributed to the subject). Payloads were secret-redacted at write time.
    audit_trail_limit = 1000
    cur.execute(
        """SELECT seq, created_at, action, resource, server_name, result,
                  trace_id, duration_ms, purpose, lawful_basis, payload
           FROM audit_events
           WHERE tenant_id = %s AND (user_id = %s OR user_id = %s)
           ORDER BY seq DESC LIMIT %s""",
        (tenant_id, str(user_id), user.get("email") or "", audit_trail_limit),
    )
    audit_trail = [dict(a) for a in cur.fetchall()]
    for a in audit_trail:
        if a.get("created_at"):
            a["created_at"] = a["created_at"].isoformat()
    cur.close()

    _write_audit_event(
        action="privacy.export",
        resource=f"user:{user_id}",
        result="ok",
        trace_id=_request_trace_id(request),
        payload={"tenant_id": tenant_id, "subject": subject,
                 "sessions": len(sessions), "roles": len(roles),
                 "audit_events": len(audit_trail)},
        user_id=get_subject_id(request),
        tenant_id=tenant_id,
    )
    return {
        "schema": "mcpfinder.privacy.export",
        "schema_version": 2,
        "tenant_id": tenant_id,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "user": _serialize_user_row(user),
        "roles": roles,
        "sessions": sessions,
        "audit_trail": audit_trail,
        "audit_trail_truncated": len(audit_trail) >= audit_trail_limit,
    }


@app.post("/privacy/erase")
async def privacy_erase(body: PrivacyEraseRequest, request: Request):
    """Right-to-erasure for a data subject's non-audit personal data.

    Gated by the privileged `privacy.erase` action. Anonymizes the user's PII
    (email/name/avatar) and deactivates the account, and revokes all active
    sessions. The immutable `audit_events` log is left untouched (archival
    retention only). Writes an audit event for the action.
    """
    _authorize_action(request, "privacy.erase")
    if body.mode not in ("anonymize", "deactivate"):
        raise HTTPException(400, "mode must be 'anonymize' or 'deactivate'")
    tenant_id = get_tenant_id(request)
    db = _get_db()
    if not db:
        raise HTTPException(502, "DB unavailable")
    import psycopg2.extras

    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    user = _resolve_privacy_subject(cur, tenant_id, subject=body.subject)
    if not user:
        cur.close()
        raise HTTPException(404, "Subject not found in tenant")
    user_id = user["id"]

    try:
        # Always revoke active sessions on erasure/deactivation.
        cur.execute(
            "UPDATE user_sessions SET revoked_at = NOW() "
            "WHERE user_id = %s AND revoked_at IS NULL",
            (user_id,),
        )
        sessions_revoked = cur.rowcount

        if body.mode == "anonymize":
            # Tombstone PII; keep the row for referential integrity. Email is
            # uniqueness-constrained so we use a deterministic per-user value.
            anon_email = f"erased+{user_id}@redacted.invalid"
            cur.execute(
                """UPDATE users
                   SET email = %s, name = NULL, avatar_url = NULL,
                       password_hash = NULL, is_active = FALSE,
                       updated_at = NOW()
                   WHERE id = %s""",
                (anon_email, user_id),
            )
        else:  # deactivate
            cur.execute(
                "UPDATE users SET is_active = FALSE, updated_at = NOW() WHERE id = %s",
                (user_id,),
            )
        db.commit()
        cur.close()
    except Exception as e:
        db.rollback()
        cur.close()
        raise HTTPException(400, f"Erasure failed: {e}")

    _write_audit_event(
        action="privacy.erase",
        resource=f"user:{user_id}",
        result="ok",
        trace_id=_request_trace_id(request),
        payload={"tenant_id": tenant_id, "mode": body.mode,
                 "sessions_revoked": sessions_revoked,
                 "audit_events_preserved": True},
        user_id=get_subject_id(request),
        tenant_id=tenant_id,
    )
    return {
        "status": "erased" if body.mode == "anonymize" else "deactivated",
        "user_id": str(user_id),
        "mode": body.mode,
        "sessions_revoked": sessions_revoked,
        "audit_events_preserved": True,
    }


def prune_operational_data(db_conn, *, retention_days: int | None = None) -> dict:
    """Opt-in pruning of expired NON-audit operational data.

    Deletes only expired/revoked `user_sessions` older than the retention
    window. NEVER touches `audit_events` (append-only, tamper-evident; archival
    retention is documented, not deletion). Returns counts of pruned rows.
    """
    days = retention_days if retention_days is not None else PRIVACY_OPERATIONAL_RETENTION_DAYS
    cur = db_conn.cursor()
    cur.execute(
        """DELETE FROM user_sessions
           WHERE (revoked_at IS NOT NULL OR (expires_at IS NOT NULL AND expires_at < NOW()))
             AND created_at < NOW() - (%s * interval '1 day')""",
        (days,),
    )
    pruned_sessions = cur.rowcount
    db_conn.commit()
    cur.close()
    return {"pruned_sessions": pruned_sessions, "retention_days": days}


# Scheduled retention: opt-out (GDPR storage limitation should not depend on
# an operator remembering to call the prune routine).
RETENTION_SCHEDULE_ENABLED = os.environ.get(
    "MCPFINDER_RETENTION_SCHEDULE", "true"
).lower() in ("1", "true", "yes")
RETENTION_INTERVAL_HOURS = int(os.environ.get("MCPFINDER_RETENTION_INTERVAL_HOURS", "24"))


def run_retention_cycle() -> dict:
    """One scheduled retention pass: prune operational data, report audit backlog.

    audit_events is append-only (tamper-evident); rows past AUDIT_RETENTION_DAYS
    are counted and reported for archival, never deleted here.
    """
    conn = _get_db()
    if not conn:
        return {"skipped": "db unavailable"}
    try:
        summary = prune_operational_data(conn)
    except Exception as e:
        # e.g. deployments without portal auth tables (no user_sessions)
        try:
            conn.rollback()
        except Exception:
            pass
        summary = {"pruned_sessions": 0, "prune_error": str(e).strip().split("\n")[0]}
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM audit_events WHERE created_at < NOW() - (%s * interval '1 day')",
            (AUDIT_RETENTION_DAYS,),
        )
        summary["audit_events_past_retention"] = int(cur.fetchone()[0])
        summary["audit_retention_days"] = AUDIT_RETENTION_DAYS
        cur.close()
    except Exception as e:
        summary["audit_events_past_retention_error"] = str(e)
    _write_audit_event(
        action="retention.prune",
        resource="user_sessions",
        result="ok",
        payload=summary,
        purpose="compliance",
        lawful_basis="legal_obligation",
    )
    return summary


async def _retention_loop():
    """Daily scheduled retention enforcement (see run_retention_cycle)."""
    interval = max(1, RETENTION_INTERVAL_HOURS) * 3600
    while True:
        try:
            summary = await asyncio.to_thread(run_retention_cycle)
            print(f"[router] retention cycle: {summary}")
        except Exception as e:
            print(f"[router] retention cycle warning: {e}")
        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# SCIM v2 lifecycle endpoints
# ---------------------------------------------------------------------------


def _scim_upsert_user(tenant_id: str, user: dict) -> dict:
    db = _get_db()
    if not db:
        raise HTTPException(502, "DB unavailable")
    import psycopg2.extras
    username = user.get("userName") or user.get("email")
    if not username:
        raise HTTPException(400, "SCIM userName is required")
    active = bool(user.get("active", True))
    display_name = user.get("displayName") or user.get("name", {}).get("formatted") or username
    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT id, tenant_id FROM users WHERE email = %s",
        (username,),
    )
    existing = cur.fetchone()
    if existing and str(existing["tenant_id"]) != str(tenant_id):
        db.rollback()
        cur.close()
        raise HTTPException(409, "SCIM userName/email is already owned by another tenant")

    if existing:
        cur.execute(
            """
            UPDATE users
               SET name = %s,
                   is_active = %s,
                   auth_provider = 'scim',
                   updated_at = NOW()
             WHERE id = %s AND tenant_id::text = %s
             RETURNING id, email, name, is_active
            """,
            (display_name, active, existing["id"], tenant_id),
        )
    else:
        cur.execute(
            """
            INSERT INTO users (tenant_id, email, name, auth_provider, is_active)
            VALUES (%s, %s, %s, 'scim', %s)
            ON CONFLICT (email) DO UPDATE SET
              name = EXCLUDED.name,
              is_active = EXCLUDED.is_active,
              auth_provider = 'scim',
              updated_at = NOW()
            WHERE users.tenant_id::text = EXCLUDED.tenant_id::text
            RETURNING id, email, name, is_active
            """,
            (tenant_id, username, display_name, active),
        )
    fetched = cur.fetchone()
    if not fetched:
        db.rollback()
        cur.close()
        raise HTTPException(409, "SCIM userName/email is already owned by another tenant")
    row = dict(fetched)
    db.commit()
    cur.close()
    return {
        "id": str(row["id"]),
        "userName": row["email"],
        "displayName": row["name"],
        "active": row["is_active"],
    }


def _row_value(row, key: str, index: int):
    if row is None:
        return None
    if isinstance(row, Mapping):
        return row.get(key)
    return row[index]


def _scim_validate_patch_target(tenant_id: str, user_id: str, user: dict) -> None:
    """Ensure SCIM PATCH cannot mutate a user other than the path user_id."""
    body_id = str(user.get("id") or "").strip()
    username = str(user.get("userName") or user.get("email") or "").strip()
    if not body_id and not username:
        return
    db = _get_db()
    if not db:
        raise HTTPException(502, "DB unavailable")
    if body_id and body_id != str(user_id):
        db.rollback()
        raise HTTPException(409, "SCIM PATCH path id is authoritative; body id resolves to a different user")
    if not username:
        return
    cur = db.cursor()
    try:
        cur.execute(
            """
            SELECT id, tenant_id, email
            FROM users
            WHERE email = %s
            LIMIT 1
            """,
            (username,),
        )
        row = cur.fetchone()
        if row is not None and str(_row_value(row, "tenant_id", 1)) != str(tenant_id):
            db.rollback()
            raise HTTPException(409, "SCIM userName/email is already owned by another tenant")
        if row is not None and str(_row_value(row, "id", 0)) != str(user_id):
            db.rollback()
            raise HTTPException(409, "SCIM PATCH path id is authoritative; body userName/email resolves to a different user")
    finally:
        cur.close()


def _scim_patch_user_record(tenant_id: str, user_id: str, user: dict) -> dict:
    _scim_validate_patch_target(tenant_id, user_id, user)
    db = _get_db()
    if not db:
        raise HTTPException(502, "DB unavailable")
    import psycopg2.extras
    username = user.get("userName") or user.get("email")
    active = bool(user.get("active", True))
    display_name = user.get("displayName") or user.get("name", {}).get("formatted") or username
    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            """
            UPDATE users
               SET email = COALESCE(%s, email),
                   name = COALESCE(%s, name),
                   is_active = %s,
                   auth_provider = 'scim',
                   updated_at = NOW()
             WHERE id::text = %s AND tenant_id::text = %s
             RETURNING id, email, name, is_active
            """,
            (username, display_name, active, user_id, tenant_id),
        )
        fetched = cur.fetchone()
        if not fetched:
            db.rollback()
            raise HTTPException(404, "SCIM user not found")
        row = dict(fetched)
        db.commit()
        return {
            "id": str(row["id"]),
            "userName": row["email"],
            "displayName": row["name"],
            "active": row["is_active"],
        }
    finally:
        cur.close()


def _scim_deactivate_user(tenant_id: str, user_id: str) -> dict:
    db = _get_db()
    if not db:
        raise HTTPException(502, "DB unavailable")
    cur = db.cursor()
    cur.execute(
        "UPDATE users SET is_active = FALSE, updated_at = NOW() WHERE id::text = %s AND tenant_id::text = %s",
        (user_id, tenant_id),
    )
    if cur.rowcount == 0:
        db.rollback()
        cur.close()
        raise HTTPException(404, "SCIM user not found")
    cur.execute("DELETE FROM user_roles WHERE user_id::text = %s AND assignment_source = 'scim'", (user_id,))
    cur.execute("UPDATE user_sessions SET revoked_at = NOW() WHERE user_id::text = %s AND revoked_at IS NULL", (user_id,))
    db.commit()
    cur.close()
    return {"id": user_id, "active": False, "sessions_revoked": True}


def _scim_sync_group_roles(tenant_id: str, group_id: str, group: dict) -> dict:
    db = _get_db()
    if not db:
        raise HTTPException(502, "DB unavailable")
    display_name = group.get("displayName") or group_id
    roles = [str(role) for role in group.get("roles", [])]
    cur = db.cursor()
    cur.execute(
        """
        INSERT INTO scim_group_role_mappings (tenant_id, external_group_id, display_name, role_names)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (tenant_id, external_group_id) DO UPDATE SET
          display_name = EXCLUDED.display_name,
          role_names = EXCLUDED.role_names,
          updated_at = NOW()
        """,
        (tenant_id, group_id, display_name, roles),
    )
    db.commit()
    cur.close()
    return {"id": group_id, "displayName": display_name, "roles": roles}


# ---------------------------------------------------------------------------
# Licensing / entitlement (open-core)
# ---------------------------------------------------------------------------

def _require_feature(feature: str) -> None:
    """402 unless the current license entitles this enterprise feature."""
    if not licensing.feature_enabled(feature):
        raise HTTPException(
            status_code=402,
            detail=f"'{feature}' requires a Sealfleet Enterprise license. "
                   f"See GET /license; contact sales to unlock enterprise features.",
        )


@app.get("/license")
async def get_license():
    """Public: report the current entitlement (tier + unlocked features).

    Consumed by the portal to gate SSO/multi-user and show the upgrade banner.
    Never exposes the license key itself.
    """
    ent = licensing.resolve_entitlement()
    return {
        **ent.to_public_dict(),
        "enterprise_features": sorted(licensing.ENTERPRISE_FEATURES),
    }


@app.post("/scim/v2/Users", status_code=201)
async def scim_create_user(body: dict, request: Request):
    _require_feature(licensing.FEATURE_SCIM)
    _authorize_action(request, "policy.admin")
    return _scim_upsert_user(get_tenant_id(request), body)


@app.patch("/scim/v2/Users/{user_id}")
async def scim_patch_user(user_id: str, body: dict, request: Request):
    _require_feature(licensing.FEATURE_SCIM)
    _authorize_action(request, "policy.admin")
    tenant_id = get_tenant_id(request)
    if body.get("active") is False:
        _scim_validate_patch_target(tenant_id, user_id, body)
        return _scim_deactivate_user(tenant_id, user_id)
    return _scim_patch_user_record(tenant_id, user_id, body)


@app.put("/scim/v2/Groups/{group_id}")
async def scim_put_group(group_id: str, body: dict, request: Request):
    _require_feature(licensing.FEATURE_SCIM)
    _authorize_action(request, "policy.admin")
    return _scim_sync_group_roles(get_tenant_id(request), group_id, body)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "manifests": len(manifests),
        "typed_manifests": len(typed_manifests),
        "types": len(types_registry),
        "producers": sum(len(v) for v in type_graph.producers.values()),
        "named_pipelines": len(named_pipelines),
        "v2_pipelines": len(v2_pipelines),
        "scale_to_zero": {
            "enabled": scale_manager.enabled,
            "idle_timeout_secs": scale_manager.idle_timeout,
            "tracked_mcps": list(scale_manager.last_call.keys()),
        },
    }


@app.get("/ready")
async def ready():
    db_available = _get_db() is not None
    return {
        "status": "ready" if db_available else "degraded",
        "service": "mcpfinder-runtime",
        "checks": {
            "database": "ok" if db_available else "unavailable",
            "manifests_loaded": len(manifests),
            "named_pipelines_loaded": len(named_pipelines),
        },
    }


@app.get("/enterprise/contract")
async def enterprise_contract():
    """Return the shared enterprise identity/auth/compliance contract."""
    if _enterprise_contract_v1 is None:
        raise HTTPException(status_code=503, detail="enterprise contract package unavailable")
    contract = _enterprise_contract_v1()
    return contract.model_dump() if hasattr(contract, "model_dump") else contract


# ---------------------------------------------------------------------------
# Router JWKS + RFC 8693 Token Exchange
# ---------------------------------------------------------------------------
TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
JWT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:jwt"


def _load_or_generate_router_keypair():
    _assert_router_key_configured()
    pem = os.getenv("ROUTER_RS256_PRIVATE_KEY")
    if pem:
        return serialization.load_pem_private_key(pem.encode(), password=None, backend=default_backend())
    return rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())


_router_private_key = _load_or_generate_router_keypair()
_router_public_key = _router_private_key.public_key()
_ROUTER_KID = hashlib.sha256(
    _router_public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
).hexdigest()[:16]


def _get_router_jwk() -> dict:
    from jwt.algorithms import RSAAlgorithm

    jwk = json.loads(RSAAlgorithm.to_jwk(_router_public_key))
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    jwk["kid"] = _ROUTER_KID
    return jwk


def _issue_mcp_token(subject: str, tenant_id: str, email: str, audience: str, scope: str, is_admin: bool = False, ttl_seconds: int = 900) -> str:
    import jwt as _jwt_mod

    now = int(time.time())
    payload = {
        "iss": ROUTER_ISSUER,
        "sub": subject,
        "aud": audience.rstrip("/"),
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": str(uuid.uuid4()),
        "scope": scope,
        "tenant_id": tenant_id,
        "email": email,
        "is_admin": is_admin,
        "act": {"sub": ROUTER_ISSUER},
    }
    return _jwt_mod.encode(payload, _router_private_key, algorithm="RS256", headers={"kid": _ROUTER_KID})


def _token_subject_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _client_ip(request: Request) -> str:
    direct_ip = request.client.host if request.client else "unknown"
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for and _is_trusted_proxy_ip(direct_ip):
        return forwarded_for.split(",", 1)[0].strip() or direct_ip
    return direct_ip


def _is_trusted_proxy_ip(ip_address: str) -> bool:
    if not TRUSTED_PROXY_CIDRS or ip_address == "unknown":
        return False
    try:
        client_ip = ipaddress.ip_address(ip_address)
    except ValueError:
        return False
    for cidr in TRUSTED_PROXY_CIDRS:
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        if client_ip in network:
            return True
    return False


def _prune_token_exchange_rate_limits(window_start: float) -> None:
    """Drop expired or empty token-exchange rate-limit buckets globally."""
    for bucket, bucket_attempts in list(_token_exchange_rate_limits.items()):
        attempts = [ts for ts in bucket_attempts if ts >= window_start]
        if attempts:
            _token_exchange_rate_limits[bucket] = attempts
        else:
            _token_exchange_rate_limits.pop(bucket, None)


def _evict_token_exchange_rate_limit_buckets(protected_buckets: set[str]) -> None:
    """Bound rate-limit memory by evicting the stalest non-current buckets."""
    max_buckets = max(1, TOKEN_EXCHANGE_RATE_LIMIT_MAX_BUCKETS)
    overflow = len(_token_exchange_rate_limits) - max_buckets
    if overflow <= 0:
        return

    evictable = sorted(
        (
            (max(attempts) if attempts else 0.0, bucket)
            for bucket, attempts in _token_exchange_rate_limits.items()
            if bucket not in protected_buckets
        ),
        key=lambda item: item[0],
    )
    for _last_seen, bucket in evictable[:overflow]:
        _token_exchange_rate_limits.pop(bucket, None)


def _check_token_exchange_rate_limit(ip_address: str, subject_key: str) -> bool:
    now = time.monotonic()
    window_start = now - TOKEN_EXCHANGE_RATE_LIMIT_WINDOW_SECONDS
    _prune_token_exchange_rate_limits(window_start)

    buckets = (f"ip:{ip_address}", f"subject:{subject_key}")
    for bucket in buckets:
        attempts = _token_exchange_rate_limits.get(bucket, [])
        if len(attempts) >= TOKEN_EXCHANGE_RATE_LIMIT_MAX:
            _token_exchange_rate_limits[bucket] = attempts
            return False
        attempts.append(now)
        _token_exchange_rate_limits[bucket] = attempts

    _evict_token_exchange_rate_limit_buckets(set(buckets))
    return True


def _registered_mcp_resources() -> dict[str, str]:
    resources: dict[str, str] = {}
    for name, manifest in manifests.items():
        endpoint = getattr(manifest, "endpoint", "")
        if endpoint:
            resources[endpoint.rstrip("/")] = name
        resources[name] = name
    for name, manifest in typed_manifests.items():
        endpoint = manifest.get("endpoint", "") if isinstance(manifest, dict) else ""
        if endpoint:
            resources[endpoint.rstrip("/")] = name
        resources[name] = name
    return resources


def _resolve_registered_mcp_resource(resource: str) -> tuple[str, str] | None:
    normalized = resource.rstrip("/") if resource else ""
    server_name = _registered_mcp_resources().get(normalized)
    if not server_name:
        return None
    return normalized, server_name


def _scope_set(scope: str) -> set[str]:
    return {item for item in scope.split() if item}


def _is_platform_admin(user_info: dict) -> bool:
    roles = user_info.get("roles") or user_info.get("role") or []
    if isinstance(roles, str):
        roles = [roles]
    return bool(user_info.get("is_admin") or user_info.get("platform_admin") or "platform_admin" in roles)


def _audit_token_exchange(*, result: str, resource: str = "", server_name: str = "", user_id: str = "system", tenant_id: str = "system", ip_address: str = "", reason: str = "", scope: str = "", trace_id: str = ""):
    payload = {"ip_address": ip_address}
    if reason:
        payload["reason"] = reason
    if scope:
        payload["scope"] = scope
    _write_audit_event(action="token_exchange", resource=resource, server_name=server_name, result=result, trace_id=trace_id or _new_trace_id(), payload=payload, user_id=user_id or "system", tenant_id=tenant_id or "system")


def _json_error(error: str, status_code: int, *, description: str = ""):
    from fastapi.responses import JSONResponse
    body = {"error": error}
    if description:
        body["error_description"] = description
    return JSONResponse(body, status_code=status_code)


def _validate_mcp_access_token(token: str, *, audience: str, required_scope: str = "mcp:call") -> dict | None:
    try:
        import jwt
        payload = jwt.decode(
            token,
            _router_public_key,
            algorithms=["RS256"],
            audience=audience.rstrip("/"),
            issuer=ROUTER_ISSUER,
            options={"require": ["exp", "aud", "sub"]},
        )
        if required_scope not in _scope_set(payload.get("scope", "")):
            return None
        return payload
    except Exception:
        return None


@app.get("/.well-known/jwks.json", include_in_schema=False)
async def router_jwks():
    from fastapi.responses import JSONResponse
    return JSONResponse(
        {"keys": [_get_router_jwk()]},
        headers={"Cache-Control": "public, max-age=300", "Content-Type": "application/jwk-set+json"},
    )


_ROUTER_RESOURCE_METADATA = {
    "resource": ROUTER_ISSUER,
    "authorization_servers": [ROUTER_ISSUER],
    "bearer_methods_supported": ["header"],
    "scopes_supported": ["mcp:call", "mcp:admin"],
    "resource_documentation": "https://docs.sealfleet.io/auth",
}


@app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
async def router_resource_metadata():
    from fastapi.responses import JSONResponse
    return JSONResponse(_ROUTER_RESOURCE_METADATA, headers={"Cache-Control": "public, max-age=3600"})


@app.post("/token")
async def token_exchange(
    request: Request,
    grant_type: str = Form(...),
    subject_token: str = Form(...),
    subject_token_type: str = Form(JWT_TOKEN_TYPE),
    resource: str = Form(None),
    scope: str = Form(""),
    audience: str = Form(None),
):
    from fastapi.responses import JSONResponse

    trace_id = _request_trace_id(request)
    ip_address = _client_ip(request)
    subject_key = _token_subject_hash(subject_token)
    requested_scope = scope or "mcp:call"
    if not _check_token_exchange_rate_limit(ip_address, subject_key):
        _audit_token_exchange(trace_id=trace_id, result="rate_limited", ip_address=ip_address, reason="rate_limit_exceeded", scope=requested_scope)
        return _json_error("rate_limited", 429, description="too many token exchange attempts")

    if grant_type != TOKEN_EXCHANGE_GRANT:
        _audit_token_exchange(trace_id=trace_id, result="denied", ip_address=ip_address, reason="unsupported_grant_type")
        return _json_error("unsupported_grant_type", 400)
    if subject_token_type != JWT_TOKEN_TYPE:
        _audit_token_exchange(trace_id=trace_id, result="denied", ip_address=ip_address, reason="unsupported_subject_token_type")
        return _json_error("invalid_request", 400, description="subject_token_type must be JWT")

    user_info = _validate_user_jwt(subject_token)
    if not user_info:
        _audit_token_exchange(trace_id=trace_id, result="denied", ip_address=ip_address, reason="invalid_subject_token")
        return _json_error("invalid_token", 401)

    token_audience = resource or audience
    if not token_audience:
        _audit_token_exchange(trace_id=trace_id, result="denied", ip_address=ip_address, user_id=user_info.get("user_id", "system"), tenant_id=user_info.get("tenant_id", "system"), reason="missing_resource_or_audience")
        return _json_error("invalid_request", 400, description="resource or audience required")

    resolved_resource = _resolve_registered_mcp_resource(token_audience)
    if not resolved_resource:
        _audit_token_exchange(trace_id=trace_id, result="denied", resource=token_audience, ip_address=ip_address, user_id=user_info.get("user_id", "system"), tenant_id=user_info.get("tenant_id", "system"), reason="unregistered_resource")
        return _json_error("invalid_target", 400, description="resource is not a registered MCP resource")

    normalized_resource, server_name = resolved_resource
    requested_scopes = _scope_set(requested_scope)
    allowed_scopes = {"mcp:call", "mcp:admin"} if _is_platform_admin(user_info) else {"mcp:call"}
    if not requested_scopes or not requested_scopes.issubset(allowed_scopes):
        _audit_token_exchange(trace_id=trace_id, result="denied", resource=normalized_resource, server_name=server_name, ip_address=ip_address, user_id=user_info.get("user_id", "system"), tenant_id=user_info.get("tenant_id", "system"), reason="scope_not_allowed", scope=requested_scope)
        return _json_error("insufficient_scope", 403, description="requested scope is not allowed")

    issued_scope = " ".join(sorted(requested_scopes))
    mcp_token = _issue_mcp_token(
        subject=user_info.get("sub") or user_info.get("user_id") or "",
        tenant_id=user_info.get("tenant_id") or "",
        email=user_info.get("email") or "",
        audience=normalized_resource,
        scope=issued_scope,
        is_admin=_is_platform_admin(user_info),
    )
    _audit_token_exchange(trace_id=trace_id, result="ok", resource=normalized_resource, server_name=server_name, ip_address=ip_address, user_id=user_info.get("user_id", "system"), tenant_id=user_info.get("tenant_id", "system"), scope=issued_scope)
    return JSONResponse({"access_token": mcp_token, "issued_token_type": JWT_TOKEN_TYPE, "token_type": "Bearer", "expires_in": 900, "scope": issued_scope})


# ---------------------------------------------------------------------------
# Optional private extensions
# ---------------------------------------------------------------------------

def _load_internal_extensions() -> None:
    """Load optional private extensions from runtime/router_internal.py.

    The file is not part of the platform kit; deployments without it run the
    pure platform. When present (overlaid from a private repo), its register()
    hook may add routes, deployment-map entries, and pipeline listings.
    """
    try:
        import router_internal
    except ImportError:
        return
    try:
        router_internal.register(app)
        print("[router] internal extensions loaded (router_internal)")
    except Exception as e:  # fail open to pure platform, but say so loudly
        print(f"[router] WARNING: internal extensions failed to load: {e}")


_load_internal_extensions()
