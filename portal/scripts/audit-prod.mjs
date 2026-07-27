#!/usr/bin/env node

const REVIEWED_FINDINGS = {
  postcss: {
    sources: new Set([1117015, 1124252, 1124288]),
    reason:
      "Transitive Next.js-bundled PostCSS advisories; no non-breaking patched Next.js release is available yet. Portal does not stringify attacker-controlled CSS or process attacker-controlled source maps.",
  },
  sharp: {
    sources: new Set([1124066]),
    reason:
      "Transitive Next.js image pipeline advisory; no non-breaking patched Next.js release is available yet. Portal does not expose user-uploaded image processing in OSS quickstart.",
  },
  next: {
    viaNames: new Set(["postcss", "sharp"]),
    reason: "Meta finding caused only by the reviewed postcss/sharp transitive advisories above.",
  },
  "next-auth": {
    viaNames: new Set(["next"]),
    reason: "Meta finding inherited only from the reviewed Next.js transitive advisories above.",
  },
};

function viaSources(vulnerability) {
  return (vulnerability.via || [])
    .filter((entry) => typeof entry === "object" && entry !== null && Number.isInteger(entry.source))
    .map((entry) => entry.source);
}

function viaNames(vulnerability) {
  return (vulnerability.via || [])
    .map((entry) => (typeof entry === "string" ? entry : entry?.name))
    .filter(Boolean);
}

function isReviewed(name, vulnerability) {
  const policy = REVIEWED_FINDINGS[name];
  if (!policy) return false;

  if (policy.sources) {
    const sources = viaSources(vulnerability);
    return sources.length > 0 && sources.every((source) => policy.sources.has(source));
  }

  if (policy.viaNames) {
    const names = viaNames(vulnerability);
    return names.length > 0 && names.every((viaName) => policy.viaNames.has(viaName));
  }

  return false;
}

export function evaluateAudit(audit) {
  const vulnerabilities = audit?.vulnerabilities || {};
  const reviewed = [];
  const unreviewed = [];
  const justifications = {};

  for (const [name, vulnerability] of Object.entries(vulnerabilities)) {
    if (isReviewed(name, vulnerability)) {
      reviewed.push(name);
      justifications[name] = REVIEWED_FINDINGS[name].reason;
    } else {
      unreviewed.push(name);
    }
  }

  reviewed.sort();
  unreviewed.sort();
  return {
    passed: unreviewed.length === 0,
    reviewed,
    unreviewed,
    justifications,
  };
}

async function main() {
  let input = "";
  for await (const chunk of process.stdin) input += chunk;

  let audit;
  try {
    audit = JSON.parse(input);
  } catch (error) {
    console.error(`Could not parse npm audit JSON: ${error.message}`);
    process.exit(2);
  }

  const result = evaluateAudit(audit);
  console.log(JSON.stringify(result, null, 2));
  process.exit(result.passed ? 0 : 1);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main();
}
