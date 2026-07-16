import { describe, expect, it } from "vitest";

import {
  assertPersistentSigningKeyConfigured,
  isEphemeralSigningKeyAllowed,
} from "@/lib/auth-keys";
import {
  isDangerousEmailAccountLinkingAllowed,
  isPlatformSsoEmailAllowed,
} from "@/lib/auth-policy";

describe("auth hardening policy", () => {
  it("refuses missing portal RS256 keys in production-like environments", () => {
    const env = {
      NODE_ENV: "production",
      AUTH_ALLOW_EPHEMERAL_KEYS: "true",
    };

    expect(() => assertPersistentSigningKeyConfigured(env)).toThrow(
      /NEXTAUTH_RS256_PRIVATE_KEY is required/
    );
    expect(isEphemeralSigningKeyAllowed(env)).toBe(false);
  });

  it("allows ephemeral portal RS256 keys only with an explicit non-production development flag", () => {
    expect(
      isEphemeralSigningKeyAllowed({
        NODE_ENV: "development",
        AUTH_ALLOW_EPHEMERAL_KEYS: "true",
      })
    ).toBe(true);

    expect(
      isEphemeralSigningKeyAllowed({
        NODE_ENV: "development",
      })
    ).toBe(false);
  });

  it("requires an explicit invite or domain allowlist before falling back to the platform tenant", () => {
    expect(isPlatformSsoEmailAllowed("new.user@example.com", {})).toBe(false);
    expect(
      isPlatformSsoEmailAllowed("new.user@example.com", {
        PLATFORM_SSO_ALLOWED_DOMAINS: "example.com, example.com",
      })
    ).toBe(true);
    expect(
      isPlatformSsoEmailAllowed("new.user@example.com", {
        PLATFORM_SSO_ALLOWED_EMAILS: "new.user@example.com",
      })
    ).toBe(true);
  });

  it("disables dangerous email account linking by default", () => {
    expect(isDangerousEmailAccountLinkingAllowed({})).toBe(false);
    expect(
      isDangerousEmailAccountLinkingAllowed({
        AUTH_ALLOW_DANGEROUS_EMAIL_ACCOUNT_LINKING: "true",
      })
    ).toBe(true);
  });
});
