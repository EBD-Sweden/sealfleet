"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import {
  Clock,
  Loader2,
  CheckCircle2,
  XCircle,
  ChevronDown,
  ChevronRight,
  Cog,
} from "lucide-react";

interface Job {
  job_id: string;
  id?: string;
  pipeline_name: string;
  pipeline?: string;
  status: string;
  created_at: string;
  started_at?: string;
  completed_at?: string;
  duration_ms?: number;
  inputs?: Record<string, unknown>;
  result?: Record<string, unknown>;
  error?: string;
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

function formatDuration(ms?: number): string {
  if (!ms) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatTime(iso?: string): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export default function JobsPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const fetchJobs = useCallback(async () => {
    try {
      const res = await fetch("/api/jobs");
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      const list = Array.isArray(data) ? data : data.jobs || [];
      setJobs(list);
      return list;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return [];
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial fetch
  useEffect(() => {
    fetchJobs();
  }, [fetchJobs]);

  // Auto-refresh when any job is running/queued
  useEffect(() => {
    const hasActive = jobs.some(
      (j) => j.status === "running" || j.status === "queued",
    );
    if (!hasActive) return;

    const interval = setInterval(fetchJobs, 5000);
    return () => clearInterval(interval);
  }, [jobs, fetchJobs]);

  const toggleExpand = (id: string) => {
    setExpandedId((prev) => (prev === id ? null : id));
  };

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-6">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <div className="mb-8 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-white flex items-center gap-2">
              <Cog className="h-6 w-6 text-blue-400" />
              Jobs
            </h1>
            <p className="text-sm text-gray-400 mt-1">
              Monitor async pipeline job execution
            </p>
          </div>
          <button
            onClick={() => {
              setLoading(true);
              fetchJobs();
            }}
            className="bg-gray-800 hover:bg-gray-700 text-gray-300 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors border border-gray-700"
          >
            Refresh
          </button>
        </div>

        {loading && jobs.length === 0 && (
          <div className="flex items-center gap-2 text-gray-400 text-sm">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading jobs…
          </div>
        )}

        {error && (
          <div className="bg-red-900/30 border border-red-700 rounded-lg p-4 mb-6 text-red-300 text-sm">
            {error}
          </div>
        )}

        {!loading && !error && jobs.length === 0 && (
          <div className="bg-gray-900 rounded-xl border border-gray-800 p-8 text-center">
            <Cog className="h-10 w-10 text-gray-600 mx-auto mb-3" />
            <p className="text-gray-400">No jobs found.</p>
            <p className="text-gray-500 text-sm mt-1">
              Run a pipeline as a job from the{" "}
              <Link href="/pipelines" className="text-blue-400 hover:underline">
                Pipelines
              </Link>{" "}
              page.
            </p>
          </div>
        )}

        {jobs.length > 0 && (
          <div className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800">
                  <th className="w-8" />
                  <th className="text-left px-4 py-3 text-xs text-gray-500 uppercase font-medium">
                    Job ID
                  </th>
                  <th className="text-left px-4 py-3 text-xs text-gray-500 uppercase font-medium">
                    Pipeline
                  </th>
                  <th className="text-left px-4 py-3 text-xs text-gray-500 uppercase font-medium">
                    Status
                  </th>
                  <th className="text-left px-4 py-3 text-xs text-gray-500 uppercase font-medium">
                    Created
                  </th>
                  <th className="text-right px-4 py-3 text-xs text-gray-500 uppercase font-medium">
                    Duration
                  </th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => {
                  const id = job.job_id || job.id || "unknown";
                  const pName = job.pipeline_name || job.pipeline || "—";
                  const isExpanded = expandedId === id;

                  return (
                    <tbody key={id}>
                      <tr
                        onClick={() => toggleExpand(id)}
                        className="border-b border-gray-800/50 hover:bg-gray-800/30 cursor-pointer transition-colors"
                      >
                        <td className="pl-3">
                          {isExpanded ? (
                            <ChevronDown className="h-3.5 w-3.5 text-gray-500" />
                          ) : (
                            <ChevronRight className="h-3.5 w-3.5 text-gray-500" />
                          )}
                        </td>
                        <td className="px-4 py-3 font-mono text-xs text-gray-300">
                          {id.length > 12 ? id.slice(0, 12) + "…" : id}
                        </td>
                        <td className="px-4 py-3">
                          <Link
                            href={`/pipelines/${encodeURIComponent(pName)}`}
                            className="text-blue-400 hover:underline text-sm"
                            onClick={(e) => e.stopPropagation()}
                          >
                            {pName}
                          </Link>
                        </td>
                        <td className="px-4 py-3">
                          <JobStatusBadge status={job.status} />
                        </td>
                        <td className="px-4 py-3 text-xs text-gray-500">
                          {formatTime(job.created_at)}
                        </td>
                        <td className="px-4 py-3 text-right font-mono text-xs text-gray-400">
                          {formatDuration(job.duration_ms)}
                        </td>
                      </tr>
                      {isExpanded && (
                        <tr className="bg-gray-800/20">
                          <td colSpan={6} className="px-6 py-4">
                            <div className="space-y-3">
                              {job.inputs && (
                                <div>
                                  <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
                                    Inputs
                                  </h4>
                                  <pre className="text-xs bg-gray-800 rounded-lg p-3 overflow-x-auto text-gray-400">
                                    {JSON.stringify(job.inputs, null, 2)}
                                  </pre>
                                </div>
                              )}
                              {job.result && (
                                <div>
                                  <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
                                    Result
                                  </h4>
                                  <pre className="text-xs bg-gray-800 rounded-lg p-3 overflow-x-auto text-gray-400 max-h-96">
                                    {JSON.stringify(job.result, null, 2)}
                                  </pre>
                                </div>
                              )}
                              {job.error && (
                                <div>
                                  <h4 className="text-xs font-semibold text-red-500 uppercase tracking-wider mb-1">
                                    Error
                                  </h4>
                                  <p className="text-sm text-red-300">
                                    {job.error}
                                  </p>
                                </div>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </tbody>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
