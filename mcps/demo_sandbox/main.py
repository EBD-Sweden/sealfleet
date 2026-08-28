"""Fake-data-only MCP used by the external demo sandbox.

The functions are deterministic and contain no network, file, credential, or sealed-input access.
They are safe to expose only behind the demo tenant/workspace auth boundary.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

CLASSIFICATION = "fake-demo-only"
AUTO_APPROVE_THRESHOLD_USD = 5_000

app = FastAPI(title="Sealfleet Demo Sandbox MCP")


class ToolCall(BaseModel):
    tool: str
    inputs: dict[str, Any] = Field(default_factory=dict)


def summarize_fake_invoice(invoice_id: str, amount_usd: float, vendor_name: str) -> dict[str, Any]:
    reasons: list[str] = []
    if amount_usd > AUTO_APPROVE_THRESHOLD_USD:
        reasons.append("amount exceeds demo auto-approve threshold")
    if vendor_name.strip().lower() == "northwind demo supplies":
        reasons.append("fake vendor onboarding check required")

    return {
        "classification": CLASSIFICATION,
        "invoice_id": invoice_id,
        "vendor_name": vendor_name,
        "status": "review_required" if reasons else "demo_auto_approved",
        "reasons": reasons,
        # Fake contact — declared as a PII field in the manifest so the
        # runtime redacts it at the execution boundary (demo of pii_fields).
        "approver_contact": "demo.approver@example.com",
    }


def score_fake_vendor(vendor_name: str, country: str, risk_hint: str | None = None) -> dict[str, Any]:
    score = 80
    hint = (risk_hint or "").lower()
    if "new vendor" in hint:
        score -= 8
    if country.upper() not in {"SE", "NO", "DK", "FI", "US"}:
        score -= 10

    tier = "demo-low" if score >= 80 else "demo-medium" if score >= 60 else "demo-high"
    return {
        "classification": CLASSIFICATION,
        "vendor_name": vendor_name,
        "country": country,
        "score": max(0, min(100, score)),
        "tier": tier,
        "risk_hint_used": bool(risk_hint),
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "service": "demo-sandbox-mcp", "classification": CLASSIFICATION}


@app.get("/tools")
def tools() -> list[dict[str, Any]]:
    # `inputs` names every accepted argument. Agents that author pipelines read
    # this catalog (via the router's /mcp-tools), so it must match the function
    # signatures exactly — an unknown kwarg is a 400 from /call.
    return [
        {
            "name": "summarize_fake_invoice",
            "description": "Summarize a fake demo invoice.",
            "inputs": {
                "invoice_id": "str — demo invoice id",
                "amount_usd": "float — invoice amount",
                "vendor_name": "str — vendor name",
            },
        },
        {
            "name": "score_fake_vendor",
            "description": "Score a fake demo vendor.",
            "inputs": {
                "vendor_name": "str — vendor name",
                "country": "str — ISO country code",
                "risk_hint": "str, optional — free-text risk note",
            },
        },
    ]


@app.post("/call")
def call_tool(call: ToolCall) -> dict[str, Any]:
    if call.tool == "summarize_fake_invoice":
        fn = summarize_fake_invoice
    elif call.tool == "score_fake_vendor":
        fn = score_fake_vendor
    else:
        raise HTTPException(status_code=404, detail="unknown demo sandbox tool")
    try:
        return fn(**call.inputs)
    except TypeError as e:
        # Wrong/missing argument names are the caller's error, not a crash.
        raise HTTPException(status_code=400, detail=f"{call.tool}: {e}") from e
