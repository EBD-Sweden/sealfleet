import { describe, expect, it, vi } from "vitest";
import { isPublicPortalPath } from "@/lib/portal-route-policy";

describe("portal route default-deny policy", () => {
  it.each([
    "/login",
    "/api/auth/signin",
    "/api/auth/callback/credentials",
    "/api/sso/start",
    "/api/.well-known/jwks.json",
    "/api/.well-known/oauth-protected-resource",
    "/api/health",
    "/api/ready",
  ])("explicitly allows public path %s", (path) => {
    expect(isPublicPortalPath(path)).toBe(true);
  });

  it.each([
    "/api/sealed",
    "/api/credentials",
    "/api/deploy",
    "/api/policy/check",
    "/api/call",
    "/api/audit",
    "/api/pipelines/us-value-mpt/run",
    "/api/authenticate",
    "/api/sso/start-admin",
    "/api/sso/start/admin",
    "/api/authenticate",
    "/api/auth-admin/signin",
    "/_next-admin/static/chunk.js",
  ])("default-denies sensitive API path %s", (path) => {
    expect(isPublicPortalPath(path)).toBe(false);
  });
});

describe("PORTAL_EXTRA_PUBLIC_PATHS overlay", () => {
  it("whitelists env-declared exact paths and ignores malformed entries", async () => {
    vi.resetModules();
    process.env.PORTAL_EXTRA_PUBLIC_PATHS = "/partner/callback, not-a-path ,/other/hook";
    const fresh = await import("@/lib/portal-route-policy");
    expect(fresh.isPublicPortalPath("/partner/callback")).toBe(true);
    expect(fresh.isPublicPortalPath("/other/hook")).toBe(true);
    expect(fresh.isPublicPortalPath("not-a-path")).toBe(false);
    expect(fresh.isPublicPortalPath("/partner/callback/child")).toBe(false);
    delete process.env.PORTAL_EXTRA_PUBLIC_PATHS;
    vi.resetModules();
  });
});
