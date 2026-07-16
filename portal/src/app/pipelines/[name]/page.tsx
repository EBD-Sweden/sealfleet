"use client";

import { useState, useEffect, useCallback, use } from "react";
import Link from "next/link";
import {
  Workflow,
  Loader2,
  Play,
  ArrowLeft,
  Clock,
  CheckCircle2,
  XCircle,
  Layers,
  Tag,
} from "lucide-react";

interface InputDef {
  type: string;
  description: string;
}

interface PipelineStage {
  name: string;
  mcp: string;
  tool: string;
  input_channel: string | null;
  output_channel: string | null;
}

interface PipelineDetail {
  name: string;
  description: string;
  inputs: Record<string, string | InputDef>;
  stages: PipelineStage[] | number;
  output_stage: string;
  tags: string[];
  created_at: string;
}

interface StepResult {
  step: number;
  stage?: string;
  mcp: string;
  tool: string;
  result: Record<string, unknown>;
  duration_ms: number;
}

interface PipelineResult {
  trace_id: string;
  total_duration_ms: number;
  steps: StepResult[];
  final?: Record<string, unknown>;
}

interface LandscapeItem {
  [key: string]: unknown;
}

function getInputDefs(inputs: Record<string, string | InputDef>): Array<{ name: string; type: string; description: string }> {
  return Object.entries(inputs).map(([name, def]) => {
    if (typeof def === "object" && def !== null) {
      return { name, type: def.type || "string", description: def.description || name };
    }
    return { name, type: "string", description: String(def) || name };
  });
}

function MemoDisplay({ text }: { text: string }) {
  const lines = text.split("\n");
  return (
    <div className="space-y-1">
      {lines.map((l, i) => {
        if (l.startsWith("## "))
          return (
            <h2 key={i} className="text-base font-bold text-white mt-5 mb-1">
              {l.slice(3)}
            </h2>
          );
        if (l.startsWith("# "))
          return (
            <h1 key={i} className="text-lg font-bold text-blue-300 mt-4 mb-2">
              {l.slice(2)}
            </h1>
          );
        if (l.startsWith("---"))
          return <hr key={i} className="border-gray-700 my-3" />;
        if (l.startsWith("- ") || l.startsWith("• ")) {
          return (
            <p
              key={i}
              className="text-gray-300 text-sm ml-4"
              dangerouslySetInnerHTML={{
                __html:
                  "• " +
                  l
                    .slice(2)
                    .replace(
                      /\*\*(.+?)\*\*/g,
                      "<strong class='text-white'>$1</strong>",
                    ),
              }}
            />
          );
        }
        if (!l.trim()) return <div key={i} className="h-1.5" />;
        return (
          <p
            key={i}
            className="text-gray-300 text-sm leading-relaxed"
            dangerouslySetInnerHTML={{
              __html: l
                .replace(
                  /\*\*(.+?)\*\*/g,
                  "<strong class='text-white'>$1</strong>",
                )
                .replace(
                  /`(.+?)`/g,
                  "<code class='bg-gray-800 px-1 rounded text-xs text-blue-300'>$1</code>",
                ),
            }}
          />
        );
      })}
    </div>
  );
}

function LandscapeCards({ items }: { items: LandscapeItem[] }) {
  // Group by a common key if available (e.g. category, type, group)
  const groupKey = ["category", "type", "group", "sector"].find(
    (k) => items[0] && k in items[0],
  );

  if (groupKey) {
    const groups: Record<string, LandscapeItem[]> = {};
    for (const item of items) {
      const g = String(item[groupKey] || "Other");
      if (!groups[g]) groups[g] = [];
      groups[g].push(item);
    }
    return (
      <div className="space-y-6">
        {Object.entries(groups).map(([group, groupItems]) => (
          <div key={group}>
            <h3 className="text-sm font-semibold text-gray-300 mb-3">
              {group}
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
              {groupItems.map((item, i) => (
                <ItemCard key={i} item={item} excludeKey={groupKey} />
              ))}
            </div>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
      {items.map((item, i) => (
        <ItemCard key={i} item={item} />
      ))}
    </div>
  );
}

function ItemCard({
  item,
  excludeKey,
}: {
  item: LandscapeItem;
  excludeKey?: string;
}) {
  const nameKey = ["name", "title", "company", "label"].find(
    (k) => k in item,
  );
  const name = nameKey ? String(item[nameKey]) : undefined;
  const entries = Object.entries(item).filter(
    ([k]) => k !== nameKey && k !== excludeKey,
  );

  return (
    <div className="bg-gray-800/50 rounded-lg border border-gray-700/50 p-3">
      {name && (
        <h4 className="text-sm font-medium text-white mb-2">{name}</h4>
      )}
      <div className="space-y-1">
        {entries.slice(0, 6).map(([k, v]) => (
          <div key={k} className="flex justify-between text-xs">
            <span className="text-gray-500">{k}</span>
            <span className="text-gray-300 text-right max-w-[60%] truncate">
              {typeof v === "object" ? JSON.stringify(v) : String(v)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ResultDisplay({ result }: { result: PipelineResult }) {
  const final = result.final || result.steps[result.steps.length - 1]?.result;
  if (!final) {
    return (
      <pre className="text-xs bg-gray-800 rounded-lg p-4 overflow-x-auto text-gray-300">
        {JSON.stringify(result, null, 2)}
      </pre>
    );
  }

  // Credit memo
  if ("credit_memo" in final && typeof final.credit_memo === "string") {
    return <MemoDisplay text={final.credit_memo} />;
  }

  // Landscape array
  if (
    "landscape" in final &&
    Array.isArray(final.landscape) &&
    final.landscape.length > 0
  ) {
    return <LandscapeCards items={final.landscape as LandscapeItem[]} />;
  }

  // Generic JSON
  return (
    <pre className="text-xs bg-gray-800 rounded-lg p-4 overflow-x-auto text-gray-300">
      {JSON.stringify(final, null, 2)}
    </pre>
  );
}

export default function PipelineRunPage({
  params,
}: {
  params: Promise<{ name: string }>;
}) {
  const { name } = use(params);
  const decodedName = decodeURIComponent(name);

  const [pipeline, setPipeline] = useState<PipelineDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<PipelineResult | null>(null);
  const [runError, setRunError] = useState("");

  // Job submission state
  const [submittingJob, setSubmittingJob] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<string | null>(null);
  const [jobResult, setJobResult] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    fetch(`/api/pipelines/${encodeURIComponent(decodedName)}`)
      .then((r) => r.json())
      .then((data) => {
        if (data.error) throw new Error(data.detail || data.error);
        setPipeline(data);
        // Initialize inputs
        const init: Record<string, string> = {};
        const inputDefs = data.inputs || {};
        for (const key of Object.keys(inputDefs)) {
          init[key] = "";
        }
        setInputs(init);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [decodedName]);

  const handleRun = useCallback(async () => {
    if (!pipeline) return;
    setRunning(true);
    setResult(null);
    setRunError("");
    try {
      const res = await fetch(
        `/api/pipelines/${encodeURIComponent(decodedName)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ inputs }),
        },
      );
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || data.error || `HTTP ${res.status}`);
      setResult(data);
    } catch (e) {
      setRunError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }, [pipeline, decodedName, inputs]);

  const handleRunAsJob = useCallback(async () => {
    if (!pipeline) return;
    setSubmittingJob(true);
    setJobId(null);
    setJobStatus(null);
    setJobResult(null);
    setRunError("");
    try {
      const res = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pipeline: decodedName, inputs }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || data.error || `HTTP ${res.status}`);
      const id = data.job_id || data.id;
      setJobId(id);
      setJobStatus("queued");
      // Start polling
      pollJob(id);
    } catch (e) {
      setRunError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmittingJob(false);
    }
  }, [pipeline, decodedName, inputs]);

  const pollJob = useCallback(
    (id: string) => {
      const interval = setInterval(async () => {
        try {
          const res = await fetch(`/api/jobs/${id}`);
          const data = await res.json();
          setJobStatus(data.status || "unknown");
          if (data.status === "completed" || data.status === "failed") {
            clearInterval(interval);
            if (data.result) setJobResult(data.result);
          }
        } catch {
          clearInterval(interval);
        }
      }, 2000);
      // Cleanup after 5 minutes
      setTimeout(() => clearInterval(interval), 300_000);
    },
    [],
  );

  const inputDefs = pipeline ? getInputDefs(pipeline.inputs) : [];
  const allFilled = inputDefs.every((d) => inputs[d.name]?.trim());
  const stageCount =
    typeof pipeline?.stages === "number"
      ? pipeline.stages
      : Array.isArray(pipeline?.stages)
        ? pipeline.stages.length
        : 0;

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-6">
      <div className="max-w-4xl mx-auto">
        {/* Back link */}
        <Link
          href="/pipelines"
          className="inline-flex items-center gap-1.5 text-sm text-gray-400 hover:text-white transition-colors mb-6"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          All Pipelines
        </Link>

        {loading && (
          <div className="flex items-center gap-2 text-gray-400 text-sm">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading pipeline…
          </div>
        )}

        {error && (
          <div className="bg-red-900/30 border border-red-700 rounded-lg p-4 text-red-300 text-sm">
            {error}
          </div>
        )}

        {pipeline && (
          <div className="space-y-6">
            {/* Pipeline header */}
            <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
              <div className="flex items-start justify-between">
                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <Workflow className="h-5 w-5 text-blue-400" />
                    <h1 className="text-xl font-bold text-white">
                      {pipeline.name}
                    </h1>
                  </div>
                  <p className="text-sm text-gray-400 leading-relaxed">
                    {pipeline.description || "No description"}
                  </p>
                </div>
                <div className="flex items-center gap-3 text-xs text-gray-500 shrink-0 ml-4">
                  <span className="flex items-center gap-1">
                    <Layers className="h-3 w-3" /> {stageCount} stages
                  </span>
                </div>
              </div>
              {pipeline.tags?.length > 0 && (
                <div className="flex gap-1.5 mt-3 pt-3 border-t border-gray-800">
                  {pipeline.tags.map((tag) => (
                    <span
                      key={tag}
                      className="flex items-center gap-0.5 text-[10px] text-gray-500 bg-gray-800 px-2 py-0.5 rounded"
                    >
                      <Tag className="h-2.5 w-2.5" />
                      {tag}
                    </span>
                  ))}
                </div>
              )}
            </div>

            {/* Input form */}
            <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
              <h2 className="text-sm font-semibold text-gray-300 mb-4">
                Pipeline Inputs
              </h2>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {inputDefs.map((def) => (
                  <div key={def.name}>
                    <label className="block text-xs text-gray-400 mb-1">
                      {def.description || def.name}
                    </label>
                    <input
                      value={inputs[def.name] || ""}
                      onChange={(e) =>
                        setInputs((prev) => ({
                          ...prev,
                          [def.name]: e.target.value,
                        }))
                      }
                      onKeyDown={(e) =>
                        e.key === "Enter" && allFilled && handleRun()
                      }
                      placeholder={def.name}
                      className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500"
                    />
                    <span className="text-[10px] text-gray-600 mt-0.5 font-mono">
                      {def.type}
                    </span>
                  </div>
                ))}
              </div>

              {/* Action buttons */}
              <div className="flex gap-3 mt-5">
                <button
                  onClick={handleRun}
                  disabled={running || !allFilled}
                  className="bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 text-white px-5 py-2 rounded-lg text-sm font-medium transition-colors flex items-center gap-2"
                >
                  {running ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" /> Running…
                    </>
                  ) : (
                    <>
                      <Play className="h-4 w-4" /> Run Pipeline
                    </>
                  )}
                </button>
                <button
                  onClick={handleRunAsJob}
                  disabled={submittingJob || !allFilled}
                  className="bg-gray-800 hover:bg-gray-700 disabled:bg-gray-800 disabled:text-gray-600 text-gray-200 px-5 py-2 rounded-lg text-sm font-medium transition-colors flex items-center gap-2 border border-gray-700"
                >
                  {submittingJob ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" /> Submitting…
                    </>
                  ) : (
                    <>
                      <Clock className="h-4 w-4" /> Run as Job (async)
                    </>
                  )}
                </button>
              </div>

              {runError && (
                <div className="mt-3 bg-red-900/30 border border-red-700 rounded-lg p-3 text-red-300 text-sm">
                  {runError}
                </div>
              )}
            </div>

            {/* Job status */}
            {jobId && (
              <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
                <h2 className="text-sm font-semibold text-gray-300 mb-3">
                  Job Status
                </h2>
                <div className="flex items-center gap-3">
                  <span className="text-xs text-gray-500 font-mono">
                    {jobId}
                  </span>
                  <JobStatusBadge status={jobStatus || "queued"} />
                </div>
                {jobResult && (
                  <div className="mt-4">
                    <pre className="text-xs bg-gray-800 rounded-lg p-4 overflow-x-auto text-gray-300">
                      {JSON.stringify(jobResult, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            )}

            {/* Run result */}
            {result && (
              <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
                <div className="flex items-center justify-between mb-4 pb-3 border-b border-gray-800">
                  <h2 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
                    <CheckCircle2 className="h-4 w-4 text-green-400" />
                    Result
                  </h2>
                  <div className="flex items-center gap-3 text-xs text-gray-500">
                    <span className="font-mono">{result.trace_id}</span>
                    <span>{result.total_duration_ms}ms</span>
                  </div>
                </div>
                <ResultDisplay result={result} />

                {/* Step details */}
                {result.steps.length > 1 && (
                  <div className="mt-6 pt-4 border-t border-gray-800">
                    <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">
                      Step Details
                    </h3>
                    <div className="space-y-2">
                      {result.steps.map((step, i) => (
                        <details
                          key={i}
                          className="bg-gray-800/50 rounded-lg border border-gray-700/50"
                        >
                          <summary className="px-3 py-2 text-sm cursor-pointer hover:bg-gray-800 rounded-lg flex items-center gap-2">
                            <span className="text-gray-500 font-mono text-xs">
                              #{step.step}
                            </span>
                            <span className="text-gray-300">
                              {step.stage || step.tool}
                            </span>
                            <span className="text-xs text-gray-600 font-mono ml-auto">
                              {step.mcp} · {step.duration_ms}ms
                            </span>
                          </summary>
                          <div className="px-3 pb-3">
                            <pre className="text-xs bg-gray-900 rounded p-3 overflow-x-auto text-gray-400">
                              {JSON.stringify(step.result, null, 2)}
                            </pre>
                          </div>
                        </details>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function JobStatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    queued: "bg-gray-700 text-gray-300",
    running: "bg-blue-900/50 text-blue-300 animate-pulse",
    completed: "bg-green-900/50 text-green-300",
    failed: "bg-red-900/50 text-red-300",
  };
  const icons: Record<string, React.ReactNode> = {
    queued: <Clock className="h-3 w-3" />,
    running: <Loader2 className="h-3 w-3 animate-spin" />,
    completed: <CheckCircle2 className="h-3 w-3" />,
    failed: <XCircle className="h-3 w-3" />,
  };

  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${styles[status] || styles.queued}`}
    >
      {icons[status] || icons.queued}
      {status}
    </span>
  );
}
