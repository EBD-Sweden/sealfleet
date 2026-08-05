// AWS Marketplace subscription notifications (delivered via SNS). Public path,
// authenticated by SNS signature verification. In the self-host license model we
// track the subscription's lifecycle and alert the operator when a license should
// be issued (subscribe-success) or revoked / not renewed (unsubscribe). No hosted
// tenant keys are flipped — the customer runs Sealfleet in their own infra.

import { NextRequest, NextResponse } from "next/server";
import {
  verifySnsSignature,
  confirmSnsSubscription,
  setAwsStatus,
  getAwsCustomer,
  notifyOperator,
  type SnsMessage,
  type AwsStatus,
} from "@/lib/aws-marketplace";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const raw = await req.text();
  let msg: SnsMessage;
  try {
    msg = JSON.parse(raw);
  } catch {
    return NextResponse.json({ error: "Invalid body" }, { status: 400 });
  }

  if (!(await verifySnsSignature(msg))) {
    console.warn("SNS signature rejected");
    return NextResponse.json({ error: "Invalid signature" }, { status: 403 });
  }

  // Auto-confirm the SNS subscription on first hookup.
  if (msg.Type === "SubscriptionConfirmation") {
    await confirmSnsSubscription(msg);
    return NextResponse.json({ ok: true, confirmed: true });
  }

  if (msg.Type === "Notification") {
    let evt: { action?: string; "customer-identifier"?: string };
    try {
      evt = JSON.parse(msg.Message);
    } catch {
      return NextResponse.json({ ok: true }); // ignore non-JSON notifications
    }
    const cid = evt["customer-identifier"];
    const action = evt.action;
    if (cid && action) {
      const map: Record<string, AwsStatus | undefined> = {
        "subscribe-success": "active",
        "subscribe-fail": "unsubscribed",
        "unsubscribe-pending": "unsubscribe_pending",
        "unsubscribe-success": "unsubscribed",
      };
      const status = map[action];
      if (status) {
        await setAwsStatus(cid, status);
        // Alert the operator on the lifecycle events that need a human action:
        // issue a license (entitlement now confirmed) or revoke/stop renewal.
        if (action === "subscribe-success" || action === "unsubscribe-success") {
          const row = await getAwsCustomer(cid).catch(() => null);
          await notifyOperator({
            event: action === "subscribe-success" ? "entitlement-confirmed" : "unsubscribe",
            customerIdentifier: cid,
            awsAccountId: row?.aws_account_id,
            org: row?.org_name,
            email: row?.contact_email,
            note:
              action === "subscribe-success"
                ? row?.license_issued
                  ? "renewal confirmed — license already issued"
                  : "entitlement confirmed — issue the self-host license"
                : "subscription ended — revoke / do not renew the license",
          });
        }
      }
    }
  }

  return NextResponse.json({ ok: true });
}
