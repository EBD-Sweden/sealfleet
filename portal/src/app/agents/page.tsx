"use client";

import { useEffect, useState, useCallback } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  RefreshCw,
  Clock,
  Calendar,
  Bot,
  Activity,
  AlertCircle,
  Users,
} from "lucide-react";

// ── types ────────────────────────────────────────────────────────────────────

interface CronEntry {
  id: string;
  name: string;
  enabled: boolean;
  schedule: Record<string, unknown>;
  description?: string;
}

interface AgentData {
  id: string;
  name: string;
  workspace: string;
  status: "active" | "idle" | "offline";
  lastSeenMs: number | null;
  minutesAgo: number;
  lastUser: string;
  lastAssistant: string;
  sessionCount: number;
  subagentCount: number;
  cronCount: number;
  crons: CronEntry[];
}

interface AgentsResponse {
  agents: AgentData[];
  generatedAt: number;
}

// ── static metadata (emojis + correct display names) ─────────────────────────

const AGENT_META: Record<string, { emoji: string; displayName: string; tagline: string }> = {
  main: {
    emoji: "⚡",
    displayName: "Anakin (Ani)",
    tagline: "Main assistant — builder, fixer, gets things done",
  },
  quant: {
    emoji: "📊",
    displayName: "Padmé Amidala",
    tagline: "Quant trading — strategic, analytical, Queen of alpha",
  },
  mcpfinder: {
    emoji: "⚔️",
    displayName: "Obi-Wan Kenobi",
    tagline: "Sealfleet platform — Jedi patience, dry wit",
  },
  zofye: {
    emoji: "🔮",
    displayName: "Zofye",
    tagline: "General purpose agent",
  },
  luc: {
    emoji: "🌟",
    displayName: "Luc Skywalker",
    tagline: "Disciplined analyst — Jedi clarity, banker precision",
  },
  polymer: {
    emoji: "🧪",
    displayName: "Palmer Sand",
    tagline: "Adaptive builder — curious, builds things that stick",
  },
  polyrust: {
    emoji: "🦀",
    displayName: "Polyrust",
    tagline: "Systems-level, precise, zero-abstraction-waste",
  },
  russle: {
    emoji: "⚖️",
    displayName: "Russle",
    tagline: "Regulatory analyst — precise, citation-driven",
  },
};

// ── helpers ──────────────────────────────────────────────────────────────────

function timeAgo(ms: number | null): string {
  if (!ms) return "never";
  const diff = Date.now() - ms;
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function formatSchedule(schedule: Record<string, unknown>): string {
  if (!schedule) return "—";
  const kind = schedule.kind as string;
  if (kind === "every") {
    const ms = schedule.everyMs as number;
    if (ms < 60_000) return `every ${ms / 1000}s`;
    if (ms < 3_600_000) return `every ${Math.round(ms / 60_000)}m`;
    if (ms < 86_400_000) return `every ${Math.round(ms / 3_600_000)}h`;
    return `every ${Math.round(ms / 86_400_000)}d`;
  }
  if (kind === "cron") return `cron: ${schedule.expr}`;
  if (kind === "at")
    return `once ${new Date(schedule.at as string).toLocaleDateString()}`;
  return kind;
}

/** Strip metadata/untrusted-content wrappers and return a clean 1-line summary */
function cleanMessage(text: string): string {
  if (!text?.trim()) return "";
  // Strip EXTERNAL_UNTRUSTED_CONTENT blocks — take inner content
  const untrustedMatch = text.match(/<<<EXTERNAL_UNTRUSTED_CONTENT[^>]*>>>([\s\S]*?)<<<END/);
  if (untrustedMatch) text = untrustedMatch[1];
  // Strip WhatsApp/Telegram metadata JSON blocks at the top
  text = text.replace(/^Conversation info.*?```json[\s\S]*?```\s*/gm, "");
  text = text.replace(/^Sender.*?```json[\s\S]*?```\s*/gm, "");
  // Strip "Task: X | Job ID: ... | Received: ..." lines
  text = text.replace(/^Task:.*?(\n|$)/m, "");
  // Collapse whitespace
  return text.replace(/\n+/g, " ").trim().slice(0, 220);
}

// ── sub-components ───────────────────────────────────────────────────────────

function StatusDot({ status }: { status: AgentData["status"] }) {
  const base = "relative inline-flex rounded-full h-2.5 w-2.5";
  if (status === "active")
    return (
      <span className="relative flex h-2.5 w-2.5">
        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
        <span className={`${base} bg-green-500`} />
      </span>
    );
  if (status === "idle")
    return <span className={`${base} bg-yellow-500`} />;
  return <span className={`${base} bg-gray-400`} />;
}

function StatusBadge({ status }: { status: AgentData["status"] }) {
  const cls = {
    active:
      "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400",
    idle: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400",
    offline:
      "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400",
  }[status];
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}
    >
      <StatusDot status={status} />
      {status}
    </span>
  );
}

function AgentCard({ agent }: { agent: AgentData }) {
  const meta = AGENT_META[agent.id] ?? {
    emoji: "🤖",
    displayName: agent.name,
    tagline: agent.workspace,
  };

  const activeCrons = agent.crons.filter((c) => c.enabled);
  const summary =
    cleanMessage(agent.lastAssistant) ||
    cleanMessage(agent.lastUser) ||
    "No recent activity";

  return (
    <Card className="flex flex-col">
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-2xl shrink-0" role="img" aria-label={meta.displayName}>
              {meta.emoji}
            </span>
            <div className="min-w-0">
              <CardTitle className="text-base leading-tight">{meta.displayName}</CardTitle>
              <p className="text-xs text-muted-foreground mt-0.5 font-mono">{agent.id}</p>
            </div>
          </div>
          <StatusBadge status={agent.status} />
        </div>
        <p className="text-xs text-muted-foreground mt-1">{meta.tagline}</p>
      </CardHeader>

      <CardContent className="flex-1 space-y-4 pt-0">
        {/* Stats row */}
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <Clock className="h-3 w-3" />
            <span className={agent.status === "active" ? "text-green-600 font-medium" : ""}>
              {timeAgo(agent.lastSeenMs)}
            </span>
          </span>
          <span>{agent.sessionCount} sessions</span>
          {agent.subagentCount > 0 && (
            <span>{agent.subagentCount} sub-agents</span>
          )}
        </div>

        {/* What they're working on */}
        <div className="space-y-1">
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1">
            <Activity className="h-3 w-3" />
            Last activity
          </p>
          <p className="text-sm leading-relaxed line-clamp-3 text-foreground/80">
            {summary}
          </p>
        </div>

        {/* Scheduled crons */}
        {agent.crons.length > 0 ? (
          <div className="space-y-1.5">
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1">
              <Calendar className="h-3 w-3" />
              Scheduled ({activeCrons.length}/{agent.crons.length} active)
            </p>
            <div className="space-y-1">
              {agent.crons.map((job) => (
                <div
                  key={job.id}
                  className={`flex items-start justify-between gap-2 rounded-md px-2 py-1.5 text-xs ${
                    job.enabled ? "bg-muted/60" : "bg-muted/20 opacity-50"
                  }`}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="font-medium truncate">{job.name}</span>
                      {!job.enabled && (
                        <Badge variant="outline" className="text-[10px] h-4 px-1">
                          off
                        </Badge>
                      )}
                    </div>
                    <p className="text-muted-foreground mt-0.5">
                      {formatSchedule(job.schedule)}
                      {job.description ? ` · ${job.description.slice(0, 50)}` : ""}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : agent.cronCount > 0 ? (
          <div className="text-xs text-muted-foreground flex items-center gap-1">
            <Calendar className="h-3 w-3" />
            {agent.cronCount} scheduled job{agent.cronCount !== 1 ? "s" : ""}
          </div>
        ) : (
          <div className="text-xs text-muted-foreground flex items-center gap-1">
            <Calendar className="h-3 w-3" />
            No scheduled pipelines
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── main page ────────────────────────────────────────────────────────────────

export default function AgentsPage() {
  const [data, setData] = useState<AgentsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchAgents = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/agents", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      if (json.error) throw new Error(json.error);
      setData(json);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAgents();
    const iv = setInterval(fetchAgents, 30_000);
    return () => clearInterval(iv);
  }, [fetchAgents]);

  const activeCount = data?.agents.filter((a) => a.status === "active").length ?? 0;
  const idleCount = data?.agents.filter((a) => a.status === "idle").length ?? 0;
  const offlineCount = data?.agents.filter((a) => a.status === "offline").length ?? 0;
  const totalActiveCrons = data?.agents.reduce(
    (s, a) => s + a.crons.filter((c) => c.enabled).length,
    0,
  ) ?? 0;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Users className="h-6 w-6 text-primary" />
            Agents Dashboard
          </h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Agent fleet — real-time status &amp; scheduled pipelines
          </p>
        </div>
        <div className="flex items-center gap-3">
          {data && (
            <p className="text-xs text-muted-foreground hidden sm:block">
              {timeAgo(data.generatedAt)} · auto-refresh 30s
            </p>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={fetchAgents}
            disabled={loading}
            className="gap-1.5"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </div>

      {/* Stats */}
      {data && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            {
              label: "Active",
              value: activeCount,
              dot: <span className="relative flex h-2.5 w-2.5"><span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" /><span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-green-500" /></span>,
            },
            {
              label: "Idle",
              value: idleCount,
              dot: <span className="h-2.5 w-2.5 rounded-full bg-yellow-500 shrink-0" />,
            },
            {
              label: "Offline",
              value: offlineCount,
              dot: <span className="h-2.5 w-2.5 rounded-full bg-gray-400 shrink-0" />,
            },
            {
              label: "Active crons",
              value: totalActiveCrons,
              dot: <Calendar className="h-3.5 w-3.5 text-muted-foreground shrink-0" />,
            },
          ].map((stat) => (
            <Card key={stat.label} className="px-4 py-3">
              <div className="flex items-center gap-2">
                {stat.dot}
                <div>
                  <p className="text-xl font-bold leading-none">{stat.value}</p>
                  <p className="text-xs text-muted-foreground mt-0.5">{stat.label}</p>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive flex items-center gap-2">
          <AlertCircle className="h-4 w-4 shrink-0" />
          {error}
        </div>
      )}

      {/* Agent Grid */}
      {loading && !data ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <Card key={i}>
              <CardHeader className="pb-3">
                <div className="flex items-center gap-3">
                  <Skeleton className="h-8 w-8 rounded" />
                  <div className="space-y-1.5 flex-1">
                    <Skeleton className="h-4 w-32" />
                    <Skeleton className="h-3 w-16" />
                  </div>
                </div>
              </CardHeader>
              <CardContent className="space-y-2">
                <Skeleton className="h-3 w-full" />
                <Skeleton className="h-3 w-3/4" />
                <Skeleton className="h-16 w-full mt-2" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        data && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {data.agents.map((agent) => (
              <AgentCard key={agent.id} agent={agent} />
            ))}
          </div>
        )
      )}
    </div>
  );
}
