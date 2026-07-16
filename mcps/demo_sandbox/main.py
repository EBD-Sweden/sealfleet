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
def tools() -> list[dict[str, str]]:
    return [
        {"name": "summarize_fake_invoice", "description": "Summarize a fake demo invoice."},
        {"name": "score_fake_vendor", "description": "Score a fake demo vendor."},
    ]


@app.post("/call")
def call_tool(call: ToolCall) -> dict[str, Any]:
    if call.tool == "summarize_fake_invoice":
        return summarize_fake_invoice(**call.inputs)
    if call.tool == "score_fake_vendor":
        return score_fake_vendor(**call.inputs)
    raise HTTPException(status_code=404, detail="unknown demo sandbox tool")
