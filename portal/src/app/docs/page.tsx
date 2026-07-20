"use client";

import { useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import Link from "next/link";
import {
  BookOpen,
  Rocket,
  MessageCircle,
  Server,
  FlaskConical,
  Workflow,
  ShieldCheck,
  FileCode,
  Zap,
  ScrollText,
  ChevronRight,
  ArrowDown,
  Container,
} from "lucide-react";

const sections = [
  { id: "overview", label: "Overview", icon: BookOpen },
  { id: "quickstart", label: "Quick Start", icon: Zap },
  { id: "ask", label: "Ask Agent", icon: MessageCircle },
  { id: "catalog", label: "Catalog", icon: Server },
  { id: "test", label: "Test Console", icon: FlaskConical },
  { id: "pipeline", label: "Pipeline", icon: Workflow },
  { id: "deploy", label: "Deploy", icon: Rocket },
  { id: "audit", label: "Audit Log", icon: ShieldCheck },
  { id: "create", label: "Create MCP", icon: FileCode },
  { id: "transport", label: "Transport", icon: Container },
  { id: "api", label: "API Reference", icon: ScrollText },
  { id: "security", label: "Security", icon: ShieldCheck },
] as const;

type SectionId = (typeof sections)[number]["id"];

function CodeBlock({ children, title }: { children: string; title?: string }) {
  return (
    <div className="my-3">
      {title && (
        <div className="rounded-t-lg bg-slate-800 px-4 py-1.5 text-xs font-medium text-slate-400">
          {title}
        </div>
      )}
      <pre
        className={`overflow-x-auto ${title ? "rounded-b-lg" : "rounded-lg"} bg-slate-950 p-4 font-mono text-xs leading-relaxed text-slate-50`}
      >
        {children}
      </pre>
    </div>
  );
}

function MethodBadge({ method }: { method: "GET" | "POST" | "DELETE" }) {
  const colors = {
    GET: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300",
    POST: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300",
    DELETE: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300",
  };
  return (
    <span
      className={`inline-block rounded px-1.5 py-0.5 font-mono text-xs font-bold ${colors[method]}`}
    >
      {method}
    </span>
  );
}

function SectionHeader({
  title,
  tryLink,
  tryLabel,
}: {
  title: string;
  tryLink?: string;
  tryLabel?: string;
}) {
  return (
    <div className="mb-6 flex items-center justify-between border-b pb-3">
      <h2 className="text-xl font-bold">{title}</h2>
      {tryLink && (
        <Link
          href={tryLink}
          className="flex items-center gap-1 text-sm font-medium text-primary hover:underline"
        >
          {tryLabel || "Try it"} <ChevronRight className="h-3.5 w-3.5" />
        </Link>
      )}
    </div>
  );
}

function PipelineStageBox({
  server,
  tool,
  description,
  channel,
}: {
  server: string;
  tool: string;
  description: string;
  channel?: string;
}) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex items-center gap-2">
        <Badge variant="outline" className="font-mono text-xs">
          {server}
        </Badge>
        <span className="font-mono text-sm font-semibold">{tool}</span>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{description}</p>
      {channel && (
        <div className="mt-2">
          <Badge className="font-mono text-xs">{channel}</Badge>
        </div>
      )}
    </div>
  );
}

/* ─────────────────────── Section content ─────────────────────── */

function OverviewSection() {
  return (
    <div className="space-y-6">
      <SectionHeader title="What is Sealfleet?" />
      <p className="text-sm leading-relaxed text-muted-foreground">
        <strong className="text-foreground">Sealfleet</strong> is an open-source
        MCP (Model Context Protocol) Agent Platform. It lets you register,
        discover, test, and run AI tools as MCP servers — all from one portal.
      </p>

      <div className="grid gap-3 sm:grid-cols-3">
        <Card>
          <CardContent className="pt-4">
            <div className="mb-1 text-2xl">1.</div>
            <h4 className="text-sm font-semibold">Register</h4>
            <p className="mt-1 text-xs text-muted-foreground">
              Register your tools once — AI agents can discover and call them
              automatically.
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <div className="mb-1 text-2xl">2.</div>
            <h4 className="text-sm font-semibold">Chain</h4>
            <p className="mt-1 text-xs text-muted-foreground">
              Chain tools into Pipelines — one LLM call triggers a full
              multi-step workflow.
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <div className="mb-1 text-2xl">3.</div>
            <h4 className="text-sm font-semibold">Audit</h4>
            <p className="mt-1 text-xs text-muted-foreground">
              Everything is audited — every tool call leaves an immutable trace
              with full context.
            </p>
          </CardContent>
        </Card>
      </div>

      <div>
        <h3 className="mb-3 text-sm font-semibold">Platform Architecture</h3>
        <pre className="overflow-x-auto rounded-lg bg-slate-100 p-4 font-mono text-xs leading-relaxed text-slate-800 dark:bg-slate-900 dark:text-slate-200">
{`┌─────────────────────────────────────────────────────┐
│                    Sealfleet Platform                │
│                                                     │
│  ┌──────────┐    ┌──────────┐    ┌──────────────┐  │
│  │  Portal  │───▶│  Router  │───▶│  MCP Servers │  │
│  │  :3004   │    │  :8040   │    │  :8022-8099  │  │
│  └──────────┘    └────┬─────┘    └──────────────┘  │
│                       │                             │
│  ┌──────────┐    ┌────▼─────┐    ┌──────────────┐  │
│  │  Registry│◀───│  Broker  │───▶│  Audit Log   │  │
│  │  :8010   │    │          │    │  (Postgres)  │  │
│  └──────────┘    └──────────┘    └──────────────┘  │
└─────────────────────────────────────────────────────┘`}
        </pre>
      </div>

      <div>
        <h3 className="mb-3 text-sm font-semibold">Key Concepts</h3>
        <div className="grid gap-3 sm:grid-cols-3">
          <Card>
            <CardContent className="pt-4">
              <h4 className="text-sm font-semibold">MCP Server</h4>
              <p className="mt-1 text-xs text-muted-foreground">
                A service that exposes one or more callable tools. Can be remote
                (HTTPS) or deployed locally in k8s.
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-4">
              <h4 className="text-sm font-semibold">Pipeline</h4>
              <p className="mt-1 text-xs text-muted-foreground">
                A named chain of MCP tool calls. One input triggers multiple
                tools in sequence via channels. LLM calls{" "}
                <code className="rounded bg-muted px-1 font-mono text-xs">
                  weather_trip_planner
                </code>{" "}
                — not raw steps.
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-4">
              <h4 className="text-sm font-semibold">Channel</h4>
              <p className="mt-1 text-xs text-muted-foreground">
                The message bus between pipeline stages. Data flows through named
                channels (e.g.,{" "}
                <code className="rounded bg-muted px-1 font-mono text-xs">
                  weather.current
                </code>
                ), not direct calls.
              </p>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

function QuickStartSection() {
  return (
    <div className="space-y-6">
      <SectionHeader title="Get started in 5 minutes" />

      <div className="space-y-6">
        <div className="flex gap-4">
          <div className="flex flex-col items-center">
            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
              1
            </div>
            <div className="mt-2 w-px flex-1 bg-border" />
          </div>
          <div className="pb-6">
            <h3 className="text-sm font-semibold">Open the Ask page</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              The fastest way to see Sealfleet in action. Go to the Ask page and
              type a natural language question.
            </p>
            <CodeBlock>{`"What should I wear in Tokyo today?"`}</CodeBlock>
            <p className="text-xs text-muted-foreground">
              Sealfleet&apos;s core agent will automatically: identify the right
              pipeline, fetch real weather data from Open-Meteo, and return an
              outfit recommendation — all in ~3 seconds.
            </p>
            <Link
              href="/ask"
              className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
            >
              Try Ask <ChevronRight className="h-3 w-3" />
            </Link>
          </div>
        </div>

        <div className="flex gap-4">
          <div className="flex flex-col items-center">
            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
              2
            </div>
            <div className="mt-2 w-px flex-1 bg-border" />
          </div>
          <div className="pb-6">
            <h3 className="text-sm font-semibold">Browse the Catalog</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              Go to Catalog to see all registered MCP servers. Click any server
              to see its tools, schema, and endpoint.
            </p>
            <Link
              href="/catalog"
              className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
            >
              Open Catalog <ChevronRight className="h-3 w-3" />
            </Link>
          </div>
        </div>

        <div className="flex gap-4">
          <div className="flex flex-col items-center">
            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
              3
            </div>
            <div className="mt-2 w-px flex-1 bg-border" />
          </div>
          <div className="pb-6">
            <h3 className="text-sm font-semibold">Test a tool live</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              Go to Test Console. Select a server (e.g., Acme Financial),
              pick a tool, fill in the form, and click Run. See the live JSON
              response.
            </p>
            <Link
              href="/test"
              className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
            >
              Open Test Console <ChevronRight className="h-3 w-3" />
            </Link>
          </div>
        </div>

        <div className="flex gap-4">
          <div className="flex flex-col items-center">
            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
              4
            </div>
            <div className="mt-2 w-px flex-1 bg-border" />
          </div>
          <div className="pb-6">
            <h3 className="text-sm font-semibold">Run a pipeline</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              Open Weather Example. Pick a few cities and preferences, then run
              it: the pipeline gathers each city&apos;s weather and ranks them,
              and the page visualizes the result.
            </p>
            <Link
              href="/weather-trip"
              className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
            >
              Open Weather Example <ChevronRight className="h-3 w-3" />
            </Link>
          </div>
        </div>

        <div className="flex gap-4">
          <div className="flex flex-col items-center">
            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
              5
            </div>
          </div>
          <div>
            <h3 className="text-sm font-semibold">Register your own MCP</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              See the &quot;Create an MCP&quot; section below. Once registered,
              your tools appear in the Catalog and can be called from the Ask
              page.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

function AskSection() {
  return (
    <div className="space-y-6">
      <SectionHeader
        title="Ask — Natural Language → Pipeline"
        tryLink="/ask"
        tryLabel="Open Ask"
      />

      <p className="text-sm leading-relaxed text-muted-foreground">
        The Ask page is Sealfleet&apos;s AI interface. Type any question in
        plain English and the core agent will:
      </p>
      <ol className="list-inside list-decimal space-y-1 text-sm text-muted-foreground">
        <li>Match your question to a registered pipeline using LLM tool-calling</li>
        <li>Extract parameters (e.g., city name) automatically</li>
        <li>Execute the pipeline end-to-end</li>
        <li>Return a plain-language answer + the full trace</li>
      </ol>

      <div className="grid gap-4 lg:grid-cols-2">
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Input
          </h4>
          <CodeBlock>
{`{
  "question": "What should I wear in Stockholm today?"
}`}
          </CodeBlock>
        </div>
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Output
          </h4>
          <CodeBlock>
{`{
  "question": "Where should I go for sun in the next 10 days?",
  "output_type": "weather_trip_planner",
  "resolved_chain": [
    "weather-trip-mcp.fetch_cities_weather",
    "weather-trip-mcp.rank_cities"
  ],
  "inputs_used": { "cities": ["Stockholm", "Barcelona", "Lisbon"] },
  "answer": "Barcelona is the best pick: 5 near-perfect days, avg 12h sun, avg max 25°C.",
  "reasoning": "LLM selected 'weather_trip_planner' with default preferences. Completed in 3.2s."
}`}
          </CodeBlock>
        </div>
      </div>

      <div>
        <h4 className="mb-2 text-sm font-semibold">Direct API call</h4>
        <CodeBlock title="bash">
{`curl -X POST http://localhost:8050/ask \\
  -H "Content-Type: application/json" \\
  -d '{"question": "What should I wear in Tokyo today?"}'`}
        </CodeBlock>
        <p className="mt-2 text-xs text-muted-foreground">
          The agent falls back to keyword detection if LLM tool-calling is
          unavailable. Always returns a structured response.
        </p>
      </div>
    </div>
  );
}

function CatalogSection() {
  return (
    <div className="space-y-6">
      <SectionHeader
        title="Catalog — Browse & Discover MCP Servers"
        tryLink="/catalog"
        tryLabel="Open Catalog"
      />

      <p className="text-sm leading-relaxed text-muted-foreground">
        The Catalog lists every registered MCP server with name, description,
        endpoint, tool count, and status. Click any server to see its full
        detail: all tools with their input schemas.
      </p>

      <div>
        <h4 className="mb-2 text-sm font-semibold">Two types of servers</h4>
        <div className="grid gap-3 sm:grid-cols-2">
          <Card>
            <CardContent className="pt-4">
              <Badge variant="outline" className="mb-2">
                Type 1 — Remote / BYOM
              </Badge>
              <p className="text-xs text-muted-foreground">
                External HTTPS endpoint, e.g.{" "}
                <code className="rounded bg-muted px-1 font-mono text-xs">
                  https://mcp.example.com
                </code>
                . You bring your own model/deployment.
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-4">
              <Badge variant="outline" className="mb-2">
                Type 2 — k8s
              </Badge>
              <p className="text-xs text-muted-foreground">
                Deployed locally in the mcpfinder k3d cluster, e.g.{" "}
                <code className="rounded bg-muted px-1 font-mono text-xs">
                  http://weather-trip-mcp:8080
                </code>
                .
              </p>
            </CardContent>
          </Card>
        </div>
      </div>

      <div>
        <h4 className="mb-2 text-sm font-semibold">Register a server via API</h4>
        <CodeBlock title="bash">
{`curl -X POST http://localhost:8010/servers \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "my-mcp-server",
    "description": "Does something useful",
    "endpoint": "https://my-server.example.com",
    "tools": [
      {
        "name": "my_tool",
        "description": "Does X",
        "input_schema": {
          "type": "object",
          "properties": { "query": { "type": "string" } },
          "required": ["query"]
        }
      }
    ]
  }'`}
        </CodeBlock>
      </div>

      <div>
        <h4 className="mb-2 text-sm font-semibold">List all servers</h4>
        <CodeBlock title="bash">{`curl http://localhost:8010/servers`}</CodeBlock>
      </div>
    </div>
  );
}

function TestConsoleSection() {
  return (
    <div className="space-y-6">
      <SectionHeader
        title="Test Console — Live Tool Calls"
        tryLink="/test"
        tryLabel="Open Test Console"
      />

      <p className="text-sm leading-relaxed text-muted-foreground">
        Select any registered MCP server from the dropdown, pick a tool — the
        form auto-renders based on the tool&apos;s input schema. Enter your API
        key if the server requires auth, then click &quot;Run Tool&quot; to see
        the live JSON response.
      </p>

      <div>
        <h4 className="mb-2 text-sm font-semibold">
          Available servers in the test console
        </h4>
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/50">
                <th className="px-4 py-2 text-left text-xs font-semibold">
                  Server
                </th>
                <th className="px-4 py-2 text-left text-xs font-semibold">
                  Tools
                </th>
                <th className="px-4 py-2 text-left text-xs font-semibold">
                  Auth
                </th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-b">
                <td className="px-4 py-2 font-medium">
                  Acme Financial
                </td>
                <td className="px-4 py-2 text-muted-foreground">
                  19 tools (stock data, options, analysis)
                </td>
                <td className="px-4 py-2">
                  <Badge variant="outline" className="font-mono text-xs">
                    Bearer token
                  </Badge>
                </td>
              </tr>
              <tr>
                <td className="px-4 py-2 font-medium">
                  Market Risk Indicators
                </td>
                <td className="px-4 py-2 text-muted-foreground">
                  3 tools (volatility, risk metrics)
                </td>
                <td className="px-4 py-2">
                  <Badge
                    variant="secondary"
                    className="text-xs"
                  >
                    None
                  </Badge>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <h4 className="mb-2 text-sm font-semibold">
          Direct API call (bypassing the portal)
        </h4>
        <CodeBlock title="bash">
{`# Call a tool via the Runtime Router
curl -X POST http://localhost:8040/pipelines/tools/call \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "get_stock_volatility",
    "arguments": { "ticker": "BTC" }
  }'`}
        </CodeBlock>
      </div>
    </div>
  );
}

function PipelineSection() {
  return (
    <div className="space-y-6">
      <SectionHeader
        title="Pipeline — Chained MCP Tool Calls"
        tryLink="/pipeline"
        tryLabel="Open Pipeline"
      />

      <p className="text-sm leading-relaxed text-muted-foreground">
        A <strong className="text-foreground">Pipeline</strong> is a named,
        YAML-defined sequence of MCP tool calls that execute in order, passing
        data between stages through typed channels.
      </p>

      <div>
        <h4 className="mb-2 text-sm font-semibold">Why pipelines?</h4>
        <ul className="list-inside list-disc space-y-1 text-sm text-muted-foreground">
          <li>
            LLM calls{" "}
            <code className="rounded bg-muted px-1 font-mono text-xs">
              weather_trip_planner
            </code>{" "}
            as a single black-box tool — doesn&apos;t need to know the chain
          </li>
          <li>
            Stages communicate via channels (not direct calls) — decoupled and
            resilient
          </li>
          <li>
            Type contracts validate data at registration time — chain can&apos;t
            break at runtime
          </li>
        </ul>
      </div>

      <div>
        <h4 className="mb-3 text-sm font-semibold">
          Example pipeline:{" "}
          <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
            weather_trip_planner
          </code>
        </h4>

        {/* Pipeline flow diagram */}
        <div className="flex flex-col items-center gap-0">
          {/* Input */}
          <div className="w-full max-w-md rounded-lg border border-dashed bg-muted/50 px-4 py-2 text-center">
            <span className="font-mono text-xs text-muted-foreground">
              Input:{" "}
            </span>
            <code className="font-mono text-xs">
              {"{ cities: [\"Stockholm\", \"Barcelona\"], target_temp_c: 27 }"}
            </code>
          </div>

          <div className="flex h-8 items-center">
            <ArrowDown className="h-4 w-4 text-muted-foreground" />
          </div>

          {/* Stage 1 */}
          <PipelineStageBox
            server="weather-trip-mcp"
            tool="fetch_cities_weather"
            description="Real daily weather per city: past week + next 10 days (Open-Meteo / met.no)"
          />

          <div className="flex h-10 flex-col items-center justify-center">
            <span className="font-mono text-xs text-muted-foreground">
              {"{ cities: [{ name, days[] }] }"}
            </span>
            <ArrowDown className="mt-1 h-4 w-4 text-muted-foreground" />
          </div>

          {/* Stage 2 */}
          <PipelineStageBox
            server="weather-trip-mcp"
            tool="rank_cities"
            description="Deterministic trip score per city (sunshine, target temp, wind)"
          />

          <div className="flex h-8 items-center">
            <ArrowDown className="h-4 w-4 text-muted-foreground" />
          </div>

          {/* Output */}
          <div className="w-full max-w-md rounded-lg border border-dashed bg-muted/50 px-4 py-2 text-center">
            <span className="font-mono text-xs text-muted-foreground">
              Output:{" "}
            </span>
            <code className="font-mono text-xs">
              {"{ ranking[], best_city, summary }"}
            </code>
          </div>
        </div>
      </div>

      <div>
        <h4 className="mb-2 text-sm font-semibold">Run via API</h4>
        <CodeBlock title="bash">
{`# Run by name
curl -X POST http://localhost:8040/v2/pipelines/run \\
  -H "Content-Type: application/json" \\
  -d '{"pipeline": "weather_trip_planner", "inputs": {"cities": ["Stockholm", "Barcelona"]}}'

# List all pipelines
curl http://localhost:8040/pipelines

# Get pipelines as MCP tools
curl http://localhost:8040/pipelines/tools`}
        </CodeBlock>
      </div>

      <div>
        <h4 className="mb-2 text-sm font-semibold">Define a new pipeline (YAML)</h4>
        <CodeBlock title="runtime/pipelines/v2/my_pipeline.yaml">
{`name: my_pipeline
version: 2
inputs:
  cities:
    type: list
    default: ["Stockholm", "Barcelona"]
  target_temp_c:
    type: number
    default: 27
steps:
  - id: fetch
    mcp: weather-trip-mcp
    tool: fetch_cities_weather
    inputs:
      cities: "{{inputs.cities}}"
  - id: rank
    mcp: weather-trip-mcp
    tool: rank_cities
    inputs:
      weather: "{{steps.fetch.output}}"
      target_temp_c: "{{inputs.target_temp_c}}"
output:
  ranking: "{{steps.rank.output}}"`}
        </CodeBlock>
      </div>
    </div>
  );
}

function DeploySection() {
  return (
    <div className="space-y-6">
      <SectionHeader
        title="Deploy — Git → Docker → Kubernetes"
        tryLink="/deploy"
        tryLabel="Open Deploy"
      />

      <p className="text-sm leading-relaxed text-muted-foreground">
        The Deploy page lets you take any Git repo with a Dockerfile and deploy
        it as an MCP server to the local k3d cluster in one click.
      </p>

      <div>
        <h4 className="mb-2 text-sm font-semibold">How it works</h4>
        <ol className="list-inside list-decimal space-y-1 text-sm text-muted-foreground">
          <li>Paste a Git repo URL</li>
          <li>
            Sealfleet clones the repo, runs{" "}
            <code className="rounded bg-muted px-1 font-mono text-xs">
              docker build
            </code>
            , pushes to the local registry (
            <code className="rounded bg-muted px-1 font-mono text-xs">
              localhost:5050
            </code>
            )
          </li>
          <li>Applies a k8s Deployment + Service manifest</li>
          <li>Live build logs stream in real-time via SSE</li>
        </ol>
      </div>

      <div>
        <h4 className="mb-2 text-sm font-semibold">
          Requirements for your repo
        </h4>
        <ul className="list-inside list-disc space-y-1 text-sm text-muted-foreground">
          <li>
            Must have a{" "}
            <code className="rounded bg-muted px-1 font-mono text-xs">
              Dockerfile
            </code>{" "}
            in the root
          </li>
          <li>
            Must expose an HTTP server (Sealfleet will create a k8s NodePort
            service)
          </li>
          <li>
            Should expose a{" "}
            <code className="rounded bg-muted px-1 font-mono text-xs">
              /health
            </code>{" "}
            endpoint returning{" "}
            <code className="rounded bg-muted px-1 font-mono text-xs">
              {`{"status": "ok"}`}
            </code>
          </li>
        </ul>
      </div>

      <div>
        <h4 className="mb-2 text-sm font-semibold">Deploy via API</h4>
        <CodeBlock title="bash">
{`curl -X POST http://localhost:8030/deploy \\
  -H "Content-Type: application/json" \\
  -d '{
    "repo_url": "https://github.com/your-org/your-mcp",
    "service_name": "my-mcp",
    "port": 8080
  }'

# Watch deploy logs (SSE stream)
curl -N http://localhost:8030/deploy/{deploy_id}/logs`}
        </CodeBlock>
      </div>

      <p className="text-xs text-muted-foreground">
        After deploy, your server will be accessible at{" "}
        <code className="rounded bg-muted px-1 font-mono text-xs">
          localhost:3XXXX
        </code>{" "}
        (NodePort) and should be registered in the Catalog.
      </p>
    </div>
  );
}

function AuditSection() {
  return (
    <div className="space-y-6">
      <SectionHeader
        title="Audit Log — Full Traceability"
        tryLink="/audit"
        tryLabel="Open Audit Log"
      />

      <p className="text-sm leading-relaxed text-muted-foreground">
        Every action in Sealfleet creates an immutable audit event. The Audit
        Log page shows a live feed of all tool calls, pipeline runs, and
        registration events.
      </p>

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <h4 className="mb-2 text-sm font-semibold">What&apos;s logged</h4>
          <ul className="list-inside list-disc space-y-1 text-xs text-muted-foreground">
            <li>
              Tool invocations: server, tool name, inputs (redacted for
              sensitive fields), output type, latency, status
            </li>
            <li>Pipeline runs: pipeline name, trace ID, stage-by-stage results</li>
            <li>Registration events: when servers are added or updated</li>
            <li>Policy decisions: allowed/denied with reason</li>
          </ul>
        </div>
        <div>
          <h4 className="mb-2 text-sm font-semibold">Each event includes</h4>
          <ul className="list-inside list-disc space-y-1 text-xs text-muted-foreground">
            <li>Timestamp (UTC)</li>
            <li>Actor (API key / user)</li>
            <li>Resource (server + tool)</li>
            <li>Trace ID (link to full trace)</li>
            <li>Status (success / error / denied)</li>
          </ul>
        </div>
      </div>

      <div>
        <h4 className="mb-2 text-sm font-semibold">Query audit events via API</h4>
        <CodeBlock title="bash">
{`# All events (paginated)
curl "http://localhost:8010/audit?limit=50"

# Filter by server
curl "http://localhost:8010/audit?server=research-mcp"`}
        </CodeBlock>
      </div>
    </div>
  );
}

function CreateMcpSection() {
  return (
    <div className="space-y-6">
      <SectionHeader title="Create Your Own MCP Server" />

      <p className="text-sm leading-relaxed text-muted-foreground">
        Full guide from zero to a registered, callable MCP server.
      </p>

      {/* Option A */}
      <div>
        <h3 className="mb-3 text-base font-semibold">
          Option A: Python + FastAPI (simplest)
        </h3>

        <div className="space-y-4">
          <div>
            <h4 className="mb-1 text-sm font-semibold">
              Step 1 — Create the server
            </h4>
            <CodeBlock title="server.py">
{`import os, httpx
from fastapi import FastAPI

app = FastAPI()
RUNTIME_URL = os.getenv("RUNTIME_URL", "http://localhost:8040")

@app.get("/health")
async def health():
    return {"status": "ok", "mcp": "my-mcp"}

@app.get("/tools")
async def list_tools():
    return [{
        "name": "my_tool",
        "description": "Does something useful",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Input query"}
            },
            "required": ["query"]
        }
    }]

@app.post("/call")
async def call_tool(request: dict):
    tool = request.get("tool")
    inputs = request.get("inputs", {})
    if tool == "my_tool":
        return {"tool": tool, "result": {"output": f"Processed: {inputs['query']}"}}
    return {"error": f"Unknown tool: {tool}"}`}
            </CodeBlock>
          </div>

          <div>
            <h4 className="mb-1 text-sm font-semibold">
              Step 2 — Create a Dockerfile
            </h4>
            <CodeBlock title="Dockerfile">
{`FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install fastapi uvicorn httpx
COPY server.py .
EXPOSE 8080
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]`}
            </CodeBlock>
          </div>

          <div>
            <h4 className="mb-1 text-sm font-semibold">
              Step 3 — Create mcp.yaml
            </h4>
            <CodeBlock title="mcp.yaml">
{`name: my-mcp
description: "My custom MCP server"
endpoint: http://my-mcp:8080
tools:
  - name: my_tool
    description: "Does something useful"
    input_type: String
    output_type: String`}
            </CodeBlock>
          </div>

          <div>
            <h4 className="mb-1 text-sm font-semibold">
              Step 4 — Register with the catalog
            </h4>
            <CodeBlock title="bash">
{`curl -X POST http://localhost:8010/servers \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "my-mcp",
    "description": "My custom MCP server",
    "endpoint": "http://localhost:8080",
    "tools": [{"name": "my_tool", "description": "Does something useful"}]
  }'`}
            </CodeBlock>
          </div>

          <div>
            <h4 className="mb-1 text-sm font-semibold">
              Step 5 — Deploy to k8s
            </h4>
            <p className="text-xs text-muted-foreground">
              Use the Deploy page or API (see Deploy section above).
            </p>
          </div>
        </div>
      </div>

      {/* Protocol */}
      <div>
        <h3 className="mb-3 text-base font-semibold">MCP Tool Call Protocol</h3>
        <p className="mb-2 text-xs text-muted-foreground">
          Sealfleet calls your server with:
        </p>
        <CodeBlock title="Request → POST /call">
{`{
  "tool": "my_tool",
  "inputs": { "query": "hello" }
}`}
        </CodeBlock>
        <p className="mb-2 text-xs text-muted-foreground">
          Your server must respond with:
        </p>
        <CodeBlock title="Response">
{`{
  "tool": "my_tool",
  "result": { "output": "Processed: hello" }
}`}
        </CodeBlock>
      </div>

      {/* Option B */}
      <div>
        <h3 className="mb-3 text-base font-semibold">
          Option B: Any language, any framework
        </h3>
        <p className="mb-2 text-sm text-muted-foreground">
          As long as your server implements these three endpoints:
        </p>
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/50">
                <th className="px-4 py-2 text-left text-xs font-semibold">
                  Endpoint
                </th>
                <th className="px-4 py-2 text-left text-xs font-semibold">
                  Response
                </th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-b">
                <td className="px-4 py-2 font-mono text-xs">
                  <MethodBadge method="GET" /> /health
                </td>
                <td className="px-4 py-2 font-mono text-xs text-muted-foreground">
                  {`{"status": "ok"}`}
                </td>
              </tr>
              <tr className="border-b">
                <td className="px-4 py-2 font-mono text-xs">
                  <MethodBadge method="GET" /> /tools
                </td>
                <td className="px-4 py-2 text-xs text-muted-foreground">
                  Array of tool definitions with name, description, input_schema
                </td>
              </tr>
              <tr>
                <td className="px-4 py-2 font-mono text-xs">
                  <MethodBadge method="POST" /> /call
                </td>
                <td className="px-4 py-2 font-mono text-xs text-muted-foreground">
                  {`{"tool": "...", "inputs": {...}}`}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          You can use Node.js, Go, Rust — anything that speaks HTTP.
        </p>
      </div>
    </div>
  );
}

function TransportModesSection() {
  return (
    <div className="space-y-6">
      <SectionHeader title="MCP Transport Modes" />

      <p className="text-sm leading-relaxed text-muted-foreground">
        Sealfleet supports two transport modes for calling MCP tools. Each MCP
        server declares its transport mode in its manifest.
      </p>

      {/* HTTP mode */}
      <div>
        <h3 className="mb-2 text-base font-semibold">
          HTTP Transport{" "}
          <Badge variant="secondary" className="ml-1 text-xs">
            default
          </Badge>
        </h3>
        <p className="mb-2 text-sm text-muted-foreground">
          Long-running HTTP server (FastAPI, Express, etc.). The router sends{" "}
          <code className="rounded bg-muted px-1 py-0.5 text-xs">
            POST /call
          </code>{" "}
          to the endpoint. Supports scale-to-zero via k8s (10-15s cold start) or
          always-on (0ms cold start, ~400ms per call). Stateful — the process
          persists between calls so you can cache data, hold connections, etc.
        </p>
        <CodeBlock title="mcp.yaml — HTTP mode">
{`name: my-mcp
endpoint: http://my-mcp:8080
transport: http           # default — can be omitted
tools:
  - name: my_tool
    description: "Does something useful"`}
        </CodeBlock>
      </div>

      {/* stdio mode */}
      <div>
        <h3 className="mb-2 text-base font-semibold">
          stdio Transport{" "}
          <Badge variant="outline" className="ml-1 text-xs">
            new
          </Badge>
        </h3>
        <p className="mb-2 text-sm text-muted-foreground">
          One-shot Docker container via stdin/stdout. The router runs{" "}
          <code className="rounded bg-muted px-1 py-0.5 text-xs">
            docker run --rm -i &lt;image&gt;
          </code>
          , writes the tool call as JSON to stdin, and reads the result from
          stdout. The container exits after each call. Sub-200ms cold start
          (with cached layers), per-call isolation, no k8s scheduling needed.
          Stateless — each call gets a fresh container.
        </p>
        <CodeBlock title="mcp.yaml — stdio mode">
{`name: my-stdio-mcp
transport: stdio
image: localhost:5050/my-stdio-mcp:latest
tools:
  - name: my_tool
    description: "Does something useful"`}
        </CodeBlock>

        <p className="mt-3 mb-1 text-xs font-medium text-muted-foreground">
          stdio protocol:
        </p>
        <CodeBlock title="stdin (router writes)">
{`{"tool": "my_tool", "inputs": {"query": "hello"}}`}
        </CodeBlock>
        <CodeBlock title="stdout (container writes)">
{`{"result": {"output": "Processed: hello"}}`}
        </CodeBlock>
      </div>

      {/* Comparison table */}
      <div>
        <h3 className="mb-3 text-base font-semibold">When to choose each</h3>
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/50">
                <th className="px-4 py-2 text-left text-xs font-semibold" />
                <th className="px-4 py-2 text-left text-xs font-semibold">
                  HTTP
                </th>
                <th className="px-4 py-2 text-left text-xs font-semibold">
                  stdio
                </th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-b">
                <td className="px-4 py-2 text-xs font-medium">Cold start</td>
                <td className="px-4 py-2 text-xs text-muted-foreground">
                  10-15s (scale-to-zero) / 0 (always-on)
                </td>
                <td className="px-4 py-2 text-xs text-muted-foreground">
                  100-200ms (image cached)
                </td>
              </tr>
              <tr className="border-b">
                <td className="px-4 py-2 text-xs font-medium">Warm call</td>
                <td className="px-4 py-2 text-xs text-muted-foreground">
                  ~400ms
                </td>
                <td className="px-4 py-2 text-xs text-muted-foreground">
                  100-200ms (always cold)
                </td>
              </tr>
              <tr className="border-b">
                <td className="px-4 py-2 text-xs font-medium">Isolation</td>
                <td className="px-4 py-2 text-xs text-muted-foreground">
                  Shared pod between calls
                </td>
                <td className="px-4 py-2 text-xs text-muted-foreground">
                  Fresh container per call
                </td>
              </tr>
              <tr className="border-b">
                <td className="px-4 py-2 text-xs font-medium">Stateful</td>
                <td className="px-4 py-2 text-xs text-muted-foreground">
                  Yes
                </td>
                <td className="px-4 py-2 text-xs text-muted-foreground">
                  No
                </td>
              </tr>
              <tr>
                <td className="px-4 py-2 text-xs font-medium">Best for</td>
                <td className="px-4 py-2 text-xs text-muted-foreground">
                  Caching, DB connections, high-frequency calls
                </td>
                <td className="px-4 py-2 text-xs text-muted-foreground">
                  Untrusted code, simple transforms, security-sensitive ops
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function ApiTable({
  title,
  port,
  rows,
}: {
  title: string;
  port: string;
  rows: { method: "GET" | "POST" | "DELETE"; endpoint: string; description: string }[];
}) {
  return (
    <div>
      <h4 className="mb-2 text-sm font-semibold">
        {title}{" "}
        <span className="font-mono text-xs font-normal text-muted-foreground">
          (:{port})
        </span>
      </h4>
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b bg-muted/50">
              <th className="px-3 py-2 text-left text-xs font-semibold">
                Method
              </th>
              <th className="px-3 py-2 text-left text-xs font-semibold">
                Endpoint
              </th>
              <th className="px-3 py-2 text-left text-xs font-semibold">
                Description
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className={i < rows.length - 1 ? "border-b" : ""}>
                <td className="px-3 py-1.5">
                  <MethodBadge method={row.method} />
                </td>
                <td className="px-3 py-1.5 font-mono text-xs">
                  {row.endpoint}
                </td>
                <td className="px-3 py-1.5 text-xs text-muted-foreground">
                  {row.description}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ApiReferenceSection() {
  return (
    <div className="space-y-6">
      <SectionHeader title="API Reference" />

      <ApiTable
        title="Runtime Router"
        port="8040"
        rows={[
          { method: "GET", endpoint: "/health", description: "Service health + stats" },
          { method: "GET", endpoint: "/pipelines", description: "List named pipelines" },
          { method: "GET", endpoint: "/pipelines/{name}", description: "Get pipeline definition" },
          { method: "POST", endpoint: "/pipelines/{name}/run", description: "Run a pipeline" },
          { method: "POST", endpoint: "/pipelines/register", description: "Register a new pipeline" },
          { method: "GET", endpoint: "/pipelines/tools", description: "Pipelines as MCP tools" },
          { method: "POST", endpoint: "/pipelines/tools/call", description: "Call a pipeline as MCP tool" },
          { method: "POST", endpoint: "/publish/{channel}", description: "Publish data to a channel" },
          { method: "GET", endpoint: "/subscribe/{channel}", description: "Subscribe to a channel (SSE)" },
          { method: "GET", endpoint: "/transport/modes", description: "List available transport modes (http, stdio)" },
        ]}
      />

      <ApiTable
        title="Registry API"
        port="8010"
        rows={[
          { method: "GET", endpoint: "/health", description: "Service health" },
          { method: "GET", endpoint: "/servers", description: "List all servers" },
          { method: "GET", endpoint: "/servers/{id}", description: "Get server + tools" },
          { method: "POST", endpoint: "/servers", description: "Register a server" },
          { method: "GET", endpoint: "/audit", description: "Get audit events" },
        ]}
      />

      <ApiTable
        title="Core Agent"
        port="8050"
        rows={[
          { method: "GET", endpoint: "/health", description: "Service health" },
          { method: "GET", endpoint: "/tools", description: "Available pipeline tools" },
          { method: "POST", endpoint: "/ask", description: "Natural language → pipeline execution" },
        ]}
      />

      <ApiTable
        title="Deploy Service"
        port="8030"
        rows={[
          { method: "GET", endpoint: "/health", description: "Service health" },
          { method: "POST", endpoint: "/deploy", description: "Start a deployment" },
          { method: "GET", endpoint: "/deployments", description: "List all deployments" },
          { method: "GET", endpoint: "/deploy/{id}/logs", description: "Stream deploy logs (SSE)" },
        ]}
      />
    </div>
  );
}

function SecuritySection() {
  return (
    <div className="space-y-6">
      <SectionHeader title="Security Model" />

      <div className="space-y-5">
        <div>
          <h4 className="text-sm font-semibold">1. LLM Never Sees Secrets</h4>
          <p className="mt-1 text-sm text-muted-foreground">
            The language model receives opaque handles, never raw API keys,
            passwords, or tokens. Credentials are injected by the broker at
            execution time.
          </p>
        </div>

        <div>
          <h4 className="text-sm font-semibold">2. Sealed Inputs</h4>
          <p className="mt-1 text-sm text-muted-foreground">
            Sensitive data is collected via secure UI components that bypass the
            LLM context entirely. Encrypted client-side, decrypted only at
            execution time by the broker.
          </p>
        </div>

        <div>
          <h4 className="text-sm font-semibold">3. Channel Isolation</h4>
          <p className="mt-1 text-sm text-muted-foreground">
            MCP servers don&apos;t call each other directly. All data flows
            through named typed channels enforced by the runtime router. A
            server can only read channels it&apos;s authorized for.
          </p>
        </div>

        <div>
          <h4 className="text-sm font-semibold">4. Policy Engine</h4>
          <p className="mt-1 text-sm text-muted-foreground">
            Every tool call is scoped by:
          </p>
          <ul className="mt-1 list-inside list-disc text-sm text-muted-foreground">
            <li>Identity (API key / user)</li>
            <li>Action (which tool)</li>
            <li>Resource (which server)</li>
            <li>Data constraints (e.g., max amount $1000)</li>
            <li>Time window (e.g., business hours only)</li>
          </ul>
        </div>

        <div>
          <h4 className="text-sm font-semibold">5. Immutable Audit Trail</h4>
          <p className="mt-1 text-sm text-muted-foreground">
            Every action creates an audit event with trace ID, stored in
            PostgreSQL. Cannot be deleted or modified.
          </p>
        </div>
      </div>
    </div>
  );
}

/* ─────────────────────── Main page ─────────────────────── */

export default function DocsPage() {
  const [active, setActive] = useState<SectionId>("overview");

  const sectionContent: Record<SectionId, React.ReactNode> = {
    overview: <OverviewSection />,
    quickstart: <QuickStartSection />,
    ask: <AskSection />,
    catalog: <CatalogSection />,
    test: <TestConsoleSection />,
    pipeline: <PipelineSection />,
    deploy: <DeploySection />,
    audit: <AuditSection />,
    create: <CreateMcpSection />,
    transport: <TransportModesSection />,
    api: <ApiReferenceSection />,
    security: <SecuritySection />,
  };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Documentation</h1>

      <div className="flex gap-6">
        {/* Sidebar */}
        <nav className="hidden w-48 shrink-0 md:block">
          <div className="sticky top-4 space-y-0.5">
            {sections.map((s) => {
              const Icon = s.icon;
              const isActive = active === s.id;
              return (
                <button
                  key={s.id}
                  onClick={() => setActive(s.id)}
                  className={`flex w-full items-center gap-2 rounded-md px-3 py-1.5 text-left text-sm transition-colors ${
                    isActive
                      ? "bg-primary font-medium text-primary-foreground"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground"
                  }`}
                >
                  <Icon className="h-3.5 w-3.5 shrink-0" />
                  {s.label}
                </button>
              );
            })}
          </div>
        </nav>

        {/* Mobile section picker */}
        <div className="w-full md:hidden">
          <select
            value={active}
            onChange={(e) => setActive(e.target.value as SectionId)}
            className="mb-4 w-full rounded-md border bg-background px-3 py-2 text-sm"
          >
            {sections.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        </div>

        {/* Content */}
        <div className="min-w-0 flex-1">{sectionContent[active]}</div>
      </div>
    </div>
  );
}
