import type { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  jsonMock,
  verifySnsSignatureMock,
  confirmSnsSubscriptionMock,
  setAwsStatusMock,
  getAwsCustomerMock,
  notifyOperatorMock,
} = vi.hoisted(() => ({
  jsonMock: vi.fn((body: unknown, init?: ResponseInit) => ({ body, status: init?.status ?? 200 })),
  verifySnsSignatureMock: vi.fn(),
  confirmSnsSubscriptionMock: vi.fn(),
  setAwsStatusMock: vi.fn(),
  getAwsCustomerMock: vi.fn(),
  notifyOperatorMock: vi.fn(),
}));

vi.mock("next/server", () => ({ NextResponse: { json: jsonMock } }));
vi.mock("@/lib/aws-marketplace", () => ({
  verifySnsSignature: verifySnsSignatureMock,
  confirmSnsSubscription: confirmSnsSubscriptionMock,
  setAwsStatus: setAwsStatusMock,
  getAwsCustomer: getAwsCustomerMock,
  notifyOperator: notifyOperatorMock,
}));

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
  setAwsStatusMock.mockResolvedValue(undefined);
  getAwsCustomerMock.mockResolvedValue({
    customer_identifier: "cust-1",
    aws_account_id: "1234",
    org_name: "Acme",
    contact_email: "ops@acme.com",
    license_issued: false,
  });
});

describe("aws-marketplace notification route (self-host license)", () => {
  it("rejects an invalid SNS signature (403)", async () => {
    verifySnsSignatureMock.mockResolvedValue(false);
    const res = await POST(req(notif("subscribe-success")));
    expect(res.status).toBe(403);
    expect(setAwsStatusMock).not.toHaveBeenCalled();
    expect(notifyOperatorMock).not.toHaveBeenCalled();
  });

  it("confirms an SNS subscription", async () => {
    const res = await POST(req({ Type: "SubscriptionConfirmation", SubscribeURL: "https://sns.x" }));
    expect(confirmSnsSubscriptionMock).toHaveBeenCalled();
    expect(res.status).toBe(200);
  });

  it("subscribe-success marks active and asks the operator to issue a license", async () => {
    await POST(req(notif("subscribe-success")));
    expect(setAwsStatusMock).toHaveBeenCalledWith("cust-1", "active");
    expect(notifyOperatorMock).toHaveBeenCalledWith(
      expect.objectContaining({ event: "entitlement-confirmed", customerIdentifier: "cust-1" }),
    );
  });

  it("unsubscribe-success marks unsubscribed and asks the operator to revoke", async () => {
    await POST(req(notif("unsubscribe-success")));
    expect(setAwsStatusMock).toHaveBeenCalledWith("cust-1", "unsubscribed");
    expect(notifyOperatorMock).toHaveBeenCalledWith(
      expect.objectContaining({ event: "unsubscribe", customerIdentifier: "cust-1" }),
    );
  });

  it("unsubscribe-pending sets the grace status without alerting the operator", async () => {
    await POST(req(notif("unsubscribe-pending")));
    expect(setAwsStatusMock).toHaveBeenCalledWith("cust-1", "unsubscribe_pending");
    expect(notifyOperatorMock).not.toHaveBeenCalled();
  });

  it("ignores unknown actions", async () => {
    await POST(req(notif("something-else")));
    expect(setAwsStatusMock).not.toHaveBeenCalled();
    expect(notifyOperatorMock).not.toHaveBeenCalled();
  });
});
