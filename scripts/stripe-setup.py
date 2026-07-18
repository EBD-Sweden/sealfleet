#!/usr/bin/env python3
"""Create/ensure the Sealfleet product catalog in Stripe. Idempotent.

Usage:
    STRIPE_SK=sk_live_xxx python scripts/stripe-setup.py
    # writes the resulting price/meter IDs to $OUT_FILE (default /tmp/sealfleet_prices.json)

Creates two products (Hosted: monthly/annual/usage/overage; Self-Hosted License:
annual/monthly) and the `sealfleet_api_calls` Billing Meter. Safe to re-run:
products are matched by name and prices by lookup_key. See docs/BILLING.md.
"""
import os, sys, json, re, urllib.parse, urllib.request

SK = os.environ["STRIPE_SK"]
BASE = "https://api.stripe.com/v1"

def _ascii(s):
    return re.sub(r"[^a-zA-Z0-9_.-]", "-", s)

def call(method, path, data=None, idem=None):
    url = f"{BASE}{path}"
    body = urllib.parse.urlencode(data, doseq=True).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {SK}")
    if body is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    if idem:
        req.add_header("Idempotency-Key", _ascii(idem))
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        print("ERROR", method, path, e.read().decode()[:400]); raise

def find_product(name):
    # search API (live-enabled). Fallback to list scan.
    q = urllib.parse.quote(f"name:'{name}'")
    try:
        res = call("GET", f"/products/search?query={q}")
        if res.get("data"):
            return res["data"][0]
    except Exception:
        pass
    res = call("GET", "/products?limit=100&active=true")
    for p in res.get("data", []):
        if p["name"] == name:
            return p
    return None

def ensure_product(name, desc):
    p = find_product(name)
    if p:
        print(f"  product exists: {name} -> {p['id']}")
        return p["id"]
    p = call("POST", "/products", {"name": name, "description": desc,
             "metadata[product]": "sealfleet"}, idem=f"prod-{name}")
    print(f"  product CREATED: {name} -> {p['id']}")
    return p["id"]

def find_price_by_lookup(lookup):
    res = call("GET", f"/prices?lookup_keys[]={urllib.parse.quote(lookup)}&limit=1")
    return res["data"][0] if res.get("data") else None

def ensure_price(product_id, lookup, params):
    existing = find_price_by_lookup(lookup)
    if existing:
        print(f"  price exists: {lookup} -> {existing['id']}")
        return existing["id"]
    data = {"product": product_id, "lookup_key": lookup, "currency": "eur",
            "metadata[product]": "sealfleet"}
    data.update(params)
    p = call("POST", "/prices", data, idem=f"price-{lookup}")
    print(f"  price CREATED: {lookup} -> {p['id']}")
    return p["id"]

def ensure_meter(event_name, display_name):
    res = call("GET", "/billing/meters?limit=100&status=active")
    for m in res.get("data", []):
        if m.get("event_name") == event_name:
            print(f"  meter exists: {event_name} -> {m['id']}")
            return m["id"]
    m = call("POST", "/billing/meters", {
        "display_name": display_name,
        "event_name": event_name,
        "default_aggregation[formula]": "sum",
        "value_settings[event_payload_key]": "value",
        "customer_mapping[type]": "by_id",
        "customer_mapping[event_payload_key]": "stripe_customer_id",
    }, idem=f"meter-{event_name}")
    print(f"  meter CREATED: {event_name} -> {m['id']}")
    return m["id"]

out = {}

print("== Usage meter ==")
meter_id = ensure_meter("sealfleet_api_calls", "Sealfleet API calls")
out["METER_ID"] = meter_id
out["METER_EVENT_NAME"] = "sealfleet_api_calls"

print("== Product 1: Sealfleet Enterprise — Hosted ==")
hosted = ensure_product("Sealfleet Enterprise — Hosted",
    "Fully-managed Sealfleet (SSO, SCIM, multi-tenant, RBAC). Scale-to-zero hosted platform.")

# Monthly flat (base 50k baked; includes 2M calls/mo)
out["HOSTED_MONTHLY"] = ensure_price(hosted, "sealfleet_hosted_monthly", {
    "unit_amount": "399000", "recurring[interval]": "month",
    "nickname": "Hosted — Monthly (incl. 2M calls/mo)"})
# Annual flat (~10% off monthly)
out["HOSTED_ANNUAL"] = ensure_price(hosted, "sealfleet_hosted_annual", {
    "unit_amount": "4300000", "recurring[interval]": "year",
    "nickname": "Hosted — Annual (incl. 2M calls/mo)"})
# Usage-only (pay-as-you-go), metered: €49 per 1M calls = 0.0049 cents/call
out["HOSTED_USAGE"] = ensure_price(hosted, "sealfleet_hosted_usage", {
    "unit_amount_decimal": "0.0049", "recurring[interval]": "month",
    "recurring[usage_type]": "metered", "recurring[meter]": meter_id,
    "billing_scheme": "per_unit",
    "nickname": "Hosted — Usage only (€49 / 1M calls)"})
# Overage add-on for monthly/annual, metered: €15 per 1M = 0.0015 cents/call
out["HOSTED_OVERAGE"] = ensure_price(hosted, "sealfleet_hosted_overage", {
    "unit_amount_decimal": "0.0015", "recurring[interval]": "month",
    "recurring[usage_type]": "metered", "recurring[meter]": meter_id,
    "billing_scheme": "per_unit",
    "nickname": "Hosted — Overage (€15 / 1M calls above included)"})

print("== Product 2: Sealfleet Enterprise — Self-Hosted License ==")
selfhost = ensure_product("Sealfleet Enterprise — Self-Hosted License",
    "Annual Sealfleet Enterprise license (signed key): SSO, SCIM, multi-tenant, RBAC, audit export. You run it (BYOF).")
# Annual €46,000 (~$50k)
out["LICENSE_ANNUAL"] = ensure_price(selfhost, "sealfleet_license_annual", {
    "unit_amount": "4600000", "recurring[interval]": "year",
    "nickname": "Self-Hosted License — Annual (~$50k)"})
# Monthly €4,500
out["LICENSE_MONTHLY"] = ensure_price(selfhost, "sealfleet_license_monthly", {
    "unit_amount": "450000", "recurring[interval]": "month",
    "nickname": "Self-Hosted License — Monthly"})

print("\n=== PRICE IDS ===")
print(json.dumps(out, indent=2))
with open(os.environ.get("OUT_FILE", "/tmp/sealfleet_prices.json"), "w") as f:
    json.dump(out, f, indent=2)
