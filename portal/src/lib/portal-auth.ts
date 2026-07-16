import { NextResponse } from "next/server";
import { auth } from "@/auth";

export interface PortalSessionUser {
  id: string;
  email?: string;
  tenant_id?: string;
  is_admin?: boolean;
  groups?: string[];
}

export interface PortalAuthContext {
  user: PortalSessionUser;
  backendHeaders: Record<string, string>;
}

export interface PortalAuthResult {
  error: NextResponse | null;
  context: PortalAuthContext | null;
}

export function unauthorized(): NextResponse {
  return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
}

export async function requirePortalSession(): Promise<PortalAuthResult> {
  const session = await auth();
  if (!session?.user) {
    return { error: unauthorized(), context: null };
  }

  const user = session.user as PortalSessionUser;
  if (!user.id) {
    return { error: unauthorized(), context: null };
  }

  const backendHeaders: Record<string, string> = {
    "X-Sealfleet-User-Id": user.id,
    "X-Sealfleet-Tenant-Id": user.tenant_id ?? "default",
  };
  if (Array.isArray(user.groups) && user.groups.length > 0) {
    // Forward IdP group claims so the router applies group->role mappings
    // at request time (trusted only for delegation-enabled API keys).
    backendHeaders["X-Sealfleet-Groups"] = user.groups.join(",");
  }

  const scopedServerKey = process.env.RUNTIME_API_KEY || process.env.MCPFINDER_BACKEND_API_KEY;
  if (scopedServerKey) {
    backendHeaders["X-API-Key"] = scopedServerKey;
  }

  return { error: null, context: { user, backendHeaders } };
}
