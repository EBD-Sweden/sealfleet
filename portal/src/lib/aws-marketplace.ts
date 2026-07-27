// AWS Marketplace SaaS fulfillment: resolve the buyer (ResolveCustomer), confirm
// entitlement (GetEntitlements), map the AWS customer to a Sealfleet tenant, and
// track subscription status from SNS notifications. Access enforcement reuses the
// billing model: the tenant's API keys' is_active follows the subscription.

import {
  MarketplaceMeteringClient,
  ResolveCustomerCommand,
} from "@aws-sdk/client-marketplace-metering";
import {
  MarketplaceEntitlementServiceClient,
  GetEntitlementsCommand,
} from "@aws-sdk/client-marketplace-entitlement-service";
import crypto from "crypto";
import { pool } from "@/lib/db";

const REGION = process.env.AWS_MARKETPLACE_REGION || "us-east-1";

export function productCode(): string {
  return process.env.SEALFLEET_AWS_MARKETPLACE_PRODUCT_CODE || "";
}
export function awsMarketplaceConfigured(): boolean {
  // The SDK finds creds from AWS_ACCESS_KEY_ID/SECRET or an instance role.
  return Boolean(productCode() && (process.env.AWS_ACCESS_KEY_ID || process.env.AWS_ROLE_ARN));
}

export interface ResolvedCustomer {
  customerIdentifier: string;
  awsAccountId?: string;
  productCode: string;
}

// --- AWS API calls ---------------------------------------------------------

export async function resolveCustomer(token: string): Promise<ResolvedCustomer> {
  const client = new MarketplaceMeteringClient({ region: REGION });
  const r = await client.send(new ResolveCustomerCommand({ RegistrationToken: token }));
  if (!r.CustomerIdentifier || !r.ProductCode) throw new Error("ResolveCustomer returned no customer");
  return { customerIdentifier: r.CustomerIdentifier, awsAccountId: r.CustomerAWSAccountId, productCode: r.ProductCode };
}

export async function hasActiveEntitlement(customerIdentifier: string): Promise<boolean> {
  const pc = productCode();
  if (!pc) return false;
  const client = new MarketplaceEntitlementServiceClient({ region: REGION });
  const r = await client.send(
    new GetEntitlementsCommand({ ProductCode: pc, Filter: { CUSTOMER_IDENTIFIER: [customerIdentifier] } }),
  );
  const now = Date.now();
  return (r.Entitlements ?? []).some(
    (e) => !e.ExpirationDate || new Date(e.ExpirationDate).getTime() > now,
  );
}

// --- DB helpers ------------------------------------------------------------

export type AwsStatus = "pending" | "active" | "unsubscribe_pending" | "unsubscribed";

export async function upsertPendingCustomer(c: ResolvedCustomer): Promise<void> {
  await pool.query(
    `INSERT INTO aws_marketplace_customers (customer_identifier, product_code, aws_account_id, status)
     VALUES ($1, $2, $3, 'pending')
     ON CONFLICT (customer_identifier) DO UPDATE SET
       product_code = EXCLUDED.product_code, aws_account_id = EXCLUDED.aws_account_id, updated_at = NOW()`,
    [c.customerIdentifier, c.productCode, c.awsAccountId ?? null],
  );
}

export async function linkCustomerToTenant(customerIdentifier: string, tenantId: string, status: AwsStatus): Promise<void> {
  await pool.query(
    `UPDATE aws_marketplace_customers SET tenant_id = $2, status = $3, updated_at = NOW()
     WHERE customer_identifier = $1`,
    [customerIdentifier, tenantId, status],
  );
}

export async function setAwsStatus(customerIdentifier: string, status: AwsStatus): Promise<string | null> {
  const { rows } = await pool.query<{ tenant_id: string | null }>(
    `UPDATE aws_marketplace_customers SET status = $2, updated_at = NOW()
     WHERE customer_identifier = $1 RETURNING tenant_id`,
    [customerIdentifier, status],
  );
  return rows[0]?.tenant_id ?? null;
}

export async function getAwsCustomer(customerIdentifier: string):
  Promise<{ tenant_id: string | null; status: AwsStatus } | null> {
  const { rows } = await pool.query<{ tenant_id: string | null; status: AwsStatus }>(
    "SELECT tenant_id, status FROM aws_marketplace_customers WHERE customer_identifier = $1",
    [customerIdentifier],
  );
  return rows[0] ?? null;
}

export function awsStatusIsEntitled(status: string | null | undefined): boolean {
  // Keep access during the unsubscribe grace window; cut it only once gone.
  return status === "active" || status === "unsubscribe_pending";
}

// --- SNS message verification (public endpoint) ----------------------------
// SNS posts signed JSON. We verify the RSA signature against the Amazon signing
// cert before acting on anything.

export interface SnsMessage {
  Type: string; MessageId: string; TopicArn: string; Message: string;
  Timestamp: string; SignatureVersion: string; Signature: string;
  SigningCertURL: string; Subject?: string; SubscribeURL?: string; Token?: string;
}

const SIGN_FIELDS: Record<string, string[]> = {
  Notification: ["Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type"],
  SubscriptionConfirmation: ["Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type"],
  UnsubscribeConfirmation: ["Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type"],
};

function validCertUrl(url: string): boolean {
  try {
    const u = new URL(url);
    return u.protocol === "https:" && /(^|\.)amazonaws\.com$/.test(u.hostname) && /sns/i.test(u.hostname);
  } catch {
    return false;
  }
}

export async function verifySnsSignature(msg: SnsMessage): Promise<boolean> {
  const fields = SIGN_FIELDS[msg.Type];
  if (!fields || !validCertUrl(msg.SigningCertURL)) return false;
  // Canonical string: "key\nvalue\n" for each present field, in the fixed order.
  let canonical = "";
  for (const f of fields) {
    const v = (msg as unknown as Record<string, string>)[f];
    if (v === undefined || v === null) continue;
    canonical += `${f}\n${v}\n`;
  }
  const certRes = await fetch(msg.SigningCertURL);
  if (!certRes.ok) return false;
  const pem = await certRes.text();
  const algo = msg.SignatureVersion === "2" ? "RSA-SHA256" : "RSA-SHA1";
  try {
    const v = crypto.createVerify(algo);
    v.update(canonical, "utf8");
    return v.verify(pem, msg.Signature, "base64");
  } catch {
    return false;
  }
}

// SubscriptionConfirmation: visit SubscribeURL to confirm the endpoint.
export async function confirmSnsSubscription(msg: SnsMessage): Promise<void> {
  if (msg.SubscribeURL && validCertUrl(msg.SigningCertURL)) {
    try { await fetch(msg.SubscribeURL); } catch { /* SNS retries */ }
  }
}
