import crypto from "crypto";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SnsMessage } from "@/lib/aws-marketplace";

vi.mock("@aws-sdk/client-marketplace-metering", () => ({
  MarketplaceMeteringClient: vi.fn(),
  ResolveCustomerCommand: vi.fn(),
}));
vi.mock("@aws-sdk/client-marketplace-entitlement-service", () => ({
  MarketplaceEntitlementServiceClient: vi.fn(),
  GetEntitlementsCommand: vi.fn(),
}));
vi.mock("@/lib/db", () => ({
  pool: { query: vi.fn() },
}));

import { confirmSnsSubscription, verifySnsSignature } from "@/lib/aws-marketplace";

const SIGN_FIELDS: Record<string, string[]> = {
  Notification: ["Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type"],
  SubscriptionConfirmation: ["Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type"],
};

function canonicalMessage(msg: SnsMessage): string {
  return SIGN_FIELDS[msg.Type]
    .filter((f) => (msg as unknown as Record<string, string>)[f] !== undefined)
    .map((f) => `${f}\n${(msg as unknown as Record<string, string>)[f]}\n`)
    .join("");
}

function signedNotification(signingCertUrl: string): { msg: SnsMessage; pem: string } {
  const { publicKey, privateKey } = crypto.generateKeyPairSync("rsa", { modulusLength: 2048 });
  const msg: SnsMessage = {
    Type: "Notification",
    Message: "{}",
    MessageId: "m1",
    Timestamp: "2026-07-27T00:00:00Z",
    TopicArn: "arn:aws:sns:us-east-1:123456789012:marketplace-topic",
    SigningCertURL: signingCertUrl,
    SignatureVersion: "2",
    Signature: "",
  };
  const signer = crypto.createSign("RSA-SHA256");
  signer.update(canonicalMessage(msg), "utf8");
  msg.Signature = signer.sign(privateKey, "base64");
  return { msg, pem: publicKey.export({ type: "spki", format: "pem" }).toString() };
}

beforeEach(() => {
  vi.clearAllMocks();
  global.fetch = vi.fn() as unknown as typeof fetch;
});

describe("AWS Marketplace SNS signature validation", () => {
  it("rejects attacker-controlled AWS hosts that merely contain sns", async () => {
    const { msg, pem } = signedNotification("https://attacker-sns.s3.amazonaws.com/pubkey.pem");
    vi.mocked(global.fetch).mockResolvedValue({ ok: true, text: async () => pem } as Response);

    await expect(verifySnsSignature(msg)).resolves.toBe(false);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("accepts signed SNS notifications from the documented regional SNS certificate host and path", async () => {
    const { msg, pem } = signedNotification("https://sns.us-east-1.amazonaws.com/SimpleNotificationService-0123456789abcdef0123456789abcdef.pem");
    vi.mocked(global.fetch).mockResolvedValue({ ok: true, text: async () => pem } as Response);

    await expect(verifySnsSignature(msg)).resolves.toBe(true);
    expect(global.fetch).toHaveBeenCalledWith(msg.SigningCertURL);
  });

  it("does not confirm subscriptions through non-SNS SubscribeURL hosts", async () => {
    const msg = {
      Type: "SubscriptionConfirmation",
      Message: "{}",
      MessageId: "m1",
      Timestamp: "2026-07-27T00:00:00Z",
      TopicArn: "arn:aws:sns:us-east-1:123456789012:marketplace-topic",
      SigningCertURL: "https://sns.us-east-1.amazonaws.com/SimpleNotificationService-0123456789abcdef0123456789abcdef.pem",
      SignatureVersion: "2",
      Signature: "sig",
      SubscribeURL: "https://attacker-sns.s3.amazonaws.com/confirm",
      Token: "tok",
    } satisfies SnsMessage;

    await confirmSnsSubscription(msg);

    expect(global.fetch).not.toHaveBeenCalled();
  });
});
