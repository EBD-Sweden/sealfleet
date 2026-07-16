import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { requireAdmin, forbidCrossTenant, isAuthorizedForTenant, type AdminContext } from "@/lib/admin-auth";

function normalizeRoleName(name: string): string {
  return name.trim().toLowerCase();
}

function isPlatformAdminRole(role: { name: string; tenant_id: string | null; tenant_slug?: string | null }): boolean {
  return normalizeRoleName(role.name) === "platform_admin" && (role.tenant_id === null || role.tenant_slug === "platform");
}

async function loadTargetUser(id: string): Promise<{ id: string; tenant_id: string } | null> {
  const result = await pool.query<{ id: string; tenant_id: string }>(
    `SELECT id, tenant_id FROM users WHERE id = $1`,
    [id]
  );
  return result.rows[0] ?? null;
}

async function validateRoleUpdate(
  roleIds: string[],
  tenantId: string,
  admin: AdminContext
): Promise<NextResponse | null> {
  if (!roleIds.length) return null;
  const roles = await pool.query<{ id: string; tenant_id: string | null; tenant_slug: string | null; name: string }>(
    `SELECT r.id, r.tenant_id, t.slug AS tenant_slug, r.name
     FROM roles r
     LEFT JOIN tenants t ON t.id = r.tenant_id
     WHERE r.id = ANY($1)`,
    [roleIds]
  );
  if (roles.rows.length !== roleIds.length) {
    return NextResponse.json({ error: "One or more roles were not found" }, { status: 400 });
  }
  if (!admin.isPlatformAdmin && roles.rows.some((role) => normalizeRoleName(role.name) === "platform_admin")) {
    return NextResponse.json({ error: "Forbidden: platform_admin cannot be assigned by tenant_admin" }, { status: 403 });
  }
  if (roles.rows.some((role) => role.tenant_id !== tenantId && !(admin.isPlatformAdmin && isPlatformAdminRole(role)))) {
    return forbidCrossTenant();
  }
  return null;
}

// GET /api/admin/users/[id] — single user with roles and mcp_permissions
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { error, admin } = await requireAdmin();
  if (error) return error;

  const { id } = await params;
  const tenantPredicate = admin!.isPlatformAdmin ? "u.id = $1" : "u.id = $1 AND u.tenant_id = $2";
  const queryParams = admin!.isPlatformAdmin ? [id] : [id, admin!.tenantId];

  const userResult = await pool.query(
    `SELECT u.id, u.email, u.name, u.tenant_id, u.auth_provider,
            u.is_active, u.is_admin, u.last_login_at, u.created_at,
            t.slug as tenant_slug, t.name as tenant_name
     FROM users u
     LEFT JOIN tenants t ON t.id = u.tenant_id
     WHERE ${tenantPredicate}`,
    queryParams
  );

  if (userResult.rows.length === 0) {
    return NextResponse.json({ error: "User not found" }, { status: 404 });
  }

  const rolesResult = await pool.query(
    `SELECT r.id, r.name, r.description
     FROM user_roles ur
     JOIN roles r ON r.id = ur.role_id
     LEFT JOIN tenants rt ON rt.id = r.tenant_id
     WHERE ur.user_id = $1
       AND (
         r.tenant_id = $2
         OR (r.name = 'platform_admin' AND (r.tenant_id IS NULL OR rt.slug = 'platform'))
       )
     ORDER BY r.name`,
    [id, userResult.rows[0].tenant_id]
  );

  const directPermsResult = await pool.query(
    `SELECT mp.id, mp.server_id, mp.allowed_tools, mp.scopes, mp.expires_at,
            s.name as server_name
     FROM mcp_permissions mp
     LEFT JOIN servers s ON s.id = mp.server_id
     WHERE mp.grantee_type = 'user' AND mp.grantee_id = $1 AND mp.tenant_id = $2`,
    [id, userResult.rows[0].tenant_id]
  );

  const roleIds = rolesResult.rows.map((r) => r.id);
  let inheritedPerms: typeof directPermsResult.rows = [];
  if (roleIds.length > 0) {
    const inheritedResult = await pool.query(
      `SELECT mp.id, mp.server_id, mp.allowed_tools, mp.scopes, mp.expires_at,
              mp.grantee_id as role_id, r.name as role_name,
              s.name as server_name
       FROM mcp_permissions mp
       LEFT JOIN servers s ON s.id = mp.server_id
       LEFT JOIN roles r ON r.id = mp.grantee_id
       WHERE mp.grantee_type = 'role' AND mp.grantee_id = ANY($1) AND mp.tenant_id = $2`,
      [roleIds, userResult.rows[0].tenant_id]
    );
    inheritedPerms = inheritedResult.rows;
  }

  return NextResponse.json({
    ...userResult.rows[0],
    roles: rolesResult.rows,
    mcp_permissions: {
      direct: directPermsResult.rows,
      inherited: inheritedPerms,
    },
  });
}

// PUT /api/admin/users/[id] — update user roles
export async function PUT(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { error, admin } = await requireAdmin();
  if (error) return error;

  const { id } = await params;
  const target = await loadTargetUser(id);
  if (!target) return NextResponse.json({ error: "User not found" }, { status: 404 });
  if (!isAuthorizedForTenant(admin!, target.tenant_id)) return forbidCrossTenant();

  const body = await req.json() as {
    role_ids: string[];
    is_active?: boolean;
  };
  const roleError = await validateRoleUpdate(body.role_ids ?? [], target.tenant_id, admin!);
  if (roleError) return roleError;

  const client = await pool.connect();
  try {
    await client.query("BEGIN");

    // Update manual roles only; SSO-managed roles are authoritative from IdP login.
    await client.query(
      `DELETE FROM user_roles WHERE user_id = $1 AND assignment_source = 'manual'`,
      [id]
    );
    for (const roleId of body.role_ids ?? []) {
      await client.query(
        `INSERT INTO user_roles (user_id, role_id, assignment_source) VALUES ($1, $2, 'manual')`,
        [id, roleId]
      );
    }

    if (body.is_active !== undefined) {
      await client.query(
        `UPDATE users SET is_active=$1, updated_at=now() WHERE id=$2 AND tenant_id=$3`,
        [body.is_active, id, target.tenant_id]
      );
    }

    await client.query("COMMIT");
    return NextResponse.json({ ok: true });
  } catch (e) {
    await client.query("ROLLBACK");
    throw e;
  } finally {
    client.release();
  }
}
