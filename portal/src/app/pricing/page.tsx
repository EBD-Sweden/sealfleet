// Public pricing page — visible without login so prospects can see plans before
// signing up. Amounts are read live from Stripe (server-side) and the page is
// ISR-cached (revalidate) so anonymous traffic doesn't hammer the Stripe API.
// Checkout requires an account, so every CTA routes to /signup.

import Link from "next/link";
import { Check, ArrowRight } from "lucide-react";
import { planDisplays, stripeConfigured } from "@/lib/stripe";
import { Button } from "@/components/ui/button";

export const runtime = "nodejs";
// Render at request time so runtime env (Stripe secret + price IDs, injected from
// Secret Manager) is available. Static build-time prerender would see no Stripe
// config and bake in the empty state. planDisplays() caches Stripe reads (10 min),
// so this stays cheap under anonymous traffic.
export const dynamic = "force-dynamic";

export const metadata = {
  title: "Pricing — Sealfleet",
  description: "Sealfleet hosted plans and self-host licensing.",
};

const PLAN_FEATURES: Record<string, string[]> = {
  monthly: ["Hosted, fully managed", "Generous monthly call allowance", "Cancel anytime"],
  annual: ["Everything in Monthly", "Two months free vs monthly", "Priority onboarding"],
  usage: ["No base fee", "Pay only for API calls", "Easiest way to start"],
};

export default async function PricingPage() {
  const plans = stripeConfigured() ? await planDisplays().catch(() => []) : [];

  return (
    <div className="min-h-screen bg-background">
      {/* top bar */}
      <header className="flex items-center justify-between border-b px-6 py-4">
        <Link href="/" className="text-lg font-bold">Sealfleet</Link>
        <div className="flex items-center gap-3">
          <Link href="/login" className="text-sm text-muted-foreground hover:text-foreground">Sign in</Link>
          <Button asChild size="sm"><Link href="/signup">Get started</Link></Button>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-6 py-16">
        <div className="mb-12 text-center">
          <h1 className="text-4xl font-bold tracking-tight">Simple, transparent pricing</h1>
          <p className="mx-auto mt-3 max-w-2xl text-muted-foreground">
            Start on the fully managed cloud in minutes, or self-host in your own
            infrastructure. No hidden fees.
          </p>
        </div>

        {/* hosted plans */}
        {plans.length > 0 ? (
          <div className="grid gap-6 md:grid-cols-3">
            {plans.map((p) => {
              const featured = p.key === "annual";
              return (
                <div
                  key={p.key}
                  className={`flex flex-col rounded-2xl border p-6 ${featured ? "border-primary shadow-lg ring-1 ring-primary" : ""}`}
                >
                  {featured && (
                    <div className="mb-2 self-start rounded-full bg-primary px-3 py-0.5 text-xs font-medium text-primary-foreground">
                      Best value
                    </div>
                  )}
                  <h2 className="text-lg font-semibold">{p.label}</h2>
                  <div className="mt-3 flex items-baseline gap-1">
                    {p.amountLabel ? (
                      <>
                        <span className="text-3xl font-bold">{p.amountLabel}</span>
                        <span className="text-sm text-muted-foreground">{p.intervalLabel}</span>
                      </>
                    ) : (
                      <span className="text-xl font-semibold text-muted-foreground">{p.blurb}</span>
                    )}
                  </div>
                  <p className="mt-2 text-sm text-muted-foreground">{p.blurb}</p>
                  <ul className="mt-4 flex-1 space-y-2 text-sm">
                    {(PLAN_FEATURES[p.key] ?? []).map((f) => (
                      <li key={f} className="flex items-start gap-2">
                        <Check className="mt-0.5 h-4 w-4 shrink-0 text-primary" /> {f}
                      </li>
                    ))}
                  </ul>
                  <Button asChild className="mt-6" variant={featured ? "default" : "outline"}>
                    <Link href={`/signup?plan=${p.key}`}>
                      {p.metered ? "Start" : "Subscribe"} <ArrowRight className="ml-1 h-4 w-4" />
                    </Link>
                  </Button>
                </div>
              );
            })}
          </div>
        ) : (
          <p className="text-center text-muted-foreground">
            Plans are being finalized. <Link href="/signup" className="underline">Create an account</Link> to get started.
          </p>
        )}

        {/* self-host / enterprise */}
        <div className="mt-10 rounded-2xl border bg-muted/30 p-8 md:flex md:items-center md:justify-between">
          <div>
            <h2 className="text-xl font-semibold">Self-host &amp; Enterprise</h2>
            <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
              Run Sealfleet in your own cloud (AWS, GCP, or on-prem) with an annual
              license — SSO/SCIM, full audit, and support included. Available directly
              or through AWS Marketplace.
            </p>
          </div>
          <Button asChild variant="outline" className="mt-4 md:mt-0">
            <Link href="/signup?plan=enterprise">Contact sales</Link>
          </Button>
        </div>

        <p className="mt-8 text-center text-xs text-muted-foreground">
          Prices shown exclude applicable taxes. Hosted plans are billed through Stripe.
        </p>
      </main>
    </div>
  );
}
