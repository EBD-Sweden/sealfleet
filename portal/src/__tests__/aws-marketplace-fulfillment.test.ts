import type { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  jsonMock,
  redirectMock,
  resolveCustomerMock,
  upsertPendingCustomerMock,
  awsMarketplaceConfiguredMock,
  signMock,
  signJwtMock,
} = vi.hoisted(() => {
  const sign = vi.fn();
  return {
    jsonMock: vi.fn((body: unknown, init?: ResponseInit) => ({ body, status: init?.status ?? 200 })),
    redirectMock: vi.fn((url: string, status?: number) => ({
      url,
      status: status ?? 307,
      cookies: { set: vi.fn() },
    })),
    resolveCustomerMock: vi.fn(),
    upsertPendingCustomerMock: vi.fn(),
    awsMarketplaceConfiguredMock: vi.fn(),
    signMock: sign,
    signJwtMock: vi.fn(function SignJWT() {
      return {
        setProtectedHeader: vi.fn().mockReturnThis(),
        setExpirationTime: vi.fn().mockReturnThis(),
        sign,
      };
    }),
  };
});

vi.mock("next/server", () => ({ NextResponse: { json: jsonMock, redirect: redirectMock } }));
vi.mock("jose", () => ({
  SignJWT: signJwtMock,
}));
vi.mock("@/lib/aws-marketplace", () => ({
  resolveCustomer: resolveCustomerMock,
  upsertPendingCustomer: upsertPendingCustomerMock,
  awsMarketplaceConfigured: awsMarketplaceConfiguredMock,
}));

import { POST } from "@/app/api/aws-marketplace/route";

function req(token = "token"): NextRequest {
  return {
    nextUrl: { origin: "https://portal.example.test" },
    formData: async () => new Map([["x-amzn-marketplace-token", token]]),
  } as unknown as NextRequest;
}

beforeEach(() => {
  vi.clearAllMocks();
  process.env.NEXTAUTH_SECRET = "test-secret";
  awsMarketplaceConfiguredMock.mockReturnValue(true);
  resolveCustomerMock.mockResolvedValue({ customerIdentifier: "cust-1", productCode: "prod-1" });
  upsertPendingCustomerMock.mockResolvedValue(undefined);
  signMock.mockResolvedValue("signed-cookie");
});

describe("aws-marketplace fulfillment route", () => {
  it("fails closed when marketplace is configured but NEXTAUTH_SECRET is missing", async () => {
    delete process.env.NEXTAUTH_SECRET;

    const res = await POST(req());

    expect(res.status).toBe(500);
    expect(resolveCustomerMock).not.toHaveBeenCalled();
    expect(signMock).not.toHaveBeenCalled();
  });

  it("mints the marketplace session cookie when NEXTAUTH_SECRET is configured", async () => {
    const res = await POST(req());

    expect(res.status).toBe(303);
    expect(resolveCustomerMock).toHaveBeenCalledWith("token");
    expect(res.cookies.set).toHaveBeenCalledWith(
      "aws_mp",
      "signed-cookie",
      expect.objectContaining({ httpOnly: true, secure: true, sameSite: "lax" }),
    );
  });
});
