"use client";

import React, { use, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Loader2,
  Play,
  Search,
  XCircle,
} from "lucide-react";

interface McpTool {
  tool_id: string;
  server_id: string;
  name: string;
  description: string;
  input_schema: ToolInputSchema;
  category: string;
  tags: string[];
  version: string;
}

interface McpServer {
  server_id: string;
  name: string;
  endpoint: string;
  description: string;
  auth_methods: string[];
  status: string;
  tool_count: number;
  metadata?: Record<string, unknown>;
}

interface ToolInputProperty {
  example?: unknown;
  default?: unknown;
  type?: string;
  description?: string;
}

interface ToolInputSchema {
  properties?: Record<string, ToolInputProperty>;
  required?: string[];
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

const statusColor: Record<string, string> = {
  active: "bg-green-500",
  online: "bg-green-500",
  degraded: "bg-yellow-500",
  inactive: "bg-red-500",
  offline: "bg-red-500",
};

function ToolRunner({ serverName, tool }: { serverName: string; tool: McpTool }) {
  const [inputs, setInputs] = useState("{}");
  const [result, setResult] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  const run = async () => {
    setRunning(true);
    setResult(null);
    setRunError(null);
    try {
      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(inputs);
      } catch {
        throw new Error("Invalid JSON in inputs");
      }

      const res = await fetch("/api/call", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mcp: serverName, tool: tool.name, inputs: parsed }),
      });
      const data = await res.json() as { error?: string; detail?: string };
      if (!res.ok) throw new Error(data.error || data.detail || `HTTP ${res.status}`);
      setResult(JSON.stringify(data, null, 2));
    } catch (e: unknown) {
      setRunError(errorMessage(e, "Execution failed"));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <label className="text-sm font-medium">Input (JSON)</label>
          {Object.keys(tool.input_schema?.properties ?? {}).length > 0 && (
            <button
              className="text-xs text-muted-foreground hover:text-primary"
              onClick={() => {
                const ex: Record<string, unknown> = {};
                for (const [k, v] of Object.entries(tool.input_schema.properties ?? {})) {
                  ex[k] =
                    v.example ?? v.default ?? (v.type === "array" ? [] : v.type === "number" ? 0 : "");
                }
                setInputs(JSON.stringify(ex, null, 2));
              }}
            >
              Fill example
            </button>
          )}
        </div>
        <Textarea
          className="min-h-[120px] font-mono text-xs"
          value={inputs}
          onChange={(e) => setInputs(e.target.value)}
          placeholder='{"ticker": "NVDA", "keywords": ["HBM", "GPU"]}'
        />
      </div>

      <Button onClick={run} disabled={running} className="w-full">
        {running ? (
          <>
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            Running...
          </>
        ) : (
          <>
            <Play className="mr-2 h-4 w-4" />
            Run tool
          </>
        )}
      </Button>

      {runError && (
        <div className="flex items-start gap-2 rounded-md bg-destructive/10 p-3 text-sm text-destructive">
          <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{runError}</span>
        </div>
      )}

      {result && (
        <div className="space-y-2">
          <div className="flex items-center gap-1.5 text-sm text-green-600 dark:text-green-400">
            <CheckCircle2 className="h-4 w-4" />
            <span>Result</span>
          </div>
          <pre className="max-h-[400px] overflow-auto whitespace-pre-wrap rounded-md bg-muted p-3 text-xs">
            {result}
          </pre>
        </div>
      )}
    </div>
  );
}

export default function ServerDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [server, setServer] = useState<McpServer | null>(null);
  const [tools, setTools] = useState<McpTool[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [expandedSchemas, setExpandedSchemas] = useState<Set<string>>(new Set());
  const [toolSearch, setToolSearch] = useState("");

  const toggleSchema = (toolId: string) => {
    setExpandedSchemas((prev) => {
      const next = new Set(prev);
      if (next.has(toolId)) next.delete(toolId);
      else next.add(toolId);
      return next;
    });
  };

  useEffect(() => {
    const fetchServer = async () => {
      try {
        setLoading(true);
        const res = await fetch(`/api/servers/${id}`);
        if (!res.ok) throw new Error("Server not found");
        const data = await res.json();
        setServer(data.server);
        setTools(data.tools ?? []);
      } catch (e: unknown) {
        setError(errorMessage(e, "Failed to load server"));
      } finally {
        setLoading(false);
      }
    };
    fetchServer();
  }, [id]);

  const filteredTools = useMemo(() => {
    const query = toolSearch.trim().toLowerCase();
    if (!query) return tools;
    return tools.filter((tool) => {
      const haystack = [
        tool.name,
        tool.tool_id,
        tool.description,
        tool.category,
        tool.version,
        ...(tool.tags ?? []),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(query);
    });
  }, [toolSearch, tools]);

  if (loading) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-3">
          <Link href="/catalog">
            <Button variant="ghost" size="icon">
              <ArrowLeft className="h-4 w-4" />
            </Button>
          </Link>
          <div className="space-y-2">
            <Skeleton className="h-7 w-48" />
            <Skeleton className="h-4 w-72" />
          </div>
        </div>
        <div className="flex flex-wrap gap-4">
          {[1, 2, 3].map((i) => (
            <Card key={i} className="min-w-[200px] flex-1">
              <CardHeader className="pb-2">
                <Skeleton className="h-4 w-16" />
              </CardHeader>
              <CardContent>
                <Skeleton className="h-5 w-24" />
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    );
  }

  if (error || !server) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-3">
          <Link href="/catalog">
            <Button variant="ghost" size="icon">
              <ArrowLeft className="h-4 w-4" />
            </Button>
          </Link>
          <h1 className="text-2xl font-bold">Server Not Found</h1>
        </div>
        <Card>
          <CardContent className="py-8 text-center text-destructive">
            {error || "This server does not exist."}
          </CardContent>
        </Card>
      </div>
    );
  }

  const category = server.metadata?.category as string | undefined;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link href="/catalog">
          <Button variant="ghost" size="icon">
            <ArrowLeft className="h-4 w-4" />
          </Button>
        </Link>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-bold">{server.name}</h1>
            {category && (
              <Badge variant="outline" className="capitalize">
                {category}
              </Badge>
            )}
          </div>
          <p className="mt-0.5 text-sm text-muted-foreground">{server.description}</p>
        </div>
      </div>

      <div className="flex flex-wrap gap-4">
        <Card className="min-w-[160px] flex-1">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Status</CardTitle>
          </CardHeader>
          <CardContent className="flex items-center gap-2">
            <span className={`inline-block h-2.5 w-2.5 rounded-full ${statusColor[server.status] ?? "bg-gray-400"}`} />
            <span className="font-medium capitalize">{server.status}</span>
          </CardContent>
        </Card>
        <Card className="min-w-[200px] flex-1">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Endpoint</CardTitle>
          </CardHeader>
          <CardContent className="truncate font-mono text-sm">{server.endpoint}</CardContent>
        </Card>
        <Card className="min-w-[120px] flex-1">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Tools</CardTitle>
          </CardHeader>
          <CardContent className="font-medium">{server.tool_count ?? tools.length}</CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="space-y-4">
          <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <CardTitle>Tools ({filteredTools.length})</CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                Borrowed from the reference UI: surface useful tool metadata inline, but keep the page native to Sealfleet.
              </p>
            </div>
          </div>
          <div className="relative max-w-md">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              className="pl-8"
              placeholder="Search tools, ids, versions, tags..."
              value={toolSearch}
              onChange={(e) => setToolSearch(e.target.value)}
            />
          </div>
        </CardHeader>
        <CardContent>
          {filteredTools.length === 0 ? (
            <p className="py-4 text-center text-sm text-muted-foreground">
              No tools match your search.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Tool</TableHead>
                  <TableHead>Description</TableHead>
                  <TableHead>Metadata</TableHead>
                  <TableHead className="w-[100px]">Action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredTools.map((tool) => {
                  const props = tool.input_schema?.properties ?? {};
                  const required = tool.input_schema?.required ?? [];
                  const hasSchema = Object.keys(props).length > 0;
                  const isExpanded = expandedSchemas.has(tool.tool_id);
                  const tagList = tool.tags ?? [];
                  return (
                    <React.Fragment key={tool.tool_id}>
                      <TableRow>
                        <TableCell className="align-top">
                          <div className="flex items-start gap-1">
                            {hasSchema ? (
                              <button
                                onClick={() => toggleSchema(tool.tool_id)}
                                className="mt-0.5 rounded p-0.5 hover:bg-muted"
                              >
                                {isExpanded ? (
                                  <ChevronDown className="h-3.5 w-3.5" />
                                ) : (
                                  <ChevronRight className="h-3.5 w-3.5" />
                                )}
                              </button>
                            ) : (
                              <span className="mt-0.5 h-4 w-4" />
                            )}
                            <div className="space-y-1">
                              <div className="font-mono text-sm font-medium">{tool.name}</div>
                              <div className="text-xs text-muted-foreground">{tool.tool_id}</div>
                            </div>
                          </div>
                        </TableCell>
                        <TableCell className="align-top text-sm text-muted-foreground">
                          {tool.description || "No description available."}
                        </TableCell>
                        <TableCell className="align-top">
                          <div className="flex flex-wrap gap-1.5">
                            <Badge variant="secondary">v{tool.version || "unknown"}</Badge>
                            <Badge variant="outline">
                              {Object.keys(props).length} param{Object.keys(props).length === 1 ? "" : "s"}
                            </Badge>
                            {required.length > 0 && (
                              <Badge variant="outline">{required.length} required</Badge>
                            )}
                            {tool.category && (
                              <Badge variant="outline" className="capitalize">
                                {tool.category}
                              </Badge>
                            )}
                            {tagList.map((tag) => (
                              <Badge key={tag} variant="outline">
                                {tag}
                              </Badge>
                            ))}
                          </div>
                        </TableCell>
                        <TableCell className="align-top">
                          <Dialog>
                            <DialogTrigger asChild>
                              <Button variant="outline" size="sm">
                                <Play className="mr-1 h-3 w-3" />
                                Try it
                              </Button>
                            </DialogTrigger>
                            <DialogContent className="max-w-lg">
                              <DialogHeader>
                                <DialogTitle className="font-mono">{tool.name}</DialogTitle>
                                <DialogDescription>{tool.description}</DialogDescription>
                              </DialogHeader>
                              <ToolRunner serverName={server.name} tool={tool} />
                            </DialogContent>
                          </Dialog>
                        </TableCell>
                      </TableRow>
                      {isExpanded && hasSchema && (
                        <TableRow>
                          <TableCell colSpan={4} className="bg-muted/30 px-6 py-3">
                            <div className="mb-2 text-xs font-medium">Input Schema</div>
                            <div className="grid grid-cols-[auto_auto_1fr] gap-x-4 gap-y-1 text-xs">
                              {Object.entries(props).map(([field, def]) => {
                                const d = def as { type?: string; description?: string };
                                const isReq = required.includes(field);
                                return (
                                  <React.Fragment key={field}>
                                    <span className="font-mono font-medium">
                                      {field}
                                      {isReq && <span className="text-red-500">*</span>}
                                    </span>
                                    <span className="text-muted-foreground">{d.type ?? "any"}</span>
                                    <span className="text-muted-foreground">{d.description ?? ""}</span>
                                  </React.Fragment>
                                );
                              })}
                            </div>
                          </TableCell>
                        </TableRow>
                      )}
                    </React.Fragment>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
