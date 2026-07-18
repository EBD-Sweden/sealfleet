import { describe, expect, it } from "vitest";
import crypto from "crypto";
import { verifyWebhook } from "@/lib/stripe";
import { isEntitled } from "@/lib/billing";
import { slugify, generateApiKey } from "@/lib/provisioning";

const SECRET = "whsec_test_secret";

function sign(body: string, ts: number, secret = SECRET): string {
  const sig = crypto.createHmac("sha256", secret).update(`${ts}.${body}`, "utf8").digest("hex");
  return `t=${ts},v1=${sig}`;
}

describe("verifyWebhook", () => {
  const now = 1_700_000_000;
  const body = JSON.stringify({ type: "checkout.session.completed", data: { object: {} } });

  it("accepts a correctly signed payload", () => {
    const event = verifyWebhook(body, sign(body, now), SECRET, now);
    expect(event.type).toBe("checkout.session.completed");
  });

  it("rejects a tampered body", () => {
    const header = sign(body, now);
    expect(() => verifyWebhook(body + "x", header, SECRET, now)).toThrow();
  });

  it("rejects a wrong secret", () => {
    const header = sign(body, now, "whsec_other");
    expect(() => verifyWebhook(body, header, SECRET, now)).toThrow();
  });

  it("rejects a timestamp outside tolerance (replay)", () => {
    const header = sign(body, now - 10_000);
    expect(() => verifyWebhook(body, header, SECRET, now)).toThrow(/tolerance/);
  });

  it("rejects a missing signature header", () => {
    expect(() => verifyWebhook(body, null, SECRET, now)).toThrow(/Missing/);
  });

  it("rejects a malformed header", () => {
    expect(() => verifyWebhook(body, "garbage", SECRET, now)).toThrow();
  });
});

describe("isEntitled", () => {
  it("is true only for trialing/active", () => {
    expect(isEntitled("active")).toBe(true);
    expect(isEntitled("trialing")).toBe(true);
    expect(isEntitled("past_due")).toBe(false);
    expect(isEntitled("canceled")).toBe(false);
    expect(isEntitled("inactive")).toBe(false);
    expect(isEntitled(null)).toBe(false);
    expect(isEntitled(undefined)).toBe(false);
  });
});

describe("provisioning helpers", () => {
  it("slugify produces safe slugs", () => {
    expect(slugify("Acme Inc")).toBe("acme-inc");
    expect(slugify("  Weird!!  Name  ")).toBe("weird-name");
    expect(slugify("")).toBe("org");
    expect(slugify("A".repeat(100)).length).toBeLessThanOrEqual(40);
  });

  it("generateApiKey is prefixed and unique", () => {
    const a = generateApiKey();
    const b = generateApiKey();
    expect(a.startsWith("mcpf_")).toBe(true);
    expect(a).not.toBe(b);
    expect(a.length).toBeGreaterThan(20);
  });
});
