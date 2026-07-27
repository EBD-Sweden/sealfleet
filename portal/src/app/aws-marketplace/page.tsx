"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Check, Copy, Cloud } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";

export default function AwsMarketplacePage() {
  const router = useRouter();
  const [org, setOrg] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState<{ api_key: string; entitled: boolean; message: string } | null>(null);
  const [copied, setCopied] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch("/api/aws-marketplace/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ org, email, password }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || "Registration failed");
        setLoading(false);
        return;
      }
      setDone({ api_key: data.api_key, entitled: data.entitled, message: data.message });
    } catch {
      setError("Something went wrong. Please try again.");
      setLoading(false);
    }
  }

  if (done) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background p-4">
        <Card className="w-full max-w-md">
          <CardHeader className="text-center">
            <div className="mb-2 flex items-center justify-center gap-2">
              <Check className="h-6 w-6 text-primary" />
              <span className="font-bold text-xl">Subscription linked</span>
            </div>
            <CardDescription>{done.message}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center gap-2 rounded-md border bg-muted p-2">
              <code className="flex-1 truncate text-xs">{done.api_key}</code>
              <Button type="button" variant="outline" size="sm"
                onClick={() => { navigator.clipboard.writeText(done.api_key); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
                {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
              </Button>
            </div>
            <Button className="w-full" onClick={() => { router.push("/"); router.refresh(); }}>
              Go to dashboard
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <div className="mb-2 flex items-center justify-center gap-2">
            <Cloud className="h-6 w-6 text-primary" />
            <span className="font-bold text-xl">Set up Sealfleet</span>
          </div>
          <CardDescription>
            Finish setting up your account for your AWS Marketplace subscription.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="org">Organization name</Label>
              <Input id="org" value={org} onChange={(e) => setOrg(e.target.value)} placeholder="Acme Inc" required autoFocus disabled={loading} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="email">Work email</Label>
              <Input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@acme.com" required disabled={loading} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="At least 8 characters" required minLength={8} disabled={loading} />
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? "Setting up..." : "Create account"}
            </Button>
          </form>
          <p className="mt-4 text-center text-xs text-muted-foreground">
            Billed through AWS Marketplace. Start from your AWS subscription if you landed here directly.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
