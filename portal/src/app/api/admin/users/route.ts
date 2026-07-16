import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { requireAdmin, forbidCrossTenant, isAuthorizedForTenant, type AdminContext } from "@/lib/admin-auth";
import bcrypt from "bcryptjs";
import crypto from "crypto";

function normalizeRoleName(name: string): string {
  return name.trim().toLowerCase();
}

function isPlatformAdminRole(role: { name: string; tenant_id: string | null; tenant_slug?: string | null }): boolean {
  return normalizeRoleName(role.name) === "platform_admin" && (role.tenant_id === null || role.tenant_slug === "platform");
}

async function validateTenantScopedRoles(
  roleIds: string[] | undefined,
  tenantId: string,
  admin: AdminContext
): Promise<NextResponse | null> {
  if (!roleIds?.length) return null;
  const result = await pool.query<{ id: string; tenant_id: string | null; tenant_slug: string | null; name: string }>(
    `SELECT r.id, r.tenant_id, t.slug AS tenant_slug, r.name
     FROM roles r
     LEFT JOIN tenants t ON t.id = r.tenant_id
     WHERE r.id = ANY($1)`,
    [roleIds]
  );
  if (result.rows.length !== roleIds.length) {
    return NextResponse.json({ error: "One or more roles were not found" }, { status: 400 });
  }
  if (result.rows.some((role) => role.tenant_id !== tenantId && !(admin.isPlatformAdmin && isPlatformAdminRole(role)))) {
    return forbidCrossTenant();
  }
  if (!admin.isPlatformAdmin && result.rows.some((role) => isPlatformAdminRole(role) || normalizeRoleName(role.name) === "platform_admin")) {
    return NextResponse.json({ error: "Forbidden: platform_admin cannot be assigned by tenant_admin" }, { status: 403 });
  }
  return null;
}

export async function GET() {
  const { error, admin } = await requireAdmin();
  if (error) return error;

  const userWhere = admin!.isPlatformAdmin ? "" : "WHERE u.tenant_id = $1";
  const params = admin!.isPlatformAdmin ? [] : [admin!.tenantId];
  const result = await pool.query(
    `SELECT u.id, u.tenant_id, u.email, u.name, u.auth_provider, u.is_active, u.is_admin,
     u.last_login_at, u.created_at, t.name as tenant_name
     FROM users u
     LEFT JOIN tenants t ON t.id = u.tenant_id
     ${userWhere}
     ORDER BY u.created_at DESC`,
    params
  );

  const rolesResult = await pool.query(
    `SELECT ur.user_id, r.id, r.name
     FROM user_roles ur
     JOIN roles r ON r.id = ur.role_id
     JOIN users u ON u.id = ur.user_id
     ${userWhere}`,
    params
  );

  const rolesByUser = new Map<string, { id: string; name: string }[]>();
  for (const row of rolesResult.rows) {
    const existing = rolesByUser.get(row.user_id) || [];
    existing.push({ id: row.id, name: row.name });
    rolesByUser.set(row.user_id, existing);
  }

  const users = result.rows.map((u) => ({
    ...u,
    roles: rolesByUser.get(u.id) || [],
  }));

  return NextResponse.json(users);
}

export async function POST(req: NextRequest) {
  const { error, admin } = await requireAdmin();
  if (error) return error;

  const body = await req.json() as {
    email: string;
    name: string;
    tenant_id?: string;
    role_ids?: string[];
  };

  const targetTenantId = admin!.isPlatformAdmin ? body.tenant_id : admin!.tenantId;
  if (!targetTenantId || !isAuthorizedForTenant(admin!, targetTenantId)) {
    return forbidCrossTenant();
  }
  if (body.tenant_id && body.tenant_id !== targetTenantId) {
    return forbidCrossTenant();
  }

  const roleError = await validateTenantScopedRoles(body.role_ids, targetTenantId, admin!);
  if (roleError) return roleError;

  const tempPassword = crypto.randomBytes(12).toString("base64url");
  const passwordHash = await bcrypt.hash(tempPassword, 10);

  const client = await pool.connect();
  try {
    await client.query("BEGIN");

    const userResult = await client.query(
      `INSERT INTO users (email, name, tenant_id, password_hash, auth_provider)
       VALUES ($1, $2, $3, $4, 'native')
       RETURNING id, email, name, tenant_id, auth_provider, is_active, created_at`,
      [body.email, body.name, targetTenantId, passwordHash]
    );
    const user = userResult.rows[0];

    if (body.role_ids?.length) {
      for (const roleId of body.role_ids) {
        await client.query(
          `INSERT INTO user_roles (user_id, role_id, assignment_source) VALUES ($1, $2, 'manual')`,
          [user.id, roleId]
        );
      }
    }

    await client.query("COMMIT");
    return NextResponse.json({ ...user, temp_password: tempPassword }, { status: 201 });
  } catch (e) {
    await client.query("ROLLBACK");
    throw e;
  } finally {
    client.release();
  }
}
