#!/usr/bin/env python3
"""Run the safe public-demo OpenAPI-to-MCP creation flow locally.

No network calls, no credentials, and no real deploy are performed. The command
writes generated demo artifacts and can invoke the generated fake tool.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
sys.path.insert(0, str(RUNTIME))

from openapi_demo import DemoOpenAPIError, create_demo_openapi_mcp, invoke_generated_demo_tool  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Safe fake OpenAPI-to-MCP demo generator")
    parser.add_argument("--output-dir", default=str(ROOT / "runtime" / ".generated"), help="Directory for generated artifacts")
    parser.add_argument("--invoke", action="store_true", help="Invoke get_demo_customer after generating artifacts")
    parser.add_argument("--customer-id", default="CUST-DEMO-001")
    args = parser.parse_args()

    request = {
        "mode": "public_demo",
        "tenant_id": "demo-sandbox",
        "workspace_id": "demo-external-evaluation",
        "spec_ref": "checked-in:fake-crm-openapi",
        "deploy_action": "dry_run",
        "output_dir": args.output_dir,
    }
    try:
        receipt = create_demo_openapi_mcp(request)
        if args.invoke:
            receipt["invocation"] = invoke_generated_demo_tool(
                receipt["artifact_dir"],
                "get_demo_customer",
                {"customer_id": args.customer_id},
            )
    except DemoOpenAPIError as exc:
        print(json.dumps(exc.to_dict(), indent=2, sort_keys=True), file=sys.stderr)
        return 2

    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
