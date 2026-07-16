type EnvLike = Record<string, string | undefined>;

export function parseCsv(value: string | undefined): string[] {
  return (value ?? "")
    .split(",")
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);
}

function truthy(value: string | undefined): boolean {
  return ["1", "true", "yes", "on"].includes((value ?? "").trim().toLowerCase());
}

export function isPlatformAdminEmail(
  email: string,
  env: EnvLike = process.env
): boolean {
  return parseCsv(env.PLATFORM_ADMIN_EMAILS).includes(email.trim().toLowerCase());
}

/**
 * Platform-tenant SSO fallback is invite-only by default.
 *
 * If an email domain does not map to an enterprise tenant, Google/Azure sign-in
 * may only fall back to the platform tenant when the email or domain appears in
 * an explicit public-test/demo allowlist. This prevents arbitrary external
 * Google/Microsoft accounts from self-provisioning platform users.
 */
export function isPlatformSsoEmailAllowed(
  email: string,
  env: EnvLike = process.env
): boolean {
  const normalizedEmail = email.trim().toLowerCase();
  const domain = normalizedEmail.split("@")[1];
  if (!normalizedEmail || !domain) return false;

  const allowedEmails = parseCsv(env.PLATFORM_SSO_ALLOWED_EMAILS);
  if (allowedEmails.includes(normalizedEmail)) return true;

  const allowedDomains = parseCsv(env.PLATFORM_SSO_ALLOWED_DOMAINS);
  return allowedDomains.includes(domain);
}

/**
 * Auth.js dangerous email account linking is disabled unless an operator opts in.
 * Keep default fail-closed because linking by email is only safe when every
 * enabled IdP has verified email ownership and issuer trust boundaries are clear.
 */
export function isDangerousEmailAccountLinkingAllowed(
  env: EnvLike = process.env
): boolean {
  return truthy(env.AUTH_ALLOW_DANGEROUS_EMAIL_ACCOUNT_LINKING);
}
