"""Snapshot the live tool catalog for training-data generation.

Captures exactly what the chat sends a model: the pipeline tools offered at
selection, the MCP control-server tool schemas offered to the platform loop,
and real outputs of the READ-ONLY platform tools (used as tool results in
multi-step examples). Nothing that writes is called.

Run in the core-agent pod:

    kubectl -n sealfleet cp agent/train core-agent-<pod>:/tmp/train
    kubectl -n sealfleet exec deploy/core-agent -- python /tmp/train/snapshot_catalog.py /tmp/train/catalog.json
    kubectl -n sealfleet cp core-agent-<pod>:/tmp/train/catalog.json agent/train/catalog.json
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "/app")                                   # core-agent image
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo checkout
import server  # noqa: E402  (agent/server.py)

# (tool, arguments) pairs safe to execute: listings and gets only.
READ_ONLY_CALLS = [
    ("platform_status", {}), ("mcp_list", {}), ("mcp_tools", {}), ("mcp_deployments", {}),
    ("pipeline_list", {}), ("job_list", {}), ("job_list", {"status": "failed"}),
    ("credential_list", {}), ("policy_rules", {}), ("audit_events", {"limit": 5}),
    ("scale_status", {}),
]


def _names(listing) -> list[str]:
    """MCP names from an mcp_list result ({"mcps": [...]}, or a bare list)."""
    if isinstance(listing, dict):
        listing = next((v for v in listing.values() if isinstance(v, list)), [])
    return [m["name"] for m in listing if isinstance(m, dict) and m.get("name")]


async def main(out: str) -> None:
    identity = {"X-API-Key": server.ROUTER_API_KEY}
    mcp = server.McpControlClient(identity)
    await mcp.initialize()
    platform_tools = await mcp.list_tools()
    pipeline_tools = [{k: v for k, v in t.items() if not k.startswith("_")}
                      for t in await server.fetch_pipeline_tools(identity)]
    mcps = _names(json.loads((await mcp.call_tool("mcp_list", {}))[0] or "[]"))

    observations = []
    calls = list(READ_ONLY_CALLS)
    calls += [("mcp_get", {"name": n}) for n in mcps]
    calls += [("pipeline_get", {"name": t["name"]}) for t in pipeline_tools]
    for name, args in calls:
        try:
            text, is_error = await mcp.call_tool(name, args)
        except Exception as e:  # noqa: BLE001
            text, is_error = f"error: {e}", True
        observations.append({"tool": name, "arguments": args, "is_error": is_error, "result": text[:12000]})

    Path(out).write_text(json.dumps({
        "pipeline_tools": pipeline_tools,
        "platform_tools": platform_tools,
        "mcps": mcps,
        "observations": observations,
    }, indent=1, ensure_ascii=False))
    print(f"wrote {out}: {len(pipeline_tools)} pipelines, {len(platform_tools)} platform tools, "
          f"{len(observations)} observations")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "catalog.json"))
