import Link from "next/link";
import { Cloud, Server, Boxes, ShieldCheck, GitBranch, ArrowRight } from "lucide-react";

const DEPLOY = [
  {
    icon: Cloud,
    title: "Managed cloud",
    body: "Fully-hosted SaaS on scale-to-zero infrastructure. Sign up and go — no ops, pay for what you use.",
  },
  {
    icon: Server,
    title: "Self-host",
    body: "Run it yourself with Docker or the Helm chart in your own Kubernetes. Your infra, your data.",
  },
  {
    icon: Boxes,
    title: "BYOF · AWS",
    body: "Bring-your-own-cloud on AWS — one Terraform apply provisions EKS, RDS, KMS and secrets in your account.",
  },
  {
    icon: Boxes,
    title: "BYOF · GCP",
    body: "Bring-your-own-cloud on GCP — Cloud Run (scale-to-zero) or GKE + Cloud SQL, provisioned by Terraform.",
  },
];

const FEATURES = [
  "Runtime router, registry & policy for MCP tools",
  "Enterprise SSO / SCIM, multi-tenant RBAC",
  "Tamper-evident audit + full observability",
  "Sealed credentials — never exposed to the model",
];

export function Landing() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Nav */}
      <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
        <div className="flex items-center gap-2 font-bold text-lg">
          <span className="inline-block h-6 w-6 rounded bg-primary" />
          Sealfleet
        </div>
        <nav className="flex items-center gap-3 text-sm">
          <a
            href="https://github.com/EBD-Sweden/sealfleet"
            className="hidden items-center gap-1.5 text-muted-foreground hover:text-foreground sm:flex"
          >
            <GitBranch className="h-4 w-4" /> GitHub
          </a>
          <Link href="/login" className="text-muted-foreground hover:text-foreground">
            Sign in
          </Link>
          <Link
            href="/signup"
            className="rounded-md bg-primary px-3 py-1.5 font-medium text-primary-foreground hover:opacity-90"
          >
            Get started
          </Link>
        </nav>
      </header>

      {/* Hero */}
      <section className="mx-auto max-w-6xl px-6 pb-16 pt-10 text-center sm:pt-20">
        <div className="mx-auto mb-4 inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs text-muted-foreground">
          Open-core · Apache-2.0
        </div>
        <h1 className="mx-auto max-w-3xl text-4xl font-bold tracking-tight sm:text-5xl">
          The MCP agent platform
        </h1>
        <p className="mx-auto mt-5 max-w-2xl text-lg text-muted-foreground">
          Run agent-callable tools in production — secure execution, policy enforcement, and full
          observability. Registry, runtime router, and enterprise controls built in. Deploy it on
          any cloud, or let us host it.
        </p>
        <div className="mt-8 flex items-center justify-center gap-3">
          <Link
            href="/signup"
            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-5 py-2.5 font-medium text-primary-foreground hover:opacity-90"
          >
            Get started <ArrowRight className="h-4 w-4" />
          </Link>
          <a
            href="https://github.com/EBD-Sweden/sealfleet"
            className="rounded-md border px-5 py-2.5 font-medium hover:bg-muted"
          >
            View on GitHub
          </a>
        </div>
      </section>

      {/* Deploy your way */}
      <section className="mx-auto max-w-6xl px-6 py-12">
        <h2 className="text-center text-2xl font-semibold">Deploy your way</h2>
        <p className="mx-auto mt-2 max-w-xl text-center text-sm text-muted-foreground">
          The same platform, wherever you need it — from a fully-managed SaaS to your own cloud
          account.
        </p>
        <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {DEPLOY.map((d) => (
            <div key={d.title} className="rounded-lg border bg-card p-5">
              <d.icon className="h-6 w-6 text-primary" />
              <h3 className="mt-3 font-semibold">{d.title}</h3>
              <p className="mt-1.5 text-sm text-muted-foreground">{d.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Features */}
      <section className="mx-auto max-w-6xl px-6 py-12">
        <div className="rounded-xl border bg-card p-8">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-primary" />
            <h2 className="text-xl font-semibold">Enterprise-ready core</h2>
          </div>
          <div className="mt-6 grid gap-3 sm:grid-cols-2">
            {FEATURES.map((f) => (
              <div key={f} className="flex items-start gap-2 text-sm">
                <span className="mt-1.5 inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
                {f}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="mx-auto max-w-6xl px-6 py-10 text-sm text-muted-foreground">
        <div className="flex flex-col items-center justify-between gap-3 border-t pt-6 sm:flex-row">
          <span>© EBD Sweden · Sealfleet</span>
          <div className="flex items-center gap-4">
            <a href="https://github.com/EBD-Sweden/sealfleet" className="hover:text-foreground">
              GitHub
            </a>
            <Link href="/docs" className="hover:text-foreground">
              Docs
            </Link>
            <a href="mailto:sales@sealfleet.example.com" className="hover:text-foreground">
              Contact sales
            </a>
          </div>
        </div>
      </footer>
    </div>
  );
}
