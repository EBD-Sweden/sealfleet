import type { NextAuthConfig } from "next-auth";
import * as jose from "jose";
import {
  getSigningKey,
  getVerificationKey,
  getKid,
  SIGNING_ALG,
} from "@/lib/auth-keys";

export const authConfig: NextAuthConfig = {
  providers: [],
  pages: { signIn: "/login" },
  session: { strategy: "jwt" },
  jwt: {
    async decode({ token }) {
      if (!token) return null;
      try {
        const key = await getVerificationKey();
        const { payload } = await jose.jwtVerify(token, key, {
          algorithms: [SIGNING_ALG],
        });
        return payload as unknown as import("@auth/core/jwt").JWT;
      } catch {
        return null;
      }
    },
    // The edge middleware DOES encode: next-auth refreshes the session cookie
    // (rolling expiry) once a token is older than `updateAge` (24h). Throwing
    // here turned every >24h-old session into a JWTSessionError and bounced
    // the user back to /login — an apparent login loop. Mirror auth.ts's
    // RS256 encode (auth-keys is edge-compatible: jose + crypto.subtle only).
    async encode({ token, maxAge }) {
      const now = Math.floor(Date.now() / 1000);
      const ttl = typeof maxAge === "number" ? maxAge : 30 * 24 * 60 * 60;

      const key = await getSigningKey();
      const kid = await getKid();

      const payload = { ...(token ?? {}) } as jose.JWTPayload;

      const signer = new jose.SignJWT(payload)
        .setProtectedHeader({ alg: SIGNING_ALG, kid, typ: "JWT" })
        .setIssuedAt(now)
        .setExpirationTime(now + ttl);

      const issuer = process.env.PORTAL_JWT_ISSUER || process.env.NEXTAUTH_ISSUER;
      if (issuer) signer.setIssuer(issuer);
      const audience =
        process.env.PORTAL_JWT_AUDIENCE || process.env.NEXTAUTH_AUDIENCE;
      if (audience) signer.setAudience(audience);

      const sub =
        (token as { sub?: string; user_id?: string } | undefined)?.sub ??
        (token as { user_id?: string } | undefined)?.user_id;
      if (sub) signer.setSubject(sub);

      return await signer.sign(key);
    },
  },
};
