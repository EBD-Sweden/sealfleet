import { beforeEach, describe, expect, it, vi } from "vitest";

const { authMock } = vi.hoisted(() => ({
  authMock: vi.fn(),
}));

vi.mock("@/auth", () => ({
  auth: authMock,
}));

describe("portal backend delegation auth", () => {
  beforeEach(() => {
    vi.resetModules();
    authMock.mockReset();
    delete process.env.RUNTIME_API_KEY;
    delete process.env.MCPFINDER_BACKEND_API_KEY;
    delete process.env.NEXTAUTH_SECRET;
  });

  it("does not mint an HS256 backend proxy JWT from NEXTAUTH_SECRET when no scoped backend API key is configured", async () => {
    process.env.NEXTAUTH_SECRET = "legacy-shared-secret-that-must-not-delegate";
    authMock.mockResolvedValue({
      user: {
        id: "user-123",
        email: "user@example.test",
        tenant_id: "tenant-456",
        is_admin: false,
      },
    });

    const { requirePortalSession } = await import("@/lib/portal-auth");
    const result = await requirePortalSession();

    expect(result.error).toBeNull();
    expect(result.context?.backendHeaders).toEqual({
      "X-Sealfleet-User-Id": "user-123",
      "X-Sealfleet-Tenant-Id": "tenant-456",
    });
    expect(result.context?.backendHeaders.Authorization).toBeUndefined();
  });
});
