import { describe, it, expect } from "vitest";

// Test the policy check API response shape
describe("Policy API", () => {
  it("policy rules response has correct shape", () => {
    const mockResponse = {
      rules: [
        {
          id: "block-delete",
          match: { tool_pattern: "delete_*" },
          action: "deny",
          reason: "Destructive tools blocked",
        },
      ],
      count: 1,
    };
    expect(mockResponse.rules).toBeInstanceOf(Array);
    expect(mockResponse.count).toBe(mockResponse.rules.length);
    expect(mockResponse.rules[0]).toHaveProperty("action");
  });

  it("policy check result has action field", () => {
    const result = { action: "allow", rule_id: "default", reason: "" };
    expect(["allow", "deny", "require_confirm"]).toContain(result.action);
  });
});
