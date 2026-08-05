// AWS Marketplace fulfillment for the SELF-HOST license model.
//
// AWS Marketplace sells a self-host license — the customer runs Sealfleet in their
// OWN infra (BYOF terraform); we run nothing for them on AWS. On subscribe we
// resolve the buyer (ResolveCustomer), record their contact details, confirm the
// entitlement (GetEntitlements / SNS), and NOTIFY the operator, who issues a
// signed license token OFFLINE (the private key never touches a server) and sends
// it to the buyer. aws_marketplace_customers is the operator's work queue.

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
  /** Subscription key: the LicenseArn for new agreements, CustomerIdentifier for legacy ones. */
  customerIdentifier: string;
  awsAccountId?: string;
  productCode?: string;
  licenseArn?: string;
}

// --- AWS API calls ---------------------------------------------------------

// Concurrent Agreements: a buyer may hold several simultaneous agreements for
// one product in a single AWS account, so the agreement's LicenseArn — not the
// customer — is the subscription identity. AWS requires this of every SaaS
// product created since 2026-06-01, and for those products ResolveCustomer no
// longer populates CustomerIdentifier or ProductCode at all. Requiring them
// would therefore fail every real purchase.
export async function resolveCustomer(token: string): Promise<ResolvedCustomer> {
  const client = new MarketplaceMeteringClient({ region: REGION });
  const r = await client.send(new ResolveCustomerCommand({ RegistrationToken: token }));
  const key = r.LicenseArn ?? r.CustomerIdentifier;
  if (!key) throw new Error("ResolveCustomer returned neither LicenseArn nor CustomerIdentifier");
  return {
    customerIdentifier: key,
    awsAccountId: r.CustomerAWSAccountId,
    productCode: r.ProductCode,
    licenseArn: r.LicenseArn,
  };
}

// Entitlement lookup for one agreement. Only the filter differs: new agreements
// are looked up by LICENSE_ARN, legacy ones by CUSTOMER_IDENTIFIER. ProductCode
// stays required on the request either way — unlike BatchMeterUsage, which
// rejects it alongside a LicenseArn.
export async function hasActiveEntitlement(subscriptionKey: string): Promise<boolean> {
  const pc = productCode();
  if (!pc) return false;
  const byLicense = subscriptionKey.startsWith("arn:");
  const client = new MarketplaceEntitlementServiceClient({ region: REGION });
  const r = await client.send(
    new GetEntitlementsCommand({
      ProductCode: pc,
      Filter: byLicense ? { LICENSE_ARN: [subscriptionKey] } : { CUSTOMER_IDENTIFIER: [subscriptionKey] },
    }),
  );
  const now = Date.now();
  return (r.Entitlements ?? []).some(
    (e) => !e.ExpirationDate || new Date(e.ExpirationDate).getTime() > now,
  );
}

// --- DB helpers ------------------------------------------------------------

export type AwsStatus = "pending" | "registered" | "active" | "unsubscribe_pending" | "unsubscribed";

export interface AwsCustomerRow {
  customer_identifier: string;
  product_code: string;
  aws_account_id: string | null;
  org_name: string | null;
  contact_name: string | null;
  contact_email: string | null;
  status: AwsStatus;
  license_issued: boolean;
}

export async function upsertPendingCustomer(c: ResolvedCustomer): Promise<void> {
  const pc = c.productCode ?? productCode();
  await pool.query(
    `INSERT INTO aws_marketplace_customers
       (customer_identifier, product_code, aws_account_id, license_arn, customer_aws_account_id, status)
     VALUES ($1, $2, $3, $4, $5, 'pending')
     ON CONFLICT (customer_identifier) DO UPDATE SET
       product_code = EXCLUDED.product_code,
       aws_account_id = EXCLUDED.aws_account_id,
       license_arn = EXCLUDED.license_arn,
       customer_aws_account_id = EXCLUDED.customer_aws_account_id,
       updated_at = NOW()`,
    [c.customerIdentifier, pc, c.awsAccountId ?? null, c.licenseArn ?? null, c.awsAccountId ?? null],
  );
}

// Buyer submitted their contact details at the fulfillment page. Records them and
// moves the row to 'registered' (awaiting entitlement confirmation + license issue).
export async function registerSelfHostContact(
  customerIdentifier: string,
  contact: { org: string; email: string; name?: string },
): Promise<void> {
  await pool.query(
    `UPDATE aws_marketplace_customers
       SET org_name = $2, contact_email = $3, contact_name = $4,
           status = CASE WHEN status = 'active' THEN 'active' ELSE 'registered' END,
           updated_at = NOW()
     WHERE customer_identifier = $1`,
    [customerIdentifier, contact.org, contact.email, contact.name ?? null],
  );
}

export async function setAwsStatus(customerIdentifier: string, status: AwsStatus): Promise<void> {
  await pool.query(
    `UPDATE aws_marketplace_customers SET status = $2, updated_at = NOW()
     WHERE customer_identifier = $1`,
    [customerIdentifier, status],
  );
}

export async function markOperatorNotified(customerIdentifier: string): Promise<void> {
  await pool.query(
    "UPDATE aws_marketplace_customers SET operator_notified_at = NOW(), updated_at = NOW() WHERE customer_identifier = $1",
    [customerIdentifier],
  );
}

export async function getAwsCustomer(customerIdentifier: string): Promise<AwsCustomerRow | null> {
  const { rows } = await pool.query<AwsCustomerRow>(
    `SELECT customer_identifier, product_code, aws_account_id, org_name, contact_name,
            contact_email, status, license_issued
       FROM aws_marketplace_customers WHERE customer_identifier = $1`,
    [customerIdentifier],
  );
  return rows[0] ?? null;
}

export function awsStatusIsEntitled(status: string | null | undefined): boolean {
  // Keep entitlement during the unsubscribe grace window; drop it only once gone.
  return status === "active" || status === "unsubscribe_pending";
}

// --- Operator notification -------------------------------------------------
// Self-host licenses are issued offline (private key is operator-held), so the
// fulfillment flow's job is to alert a human that a license must be issued or
// revoked. The DB row is the durable queue; this adds an optional push (a Slack /
// generic webhook, or logged if unset). Never throws — notification is best-effort.

export interface OperatorAlert {
  event: "license-requested" | "entitlement-confirmed" | "unsubscribe";
  customerIdentifier: string;
  awsAccountId?: string | null;
  org?: string | null;
  email?: string | null;
  note?: string;
}

export async function notifyOperator(alert: OperatorAlert): Promise<void> {
  const summary =
    `[Sealfleet AWS Marketplace] ${alert.event} — ` +
    `org=${alert.org ?? "?"} email=${alert.email ?? "?"} ` +
    `customer=${alert.customerIdentifier} aws_account=${alert.awsAccountId ?? "?"}` +
    (alert.note ? ` — ${alert.note}` : "");
  // Always log so it lands in the portal's logs even without a webhook.
  console.log(summary);
  const url = process.env.MARKETPLACE_ALERT_WEBHOOK;
  if (!url) return;
  try {
    await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: summary, ...alert }),
    });
  } catch (err) {
    console.warn("notifyOperator webhook failed:", err);
  }
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

type SupportedAwsPartition = "aws" | "aws-us-gov" | "aws-cn";

function snsDomainForPartition(partition: SupportedAwsPartition): string {
  return partition === "aws-cn" ? "amazonaws.com.cn" : "amazonaws.com";
}

function snsHostForTopic(topicArn: string): string | null {
  const parts = topicArn.split(":");
  const partition = parts[1] as SupportedAwsPartition | undefined;
  const service = parts[2];
  const region = parts[3];
  if (parts[0] !== "arn" || service !== "sns" || !region) return null;
  if (partition !== "aws" && partition !== "aws-us-gov" && partition !== "aws-cn") return null;
  return `sns.${region}.${snsDomainForPartition(partition)}`;
}

function isExpectedSnsUrl(url: string, topicArn: string): URL | null {
  try {
    const u = new URL(url);
    const expectedHost = snsHostForTopic(topicArn);
    if (!expectedHost) return null;
    if (u.protocol !== "https:" || u.username || u.password || u.port) return null;
    return u.hostname.toLowerCase() === expectedHost ? u : null;
  } catch {
    return null;
  }
}

function validCertUrl(url: string, topicArn: string): boolean {
  const u = isExpectedSnsUrl(url, topicArn);
  return Boolean(u && /^\/SimpleNotificationService-[A-Za-z0-9]+\.pem$/.test(u.pathname));
}

function validSubscribeUrl(url: string, topicArn: string): boolean {
  const u = isExpectedSnsUrl(url, topicArn);
  return Boolean(u && u.pathname === "/" && u.searchParams.get("Action") === "ConfirmSubscription");
}

export async function verifySnsSignature(msg: SnsMessage): Promise<boolean> {
  const fields = SIGN_FIELDS[msg.Type];
  if (!fields || !validCertUrl(msg.SigningCertURL, msg.TopicArn)) return false;
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
  if (msg.SubscribeURL && validCertUrl(msg.SigningCertURL, msg.TopicArn) && validSubscribeUrl(msg.SubscribeURL, msg.TopicArn)) {
    try { await fetch(msg.SubscribeURL); } catch { /* SNS retries */ }
  }
}
