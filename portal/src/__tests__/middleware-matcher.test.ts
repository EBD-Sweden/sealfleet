import { describe, expect, it, vi } from "vitest";

vi.mock("next-auth", () => ({
  default: () => ({
    auth: (handler: unknown) => handler,
  }),
}));

vi.mock("@/auth.config", () => ({
  authConfig: {
    providers: [],
    pages: { signIn: "/login" },
    session: { strategy: "jwt" },
  },
}));

vi.mock("next/server", () => ({
  NextResponse: {
    next: () => ({ status: 200 }),
    json: (body: unknown, init?: ResponseInit) => ({ body, status: init?.status ?? 200 }),
    redirect: (url: URL) => ({ status: 307, url: url.toString() }),
  },
}));

describe("portal middleware matcher", () => {
  it.each(["/api/health", "/api/ready"])("bypasses public probe route %s before auth middleware loads", async (path) => {
    const { config } = await import("@/middleware");
    const matcher = config.matcher[0];
    const regex = new RegExp(`^${matcher}$`);

    expect(regex.test(path)).toBe(false);
  });
});
