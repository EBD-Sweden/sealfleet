import { beforeEach, describe, expect, it, vi } from "vitest";
import type { NextRequest } from "next/server";

const requireAdminMock = vi.fn();
const requirePlatformAdminMock = vi.fn();
const queryMock = vi.fn();
const connectQueryMock = vi.fn();
const releaseMock = vi.fn();
const jsonMock = vi.fn((body: unknown, init?: ResponseInit) => ({
  body,
  status: init?.status ?? 200,
}));

vi.mock("next/server", () => ({
  NextResponse: {
    json: jsonMock,
  },
}));

vi.mock("@/lib/admin-auth", () => ({
  requireAdmin: requireAdminMock,
  requirePlatformAdmin: requirePlatformAdminMock,
  isAuthorizedForTenant: (admin: { isPlatformAdmin: boolean; tenantId: string }, tenantId: string) =>
    admin.isPlatformAdmin || admin.tenantId === tenantId,
  forbidCrossTenant: () => jsonMock({ error: "Forbidden: cross-tenant access denied" }, { status: 403 }),
}));

vi.mock("@/lib/db", () => ({
  pool: {
    query: queryMock,
    connect: vi.fn(() => ({
      query: connectQueryMock,
      release: releaseMock,
    })),
  },
}));

vi.mock("bcryptjs", () => ({
  default: { hash: vi.fn(async () => "hashed-temp-password") },
}));

describe("admin API tenant authorization", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    requireAdminMock.mockResolvedValue({
      error: null,
      session: { user: { id: "admin-a", email: "a@test", tenant_id: "tenant-a", is_admin: true } },
      admin: {
        userId: "admin-a",
        email: "a@test",
        tenantId: "tenant-a",
        isTenantAdmin: true,
        isPlatformAdmin: false,
      },
    });
    requirePlatformAdminMock.mockResolvedValue({
      error: { status: 403, body: { error: "Forbidden: platform_admin required" } },
      session: null,
      admin: null,
    });
  });

  it("tenant admins list only users in their own tenant", async () => {
    queryMock
      .mockResolvedValueOnce({ rows: [] })
      .mockResolvedValueOnce({ rows: [] });

    const { GET } = await import("@/app/api/admin/users/route");
    await GET();

    expect(queryMock).toHaveBeenNthCalledWith(
      1,
      expect.stringContaining("WHERE u.tenant_id = $1"),
      ["tenant-a"]
    );
    expect(queryMock).toHaveBeenNthCalledWith(
      2,
      expect.stringContaining("WHERE u.tenant_id = $1"),
      ["tenant-a"]
    );
  });

  it("tenant admins cannot create users in another tenant", async () => {
    const { POST } = await import("@/app/api/admin/users/route");
    const response = await POST({
      json: async () => ({ email: "x@other.test", name: "X", tenant_id: "tenant-b", role_ids: [] }),
    } as unknown as NextRequest);

    expect(response.status).toBe(403);
    expect(connectQueryMock).not.toHaveBeenCalled();
  });

  it("tenant admins cannot assign platform_admin through user role updates", async () => {
    queryMock
      .mockResolvedValueOnce({ rows: [{ id: "target", tenant_id: "tenant-a" }] })
      .mockResolvedValueOnce({ rows: [{ id: "platform-role", tenant_id: "tenant-a", name: "platform_admin" }] });

    const { PUT } = await import("@/app/api/admin/users/[id]/route");
    const response = await PUT(
      { json: async () => ({ role_ids: ["platform-role"] }) } as unknown as NextRequest,
      { params: Promise.resolve({ id: "target" }) }
    );

    expect(response.status).toBe(403);
    expect(connectQueryMock).not.toHaveBeenCalled();
  });

  it("tenant admins cannot assign mixed-case platform_admin through user role updates", async () => {
    queryMock
      .mockResolvedValueOnce({ rows: [{ id: "target", tenant_id: "tenant-a" }] })
      .mockResolvedValueOnce({ rows: [{ id: "platform-role", tenant_id: "tenant-a", name: "Platform_Admin" }] });

    const { PUT } = await import("@/app/api/admin/users/[id]/route");
    const response = await PUT(
      { json: async () => ({ role_ids: ["platform-role"] }) } as unknown as NextRequest,
      { params: Promise.resolve({ id: "target" }) }
    );

    expect(response.status).toBe(403);
    expect(connectQueryMock).not.toHaveBeenCalled();
  });

  it("SSO mappings cannot grant mixed-case platform_admin roles", async () => {
    queryMock.mockResolvedValueOnce({
      rows: [{ id: "platform-role", tenant_id: "tenant-a", name: "Platform_Admin" }],
    });

    const { POST } = await import("@/app/api/admin/tenants/[id]/sso-mappings/route");
    const response = await POST(
      {
        json: async () => ({
          idp_claim_key: "groups",
          idp_claim_value: "admins",
          role_id: "platform-role",
        }),
      } as unknown as NextRequest,
      { params: Promise.resolve({ id: "tenant-a" }) }
    );

    expect(response.status).toBe(403);
    expect(queryMock).toHaveBeenCalledTimes(1);
  });

  it("platform-level tenant list requires platform_admin", async () => {
    const { GET } = await import("@/app/api/admin/tenants/route");
    const response = await GET();

    expect(response.status).toBe(403);
    expect(queryMock).not.toHaveBeenCalled();
  });

  it("platform admins can assign global platform_admin roles to tenant users", async () => {
    requireAdminMock.mockResolvedValueOnce({
      error: null,
      session: { user: { id: "root", email: "root@test", tenant_id: "platform", is_admin: true } },
      admin: {
        userId: "root",
        email: "root@test",
        tenantId: "platform",
        isTenantAdmin: false,
        isPlatformAdmin: true,
      },
    });
    queryMock
      .mockResolvedValueOnce({ rows: [{ id: "target", tenant_id: "tenant-a" }] })
      .mockResolvedValueOnce({
        rows: [{ id: "global-platform-role", tenant_id: null, tenant_slug: null, name: "platform_admin" }],
      });

    const { PUT } = await import("@/app/api/admin/users/[id]/route");
    await PUT(
      { json: async () => ({ role_ids: ["global-platform-role"] }) } as unknown as NextRequest,
      { params: Promise.resolve({ id: "target" }) }
    );

    expect(connectQueryMock).toHaveBeenCalledWith(
      expect.stringContaining("INSERT INTO user_roles"),
      ["target", "global-platform-role"]
    );
  });
});
