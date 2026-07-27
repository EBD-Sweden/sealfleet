import type { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  jsonMock,
  verifySnsSignatureMock,
  confirmSnsSubscriptionMock,
  setAwsStatusMock,
  setTenantKeysActiveMock,
} = vi.hoisted(() => ({
  jsonMock: vi.fn((body: unknown, init?: ResponseInit) => ({ body, status: init?.status ?? 200 })),
  verifySnsSignatureMock: vi.fn(),
  confirmSnsSubscriptionMock: vi.fn(),
  setAwsStatusMock: vi.fn(),
  setTenantKeysActiveMock: vi.fn(),
}));

vi.mock("next/server", () => ({ NextResponse: { json: jsonMock } }));
vi.mock("@/lib/aws-marketplace", () => ({
  verifySnsSignature: verifySnsSignatureMock,
  confirmSnsSubscription: confirmSnsSubscriptionMock,
  setAwsStatus: setAwsStatusMock,
}));
vi.mock("@/lib/billing", () => ({ setTenantKeysActive: setTenantKeysActiveMock }));

import { POST } from "@/app/api/aws-marketplace/notification/route";

function req(body: unknown): NextRequest {
  return { text: async () => (typeof body === "string" ? body : JSON.stringify(body)) } as NextRequest;
}
function notif(action: string, cid = "cust-1") {
  return { Type: "Notification", Message: JSON.stringify({ action, "customer-identifier": cid }) };
}

beforeEach(() => {
  vi.clearAllMocks();
  verifySnsSignatureMock.mockResolvedValue(true);
  setAwsStatusMock.mockResolvedValue("tenant-1");
});

describe("aws-marketplace notification route", () => {
  it("rejects an invalid SNS signature (403)", async () => {
    verifySnsSignatureMock.mockResolvedValue(false);
    const res = await POST(req(notif("subscribe-success")));
    expect(res.status).toBe(403);
    expect(setTenantKeysActiveMock).not.toHaveBeenCalled();
  });

  it("confirms an SNS subscription", async () => {
    const res = await POST(req({ Type: "SubscriptionConfirmation", SubscribeURL: "https://sns.x" }));
    expect(confirmSnsSubscriptionMock).toHaveBeenCalled();
    expect(res.status).toBe(200);
  });

  it("subscribe-success activates the tenant's keys", async () => {
    await POST(req(notif("subscribe-success")));
    expect(setAwsStatusMock).toHaveBeenCalledWith("cust-1", "active");
    expect(setTenantKeysActiveMock).toHaveBeenCalledWith("tenant-1", true);
  });

  it("unsubscribe-success deactivates the tenant's keys", async () => {
    await POST(req(notif("unsubscribe-success")));
    expect(setAwsStatusMock).toHaveBeenCalledWith("cust-1", "unsubscribed");
    expect(setTenantKeysActiveMock).toHaveBeenCalledWith("tenant-1", false);
  });

  it("unsubscribe-pending keeps access during the grace window", async () => {
    await POST(req(notif("unsubscribe-pending")));
    expect(setTenantKeysActiveMock).toHaveBeenCalledWith("tenant-1", true);
  });

  it("ignores unknown actions without touching keys", async () => {
    await POST(req(notif("something-else")));
    expect(setTenantKeysActiveMock).not.toHaveBeenCalled();
  });
});
