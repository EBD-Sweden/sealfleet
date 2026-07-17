import { beforeEach, describe, expect, it, vi } from "vitest";

type CapturedProvider = {
  id?: string;
  authorize?: (credentials: Record<string, string>) => Promise<unknown>;
};

type CapturedNextAuthConfig = {
  providers: CapturedProvider[];
};

const { queryMock, compareMock, nextAuthConfig, jwtVerifyMock, remoteJwksMock } = vi.hoisted(() => ({
  queryMock: vi.fn(),
  compareMock: vi.fn(),
  nextAuthConfig: { current: undefined as unknown },
  jwtVerifyMock: vi.fn(),
  remoteJwksMock: vi.fn(() => ({})),
}));

vi.mock("pg", () => {
  class Pool {
    query = queryMock;
  }
  return { Pool };
});

vi.mock("next-auth", () => ({
  default: vi.fn((config) => {
    nextAuthConfig.current = config;
    return { handlers: {}, auth: vi.fn(), signIn: vi.fn(), signOut: vi.fn() };
  }),
}));

vi.mock("bcryptjs", () => ({
  default: { compare: compareMock },
}));

vi.mock("next-auth/providers/credentials", () => ({
  default: vi.fn((config) => config),
}));

vi.mock("next-auth/providers/google", () => ({
  default: vi.fn((config) => config),
}));

vi.mock("next-auth/providers/microsoft-entra-id", () => ({
  default: vi.fn((config) => config),
}));

vi.mock("@/lib/auth-keys", () => ({
  getSigningKey: vi.fn(),
  getVerificationKey: vi.fn(),
  getKid: vi.fn(),
  SIGNING_ALG: "RS256",
}));

vi.mock("jose", () => ({
  createRemoteJWKSet: remoteJwksMock,
  jwtVerify: jwtVerifyMock,
  SignJWT: vi.fn(() => ({
    setProtectedHeader: vi.fn().mockReturnThis(),
    setIssuedAt: vi.fn().mockReturnThis(),
    setExpirationTime: vi.fn().mockReturnThis(),
    setIssuer: vi.fn().mockReturnThis(),
    setAudience: vi.fn().mockReturnThis(),
    setSubject: vi.fn().mockReturnThis(),
    sign: vi.fn().mockResolvedValue("signed.jwt"),
  })),
}));

// SSO is an Enterprise feature; these tests exercise the SSO behavior, so treat
// the deployment as licensed for SSO with ample seats.
vi.mock("@/lib/entitlement", () => ({
  getEntitlement: vi.fn().mockResolvedValue({
    tier: "enterprise", features: ["sso", "multi_user"], seats: 1000, customer: "test",
  }),
  hasFeature: vi.fn().mockResolvedValue(true),
}));

describe("SSO authoritative role sync", () => {
  beforeEach(() => {
    vi.resetModules();
    queryMock.mockReset();
    compareMock.mockReset();
    jwtVerifyMock.mockReset();
    remoteJwksMock.mockClear();
    nextAuthConfig.current = undefined;
    compareMock.mockResolvedValue(true);
    delete process.env.PLATFORM_ADMIN_EMAILS;
  });

  it("rejects custom OIDC SSO when id_token email does not match the submitted email", async () => {
    const tenant = {
      id: "tenant-a",
      slug: "tenant-a",
      name: "Tenant A",
      sso_enabled: true,
      oidc_issuer: "https://idp.tenant-a.test",
      oidc_client_id: "client-a",
      oidc_client_secret: "secret-a",
      oidc_scopes: "openid email profile",
      allowed_domains: ["tenant.test"],
    };

    queryMock.mockResolvedValueOnce({ rows: [tenant] });
    jwtVerifyMock.mockResolvedValueOnce({
      payload: {
        sub: "subject-1",
        email: "attacker@tenant.test",
        email_verified: true,
        name: "Attacker",
      },
    });

    await import("@/auth");
    const capturedConfig = nextAuthConfig.current as CapturedNextAuthConfig;
    const ssoProvider = capturedConfig.providers.find((provider) => provider.id === "sso");

    const result = await ssoProvider?.authorize?.({
      email: "victim@tenant.test",
      id_token: "valid-id-token-for-attacker",
    });

    expect(result).toBeNull();
    expect(jwtVerifyMock).toHaveBeenCalledTimes(1);
    expect(queryMock).toHaveBeenCalledTimes(1);
  });

  it("rejects custom OIDC SSO when the IdP explicitly reports email_verified=false", async () => {
    const tenant = {
      id: "tenant-a",
      slug: "tenant-a",
      name: "Tenant A",
      sso_enabled: true,
      oidc_issuer: "https://idp.tenant-a.test",
      oidc_client_id: "client-a",
      oidc_client_secret: "secret-a",
      oidc_scopes: "openid email profile",
      allowed_domains: ["tenant.test"],
    };

    queryMock.mockResolvedValueOnce({ rows: [tenant] });
    jwtVerifyMock.mockResolvedValueOnce({
      payload: {
        sub: "subject-1",
        email: "member@tenant.test",
        email_verified: false,
        name: "Member",
      },
    });

    await import("@/auth");
    const capturedConfig = nextAuthConfig.current as CapturedNextAuthConfig;
    const ssoProvider = capturedConfig.providers.find((provider) => provider.id === "sso");

    const result = await ssoProvider?.authorize?.({
      email: "member@tenant.test",
      id_token: "valid-id-token-with-unverified-email",
    });

    expect(result).toBeNull();
    expect(jwtVerifyMock).toHaveBeenCalledTimes(1);
    expect(queryMock).toHaveBeenCalledTimes(1);
  });

  it("clears stale SSO-managed roles when no current IdP mappings match", async () => {
    const user = {
      id: "user-1",
      email: "member@tenant.test",
      name: "Member",
      tenant_id: "tenant-a",
      is_active: true,
    };

    queryMock
      .mockResolvedValueOnce({ rows: [user] }) // upsert user
      .mockResolvedValueOnce({ rows: [] }) // applySSORoleMappings: no mappings match
      .mockResolvedValueOnce({ rows: [] }) // DELETE SSO-managed user_roles
      .mockResolvedValueOnce({ rows: [] }); // admin role lookup

    const { upsertSsoUser } = await import("@/auth");
    const result = await upsertSsoUser({
      email: user.email,
      name: user.name,
      avatarUrl: null,
      tenantId: user.tenant_id,
      authProvider: "oidc",
      oidcClaims: { email: user.email, groups: [] },
    });

    expect(result?.is_admin).toBe(false);
    expect(queryMock).toHaveBeenCalledWith(
      expect.stringContaining("DELETE FROM user_roles"),
      [user.id]
    );
  });

  it("inserts matched SSO roles with assignment_source=sso", async () => {
    const user = {
      id: "user-1",
      email: "admin@tenant.test",
      name: "Admin",
      tenant_id: "tenant-a",
      is_active: true,
    };

    queryMock
      .mockResolvedValueOnce({ rows: [user] }) // upsert user
      .mockResolvedValueOnce({
        rows: [
          {
            id: "mapping-1",
            tenant_id: "tenant-a",
            idp_claim_key: "groups",
            idp_claim_value: "admins",
            role_id: "tenant-admin-role",
          },
        ],
      })
      .mockResolvedValueOnce({ rows: [] }) // DELETE SSO-managed roles
      .mockResolvedValueOnce({ rows: [] }) // INSERT matched role
      .mockResolvedValueOnce({ rows: [{ name: "tenant_admin", tenant_id: "tenant-a" }] }); // admin role lookup

    const { upsertSsoUser } = await import("@/auth");
    const result = await upsertSsoUser({
      email: user.email,
      name: user.name,
      avatarUrl: null,
      tenantId: user.tenant_id,
      authProvider: "oidc",
      oidcClaims: { email: user.email, groups: ["admins"] },
    });

    expect(result?.is_admin).toBe(true);
    expect(queryMock).toHaveBeenCalledWith(
      expect.stringContaining("assignment_source"),
      [user.id, "tenant-admin-role"]
    );
  });

  it("rejects SSO login when an existing email belongs to another tenant", async () => {
    const user = {
      id: "user-1",
      email: "admin@tenant-b.test",
      name: "Admin",
      tenant_id: "tenant-a",
      is_active: true,
    };

    queryMock.mockResolvedValueOnce({ rows: [] }); // conflict update refused cross-tenant row

    const { upsertSsoUser } = await import("@/auth");
    const result = await upsertSsoUser({
      email: user.email,
      name: user.name,
      avatarUrl: null,
      tenantId: "tenant-b",
      authProvider: "oidc",
      oidcClaims: { email: user.email, groups: ["admins"] },
    });

    expect(result).toBeNull();
    expect(queryMock).toHaveBeenCalledTimes(1);
    expect(queryMock.mock.calls[0]?.[0]).toContain("WHERE users.tenant_id = EXCLUDED.tenant_id");
  });

  it("does not mint credentials is_admin from cross-tenant admin roles", async () => {
    const user = {
      id: "user-1",
      email: "member@tenant-a.test",
      name: "member",
      password_hash: "hash",
      tenant_id: "tenant-a",
      is_active: true,
      auth_provider: "credentials",
    };

    queryMock
      .mockResolvedValueOnce({ rows: [user] })
      .mockResolvedValueOnce({ rows: [{ name: "tenant_admin", tenant_id: "tenant-b" }] });

    await import("@/auth");
    const capturedConfig = nextAuthConfig.current as CapturedNextAuthConfig;
    const credentialsProvider = capturedConfig.providers.find(
      (provider) => provider.id === "credentials"
    );
    const result = await credentialsProvider?.authorize?.({
      email: user.email,
      password: "correct-password",
    });

    expect(result).toMatchObject({
      id: user.id,
      tenant_id: user.tenant_id,
      is_admin: false,
    });
  });

  it("does not mint credentials is_admin from tenant-scoped platform_admin roles", async () => {
    const user = {
      id: "user-1",
      email: "member@tenant-a.test",
      name: "member",
      password_hash: "hash",
      tenant_id: "tenant-a",
      is_active: true,
      auth_provider: "credentials",
    };

    queryMock
      .mockResolvedValueOnce({ rows: [user] })
      .mockResolvedValueOnce({
        rows: [{ name: "platform_admin", tenant_id: "tenant-a", tenant_slug: "tenant-a" }],
      });

    await import("@/auth");
    const capturedConfig = nextAuthConfig.current as CapturedNextAuthConfig;
    const credentialsProvider = capturedConfig.providers.find(
      (provider) => provider.id === "credentials"
    );
    const result = await credentialsProvider?.authorize?.({
      email: user.email,
      password: "correct-password",
    });

    expect(result).toMatchObject({
      id: user.id,
      tenant_id: user.tenant_id,
      is_admin: false,
    });
  });

  it("mints credentials is_admin from a global platform_admin role", async () => {
    const user = {
      id: "user-1",
      email: "root@tenant-a.test",
      name: "root",
      password_hash: "hash",
      tenant_id: "tenant-a",
      is_active: true,
      auth_provider: "credentials",
    };

    queryMock
      .mockResolvedValueOnce({ rows: [user] })
      .mockResolvedValueOnce({
        rows: [{ name: "platform_admin", tenant_id: null, tenant_slug: null }],
      });

    await import("@/auth");
    const capturedConfig = nextAuthConfig.current as CapturedNextAuthConfig;
    const credentialsProvider = capturedConfig.providers.find(
      (provider) => provider.id === "credentials"
    );
    const result = await credentialsProvider?.authorize?.({
      email: user.email,
      password: "correct-password",
    });

    expect(result).toMatchObject({
      id: user.id,
      tenant_id: user.tenant_id,
      is_admin: true,
    });
  });
});
