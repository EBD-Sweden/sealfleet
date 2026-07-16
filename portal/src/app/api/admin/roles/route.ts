import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { requireAdmin, forbidCrossTenant, isAuthorizedForTenant } from "@/lib/admin-auth";

function normalizeRoleName(name: string): string {
  return name.trim().toLowerCase();
}

// GET /api/admin/roles — list roles with mcp_permissions (joined with server name)
export async function GET() {
  const { error, admin } = await requireAdmin();
  if (error) return error;

  const where = admin!.isPlatformAdmin ? "" : "WHERE r.tenant_id = $1";
  const params = admin!.isPlatformAdmin ? [] : [admin!.tenantId];
  const rolesResult = await pool.query(
    `SELECT r.id, r.tenant_id, r.name, r.description, t.name as tenant_name
     FROM roles r
     LEFT JOIN tenants t ON t.id = r.tenant_id
     ${where}
     ORDER BY r.name`,
    params
  );

  const permWhere = admin!.isPlatformAdmin ? "WHERE mp.grantee_type = 'role'" : "WHERE mp.grantee_type = 'role' AND mp.tenant_id = $1";
  const permsResult = await pool.query(
    `SELECT mp.id, mp.grantee_id as role_id,
            mp.server_id, mp.allowed_tools, mp.scopes, mp.expires_at,
            s.name as server_name
     FROM mcp_permissions mp
     LEFT JOIN servers s ON s.id = mp.server_id
     ${permWhere}
     ORDER BY mp.created_at`,
    params
  );

  const permsByRole = new Map<string, typeof permsResult.rows>();
  for (const p of permsResult.rows) {
    const list = permsByRole.get(p.role_id) ?? [];
    list.push(p);
    permsByRole.set(p.role_id, list);
  }

  const roles = rolesResult.rows.map((r) => ({
    ...r,
    mcp_permissions: permsByRole.get(r.id) ?? [],
  }));

  return NextResponse.json(roles);
}

// POST /api/admin/roles — create a new role
export async function POST(req: NextRequest) {
  const { error, admin } = await requireAdmin();
  if (error) return error;

  const body = await req.json() as {
    name: string;
    description?: string;
    tenant_id: string;
    permissions?: { server_id: string; allowed_tools: string[]; scopes: string[] }[];
  };

  const targetTenantId = admin!.isPlatformAdmin ? body.tenant_id : admin!.tenantId;
  if (!targetTenantId || !body.name) {
    return NextResponse.json({ error: "tenant_id and name are required" }, { status: 400 });
  }
  if (!isAuthorizedForTenant(admin!, targetTenantId) || (body.tenant_id && body.tenant_id !== targetTenantId)) {
    return forbidCrossTenant();
  }
  if (!admin!.isPlatformAdmin && normalizeRoleName(body.name) === "platform_admin") {
    return NextResponse.json({ error: "Forbidden: platform_admin role is platform-scoped" }, { status: 403 });
  }

  const client = await pool.connect();
  try {
    await client.query("BEGIN");

    const roleResult = await client.query(
      `INSERT INTO roles (name, description, tenant_id) VALUES ($1, $2, $3)
       RETURNING id, name, description, tenant_id`,
      [normalizeRoleName(body.name), body.description || null, targetTenantId]
    );
    const role = roleResult.rows[0];

    if (body.permissions?.length) {
      for (const perm of body.permissions) {
        await client.query(
          `INSERT INTO mcp_permissions (tenant_id, grantee_type, grantee_id, server_id, allowed_tools, scopes)
           VALUES ($1, 'role', $2, $3, $4, $5)`,
          [targetTenantId, role.id, perm.server_id, perm.allowed_tools.length ? perm.allowed_tools : null, perm.scopes]
        );
      }
    }

    await client.query("COMMIT");
    return NextResponse.json(role, { status: 201 });
  } catch (e) {
    await client.query("ROLLBACK");
    throw e;
  } finally {
    client.release();
  }
}
