import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  jsonMock,
  reportMeterEventMock,
  meteredSubscriptionsMock,
  usageCountBetweenMock,
  advanceUsageWatermarkMock,
} = vi.hoisted(() => ({
  jsonMock: vi.fn((body: unknown, init?: ResponseInit) => ({ body, status: init?.status ?? 200 })),
  reportMeterEventMock: vi.fn(),
  meteredSubscriptionsMock: vi.fn(),
  usageCountBetweenMock: vi.fn(),
  advanceUsageWatermarkMock: vi.fn(),
}));

vi.mock("next/server", () => ({ NextResponse: { json: jsonMock } }));
vi.mock("@/lib/stripe", () => ({ reportMeterEvent: reportMeterEventMock }));
vi.mock("@/lib/billing", () => ({
  meteredSubscriptions: meteredSubscriptionsMock,
  usageCountBetween: usageCountBetweenMock,
  advanceUsageWatermark: advanceUsageWatermarkMock,
}));

import { POST } from "@/app/api/billing/report-usage/route";

function req(secret?: string): any {
  return { headers: { get: (h: string) => (h === "x-billing-cron-secret" ? secret : null) } };
}

beforeEach(() => {
  vi.clearAllMocks();
  process.env.BILLING_CRON_SECRET = "cron-secret";
  process.env.STRIPE_SECRET_KEY = "sk_x";
  process.env.STRIPE_PRICE_HOSTED_USAGE = "price_u";
  meteredSubscriptionsMock.mockResolvedValue([]);
});

describe("report-usage route", () => {
  it("503 when not configured", async () => {
    delete process.env.BILLING_CRON_SECRET;
    expect((await POST(req())).status).toBe(503);
  });

  it("401 with wrong secret", async () => {
    expect((await POST(req("nope"))).status).toBe(401);
    expect(reportMeterEventMock).not.toHaveBeenCalled();
  });

  it("first-time subscription just plants the watermark, bills nothing", async () => {
    meteredSubscriptionsMock.mockResolvedValue([
      { tenant_id: "t1", stripe_customer_id: "cus_1", usage_reported_through: null },
    ]);
    const res = await POST(req("cron-secret"));
    expect(res.status).toBe(200);
    expect(advanceUsageWatermarkMock).toHaveBeenCalledWith("t1", expect.any(Date));
    expect(usageCountBetweenMock).not.toHaveBeenCalled();
    expect(reportMeterEventMock).not.toHaveBeenCalled();
  });

  it("reports the call delta and advances the watermark", async () => {
    meteredSubscriptionsMock.mockResolvedValue([
      { tenant_id: "t2", stripe_customer_id: "cus_2", usage_reported_through: "2026-07-18T00:00:00Z" },
    ]);
    usageCountBetweenMock.mockResolvedValue(1234);
    const res = await POST(req("cron-secret"));
    expect(res.status).toBe(200);
    expect(reportMeterEventMock).toHaveBeenCalledWith("cus_2", 1234, expect.stringMatching(/^t2-/));
    expect(advanceUsageWatermarkMock).toHaveBeenCalledWith("t2", expect.any(Date));
  });

  it("does not advance the watermark if reporting throws (so it retries)", async () => {
    meteredSubscriptionsMock.mockResolvedValue([
      { tenant_id: "t3", stripe_customer_id: "cus_3", usage_reported_through: "2026-07-18T00:00:00Z" },
    ]);
    usageCountBetweenMock.mockResolvedValue(500);
    reportMeterEventMock.mockRejectedValue(new Error("stripe down"));
    await POST(req("cron-secret"));
    expect(advanceUsageWatermarkMock).not.toHaveBeenCalled();
  });

  it("zero calls advances the watermark without a meter event", async () => {
    meteredSubscriptionsMock.mockResolvedValue([
      { tenant_id: "t4", stripe_customer_id: "cus_4", usage_reported_through: "2026-07-18T00:00:00Z" },
    ]);
    usageCountBetweenMock.mockResolvedValue(0);
    await POST(req("cron-secret"));
    expect(reportMeterEventMock).not.toHaveBeenCalled();
    expect(advanceUsageWatermarkMock).toHaveBeenCalledWith("t4", expect.any(Date));
  });
});
