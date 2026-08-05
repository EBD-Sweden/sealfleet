// AWS Marketplace fulfillment landing (self-host license). After a buyer
// subscribes, AWS POSTs here (application/x-www-form-urlencoded) with
// x-amzn-marketplace-token. We resolve the customer, stash a short-lived signed
// cookie, and send them to the page where they leave contact details so we can
// deliver their self-host license key. Public path.

import { NextRequest, NextResponse } from "next/server";
import { SignJWT } from "jose";
import { resolveCustomer, upsertPendingCustomer, awsMarketplaceConfigured } from "@/lib/aws-marketplace";

export const runtime = "nodejs";

function origin(req: NextRequest): string {
  return process.env.PORTAL_PUBLIC_URL || process.env.NEXTAUTH_URL || process.env.AUTH_URL || req.nextUrl.origin;
}

export async function POST(req: NextRequest) {
  if (!awsMarketplaceConfigured()) {
    return NextResponse.json({ error: "AWS Marketplace is not configured" }, { status: 503 });
  }
  const jwtSecret = process.env.NEXTAUTH_SECRET;
  if (!jwtSecret) {
    console.error("AWS Marketplace session signing requires NEXTAUTH_SECRET");
    return NextResponse.json({ error: "AWS Marketplace is misconfigured" }, { status: 500 });
  }

  const form = await req.formData().catch(() => null);
  const token = form?.get("x-amzn-marketplace-token")?.toString();
  if (!token) return NextResponse.json({ error: "Missing x-amzn-marketplace-token" }, { status: 400 });

  let customer;
  try {
    customer = await resolveCustomer(token);
  } catch (err) {
    console.error("ResolveCustomer failed:", err);
    return NextResponse.redirect(`${origin(req)}/aws-marketplace?error=resolve`, 303);
  }
  await upsertPendingCustomer(customer);

  // Carry the (verified) customer id to the registration page in a signed cookie.
  const secret = new TextEncoder().encode(jwtSecret);
  const jwt = await new SignJWT({ cid: customer.customerIdentifier })
    .setProtectedHeader({ alg: "HS256" })
    .setExpirationTime("30m")
    .sign(secret);

  const res = NextResponse.redirect(`${origin(req)}/aws-marketplace`, 303);
  res.cookies.set("aws_mp", jwt, { httpOnly: true, secure: true, sameSite: "lax", maxAge: 1800, path: "/" });
  return res;
}
