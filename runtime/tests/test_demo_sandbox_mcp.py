from mcps.demo_sandbox.main import score_fake_vendor, summarize_fake_invoice


def test_summarize_fake_invoice_flags_large_demo_invoice():
    result = summarize_fake_invoice(
        invoice_id="INV-DEMO-001",
        amount_usd=12450,
        vendor_name="Northwind Demo Supplies",
    )

    assert result == {
        "classification": "fake-demo-only",
        "invoice_id": "INV-DEMO-001",
        "vendor_name": "Northwind Demo Supplies",
        "status": "review_required",
        "reasons": [
            "amount exceeds demo auto-approve threshold",
            "fake vendor onboarding check required",
        ],
        # Declared as pii_fields in the manifest -> redacted by the runtime
        # at the execution boundary (raw here because we call the MCP directly).
        "approver_contact": "demo.approver@example.com",
    }


def test_score_fake_vendor_is_deterministic_and_bounded():
    result = score_fake_vendor(
        vendor_name="Northwind Demo Supplies",
        country="SE",
        risk_hint="fake data: new vendor, normal payment terms",
    )

    assert result == {
        "classification": "fake-demo-only",
        "vendor_name": "Northwind Demo Supplies",
        "country": "SE",
        "score": 72,
        "tier": "demo-medium",
        "risk_hint_used": True,
    }


# --- tool catalog contract ---------------------------------------------------
# Agents that author pipelines read /tools (through the router's /mcp-tools) to
# learn argument names, so the catalog must match the function signatures.

def test_tools_catalog_names_every_accepted_argument():
    import inspect
    from fastapi.testclient import TestClient
    from mcps.demo_sandbox.main import app, score_fake_vendor, summarize_fake_invoice

    catalog = {t["name"]: t for t in TestClient(app).get("/tools").json()}
    for fn in (summarize_fake_invoice, score_fake_vendor):
        declared = set(catalog[fn.__name__]["inputs"])
        assert declared == set(inspect.signature(fn).parameters), fn.__name__


def test_call_with_unknown_argument_is_a_400_not_a_crash():
    from fastapi.testclient import TestClient
    from mcps.demo_sandbox.main import app

    r = TestClient(app).post("/call", json={
        "tool": "summarize_fake_invoice",
        "inputs": {"invoice_id": "x", "amount_usd": 1, "vendor_name": "v", "country": "SE"},
    })
    assert r.status_code == 400
    assert "country" in r.json()["detail"]
