export const PENDING_SSO_KEY = "mcpfinder.pending-sso";

export interface PendingSsoLogin {
  email: string;
  state: string;
  redirectUri: string;
  tenantName: string;
  codeVerifier: string;
  usesPkce: boolean;
  createdAt: number;
}

function encodeBase64Url(bytes: Uint8Array): string {
  const binary = Array.from(bytes, (byte) => String.fromCharCode(byte)).join("");
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function randomBase64Url(byteLength = 32): string {
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  return encodeBase64Url(bytes);
}

export async function generatePkcePair(): Promise<{
  codeVerifier: string;
  codeChallenge: string;
}> {
  const codeVerifier = randomBase64Url(48);
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(codeVerifier),
  );

  return {
    codeVerifier,
    codeChallenge: encodeBase64Url(new Uint8Array(digest)),
  };
}

export function savePendingSsoLogin(payload: PendingSsoLogin) {
  sessionStorage.setItem(PENDING_SSO_KEY, JSON.stringify(payload));
}

export function loadPendingSsoLogin(): PendingSsoLogin | null {
  const raw = sessionStorage.getItem(PENDING_SSO_KEY);
  if (!raw) return null;

  try {
    return JSON.parse(raw) as PendingSsoLogin;
  } catch {
    return null;
  }
}

export function clearPendingSsoLogin() {
  sessionStorage.removeItem(PENDING_SSO_KEY);
}
