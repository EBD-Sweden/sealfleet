/**
 * RS256 keypair management for JWT signing.
 *
 * Loads a private key from the `NEXTAUTH_RS256_PRIVATE_KEY` env var (PKCS8 PEM),
 * or generates an ephemeral keypair for development. The same key is exposed as
 * a JWK via the JWKS endpoint so backend services can verify tokens without a
 * shared secret.
 *
 * Generate a production key with:
 *   openssl genrsa 2048 | openssl pkcs8 -topk8 -nocrypt -out private.pem
 *   export NEXTAUTH_RS256_PRIVATE_KEY="$(cat private.pem)"
 */

import {
  importPKCS8,
  importJWK,
  generateKeyPair,
  exportJWK,
  type CryptoKey,
  type JWK,
} from "jose";

const ALG = "RS256";

type EnvLike = Record<string, string | undefined>;

function truthy(value: string | undefined): boolean {
  return ["1", "true", "yes", "on"].includes((value ?? "").trim().toLowerCase());
}

export function isProductionLikeAuthEnv(env: EnvLike = process.env): boolean {
  const candidates = [
    env.NODE_ENV,
    env.VERCEL_ENV,
    env.MCPFINDER_DEPLOYMENT_ENV,
    env.DEPLOYMENT_ENV,
    env.AUTH_ENV,
  ];
  return candidates.some((value) => {
    const normalized = (value ?? "").trim().toLowerCase().replace(/_/g, "-");
    return normalized === "production" || normalized === "prod" || normalized === "public-test";
  });
}

export function isEphemeralSigningKeyAllowed(env: EnvLike = process.env): boolean {
  return truthy(env.AUTH_ALLOW_EPHEMERAL_KEYS) && !isProductionLikeAuthEnv(env);
}

export function assertPersistentSigningKeyConfigured(env: EnvLike = process.env): void {
  const pem = env.NEXTAUTH_RS256_PRIVATE_KEY;
  if (pem?.trim()) return;
  if (isEphemeralSigningKeyAllowed(env)) return;
  throw new Error(
    "[auth-keys] NEXTAUTH_RS256_PRIVATE_KEY is required unless AUTH_ALLOW_EPHEMERAL_KEYS=true in a non-production development environment."
  );
}

interface KeyMaterial {
  privateKey: CryptoKey;
  publicKey: CryptoKey;
  publicJwk: JWK;
  kid: string;
}

let cached: Promise<KeyMaterial> | null = null;

/**
 * Compute a stable Key ID from the public key SPKI.
 * `kid` = base64url(SHA-256(SPKI))[:8 bytes]
 */
async function computeKid(publicKey: CryptoKey): Promise<string> {
  const spki = await crypto.subtle.exportKey("spki", publicKey);
  const digest = await crypto.subtle.digest("SHA-256", spki);
  const first8 = new Uint8Array(digest).slice(0, 8);
  // base64url, no padding — btoa + String.fromCharCode is edge-compatible; Buffer is not
  let binary = "";
  for (let i = 0; i < first8.length; i++) binary += String.fromCharCode(first8[i]);
  const b64 = btoa(binary);
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * Derive the public CryptoKey from a private RSA key.
 * jose's importPKCS8 only yields the private key, so we round-trip via JWK,
 * strip the private RSA components, and re-import the public half.
 */
async function derivePublicKey(privateKey: CryptoKey): Promise<CryptoKey> {
  const privJwk = await exportJWK(privateKey);
  const pubJwk: JWK = {
    kty: privJwk.kty,
    n: privJwk.n,
    e: privJwk.e,
    alg: ALG,
    use: "sig",
  };
  const key = await importJWK(pubJwk, ALG, { extractable: true });
  // importJWK can return Uint8Array for symmetric keys; for RSA it's a CryptoKey.
  if (!(key as CryptoKey).type) {
    throw new Error("[auth-keys] Expected CryptoKey from importJWK for RS256");
  }
  return key as CryptoKey;
}

async function loadKeyMaterial(): Promise<KeyMaterial> {
  const pem = process.env.NEXTAUTH_RS256_PRIVATE_KEY;

  let privateKey: CryptoKey;
  let publicKey: CryptoKey;

  if (pem && pem.trim().length > 0) {
    privateKey = await importPKCS8(pem, ALG, { extractable: true });
    publicKey = await derivePublicKey(privateKey);
  } else {
    assertPersistentSigningKeyConfigured(process.env);
    console.warn(
      "[auth-keys] NEXTAUTH_RS256_PRIVATE_KEY not set — generating an explicitly enabled ephemeral RS256 keypair for local development only."
    );
    const kp = await generateKeyPair(ALG, { extractable: true });
    privateKey = kp.privateKey;
    publicKey = kp.publicKey;
  }

  const kid = await computeKid(publicKey);

  const rawJwk = await exportJWK(publicKey);
  const publicJwk: JWK = {
    ...rawJwk,
    alg: ALG,
    use: "sig",
    kid,
  };

  return { privateKey, publicKey, publicJwk, kid };
}

function getKeyMaterial(): Promise<KeyMaterial> {
  if (!cached) {
    cached = loadKeyMaterial().catch((err) => {
      // Don't cache failures — let the next call retry.
      cached = null;
      throw err;
    });
  }
  return cached;
}

/**
 * Returns the private CryptoKey used to sign JWTs (RS256).
 */
export async function getSigningKey(): Promise<CryptoKey> {
  const { privateKey } = await getKeyMaterial();
  return privateKey;
}

/**
 * Returns the public CryptoKey used to verify JWTs locally.
 */
export async function getVerificationKey(): Promise<CryptoKey> {
  const { publicKey } = await getKeyMaterial();
  return publicKey;
}

/**
 * Returns the public key as a JWK suitable for the JWKS endpoint.
 * Includes `alg`, `use`, and `kid`.
 */
export async function getPublicJwk(): Promise<JWK> {
  const { publicJwk } = await getKeyMaterial();
  return publicJwk;
}

/**
 * Returns the Key ID (`kid`) included in JWT headers and the JWKS entry.
 */
export async function getKid(): Promise<string> {
  const { kid } = await getKeyMaterial();
  return kid;
}

/**
 * The signing algorithm used. Exported as a single source of truth.
 */
export const SIGNING_ALG = ALG;
