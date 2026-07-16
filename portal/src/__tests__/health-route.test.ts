import { describe, expect, it, vi } from "vitest";

const jsonMock = vi.fn((body: unknown, init?: ResponseInit) => ({
  body,
  status: init?.status ?? 200,
  headers: init?.headers ?? {},
}));

vi.mock("next/server", () => ({
  NextResponse: {
    json: jsonMock,
  },
}));

describe("portal smoke health route", () => {
  it("returns a public bounded health payload for external smoke checks", async () => {
    const { GET } = await import("@/app/api/health/route");

    const response = await GET();

    expect(response.status).toBe(200);
    expect(response.body).toMatchObject({
      status: "ok",
      service: "mcpfinder-portal",
      public: true,
    });
    expect(response.body).not.toHaveProperty("env");
    expect(response.body).not.toHaveProperty("secrets");
  });

  it("returns a public readiness payload without secrets", async () => {
    const { GET } = await import("@/app/api/ready/route");

    const response = await GET();

    expect(response.status).toBe(200);
    expect(response.body).toMatchObject({
      status: "ready",
      service: "mcpfinder-portal",
      public: true,
    });
    expect(response.body).not.toHaveProperty("env");
    expect(response.body).not.toHaveProperty("secrets");
  });
});
