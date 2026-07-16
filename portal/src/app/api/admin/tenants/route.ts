import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { requirePlatformAdmin } from "@/lib/admin-auth";

// GET /api/admin/tenants — list all tenants with sso_role_mappings
export async function GET() {
  const { error } = await requirePlatformAdmin();
  if (error) return error;

  const tenantsResult = await pool.query(
    `SELECT id, slug, name, sso_enabled, oidc_issuer, oidc_client_id,
            oidc_scopes, allowed_domains, created_at, updated_at
     FROM tenants
     ORDER BY created_at`
  );

  // Fetch mappings for all tenants in one query
  const mappingsResult = await pool.query(
    `SELECT srm.id, srm.tenant_id, srm.idp_claim_key, srm.idp_claim_value,
            srm.role_id, r.name as role_name, srm.created_at
     FROM sso_role_mappings srm
     JOIN roles r ON r.id = srm.role_id
     ORDER BY srm.created_at`
  );

  const mappingsByTenant = new Map<string, typeof mappingsResult.rows>();
  for (const m of mappingsResult.rows) {
    const list = mappingsByTenant.get(m.tenant_id) ?? [];
    list.push(m);
    mappingsByTenant.set(m.tenant_id, list);
  }

  const tenants = tenantsResult.rows.map((t) => ({
    ...t,
    // Never expose oidc_client_secret in GET responses
    sso_role_mappings: mappingsByTenant.get(t.id) ?? [],
  }));

  return NextResponse.json(tenants);
}

// POST /api/admin/tenants — create or update tenant (upsert by slug)
export async function POST(request: NextRequest) {
  const { error } = await requirePlatformAdmin();
  if (error) return error;

  const body = await request.json() as {
    slug: string;
    name: string;
    sso_enabled?: boolean;
    oidc_issuer?: string;
    oidc_client_id?: string;
    oidc_client_secret?: string;
    oidc_scopes?: string;
    allowed_domains?: string[];
  };

  if (!body.slug || !body.name) {
    return NextResponse.json({ error: "slug and name are required" }, { status: 400 });
  }

  const result = await pool.query(
    `INSERT INTO tenants (slug, name, sso_enabled, oidc_issuer, oidc_client_id,
                          oidc_client_secret, oidc_scopes, allowed_domains)
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
     ON CONFLICT (slug) DO UPDATE SET
       name = EXCLUDED.name,
       sso_enabled = COALESCE(EXCLUDED.sso_enabled, tenants.sso_enabled),
       oidc_issuer = COALESCE(EXCLUDED.oidc_issuer, tenants.oidc_issuer),
       oidc_client_id = COALESCE(EXCLUDED.oidc_client_id, tenants.oidc_client_id),
       oidc_client_secret = COALESCE(EXCLUDED.oidc_client_secret, tenants.oidc_client_secret),
       oidc_scopes = COALESCE(EXCLUDED.oidc_scopes, tenants.oidc_scopes),
       allowed_domains = COALESCE(EXCLUDED.allowed_domains, tenants.allowed_domains),
       updated_at = NOW()
     RETURNING id, slug, name, sso_enabled, oidc_issuer, oidc_client_id,
               oidc_scopes, allowed_domains, created_at, updated_at`,
    [
      body.slug,
      body.name,
      body.sso_enabled ?? false,
      body.oidc_issuer ?? null,
      body.oidc_client_id ?? null,
      body.oidc_client_secret ?? null,
      body.oidc_scopes ?? null,
      body.allowed_domains ?? null,
    ]
  );

  return NextResponse.json(result.rows[0], { status: 201 });
}
