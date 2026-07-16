import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { requireAdmin, forbidCrossTenant, isAuthorizedForTenant } from "@/lib/admin-auth";

// DELETE /api/admin/tenants/[id]/sso-mappings/[mappingId] — remove a mapping
export async function DELETE(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string; mappingId: string }> }
) {
  const { error, admin } = await requireAdmin();
  if (error) return error;

  const { id: tenantId, mappingId } = await params;
  if (!isAuthorizedForTenant(admin!, tenantId)) return forbidCrossTenant();

  const result = await pool.query(
    `DELETE FROM sso_role_mappings WHERE id = $1 AND tenant_id = $2 RETURNING id`,
    [mappingId, tenantId]
  );

  if (result.rows.length === 0) {
    return NextResponse.json({ error: "Mapping not found" }, { status: 404 });
  }

  return NextResponse.json({ deleted: true, id: mappingId });
}
