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
  if (!isAuthorizedForTenant(admin!, id)) return forbidCrossTenant();

  const result = await pool.query(
    `SELECT m.id, m.tenant_id, m.idp_claim_key, m.idp_claim_value, m.role_id, r.name as role_name, m.created_at
     FROM sso_role_mappings m
     JOIN roles r ON r.id = m.role_id
     WHERE m.tenant_id = $1 ORDER BY m.created_at`,
    [id]
  );
  return NextResponse.json(result.rows);
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { error, admin } = await requireAdmin();
  if (error) return error;

  const { id } = await params;
  if (!isAuthorizedForTenant(admin!, id)) return forbidCrossTenant();
  const body = await req.json() as {
    idp_claim_key: string;
    idp_claim_value: string;
    role_id: string;
  };

  const roleResult = await pool.query<{ id: string; tenant_id: string; name: string }>(
    `SELECT id, tenant_id, name FROM roles WHERE id = $1`,
    [body.role_id]
  );
  const role = roleResult.rows[0];
  if (!role) return NextResponse.json({ error: "Role not found" }, { status: 404 });
  if (role.tenant_id !== id) return forbidCrossTenant();
  if (normalizeRoleName(role.name) === "platform_admin") {
    return NextResponse.json(
      { error: "Forbidden: SSO mappings cannot grant platform_admin" },
      { status: 403 }
    );
  }

  const result = await pool.query(
    `INSERT INTO sso_role_mappings (tenant_id, idp_claim_key, idp_claim_value, role_id)
     VALUES ($1, $2, $3, $4)
     RETURNING id, tenant_id, idp_claim_key, idp_claim_value, role_id, created_at`,
    [id, body.idp_claim_key, body.idp_claim_value, body.role_id]
  );

  return NextResponse.json(result.rows[0], { status: 201 });
}
