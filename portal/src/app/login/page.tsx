"use client";

import { useState } from "react";
import { signIn } from "next-auth/react";
import { useRouter } from "next/navigation";
import { Building2, Loader2, Zap } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { generatePkcePair, savePendingSsoLogin } from "@/lib/sso-client";

function GoogleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg">
      <path
        fill="#EA4335"
        d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"
      />
      <path
        fill="#4285F4"
        d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"
      />
      <path
        fill="#FBBC05"
        d="M10.53 28.59a14.5 14.5 0 0 1 0-9.18l-7.98-6.19a24.08 24.08 0 0 0 0 21.56l7.98-6.19z"
      />
      <path
        fill="#34A853"
        d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"
      />
    </svg>
  );
}

export default function LoginPage() {
  const router = useRouter();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [googleLoading, setGoogleLoading] = useState(false);
  const [microsoftLoading, setMicrosoftLoading] = useState(false);
  const [ssoLoading, setSsoLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const result = await signIn("credentials", {
        email: identifier,
        password,
        redirect: false,
      });

      if (result?.error) {
        setError("Invalid username/email or password");
      } else {
        router.push("/");
        router.refresh();
      }
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  function handleGoogleSignIn() {
    setGoogleLoading(true);
    setMicrosoftLoading(false);
    setSsoLoading(false);
    setError("");
    signIn("google", { callbackUrl: "/" });
  }

  function handleMicrosoftSignIn() {
    setMicrosoftLoading(true);
    setGoogleLoading(false);
    setSsoLoading(false);
    setError("");
    signIn("azure-ad", { callbackUrl: "/" });
  }

  async function handleTenantSsoSignIn() {
    if (!identifier.includes("@")) {
      setError("Enter your work email first, then continue with your organization.");
      return;
    }

    setSsoLoading(true);
    setGoogleLoading(false);
    setMicrosoftLoading(false);
    setError("");

    try {
      const state = crypto.randomUUID();
      const normalizedEmail = identifier.trim().toLowerCase();
      const { codeVerifier, codeChallenge } = await generatePkcePair();

      const res = await fetch("/api/sso/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: normalizedEmail,
          state,
          code_challenge: codeChallenge,
        }),
      });

      const data = (await res.json().catch(() => ({}))) as {
        authorizationUrl?: string;
        redirectUri?: string;
        tenantName?: string;
        usesPkce?: boolean;
        error?: string;
      };

      if (!res.ok || !data.authorizationUrl || !data.redirectUri || !data.tenantName) {
        throw new Error(data.error || "Tenant SSO is not configured for this email domain yet.");
      }

      savePendingSsoLogin({
        email: normalizedEmail,
        state,
        redirectUri: data.redirectUri,
        tenantName: data.tenantName,
        codeVerifier,
        usesPkce: Boolean(data.usesPkce),
        createdAt: Date.now(),
      });

      window.location.assign(data.authorizationUrl);
    } catch (e) {
      setSsoLoading(false);
      setError(e instanceof Error ? e.message : "Unable to start tenant SSO right now.");
    }
  }

  const providerBusy = loading || googleLoading || microsoftLoading || ssoLoading;

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <div className="mb-2 flex items-center justify-center gap-2">
            <Zap className="h-6 w-6 text-primary" />
            <span className="font-bold text-xl">Sealfleet</span>
          </div>
          <CardTitle>Sign in</CardTitle>
          <CardDescription>
            Sign in with your credentials, a shared provider, or your tenant SSO.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="identifier">Username or email</Label>
              <Input
                id="identifier"
                type="text"
                placeholder="admin or you@example.com"
                value={identifier}
                onChange={(e) => setIdentifier(e.target.value)}
                required
                autoFocus
                disabled={providerBusy}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                disabled={providerBusy}
              />
            </div>
            {error && (
              <p className="text-sm text-destructive">{error}</p>
            )}
            <Button type="submit" className="w-full" disabled={providerBusy}>
              {loading ? "Signing in..." : "Sign in"}
            </Button>
          </form>

          <div className="relative my-4">
            <div className="absolute inset-0 flex items-center">
              <span className="w-full border-t" />
            </div>
            <div className="relative flex justify-center text-xs uppercase">
              <span className="bg-card px-2 text-muted-foreground">or</span>
            </div>
          </div>

          <div className="space-y-2">
            <button
              type="button"
              onClick={handleTenantSsoSignIn}
              disabled={providerBusy}
              className="flex w-full items-center justify-center gap-3 rounded-md border border-input bg-background px-4 py-2.5 text-sm font-medium shadow-sm transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50"
            >
              {ssoLoading ? <Loader2 className="h-[18px] w-[18px] animate-spin" /> : <Building2 className="h-[18px] w-[18px]" />}
              {ssoLoading ? "Redirecting to your organization..." : "Continue with your organization"}
            </button>
            <p className="text-xs text-muted-foreground">
              Uses the tenant OIDC config matched from your work email domain.
            </p>

            <button
              type="button"
              onClick={handleGoogleSignIn}
              disabled={providerBusy}
              className="flex w-full items-center justify-center gap-3 rounded-md border border-input bg-white px-4 py-2.5 text-sm font-medium text-gray-700 shadow-sm transition-colors hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50"
            >
              <GoogleIcon />
              {googleLoading ? "Redirecting..." : "Continue with Google"}
            </button>

            <button
              type="button"
              onClick={handleMicrosoftSignIn}
              disabled={providerBusy}
              className="flex w-full items-center justify-center gap-3 rounded-md border border-input bg-[#2f2f2f] px-4 py-2.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-[#1a1a1a] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50"
            >
              <svg className="h-[18px] w-[18px]" viewBox="0 0 21 21" fill="none" xmlns="http://www.w3.org/2000/svg">
                <rect x="1" y="1" width="9" height="9" fill="#F25022"/>
                <rect x="11" y="1" width="9" height="9" fill="#7FBA00"/>
                <rect x="1" y="11" width="9" height="9" fill="#00A4EF"/>
                <rect x="11" y="11" width="9" height="9" fill="#FFB900"/>
              </svg>
              {microsoftLoading ? "Redirecting..." : "Continue with Microsoft"}
            </button>
          </div>

          <p className="mt-6 text-center text-sm text-muted-foreground">
            Don&apos;t have an account?{" "}
            <a href="/signup" className="font-medium text-primary hover:underline">
              Create one
            </a>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
