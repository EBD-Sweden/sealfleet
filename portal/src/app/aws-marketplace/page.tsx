"use client";

import { useState } from "react";
import { Check, Cloud } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";

export default function AwsMarketplacePage() {
  const [org, setOrg] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState<{ message: string } | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch("/api/aws-marketplace/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ org, name, email }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || "Registration failed");
        setLoading(false);
        return;
      }
      setDone({ message: data.message });
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
              <span className="font-bold text-xl">You&apos;re all set</span>
            </div>
            <CardDescription>{done.message}</CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-center text-sm text-muted-foreground">
              Next: you&apos;ll receive a signed license key and the self-host setup
              guide by email. Deploy Sealfleet in your own cloud with the BYOF
              Terraform, then drop in the key to unlock Enterprise features.
            </p>
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
            <span className="font-bold text-xl">Get your self-host license</span>
          </div>
          <CardDescription>
            Thanks for subscribing on AWS Marketplace. Tell us where to send your
            Sealfleet self-host license key.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="org">Organization name</Label>
              <Input id="org" value={org} onChange={(e) => setOrg(e.target.value)} placeholder="Acme Inc" required autoFocus disabled={loading} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="name">Your name</Label>
              <Input id="name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Jane Doe" disabled={loading} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="email">Work email</Label>
              <Input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@acme.com" required disabled={loading} />
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? "Submitting..." : "Send me my license key"}
            </Button>
          </form>
          <p className="mt-4 text-center text-xs text-muted-foreground">
            Licensed through AWS Marketplace. Start from your AWS subscription if you landed here directly.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
