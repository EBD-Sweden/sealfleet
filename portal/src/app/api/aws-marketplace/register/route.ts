// Completes AWS Marketplace self-host registration: reads the signed cookie from
// the fulfillment step, records the buyer's contact details, and notifies the
// operator to issue a self-host license token (issued OFFLINE with the operator's
// private key — never on this server). No hosted tenant is created. Public
// (authenticated by the signed cookie, not a session).

import { NextRequest, NextResponse } from "next/server";
import { jwtVerify } from "jose";
import {
  getAwsCustomer,
  registerSelfHostContact,
  hasActiveEntitlement,
  setAwsStatus,
  notifyOperator,
  markOperatorNotified,
} from "@/lib/aws-marketplace";

export const runtime = "nodejs";

function isEmail(s: string): boolean {
  return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(s);
}

export async function POST(req: NextRequest) {
  const cookie = req.cookies.get("aws_mp")?.value;
  if (!cookie) return NextResponse.json({ error: "No marketplace session; start from AWS Marketplace" }, { status: 400 });

  const jwtSecret = process.env.NEXTAUTH_SECRET;
  if (!jwtSecret) {
    console.error("AWS Marketplace session verification requires NEXTAUTH_SECRET");
    return NextResponse.json({ error: "AWS Marketplace is misconfigured" }, { status: 500 });
  }

  let cid: string;
  try {
    const secret = new TextEncoder().encode(jwtSecret);
    const { payload } = await jwtVerify(cookie, secret);
    cid = String(payload.cid);
  } catch {
    return NextResponse.json({ error: "Marketplace session expired; start again from AWS Marketplace" }, { status: 400 });
  }

  const existing = await getAwsCustomer(cid);
  if (!existing) return NextResponse.json({ error: "Unknown marketplace customer" }, { status: 400 });
  if (existing.license_issued) {
    return NextResponse.json({ error: "A license has already been issued for this subscription. Check your email or contact support." }, { status: 409 });
  }

  const body = await req.json().catch(() => ({}));
  const org = String(body.org ?? "").trim();
  const email = String(body.email ?? "").trim();
  const name = String(body.name ?? "").trim();
  if (!org) return NextResponse.json({ error: "Organization name is required" }, { status: 400 });
  if (!isEmail(email)) return NextResponse.json({ error: "A valid work email is required" }, { status: 400 });

  try {
    await registerSelfHostContact(cid, { org, email, name });
    // Confirm the entitlement now if AWS already reports it; otherwise the
    // subscribe-success SNS notification will confirm it shortly.
    const entitled = await hasActiveEntitlement(cid).catch(() => false);
    if (entitled) await setAwsStatus(cid, "active");
    await notifyOperator({
      event: "license-requested",
      customerIdentifier: cid,
      awsAccountId: existing.aws_account_id,
      org,
      email,
      note: entitled ? "entitlement confirmed — ready to issue license" : "awaiting subscribe-success confirmation",
    });
    await markOperatorNotified(cid);

    const res = NextResponse.json({
      ok: true,
      message:
        "Thanks — your self-host license is being prepared. We'll email your signed license key and the self-host setup guide to " +
        email + " shortly. It unlocks Enterprise features in your own Sealfleet deployment.",
    }, { status: 201 });
    res.cookies.delete("aws_mp");
    return res;
  } catch (err) {
    console.error("aws-marketplace register failed:", err);
    return NextResponse.json({ error: "Registration failed" }, { status: 500 });
  }
}
