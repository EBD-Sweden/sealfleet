import { beforeEach, describe, expect, it, vi } from "vitest";

const authMock = vi.fn();
const queryMock = vi.fn();

vi.mock("@/auth", () => ({
  auth: authMock,
}));

vi.mock("@/lib/db", () => ({
  pool: {
    query: queryMock,
  },
}));

describe("admin authorization", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    delete process.env.PLATFORM_ADMIN_EMAILS;
  });

  it("treats tenant_admin as tenant-scoped but not platform admin", async () => {
    authMock.mockResolvedValue({
      user: {
        id: "user-1",
        email: "admin@tenant-a.test",
        tenant_id: "tenant-a",
        is_admin: true,
      },
    });
    queryMock.mockResolvedValue({ rows: [{ name: "tenant_admin", tenant_id: "tenant-a" }] });

    const { requireAdmin } = await import("@/lib/admin-auth");
    const result = await requireAdmin();

    expect(result.error).toBeNull();
    expect(result.admin).toMatchObject({
      userId: "user-1",
      tenantId: "tenant-a",
      isTenantAdmin: true,
      isPlatformAdmin: false,
    });
  });

  it("requires platform_admin for platform-scoped admin checks", async () => {
    authMock.mockResolvedValue({
      user: {
        id: "user-1",
        email: "admin@tenant-a.test",
        tenant_id: "tenant-a",
        is_admin: true,
      },
    });
    queryMock.mockResolvedValue({ rows: [{ name: "tenant_admin", tenant_id: "tenant-a" }] });

    const { requirePlatformAdmin } = await import("@/lib/admin-auth");
    const result = await requirePlatformAdmin();

    expect(result.session).toBeNull();
    expect(result.admin).toBeNull();
    expect(result.error?.status).toBe(403);
  });

  it("recognizes platform_admin from a DB role independent of tenant_admin", async () => {
    authMock.mockResolvedValue({
      user: {
        id: "platform-user",
        email: "root@example.test",
        tenant_id: "platform-tenant",
        is_admin: true,
      },
    });
    queryMock.mockResolvedValue({
      rows: [{ name: "platform_admin", tenant_id: "platform-tenant", tenant_slug: "platform" }],
    });

    const { requirePlatformAdmin } = await import("@/lib/admin-auth");
    const result = await requirePlatformAdmin();

    expect(result.error).toBeNull();
    expect(result.admin).toMatchObject({
      isPlatformAdmin: true,
      isTenantAdmin: false,
    });
  });

  it("recognizes a global platform_admin role for a non-platform tenant user", async () => {
    authMock.mockResolvedValue({
      user: {
        id: "platform-user",
        email: "root@tenant-a.test",
        tenant_id: "tenant-a",
        is_admin: true,
      },
    });
    queryMock.mockResolvedValue({
      rows: [{ name: "platform_admin", tenant_id: null, tenant_slug: null }],
    });

    const { requirePlatformAdmin } = await import("@/lib/admin-auth");
    const result = await requirePlatformAdmin();

    expect(result.error).toBeNull();
    expect(result.admin).toMatchObject({
      isPlatformAdmin: true,
      isTenantAdmin: false,
    });
  });

  it("recognizes a platform-tenant platform_admin role for users outside the platform tenant", async () => {
    authMock.mockResolvedValue({
      user: {
        id: "platform-user",
        email: "root@tenant-a.test",
        tenant_id: "tenant-a",
        is_admin: true,
      },
    });
    queryMock.mockResolvedValue({
      rows: [{ name: "platform_admin", tenant_id: "platform-tenant", tenant_slug: "platform" }],
    });

    const { requirePlatformAdmin } = await import("@/lib/admin-auth");
    const result = await requirePlatformAdmin();

    expect(result.error).toBeNull();
    expect(result.admin).toMatchObject({
      isPlatformAdmin: true,
      isTenantAdmin: false,
    });
  });

  it("does not authorize tenant admin roles from a different tenant", async () => {
    authMock.mockResolvedValue({
      user: {
        id: "user-1",
        email: "admin@tenant-a.test",
        tenant_id: "tenant-a",
        is_admin: false,
      },
    });
    queryMock.mockResolvedValue({ rows: [{ name: "tenant_admin", tenant_id: "tenant-b" }] });

    const { requireAdmin } = await import("@/lib/admin-auth");
    const result = await requireAdmin();

    expect(result.session).toBeNull();
    expect(result.admin).toBeNull();
    expect(result.error?.status).toBe(403);
  });

  it("does not authorize tenant-scoped platform_admin roles as platform admin", async () => {
    authMock.mockResolvedValue({
      user: {
        id: "user-1",
        email: "admin@tenant-a.test",
        tenant_id: "tenant-a",
        is_admin: true,
      },
    });
    queryMock.mockResolvedValue({
      rows: [{ name: "platform_admin", tenant_id: "tenant-a", tenant_slug: "tenant-a" }],
    });

    const { requirePlatformAdmin } = await import("@/lib/admin-auth");
    const result = await requirePlatformAdmin();

    expect(result.session).toBeNull();
    expect(result.admin).toBeNull();
    expect(result.error?.status).toBe(403);
  });
});
