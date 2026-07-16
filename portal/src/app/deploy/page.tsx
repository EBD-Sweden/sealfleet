"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Rocket, Loader2, CheckCircle2, XCircle, ChevronDown } from "lucide-react";
import Link from "next/link";

// --- Types ---

interface LogEntry {
  step: string;
  status: string;
  msg: string;
  ts: string;
  endpoint?: string;
  server_id?: string;
  node_port?: number;
}

interface Deployment {
  id: string;
  name: string;
  repo_url: string | null;
  branch: string | null;
  image: string | null;
  endpoint: string | null;
  node_port: number | null;
  status: string;
  server_id: string | null;
  created_at: string | null;
  updated_at: string | null;
}

// --- Step badge colors ---

const stepColors: Record<string, string> = {
  clone: "bg-blue-500",
  detect: "bg-purple-500",
  build: "bg-yellow-600",
  deploy: "bg-orange-500",
  register: "bg-cyan-600",
  done: "bg-green-600",
  error: "bg-red-600",
};

const statusBadgeColors: Record<string, string> = {
  running: "bg-green-500 text-white",
  deploying: "bg-yellow-500 text-white",
  failed: "bg-red-500 text-white",
};

// --- Component ---

export default function DeployPage() {
  // Form state
  const [repoUrl, setRepoUrl] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [branch, setBranch] = useState("main");
  const [port, setPort] = useState("8000");
  const [tagsInput, setTagsInput] = useState("");
  const [isPublic, setIsPublic] = useState(true);
  const [envVarsText, setEnvVarsText] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);

  // Deploy state
  const [deploying, setDeploying] = useState(false);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [deployResult, setDeployResult] = useState<LogEntry | null>(null);
  const [deployError, setDeployError] = useState<string | null>(null);

  // Deployments table
  const [deployments, setDeployments] = useState<Deployment[]>([]);

  const logEndRef = useRef<HTMLDivElement>(null);

  // Auto-populate name from repo URL
  useEffect(() => {
    if (!repoUrl) return;
    try {
      const parts = repoUrl.replace(/\.git$/, "").split("/");
      const last = parts[parts.length - 1];
      if (last) setName(last.toLowerCase().replace(/[^a-z0-9-]/g, "-"));
    } catch {
      // ignore
    }
  }, [repoUrl]);

  // Scroll log to bottom
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  // Load deployments
  const fetchDeployments = useCallback(async () => {
    try {
      const res = await fetch("/api/deployments");
      if (res.ok) {
        const data = await res.json();
        setDeployments(data);
      }
    } catch {
      // silent
    }
  }, []);

  useEffect(() => {
    fetchDeployments();
  }, [fetchDeployments]);

  // --- Deploy handler ---
  const handleDeploy = async () => {
    if (!repoUrl || !name) return;
    setDeploying(true);
    setLogs([]);
    setDeployResult(null);
    setDeployError(null);

    // Parse env vars
    const envVars: Record<string, string> = {};
    envVarsText.split("\n").forEach((line) => {
      const eq = line.indexOf("=");
      if (eq > 0) {
        envVars[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
      }
    });

    const tags = tagsInput
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);

    try {
      const res = await fetch("/api/deploy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          repo_url: repoUrl,
          branch,
          name,
          description,
          tags,
          port: parseInt(port, 10) || 8000,
          is_public: isPublic,
          env_vars: envVars,
        }),
      });

      if (!res.ok || !res.body) {
        const text = await res.text();
        setDeployError(text || `HTTP ${res.status}`);
        setDeploying(false);
        return;
      }

      // Read SSE stream
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (line.startsWith("data:")) {
            const jsonStr = line.slice(5).trim();
            if (!jsonStr) continue;
            try {
              const entry: LogEntry = JSON.parse(jsonStr);
              setLogs((prev) => [...prev, entry]);

              if (entry.step === "done" && entry.status === "success") {
                setDeployResult(entry);
              }
              if (entry.status === "error") {
                setDeployError(entry.msg);
              }
            } catch {
              // not valid JSON, skip
            }
          }
        }
      }
    } catch (err) {
      setDeployError(err instanceof Error ? err.message : "Connection failed");
    } finally {
      setDeploying(false);
      fetchDeployments();
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Deploy from Git</h1>
        <p className="text-sm text-muted-foreground">
          Clone a Git repo, build a Docker image, deploy to K8s, and register in
          Sealfleet
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left: Deploy Form */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Deploy Configuration</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-sm font-medium">
                Repository URL <span className="text-destructive">*</span>
              </label>
              <Input
                placeholder="https://github.com/org/your-mcp-server"
                value={repoUrl}
                onChange={(e) => setRepoUrl(e.target.value)}
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-sm font-medium">
                Name <span className="text-destructive">*</span>
              </label>
              <Input
                placeholder="my-mcp-server"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                Slug used for image name and k8s deployment
              </p>
            </div>

            <div className="space-y-1.5">
              <label className="text-sm font-medium">Description</label>
              <textarea
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm min-h-[60px] resize-y"
                placeholder="What does this MCP server do?"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <label className="text-sm font-medium">Branch</label>
                <Input
                  value={branch}
                  onChange={(e) => setBranch(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-sm font-medium">Port</label>
                <Input
                  type="number"
                  value={port}
                  onChange={(e) => setPort(e.target.value)}
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-sm font-medium">Tags</label>
              <Input
                placeholder="mcp, finance, api"
                value={tagsInput}
                onChange={(e) => setTagsInput(e.target.value)}
              />
            </div>

            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={isPublic}
                onChange={(e) => setIsPublic(e.target.checked)}
                className="rounded"
              />
              Public (visible in catalog)
            </label>

            {/* Advanced section */}
            <button
              type="button"
              className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
              onClick={() => setShowAdvanced(!showAdvanced)}
            >
              <ChevronDown
                className={`h-4 w-4 transition-transform ${showAdvanced ? "rotate-180" : ""}`}
              />
              Advanced
            </button>

            {showAdvanced && (
              <div className="space-y-1.5">
                <label className="text-sm font-medium">
                  Environment Variables
                </label>
                <textarea
                  className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono min-h-[80px] resize-y"
                  placeholder={"KEY=value\nANOTHER_KEY=another_value"}
                  value={envVarsText}
                  onChange={(e) => setEnvVarsText(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  One per line, KEY=value format
                </p>
              </div>
            )}

            <Button
              className="w-full"
              onClick={handleDeploy}
              disabled={deploying || !repoUrl || !name}
            >
              {deploying ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Deploying...
                </>
              ) : (
                <>
                  <Rocket className="mr-2 h-4 w-4" />
                  Deploy
                </>
              )}
            </Button>
          </CardContent>
        </Card>

        {/* Right: Live Log Panel */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Deploy Log</CardTitle>
          </CardHeader>
          <CardContent>
            {/* Success banner */}
            {deployResult && (
              <div className="rounded-md bg-green-500/10 border border-green-500/30 p-3 mb-3 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="h-5 w-5 text-green-500" />
                  <div>
                    <p className="text-sm font-medium text-green-600">
                      Deployed!
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {deployResult.endpoint}
                    </p>
                  </div>
                </div>
                <Link href="/catalog">
                  <Button variant="outline" size="sm">
                    View in Catalog
                  </Button>
                </Link>
              </div>
            )}

            {/* Error banner */}
            {deployError && !deploying && !deployResult && (
              <div className="rounded-md bg-destructive/10 border border-destructive/30 p-3 mb-3 flex items-center gap-2">
                <XCircle className="h-5 w-5 text-destructive" />
                <div>
                  <p className="text-sm font-medium text-destructive">
                    Deploy failed
                  </p>
                  <p className="text-xs text-destructive/80">{deployError}</p>
                </div>
              </div>
            )}

            {/* Log entries */}
            <div className="rounded-md bg-zinc-950 p-4 overflow-auto max-h-[500px] min-h-[300px] space-y-1">
              {logs.length === 0 && !deploying && (
                <p className="text-xs text-zinc-500 font-mono">
                  Deploy logs will appear here...
                </p>
              )}
              {logs.map((entry, i) => (
                <div key={i} className="flex items-start gap-2 text-xs font-mono">
                  <span className="text-zinc-500 shrink-0">
                    {new Date(entry.ts).toLocaleTimeString()}
                  </span>
                  <Badge
                    className={`${stepColors[entry.step] || stepColors.error} text-white border-0 text-[10px] px-1.5 py-0 shrink-0`}
                  >
                    {entry.step}
                  </Badge>
                  <span
                    className={
                      entry.status === "error"
                        ? "text-red-400"
                        : entry.status === "done" || entry.status === "success"
                          ? "text-green-400"
                          : "text-zinc-300"
                    }
                  >
                    {entry.msg}
                  </span>
                </div>
              ))}
              {deploying && (
                <div className="flex items-center gap-2 text-xs font-mono text-blue-400">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  <span>Streaming...</span>
                </div>
              )}
              <div ref={logEndRef} />
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Deployments table */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Deployments</CardTitle>
        </CardHeader>
        <CardContent>
          {deployments.length === 0 ? (
            <p className="text-sm text-muted-foreground">No deployments yet</p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Repository</TableHead>
                    <TableHead>Endpoint</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Created</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {deployments.map((d) => (
                    <TableRow key={d.id}>
                      <TableCell className="font-medium">{d.name}</TableCell>
                      <TableCell className="text-xs text-muted-foreground max-w-[200px] truncate">
                        {d.repo_url || "—"}
                      </TableCell>
                      <TableCell>
                        {d.endpoint ? (
                          <code className="text-xs bg-muted px-1.5 py-0.5 rounded">
                            {d.endpoint}
                          </code>
                        ) : (
                          "—"
                        )}
                      </TableCell>
                      <TableCell>
                        <Badge
                          className={`${statusBadgeColors[d.status] || "bg-gray-500 text-white"} border-0 text-xs`}
                        >
                          {d.status}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {d.created_at
                          ? new Date(d.created_at).toLocaleDateString()
                          : "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
