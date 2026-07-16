import { NextResponse } from "next/server";
import { auth } from "@/auth";
import { pool } from "@/lib/db";
import { isPlatformAdminEmail } from "@/lib/auth-policy";

export interface AdminContext {
  userId: string;
  email: string;
  tenantId: string;
  isTenantAdmin: boolean;
  isPlatformAdmin: boolean;
}

export interface AdminAuthResult {
  error: NextResponse | null;
  session: { user: { id: string; email: string; tenant_id: string; is_admin: boolean } } | null;
  admin: AdminContext | null;
}

interface AdminRoleRow {
  name: string;
  tenant_id: string | null;
  tenant_slug: string | null;
}

function forbidden(message: string): AdminAuthResult {
  return {
    error: NextResponse.json({ error: message }, { status: 403 }),
    session: null,
    admin: null,
  };
}

function isTenantScopedAdminRole(role: AdminRoleRow, tenantId: string): boolean {
  const roleName = role.name.trim().toLowerCase();
  return (
    role.tenant_id === tenantId &&
    (roleName === "tenant_admin" || roleName === "admin")
  );
}

function isPlatformAdminRole(role: AdminRoleRow): boolean {
  const roleName = role.name.trim().toLowerCase();
  return (
    roleName === "platform_admin" &&
    (role.tenant_id === null || role.tenant_slug === "platform")
  );
}

/**
 * Resolve current admin capabilities from authoritative DB roles.
 *
 * Legacy session.user.is_admin is not accepted as authorization by itself.
 * Platform-wide operations require either PLATFORM_ADMIN_EMAILS or a global /
 * platform-tenant platform_admin role; tenant-scoped platform_admin rows are
 * ignored to prevent cross-tenant role grafts.
 */
export async function requireAdmin(): Promise<AdminAuthResult> {
  const session = await auth();
  if (!session?.user) {
    return {
      error: NextResponse.json({ error: "Unauthorized" }, { status: 401 }),
      session: null,
      admin: null,
    };
  }

  const typedSession = session as {
    user: { id: string; email: string; tenant_id: string; is_admin: boolean };
  };

  const roleResult = await pool.query<AdminRoleRow>(
    `SELECT r.name, r.tenant_id, t.slug AS tenant_slug
     FROM roles r
     JOIN user_roles ur ON ur.role_id = r.id
     LEFT JOIN tenants t ON t.id = r.tenant_id
     WHERE ur.user_id = $1`,
    [typedSession.user.id]
  );
  const hasTenantAdminRole = roleResult.rows.some((role) =>
    isTenantScopedAdminRole(role, typedSession.user.tenant_id)
  );
  const hasPlatformAdminRole = roleResult.rows.some((role) => isPlatformAdminRole(role));

  const isPlatformAdmin =
    isPlatformAdminEmail(typedSession.user.email) || hasPlatformAdminRole;
  const isTenantAdmin = hasTenantAdminRole;

  if (!isPlatformAdmin && !isTenantAdmin) {
    return forbidden("Forbidden: admin required");
  }

  return {
    error: null,
    session: typedSession,
    admin: {
      userId: typedSession.user.id,
      email: typedSession.user.email,
      tenantId: typedSession.user.tenant_id,
      isTenantAdmin,
      isPlatformAdmin,
    },
  };
}

export async function requirePlatformAdmin(): Promise<AdminAuthResult> {
  const result = await requireAdmin();
  if (result.error) return result;
  if (!result.admin?.isPlatformAdmin) {
    return forbidden("Forbidden: platform_admin required");
  }
  return result;
}

export function isAuthorizedForTenant(admin: AdminContext, tenantId: string | null | undefined): boolean {
  return admin.isPlatformAdmin || (!!tenantId && tenantId === admin.tenantId);
}

export function forbidCrossTenant(): NextResponse {
  return NextResponse.json({ error: "Forbidden: cross-tenant access denied" }, { status: 403 });
}
