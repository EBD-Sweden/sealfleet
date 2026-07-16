import { beforeEach, describe, expect, it, vi } from "vitest";

const authMock = vi.fn();

vi.mock("@/auth", () => ({
  auth: authMock,
}));

const makeRequest = (url: string, init?: RequestInit) =>
  new Request(url, init) as never;

describe("portal API default-deny auth", () => {
  beforeEach(() => {
    vi.resetModules();
    authMock.mockReset();
    global.fetch = vi.fn() as unknown as typeof fetch;
  });

  it.each([
    ["sealed list", async () => (await import("@/app/api/sealed/route")).GET()],
    [
      "sealed create",
      async () =>
        (await import("@/app/api/sealed/route")).POST(
          makeRequest("http://portal.test/api/sealed", {
            method: "POST",
            body: JSON.stringify({ label: "api_key", value: "secret" }),
          }),
        ),
    ],
    [
      "credentials list",
      async () => (await import("@/app/api/credentials/route")).GET(),
    ],
    [
      "deploy mutation",
      async () =>
        (await import("@/app/api/deploy/route")).POST(
          makeRequest("http://portal.test/api/deploy", {
            method: "POST",
            body: JSON.stringify({ name: "qa", image: "busybox", port: 9999 }),
          }),
        ),
    ],
    [
      "policy check",
      async () =>
        (await import("@/app/api/policy/check/route")).POST(
          makeRequest("http://portal.test/api/policy/check", {
            method: "POST",
            body: JSON.stringify({ mcp: "crypto-trading-mcp", tool: "execute_trade" }),
          }),
        ),
    ],
    [
      "runtime call",
      async () =>
        (await import("@/app/api/call/route")).POST(
          makeRequest("http://portal.test/api/call", {
            method: "POST",
            body: JSON.stringify({ mcp: "x", tool: "y", inputs: {} }),
          }),
        ),
    ],
  ])("rejects unauthenticated %s before proxying", async (_name, invoke) => {
    authMock.mockResolvedValue(null);

    const response = await invoke();

    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({ error: "Unauthorized" });
    expect(global.fetch).not.toHaveBeenCalled();
  });
});
