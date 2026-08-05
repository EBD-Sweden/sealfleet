import { beforeEach, describe, expect, it, vi } from "vitest";

const { sendMock, resolveCustomerCommandMock, getEntitlementsCommandMock, poolQueryMock } = vi.hoisted(() => ({
  sendMock: vi.fn(),
  resolveCustomerCommandMock: vi.fn(function ResolveCustomerCommand(input) {
    return { input };
  }),
  getEntitlementsCommandMock: vi.fn(function GetEntitlementsCommand(input) {
    return { input };
  }),
  poolQueryMock: vi.fn(),
}));

vi.mock("@aws-sdk/client-marketplace-metering", () => ({
  MarketplaceMeteringClient: vi.fn(function MarketplaceMeteringClient() {
    return { send: sendMock };
  }),
  ResolveCustomerCommand: resolveCustomerCommandMock,
}));
vi.mock("@aws-sdk/client-marketplace-entitlement-service", () => ({
  MarketplaceEntitlementServiceClient: vi.fn(function MarketplaceEntitlementServiceClient() {
    return { send: sendMock };
  }),
  GetEntitlementsCommand: getEntitlementsCommandMock,
}));
vi.mock("@/lib/db", () => ({
  pool: { query: poolQueryMock },
}));

import { hasActiveEntitlement, resolveCustomer, upsertPendingCustomer } from "@/lib/aws-marketplace";

describe("AWS Marketplace Concurrent Agreements", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    process.env.SEALFLEET_AWS_MARKETPLACE_PRODUCT_CODE = "prod-configured";
  });

  it("uses LicenseArn as the subscription key when ResolveCustomer omits legacy identifiers", async () => {
    sendMock.mockResolvedValue({
      LicenseArn: "arn:aws:license-manager:us-east-1:111122223333:license:l-abc",
      CustomerAWSAccountId: "111122223333",
    });

    const customer = await resolveCustomer("registration-token");

    expect(resolveCustomerCommandMock).toHaveBeenCalledWith({ RegistrationToken: "registration-token" });
    expect(customer).toEqual({
      customerIdentifier: "arn:aws:license-manager:us-east-1:111122223333:license:l-abc",
      awsAccountId: "111122223333",
      productCode: undefined,
      licenseArn: "arn:aws:license-manager:us-east-1:111122223333:license:l-abc",
    });
  });

  it("persists the LicenseArn agreement identity and falls back to configured product code", async () => {
    poolQueryMock.mockResolvedValue({ rowCount: 1 });

    await upsertPendingCustomer({
      customerIdentifier: "arn:aws:license-manager:us-east-1:111122223333:license:l-abc",
      awsAccountId: "111122223333",
      licenseArn: "arn:aws:license-manager:us-east-1:111122223333:license:l-abc",
    });

    expect(poolQueryMock).toHaveBeenCalledWith(
      expect.stringContaining("license_arn"),
      [
        "arn:aws:license-manager:us-east-1:111122223333:license:l-abc",
        "prod-configured",
        "111122223333",
        "arn:aws:license-manager:us-east-1:111122223333:license:l-abc",
        "111122223333",
      ],
    );
  });

  it("filters entitlement checks by LICENSE_ARN for new agreement identities", async () => {
    sendMock.mockResolvedValue({ Entitlements: [{ ExpirationDate: new Date(Date.now() + 60_000) }] });

    await expect(hasActiveEntitlement("arn:aws:license-manager:us-east-1:111122223333:license:l-abc")).resolves.toBe(true);

    expect(getEntitlementsCommandMock).toHaveBeenCalledWith({
      ProductCode: "prod-configured",
      Filter: { LICENSE_ARN: ["arn:aws:license-manager:us-east-1:111122223333:license:l-abc"] },
    });
  });
});
