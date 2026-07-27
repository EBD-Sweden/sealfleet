import { describe, expect, it } from "vitest";

import { evaluateAudit } from "../../scripts/audit-prod.mjs";

const residualNextAudit = {
  vulnerabilities: {
    next: { name: "next", via: ["postcss", "sharp"] },
    "next-auth": { name: "next-auth", via: ["next"] },
    postcss: { name: "postcss", via: [{ source: 1117015 }, { source: 1124252 }, { source: 1124288 }] },
    sharp: { name: "sharp", via: [{ source: 1124066 }] },
  },
};

const authCoreAudit = {
  vulnerabilities: {
    "@auth/core": {
      name: "@auth/core",
      via: [{ source: 1124245 }, { source: 1124248 }, { source: 1124251 }],
    },
    "next-auth": {
      name: "next-auth",
      via: ["@auth/core", { source: 1124242 }, { source: 1124244 }, { source: 1124246 }, { source: 1124250 }],
    },
  },
};

describe("production audit policy", () => {
  it("accepts only the reviewed Next.js transitive findings while upstream has no patched release", () => {
    const result = evaluateAudit(residualNextAudit);

    expect(result.passed).toBe(true);
    expect(result.reviewed).toEqual(["next", "next-auth", "postcss", "sharp"]);
    expect(result.unreviewed).toEqual([]);
  });

  it("fails closed on unrelated production advisories", () => {
    const result = evaluateAudit({
      vulnerabilities: {
        "@anthropic-ai/sdk": {
          name: "@anthropic-ai/sdk",
          via: [{ source: 1117064 }],
        },
      },
    });

    expect(result.passed).toBe(false);
    expect(result.unreviewed).toEqual(["@anthropic-ai/sdk"]);
  });

  it("fails closed on Auth.js advisories instead of justifying auth bypass or DoS risk", () => {
    const result = evaluateAudit(authCoreAudit);

    expect(result.passed).toBe(false);
    expect(result.unreviewed).toEqual(["@auth/core", "next-auth"]);
  });
});
