"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { signIn } from "next-auth/react";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertCircle, Loader2, ShieldCheck } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  clearPendingSsoLogin,
  loadPendingSsoLogin,
} from "@/lib/sso-client";

const MAX_PENDING_AGE_MS = 10 * 60 * 1000;

function SsoCallbackFallback() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <div className="mb-2 flex items-center justify-center gap-2">
            <ShieldCheck className="h-6 w-6 text-primary" />
            <span className="font-bold text-xl">Sealfleet</span>
          </div>
          <CardTitle>Finishing SSO sign-in</CardTitle>
          <CardDescription>Preparing your organization login.</CardDescription>
        </CardHeader>
        <CardContent className="py-8 text-center text-sm text-muted-foreground">
          <div className="flex flex-col items-center gap-3">
            <Loader2 className="h-6 w-6 animate-spin text-primary" />
            <p>Please wait while we load your SSO callback.</p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function SsoCallbackContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const startedRef = useRef(false);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;

    let cancelled = false;

    async function completeSignIn() {
      const providerError = searchParams.get("error");
      const providerErrorDescription = searchParams.get("error_description");
      const code = searchParams.get("code");
      const state = searchParams.get("state");

      if (providerError) {
        setError(providerErrorDescription || providerError);
        clearPendingSsoLogin();
        return;
      }

      if (!code || !state) {
        setError("The identity provider did not return a valid authorization code.");
        clearPendingSsoLogin();
        return;
      }

      const pending = loadPendingSsoLogin();
      if (!pending) {
        setError("Your SSO session expired before sign-in completed. Please start again.");
        return;
      }

      if (Date.now() - pending.createdAt > MAX_PENDING_AGE_MS) {
        clearPendingSsoLogin();
        setError("Your SSO session expired. Please start again.");
        return;
      }

      if (pending.state !== state) {
        clearPendingSsoLogin();
        setError("SSO state mismatch. Please restart the sign-in flow.");
        return;
      }

      const result = await signIn("sso", {
        email: pending.email,
        code,
        redirect_uri: pending.redirectUri,
        code_verifier: pending.usesPkce ? pending.codeVerifier : undefined,
        redirect: false,
      });

      if (cancelled) return;

      if (result?.error) {
        clearPendingSsoLogin();
        setError(
          "SSO sign-in failed. Check the tenant OIDC config, allowed domains, and mapped claims.",
        );
        return;
      }

      clearPendingSsoLogin();
      router.push("/");
      router.refresh();
    }

    completeSignIn().catch(() => {
      if (!cancelled) {
        clearPendingSsoLogin();
        setError("SSO sign-in failed unexpectedly. Please try again.");
      }
    });

    return () => {
      cancelled = true;
    };
  }, [router, searchParams]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <div className="mb-2 flex items-center justify-center gap-2">
            {error ? (
              <AlertCircle className="h-6 w-6 text-destructive" />
            ) : (
              <ShieldCheck className="h-6 w-6 text-primary" />
            )}
            <span className="font-bold text-xl">Sealfleet</span>
          </div>
          <CardTitle>{error ? "SSO sign-in failed" : "Finishing SSO sign-in"}</CardTitle>
          <CardDescription>
            {error
              ? "We could not complete your tenant sign-in flow."
              : "Completing your organization login now."}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4 text-center">
          {error ? (
            <>
              <p className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
                {error}
              </p>
              <Button asChild className="w-full">
                <Link href="/login">Back to login</Link>
              </Button>
            </>
          ) : (
            <div className="flex flex-col items-center gap-3 py-4 text-sm text-muted-foreground">
              <Loader2 className="h-6 w-6 animate-spin text-primary" />
              <p>Please wait while we verify your tenant identity and create your session.</p>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

export default function SsoCallbackPage() {
  return (
    <Suspense fallback={<SsoCallbackFallback />}>
      <SsoCallbackContent />
    </Suspense>
  );
}
