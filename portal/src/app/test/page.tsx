"use client";

import { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Play, Loader2, RefreshCw } from "lucide-react";

// --- Types ---

interface ToolParam {
  name: string;
  type: "string" | "integer" | "enum";
  description: string;
  required: boolean;
  default?: string | number;
  options?: string[];
}

interface ToolDef {
  name: string;
  description: string;
  params: ToolParam[];
}

interface ServerDef {
  name: string;
  label: string;
  endpoint: string;
  tools: ToolDef[];
}

// --- Helpers ---

function schemaToParams(inputSchema: Record<string, unknown>): ToolParam[] {
  if (!inputSchema || typeof inputSchema !== "object") return [];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const props = (inputSchema as any).properties || {};
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const required: string[] = (inputSchema as any).required || [];

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return Object.entries(props).map(([name, def]: [string, any]) => ({
    name,
    type: def.type === "integer" || def.type === "number" ? "integer" as const : def.enum ? "enum" as const : "string" as const,
    description: def.description || name,
    required: required.includes(name),
    default: def.default,
    options: def.enum,
  }));
}

type Status = "idle" | "running" | "success" | "error";

const statusColors: Record<Status, string> = {
  idle: "bg-gray-500",
  running: "bg-blue-500",
  success: "bg-green-500",
  error: "bg-red-500",
};

export default function TestConsolePage() {
  // Dynamic server loading state
  const [servers, setServers] = useState<ServerDef[]>([]);
  const [loadingServers, setLoadingServers] = useState(true);
  const [serverError, setServerError] = useState("");

  const [serverIdx, setServerIdx] = useState(0);
  const [toolIdx, setToolIdx] = useState(0);
  const [apiKey, setApiKey] = useState("");
  const [inputValues, setInputValues] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<Status>("idle");
  const [result, setResult] = useState<string>("");
  const [responseTime, setResponseTime] = useState<number | null>(null);
  const [errorMsg, setErrorMsg] = useState("");

  const server = servers[serverIdx];
  const tool = server?.tools[toolIdx];

  // Load servers dynamically from registry
  const loadServers = useCallback(async () => {
    try {
      setLoadingServers(true);
      setServerError("");
      const listResp = await fetch("/api/servers");
      if (!listResp.ok) throw new Error(`Registry returned ${listResp.status}`);
      const listData = await listResp.json();
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const serverList: any[] = listData.servers || [];

      // Load tools for each server in parallel
      const serverDefs = await Promise.all(
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        serverList.map(async (s: any) => {
          try {
            const detailResp = await fetch(`/api/servers/${s.server_id}`);
            const detail = await detailResp.json();
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const tools: ToolDef[] = (detail.tools || []).map((t: any) => ({
              name: t.name,
              description: t.description || t.name,
              params: schemaToParams(t.input_schema || {}),
            }));
            return {
              name: s.name,
              label: s.description || s.name,
              endpoint: s.endpoint,
              tools,
            } as ServerDef;
          } catch {
            return null;
          }
        })
      );

      const validServers = serverDefs.filter(Boolean) as ServerDef[];
      setServers(validServers);
      setServerIdx(0);
      setToolIdx(0);
    } catch {
      setServerError("Failed to load servers from registry");
      setServers([]);
    } finally {
      setLoadingServers(false);
    }
  }, []);

  useEffect(() => {
    loadServers();
  }, [loadServers]);

  // Load API key from localStorage
  useEffect(() => {
    const saved = localStorage.getItem("mcpfinder_api_key");
    if (saved) setApiKey(saved);
  }, []);

  // Save API key to localStorage
  useEffect(() => {
    localStorage.setItem("mcpfinder_api_key", apiKey);
  }, [apiKey]);

  // Reset tool selection and inputs when server changes
  useEffect(() => {
    setToolIdx(0);
    setInputValues({});
  }, [serverIdx]);

  // Reset inputs when tool changes
  useEffect(() => {
    if (!tool) return;
    const defaults: Record<string, string> = {};
    tool.params.forEach((p) => {
      if (p.default !== undefined) {
        defaults[p.name] = String(p.default);
      }
    });
    setInputValues(defaults);
  }, [toolIdx, tool]);

  const handleRun = useCallback(async () => {
    if (!server || !tool) return;
    setStatus("running");
    setResult("");
    setErrorMsg("");
    setResponseTime(null);

    // Build inputs object with proper types
    const inputs: Record<string, unknown> = {};
    tool.params.forEach((p) => {
      const val = inputValues[p.name];
      if (val === undefined || val === "") return;
      if (p.type === "integer") {
        inputs[p.name] = parseInt(val, 10);
      } else {
        inputs[p.name] = val;
      }
    });

    const start = performance.now();

    try {
      const res = await fetch("/api/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          serverName: server.name,
          tool: tool.name,
          inputs,
          apiKey: apiKey || undefined,
        }),
      });

      const elapsed = Math.round(performance.now() - start);
      setResponseTime(elapsed);

      const data = await res.json();

      if (!res.ok) {
        setStatus("error");
        setErrorMsg(data.error || `HTTP ${res.status}`);
        return;
      }

      setStatus("success");
      setResult(JSON.stringify(data, null, 2));
    } catch (err) {
      const elapsed = Math.round(performance.now() - start);
      setResponseTime(elapsed);
      setStatus("error");
      setErrorMsg(err instanceof Error ? err.message : "Unknown error");
    }
  }, [server, tool, inputValues, apiKey]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Test Console</h1>
        <p className="text-sm text-muted-foreground">
          Call MCP tools directly and inspect results
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left panel -- Controls */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle className="text-base">Configuration</CardTitle>
              <Button
                variant="ghost"
                size="sm"
                onClick={loadServers}
                disabled={loadingServers}
                title="Reload servers from registry"
              >
                <RefreshCw className={`h-4 w-4 ${loadingServers ? "animate-spin" : ""}`} />
              </Button>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* Server selector */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Server</label>
              {loadingServers ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground py-2">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Loading servers from registry...
                </div>
              ) : serverError ? (
                <div className="text-sm text-red-500 py-2">{serverError}</div>
              ) : servers.length === 0 ? (
                <div className="text-sm text-muted-foreground py-2">No servers registered yet.</div>
              ) : (
                <select
                  className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  value={serverIdx}
                  onChange={(e) => setServerIdx(Number(e.target.value))}
                >
                  {servers.map((s, i) => (
                    <option key={s.name} value={i}>
                      {s.label} ({s.endpoint})
                    </option>
                  ))}
                </select>
              )}
            </div>

            {/* API Key */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium">API Key</label>
              <Input
                type="password"
                placeholder="evid_sk_..."
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                Stored in localStorage. Required for external tool credentials.
              </p>
            </div>

            {server && tool && (
              <>
                {/* Tool selector */}
                <div className="space-y-1.5">
                  <label className="text-sm font-medium">Tool</label>
                  <select
                    className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                    value={toolIdx}
                    onChange={(e) => setToolIdx(Number(e.target.value))}
                  >
                    {server.tools.map((t, i) => (
                      <option key={t.name} value={i}>
                        {t.name}
                      </option>
                    ))}
                  </select>
                  <p className="text-xs text-muted-foreground">
                    {tool.description}
                  </p>
                </div>

                {/* Dynamic input fields */}
                {tool.params.map((p) => (
                  <div key={p.name} className="space-y-1.5">
                    <label className="text-sm font-medium">
                      {p.name}
                      {p.required && (
                        <span className="text-destructive ml-0.5">*</span>
                      )}
                    </label>
                    {p.type === "enum" && p.options ? (
                      <select
                        className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                        value={inputValues[p.name] ?? (p.default ? String(p.default) : "")}
                        onChange={(e) =>
                          setInputValues((prev) => ({
                            ...prev,
                            [p.name]: e.target.value,
                          }))
                        }
                      >
                        {p.options.map((opt) => (
                          <option key={opt} value={opt}>
                            {opt}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <Input
                        type={p.type === "integer" ? "number" : "text"}
                        placeholder={p.description}
                        value={inputValues[p.name] ?? ""}
                        onChange={(e) =>
                          setInputValues((prev) => ({
                            ...prev,
                            [p.name]: e.target.value,
                          }))
                        }
                      />
                    )}
                  </div>
                ))}

                {/* Run button */}
                <Button
                  className="w-full"
                  onClick={handleRun}
                  disabled={status === "running"}
                >
                  {status === "running" ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      Running...
                    </>
                  ) : (
                    <>
                      <Play className="mr-2 h-4 w-4" />
                      Run Tool
                    </>
                  )}
                </Button>
              </>
            )}
          </CardContent>
        </Card>

        {/* Right panel -- Results */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle className="text-base">Result</CardTitle>
              <div className="flex items-center gap-2">
                {responseTime !== null && (
                  <span className="text-xs text-muted-foreground">
                    {responseTime}ms
                  </span>
                )}
                <Badge
                  variant="outline"
                  className={`${statusColors[status]} text-white border-0 text-xs`}
                >
                  {status}
                </Badge>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            {errorMsg && (
              <div className="rounded-md bg-destructive/10 border border-destructive/20 p-3 mb-3">
                <p className="text-sm text-destructive font-medium">Error</p>
                <p className="text-sm text-destructive/80 mt-1">{errorMsg}</p>
              </div>
            )}
            <pre className="rounded-md bg-zinc-950 text-zinc-100 p-4 text-xs font-mono overflow-auto max-h-[600px] min-h-[200px] whitespace-pre-wrap">
              {result || (status === "idle" ? "Run a tool to see results here..." : "")}
            </pre>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
