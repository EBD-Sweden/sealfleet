"use client";

import { useEffect, useState } from "react";
import { CreditCard, Loader2 } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

interface Plan {
  key: string;
  label: string;
  blurb: string;
  metered: boolean;
}

interface Status {
  billing_enabled: boolean;
  status: string;
  entitled: boolean;
  plan: string | null;
  current_period_end: string | null;
  has_customer: boolean;
  usage_this_month: number;
  plans: Plan[];
}

const STATUS_LABEL: Record<string, string> = {
  active: "Active",
  trialing: "Trial",
  past_due: "Past due",
  canceled: "Canceled",
  inactive: "No subscription",
};

export default function BillingPage() {
  const [status, setStatus] = useState<Status | null>(null);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/billing/status")
      .then((r) => r.json())
      .then(setStatus)
      .catch(() => setError("Could not load billing status"))
      .finally(() => setLoading(false));
  }, []);

  async function go(path: string, body?: unknown) {
    setActing(true);
    setError("");
    try {
      const res = await fetch(path, {
        method: "POST",
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.url) {
        setError(data.error || "Something went wrong");
        setActing(false);
        return;
      }
      window.location.assign(data.url);
    } catch {
      setError("Something went wrong");
      setActing(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl p-6">
      <div className="mb-6 flex items-center gap-2">
        <CreditCard className="h-6 w-6 text-primary" />
        <h1 className="text-2xl font-bold">Billing</h1>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading…
        </div>
      ) : !status?.billing_enabled ? (
        <Card>
          <CardHeader>
            <CardTitle>Billing not configured</CardTitle>
            <CardDescription>
              This deployment has no Stripe configuration. For the hosted service,
              set STRIPE_SECRET_KEY and STRIPE_PRICE_ENTERPRISE. See docs/BILLING.md.
            </CardDescription>
          </CardHeader>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>Sealfleet Enterprise</CardTitle>
            <CardDescription>
              Status: <span className="font-medium">{STATUS_LABEL[status.status] ?? status.status}</span>
              {status.current_period_end && status.entitled ? (
                <> · renews {new Date(status.current_period_end).toLocaleDateString()}</>
              ) : null}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="text-sm text-muted-foreground">
              API calls this month: <span className="font-medium text-foreground">{status.usage_this_month.toLocaleString()}</span>
            </div>

            {error && <p className="text-sm text-destructive">{error}</p>}

            {status.entitled ? (
              <Button onClick={() => go("/api/billing/portal")} disabled={acting}>
                {acting ? "Opening…" : "Manage subscription"}
              </Button>
            ) : (
              <div className="space-y-3">
                <p className="text-sm font-medium">Choose a plan to get started</p>
                <div className="grid gap-3 sm:grid-cols-3">
                  {status.plans.map((p) => (
                    <div key={p.key} className="flex flex-col rounded-lg border p-4">
                      <div className="font-medium">{p.label}</div>
                      <p className="mt-1 flex-1 text-xs text-muted-foreground">{p.blurb}</p>
                      <Button
                        className="mt-3"
                        size="sm"
                        variant={p.key === "usage" ? "outline" : "default"}
                        onClick={() => go("/api/billing/checkout", { plan: p.key })}
                        disabled={acting}
                      >
                        {acting ? "Starting…" : p.metered ? "Start" : "Subscribe"}
                      </Button>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
