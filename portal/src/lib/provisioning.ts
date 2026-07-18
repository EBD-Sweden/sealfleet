// Self-serve tenant provisioning. Creates everything a new signup needs in one
// transaction: a tenant, an admin user (bcrypt native login), a tenant-scoped
// `admin` role + grant, and the tenant's first API key. Used by /api/signup.
//
// Note the two tenant_id representations in this schema: tenants.id is a UUID,
// while api_keys.tenant_id is free-form TEXT. We store the tenant UUID as text
// on the key so the router (which reads key_info["tenant_id"] verbatim) and the
// portal JWT (users.tenant_id UUID) agree on the same string.

import crypto from "crypto";
import bcrypt from "bcryptjs";
import { pool } from "@/lib/db";

// Default capabilities for a new tenant's first key — the documented CLI
// lifecycle (mirrors scripts/001_create_api_keys.sql).
const DEFAULT_KEY_PERMISSIONS = [
  "pipeline.invoke",
  "agent.invoke",
  "agent.register",
  "registry.export",
  "registry.import",
];

export function slugify(input: string): string {
  const base = input
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
  return base || "org";
}

export function generateApiKey(): string {
  return `mcpf_${crypto.randomBytes(32).toString("base64url")}`;
}

export interface SignupInput {
  org: string;
  email: string;
  password: string;
  name?: string;
}

export interface SignupResult {
  tenantId: string;
  tenantSlug: string;
  userId: string;
  apiKey: string;
}

export class SignupError extends Error {
  constructor(message: string, readonly status = 400) {
    super(message);
  }
}

// Reserve a unique slug, appending -2, -3, … on collision.
async function uniqueSlug(
  client: { query: (q: string, v?: unknown[]) => Promise<{ rows: unknown[] }> },
  desired: string,
): Promise<string> {
  for (let i = 0; i < 50; i++) {
    const candidate = i === 0 ? desired : `${desired}-${i + 1}`;
    const { rows } = await client.query("SELECT 1 FROM tenants WHERE slug = $1", [candidate]);
    if (rows.length === 0) return candidate;
  }
  // Extremely unlikely; fall back to a random suffix.
  return `${desired}-${crypto.randomBytes(3).toString("hex")}`;
}

export async function createTenantWithAdmin(input: SignupInput): Promise<SignupResult> {
  const email = input.email.trim().toLowerCase();
  const org = input.org.trim();
  if (!org) throw new SignupError("Organization name is required");
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) throw new SignupError("A valid email is required");
  if (!input.password || input.password.length < 8) {
    throw new SignupError("Password must be at least 8 characters");
  }

  // Email is globally UNIQUE across tenants — reject early with a clear message.
  const existing = await pool.query("SELECT 1 FROM users WHERE email = $1", [email]);
  if (existing.rows.length > 0) {
    throw new SignupError("An account with this email already exists", 409);
  }

  const passwordHash = await bcrypt.hash(input.password, 10);
  const apiKey = generateApiKey();

  const client = await pool.connect();
  try {
    await client.query("BEGIN");

    const slug = await uniqueSlug(client, slugify(org));
    const tenant = await client.query<{ id: string }>(
      "INSERT INTO tenants (slug, name) VALUES ($1, $2) RETURNING id",
      [slug, org],
    );
    const tenantId = tenant.rows[0].id;

    const user = await client.query<{ id: string }>(
      `INSERT INTO users (email, name, tenant_id, password_hash, auth_provider, is_admin)
       VALUES ($1, $2, $3, $4, 'native', true)
       RETURNING id`,
      [email, input.name?.trim() || email.split("@")[0], tenantId, passwordHash],
    );
    const userId = user.rows[0].id;

    const role = await client.query<{ id: string }>(
      `INSERT INTO roles (tenant_id, name, description)
       VALUES ($1, 'admin', 'Full administrative access')
       RETURNING id`,
      [tenantId],
    );
    // assignment_source is CHECK-constrained to ('manual','sso','scim'); a
    // self-serve grant is a manual one.
    await client.query(
      `INSERT INTO user_roles (user_id, role_id, assignment_source)
       VALUES ($1, $2, 'manual')
       ON CONFLICT (user_id, role_id) DO NOTHING`,
      [userId, role.rows[0].id],
    );

    await client.query(
      `INSERT INTO api_keys (api_key, tenant_id, name, is_active, action_permissions)
       VALUES ($1, $2, $3, true, $4)`,
      [apiKey, tenantId, "default", DEFAULT_KEY_PERMISSIONS],
    );

    // Seed an inactive subscription row so billing status is queryable immediately.
    await client.query(
      `INSERT INTO subscriptions (tenant_id, status) VALUES ($1, 'inactive')
       ON CONFLICT (tenant_id) DO NOTHING`,
      [tenantId],
    );

    await client.query("COMMIT");
    return { tenantId, tenantSlug: slug, userId, apiKey };
  } catch (err) {
    await client.query("ROLLBACK");
    // Unique violation on email (race with the pre-check).
    if ((err as { code?: string }).code === "23505") {
      throw new SignupError("An account with this email already exists", 409);
    }
    throw err;
  } finally {
    client.release();
  }
}
