"use client";

import React from "react";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { ArrowLeft, Loader2 } from "lucide-react";
import { getRenderer } from "@/lib/pipeline-renderers";
import { GenericResult } from "@/components/pipeline-results/GenericResult";
import type { PipelineResultProps } from "@/lib/pipeline-renderers";

interface JobResponse {
  job_id?: string;
  status?: string;
  result?: unknown;
  started_at?: string;
  completed_at?: string;
  duration_ms?: number;
  pipeline_name?: string;
  // The router may also return the result at top level
  [key: string]: unknown;
}

export default function PipelineResultPage() {
  const params = useParams<{ name: string; runId: string }>();
  const pipelineName = params.name;
  const runId = params.runId;

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [jobData, setJobData] = useState<JobResponse | null>(null);

  useEffect(() => {
    if (!pipelineName || !runId) return;

    fetch(`/api/pipelines/${encodeURIComponent(pipelineName)}/results/${encodeURIComponent(runId)}`)
      .then(async (res) => {
        if (!res.ok) {
          const text = await res.text();
          throw new Error(`Failed to fetch result: ${res.status} ${text}`);
        }
        return res.json() as Promise<JobResponse>;
      })
      .then((data) => setJobData(data))
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Unknown error"),
      )
      .finally(() => setLoading(false));
  }, [pipelineName, runId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        <span className="ml-2 text-muted-foreground">Loading result…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-4">
        <BackLink />
        <div className="rounded-md bg-destructive/10 border border-destructive/30 p-4 text-sm text-destructive">
          {error}
        </div>
      </div>
    );
  }

  if (!jobData) {
    return (
      <div className="space-y-4">
        <BackLink />
        <p className="text-muted-foreground">No result data found.</p>
      </div>
    );
  }

  // Build renderer props
  const result = jobData.result ?? jobData;
  const metadata: PipelineResultProps["metadata"] = {
    duration_ms: jobData.duration_ms ?? (jobData.total_duration_ms as number | undefined),
    started_at: jobData.started_at as string | undefined,
    completed_at: jobData.completed_at as string | undefined,
    status: jobData.status ?? "completed",
  };

  return (
    <div className="space-y-4">
      <BackLink />
      <PipelineResultRenderer
        pipelineName={pipelineName}
        runId={runId}
        result={result}
        metadata={metadata}
      />
    </div>
  );
}

function PipelineResultRenderer(props: PipelineResultProps) {
  const CustomRenderer = getRenderer(props.pipelineName);
  return CustomRenderer
    ? React.createElement(CustomRenderer, props)
    : <GenericResult {...props} />;
}

function BackLink() {
  return (
    <Link href="/pipelines">
      <Button variant="ghost" size="sm">
        <ArrowLeft className="h-4 w-4 mr-1" />
        Back to Pipelines
      </Button>
    </Link>
  );
}
