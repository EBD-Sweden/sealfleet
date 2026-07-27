// AWS Marketplace subscription notifications (delivered via SNS). Public path,
// authenticated by SNS signature verification. Flips the tenant's API keys
// active/inactive as the subscription changes (same enforcement as Stripe billing).

import { NextRequest, NextResponse } from "next/server";
import {
  verifySnsSignature,
  confirmSnsSubscription,
  setAwsStatus,
  type SnsMessage,
  type AwsStatus,
} from "@/lib/aws-marketplace";
import { setTenantKeysActive } from "@/lib/billing";

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
      // Map the AWS action to our status + key activation.
      const map: Record<string, { status: AwsStatus; keysActive: boolean } | undefined> = {
        "subscribe-success": { status: "active", keysActive: true },
        "subscribe-fail": { status: "unsubscribed", keysActive: false },
        "unsubscribe-pending": { status: "unsubscribe_pending", keysActive: true },
        "unsubscribe-success": { status: "unsubscribed", keysActive: false },
      };
      const m = map[action];
      if (m) {
        const tenantId = await setAwsStatus(cid, m.status);
        if (tenantId) await setTenantKeysActive(tenantId, m.keysActive);
      }
    }
  }

  return NextResponse.json({ ok: true });
}
