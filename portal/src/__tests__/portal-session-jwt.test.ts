// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as jose from "jose";

const { nextAuthConfig } = vi.hoisted(() => ({
  nextAuthConfig: { current: undefined as unknown },
}));

vi.mock("next-auth", () => ({
  default: vi.fn((config) => {
    nextAuthConfig.current = config;
    return { handlers: {}, auth: vi.fn(), signIn: vi.fn(), signOut: vi.fn() };
  }),
}));

vi.mock("pg", () => {
  class Pool {
    query = vi.fn();
  }
  return { Pool };
});

vi.mock("bcryptjs", () => ({
  default: { compare: vi.fn() },
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

type CapturedNextAuthConfig = {
  jwt: {
    encode: (args: { token?: Record<string, unknown>; maxAge?: number }) => Promise<string>;
  };
};

describe("portal RS256 session JWT policy", () => {
  beforeEach(() => {
    vi.resetModules();
    nextAuthConfig.current = undefined;
    delete process.env.NEXTAUTH_RS256_PRIVATE_KEY;
    delete process.env.VERCEL_ENV;
    delete process.env.MCPFINDER_DEPLOYMENT_ENV;
    delete process.env.DEPLOYMENT_ENV;
    delete process.env.AUTH_ENV;
    process.env.AUTH_ALLOW_EPHEMERAL_KEYS = "true";
    process.env.PORTAL_JWT_ISSUER = "https://portal.example.test";
    process.env.PORTAL_JWT_AUDIENCE = "mcpfinder-runtime";
  });

  it("includes configured issuer and audience claims in the actual NextAuth RS256 JWT", async () => {
    await import("@/auth");
    const capturedConfig = nextAuthConfig.current as CapturedNextAuthConfig;

    const token = await capturedConfig.jwt.encode({
      token: {
        user_id: "user-123",
        tenant_id: "tenant-456",
        email: "user@example.test",
        is_admin: false,
      },
      maxAge: 300,
    });

    const claims = jose.decodeJwt(token);
    expect(claims.iss).toBe("https://portal.example.test");
    expect(claims.aud).toBe("mcpfinder-runtime");
    expect(claims.sub).toBe("user-123");
  });
});
