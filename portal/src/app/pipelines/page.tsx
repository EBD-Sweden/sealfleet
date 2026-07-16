"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import {
  Workflow,
  Loader2,
  ArrowRight,
  Tag,
  Layers,
} from "lucide-react";

interface PipelineInputSchema {
  type: string;
  properties: Record<string, { type: string; description: string }>;
  required: string[];
}

interface PipelineSummary {
  name: string;
  description: string;
  inputs: PipelineInputSchema | Record<string, unknown>;
  tags: string[];
  stages: number | unknown[];
  output_stage: string;
}

function getInputFields(inputs: PipelineInputSchema | Record<string, unknown>): string[] {
  if (!inputs) return [];
  if ("properties" in inputs && inputs.properties && typeof inputs.properties === "object") {
    return Object.keys(inputs.properties as Record<string, unknown>);
  }
  return Object.keys(inputs);
}

export default function PipelinesPage() {
  const [pipelines, setPipelines] = useState<PipelineSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/pipelines")
      .then((r) => r.json())
      .then((data) => {
        if (data.error) throw new Error(data.error);
        setPipelines(Array.isArray(data) ? data : []);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-6">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <Workflow className="h-6 w-6 text-blue-400" />
            Pipelines
          </h1>
          <p className="text-sm text-gray-400 mt-1">
            Browse available pipelines and run them with auto-generated forms
          </p>
        </div>

        {loading && (
          <div className="flex items-center gap-2 text-gray-400 text-sm">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading pipelines…
          </div>
        )}

        {error && (
          <div className="bg-red-900/30 border border-red-700 rounded-lg p-4 mb-6 text-red-300 text-sm">
            {error}
          </div>
        )}

        {!loading && !error && pipelines.length === 0 && (
          <div className="bg-gray-900 rounded-xl border border-gray-800 p-8 text-center">
            <Workflow className="h-10 w-10 text-gray-600 mx-auto mb-3" />
            <p className="text-gray-400">No pipelines registered yet.</p>
            <p className="text-gray-500 text-sm mt-1">
              Create a pipeline YAML in runtime/pipelines/ to get started.
            </p>
          </div>
        )}

        {/* Pipeline card grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {pipelines.map((p) => {
            const inputFields = getInputFields(p.inputs);
            return (
              <Link
                key={p.name}
                href={`/pipelines/${encodeURIComponent(p.name)}`}
                className="group"
              >
                <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 h-full hover:border-blue-500/50 hover:bg-gray-900/80 transition-all cursor-pointer">
                  {/* Name + arrow */}
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <Workflow className="h-4 w-4 text-blue-400 shrink-0" />
                      <h3 className="font-semibold text-white text-sm">
                        {p.name}
                      </h3>
                    </div>
                    <ArrowRight className="h-4 w-4 text-gray-600 group-hover:text-blue-400 transition-colors shrink-0 mt-0.5" />
                  </div>

                  {/* Description */}
                  <p className="text-gray-400 text-xs leading-relaxed mb-4 line-clamp-2">
                    {p.description || "No description"}
                  </p>

                  {/* Input fields */}
                  {inputFields.length > 0 && (
                    <div className="mb-3">
                      <div className="text-[10px] uppercase text-gray-500 font-medium mb-1.5 tracking-wider">
                        Inputs
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {inputFields.map((f) => (
                          <span
                            key={f}
                            className="px-2 py-0.5 bg-gray-800 text-gray-300 rounded text-xs font-mono"
                          >
                            {f}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Footer: tags + stages */}
                  <div className="flex items-center justify-between pt-3 border-t border-gray-800">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      {p.tags?.map((tag) => (
                        <span
                          key={tag}
                          className="flex items-center gap-0.5 text-[10px] text-gray-500"
                        >
                          <Tag className="h-2.5 w-2.5" />
                          {tag}
                        </span>
                      ))}
                    </div>
                    <span className="flex items-center gap-1 text-[10px] text-gray-500">
                      <Layers className="h-3 w-3" />
                      {typeof p.stages === "number"
                        ? p.stages
                        : Array.isArray(p.stages)
                          ? p.stages.length
                          : 0}{" "}
                      stages
                    </span>
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      </div>
    </div>
  );
}
