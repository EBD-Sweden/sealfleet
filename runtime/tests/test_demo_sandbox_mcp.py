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
