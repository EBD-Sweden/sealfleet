import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { requireAdmin, forbidCrossTenant, isAuthorizedForTenant } from "@/lib/admin-auth";

function normalizeRoleName(name: string): string {
  return name.trim().toLowerCase();
}

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { error, admin } = await requireAdmin();
  if (error) return error;

  const { id } = await params;
  const rolePredicate = admin!.isPlatformAdmin ? "r.id = $1" : "r.id = $1 AND r.tenant_id = $2";
  const queryParams = admin!.isPlatformAdmin ? [id] : [id, admin!.tenantId];
  const roleResult = await pool.query(
    `SELECT r.id, r.tenant_id, r.name, r.description, t.name as tenant_name
     FROM roles r LEFT JOIN tenants t ON t.id = r.tenant_id WHERE ${rolePredicate}`,
    queryParams
  );
  if (roleResult.rows.length === 0) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  const permsResult = await pool.query(
    `SELECT mp.id, mp.server_id, mp.allowed_tools, mp.scopes, s.name as server_name
     FROM mcp_permissions mp
     LEFT JOIN servers s ON s.id = mp.server_id
     WHERE mp.grantee_type = 'role' AND mp.grantee_id = $1 AND mp.tenant_id = $2`,
    [id, roleResult.rows[0].tenant_id]
  );

  return NextResponse.json({ ...roleResult.rows[0], permissions: permsResult.rows });
}

export async function PUT(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { error, admin } = await requireAdmin();
  if (error) return error;

  const { id } = await params;
  const existingRole = await pool.query<{ id: string; tenant_id: string; name: string }>(
    `SELECT id, tenant_id, name FROM roles WHERE id = $1`,
    [id]
  );
  if (existingRole.rows.length === 0) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }
  const currentTenantId = existingRole.rows[0].tenant_id;
  if (!isAuthorizedForTenant(admin!, currentTenantId)) return forbidCrossTenant();

  const body = await req.json() as {
    name: string;
    description?: string;
    tenant_id?: string;
    permissions?: { server_id: string; allowed_tools: string[]; scopes: string[] }[];
  };
  const targetTenantId = admin!.isPlatformAdmin ? (body.tenant_id ?? currentTenantId) : currentTenantId;
  if (!isAuthorizedForTenant(admin!, targetTenantId) || (!admin!.isPlatformAdmin && body.tenant_id && body.tenant_id !== currentTenantId)) {
    return forbidCrossTenant();
  }
  if (!admin!.isPlatformAdmin && normalizeRoleName(body.name) === "platform_admin") {
    return NextResponse.json({ error: "Forbidden: platform_admin role is platform-scoped" }, { status: 403 });
  }

  const client = await pool.connect();
  try {
    await client.query("BEGIN");

    await client.query(
      `UPDATE roles SET name=$1, description=$2, tenant_id=$3 WHERE id=$4 AND tenant_id=$5`,
      [normalizeRoleName(body.name), body.description || null, targetTenantId, id, currentTenantId]
    );

    await client.query(
      `DELETE FROM mcp_permissions WHERE grantee_type='role' AND grantee_id=$1 AND tenant_id=$2`,
      [id, currentTenantId]
    );

    if (body.permissions?.length) {
      for (const perm of body.permissions) {
        await client.query(
          `INSERT INTO mcp_permissions (tenant_id, grantee_type, grantee_id, server_id, allowed_tools, scopes)
           VALUES ($1, 'role', $2, $3, $4, $5)`,
          [targetTenantId, id, perm.server_id, perm.allowed_tools.length ? perm.allowed_tools : null, perm.scopes]
        );
      }
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
