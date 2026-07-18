const PUBLIC_EXACT_PATHS = new Set([
  "/",
  "/login",
  "/signup",
  "/favicon.ico",
  "/api/health",
  "/api/ready",
  "/api/.well-known/jwks.json",
  "/api/.well-known/oauth-protected-resource",
  "/api/sso/start",
  "/api/signup",
  // Stripe calls this server-to-server; it authenticates via signature, not session.
  "/api/billing/webhook",
  // The usage-report cron calls this with a shared-secret header, not a session.
  "/api/billing/report-usage",
]);

// Deployments may whitelist additional exact public paths (e.g. an OAuth
// callback for a private page) without editing this file. Comma-separated,
// each entry must start with "/". Read at runtime in middleware.
for (const p of (process.env.PORTAL_EXTRA_PUBLIC_PATHS ?? "").split(",")) {
  const trimmed = p.trim();
  if (trimmed.startsWith("/")) PUBLIC_EXACT_PATHS.add(trimmed);
}

const PUBLIC_PATH_FAMILIES = [
  "/_next",
  "/api/auth",
];

function isPathFamilyMember(pathname: string, familyRoot: string): boolean {
  return pathname === familyRoot || pathname.startsWith(`${familyRoot}/`);
}

export function isPublicPortalPath(pathname: string): boolean {
  if (PUBLIC_EXACT_PATHS.has(pathname)) return true;
  return PUBLIC_PATH_FAMILIES.some((familyRoot) => isPathFamilyMember(pathname, familyRoot));
}

export function isApiPath(pathname: string): boolean {
  return pathname === "/api" || pathname.startsWith("/api/");
}
