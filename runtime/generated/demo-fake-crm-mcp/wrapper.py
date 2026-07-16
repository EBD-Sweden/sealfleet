"""Generated fake CRM MCP wrapper for public demo use only."""

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
