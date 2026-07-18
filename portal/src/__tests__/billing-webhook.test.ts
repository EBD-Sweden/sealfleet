import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  jsonMock,
  verifyWebhookMock,
  upsertSubscriptionMock,
  setTenantKeysActiveMock,
  findTenantIdByCustomerMock,
} = vi.hoisted(() => ({
  jsonMock: vi.fn((body: unknown, init?: ResponseInit) => ({
    body,
    status: init?.status ?? 200,
  })),
  verifyWebhookMock: vi.fn(),
  upsertSubscriptionMock: vi.fn(),
  setTenantKeysActiveMock: vi.fn(),
  findTenantIdByCustomerMock: vi.fn(),
}));

vi.mock("next/server", () => ({
  NextResponse: { json: jsonMock },
}));

vi.mock("@/lib/stripe", () => ({
  verifyWebhook: verifyWebhookMock,
}));

vi.mock("@/lib/billing", () => ({
  upsertSubscription: upsertSubscriptionMock,
  setTenantKeysActive: setTenantKeysActiveMock,
  findTenantIdByCustomer: findTenantIdByCustomerMock,
  isEntitled: (s: string) => s === "active" || s === "trialing",
}));

import { POST } from "@/app/api/billing/webhook/route";

function req(): any {
  return {
    text: async () => "{}",
    headers: { get: () => "t=1,v1=abc" },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  process.env.STRIPE_WEBHOOK_SECRET = "whsec_x";
});

describe("billing webhook route", () => {
  it("503 when webhook secret is not configured", async () => {
    delete process.env.STRIPE_WEBHOOK_SECRET;
    const res = await POST(req());
    expect(res.status).toBe(503);
  });

  it("400 on invalid signature", async () => {
    verifyWebhookMock.mockImplementation(() => {
      throw new Error("bad sig");
    });
    const res = await POST(req());
    expect(res.status).toBe(400);
    expect(setTenantKeysActiveMock).not.toHaveBeenCalled();
  });

  it("checkout.session.completed activates the tenant's keys", async () => {
    verifyWebhookMock.mockReturnValue({
      type: "checkout.session.completed",
      data: { object: { metadata: { tenant_id: "t-1" }, customer: "cus_1", subscription: "sub_1" } },
    });
    const res = await POST(req());
    expect(res.status).toBe(200);
    expect(upsertSubscriptionMock).toHaveBeenCalledWith(
      expect.objectContaining({ tenantId: "t-1", status: "active" }),
    );
    expect(setTenantKeysActiveMock).toHaveBeenCalledWith("t-1", true);
  });

  it("subscription.deleted deactivates the tenant's keys", async () => {
    verifyWebhookMock.mockReturnValue({
      type: "customer.subscription.deleted",
      data: { object: { metadata: { tenant_id: "t-2" }, customer: "cus_2", id: "sub_2", status: "canceled" } },
    });
    const res = await POST(req());
    expect(res.status).toBe(200);
    expect(upsertSubscriptionMock).toHaveBeenCalledWith(
      expect.objectContaining({ tenantId: "t-2", status: "canceled" }),
    );
    expect(setTenantKeysActiveMock).toHaveBeenCalledWith("t-2", false);
  });

  it("past_due subscription deactivates keys", async () => {
    verifyWebhookMock.mockReturnValue({
      type: "customer.subscription.updated",
      data: { object: { metadata: { tenant_id: "t-3" }, customer: "cus_3", id: "sub_3", status: "past_due" } },
    });
    await POST(req());
    expect(setTenantKeysActiveMock).toHaveBeenCalledWith("t-3", false);
  });

  it("falls back to customer lookup when tenant_id metadata is absent", async () => {
    findTenantIdByCustomerMock.mockResolvedValue("t-4");
    verifyWebhookMock.mockReturnValue({
      type: "customer.subscription.updated",
      data: { object: { customer: "cus_4", id: "sub_4", status: "active" } },
    });
    await POST(req());
    expect(findTenantIdByCustomerMock).toHaveBeenCalledWith("cus_4");
    expect(setTenantKeysActiveMock).toHaveBeenCalledWith("t-4", true);
  });
});
