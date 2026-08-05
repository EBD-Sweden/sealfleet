import type { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  jsonMock,
  jwtVerifyMock,
  getAwsCustomerMock,
  registerSelfHostContactMock,
  hasActiveEntitlementMock,
  setAwsStatusMock,
  notifyOperatorMock,
  markOperatorNotifiedMock,
} = vi.hoisted(() => ({
  jsonMock: vi.fn((body: unknown, init?: ResponseInit) => ({
    body,
    status: init?.status ?? 200,
    cookies: { delete: vi.fn() },
  })),
  jwtVerifyMock: vi.fn(),
  getAwsCustomerMock: vi.fn(),
  registerSelfHostContactMock: vi.fn(),
  hasActiveEntitlementMock: vi.fn(),
  setAwsStatusMock: vi.fn(),
  notifyOperatorMock: vi.fn(),
  markOperatorNotifiedMock: vi.fn(),
}));

vi.mock("next/server", () => ({ NextResponse: { json: jsonMock } }));
vi.mock("jose", () => ({ jwtVerify: jwtVerifyMock }));
vi.mock("@/lib/aws-marketplace", () => ({
  getAwsCustomer: getAwsCustomerMock,
  registerSelfHostContact: registerSelfHostContactMock,
  hasActiveEntitlement: hasActiveEntitlementMock,
  setAwsStatus: setAwsStatusMock,
  notifyOperator: notifyOperatorMock,
  markOperatorNotified: markOperatorNotifiedMock,
}));

import { POST } from "@/app/api/aws-marketplace/register/route";

function req(body: unknown, cookie = "mp-cookie"): NextRequest {
  return {
    cookies: { get: (name: string) => (name === "aws_mp" ? { value: cookie } : undefined) },
    json: async () => body,
  } as unknown as NextRequest;
}

beforeEach(() => {
  vi.clearAllMocks();
  process.env.NEXTAUTH_SECRET = "test-secret";
  jwtVerifyMock.mockResolvedValue({ payload: { cid: "cust-1" } });
  getAwsCustomerMock.mockResolvedValue({
    customer_identifier: "cust-1",
    aws_account_id: "1234",
    org_name: null,
    contact_name: null,
    contact_email: null,
    status: "pending",
    license_issued: false,
  });
  registerSelfHostContactMock.mockResolvedValue(undefined);
  hasActiveEntitlementMock.mockResolvedValue(false);
  setAwsStatusMock.mockResolvedValue(undefined);
  notifyOperatorMock.mockResolvedValue(undefined);
  markOperatorNotifiedMock.mockResolvedValue(undefined);
});

describe("aws-marketplace register route (self-host license)", () => {
  it("fails closed when NEXTAUTH_SECRET is missing instead of accepting dev-secret cookies", async () => {
    delete process.env.NEXTAUTH_SECRET;

    const res = await POST(req({ org: "Acme", email: "ops@acme.com" }));

    expect(res.status).toBe(500);
    expect(jwtVerifyMock).not.toHaveBeenCalled();
    expect(registerSelfHostContactMock).not.toHaveBeenCalled();
  });

  it("marks the customer active when registration sees an already-confirmed entitlement", async () => {
    hasActiveEntitlementMock.mockResolvedValue(true);

    const res = await POST(req({ org: "Acme", name: "Jane", email: "jane@acme.com" }));

    expect(res.status).toBe(201);
    expect(registerSelfHostContactMock).toHaveBeenCalledWith("cust-1", {
      org: "Acme",
      name: "Jane",
      email: "jane@acme.com",
    });
    expect(setAwsStatusMock).toHaveBeenCalledWith("cust-1", "active");
    expect(notifyOperatorMock).toHaveBeenCalledWith(
      expect.objectContaining({
        event: "license-requested",
        customerIdentifier: "cust-1",
        org: "Acme",
        email: "jane@acme.com",
        note: "entitlement confirmed — ready to issue license",
      }),
    );
  });

  it("does not mark active while AWS entitlement confirmation is still pending", async () => {
    const res = await POST(req({ org: "Acme", email: "ops@acme.com" }));

    expect(res.status).toBe(201);
    expect(setAwsStatusMock).not.toHaveBeenCalled();
    expect(notifyOperatorMock).toHaveBeenCalledWith(
      expect.objectContaining({ note: "awaiting subscribe-success confirmation" }),
    );
  });
});
