"use client";

import { useState, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Copy, Check, ChevronRight, ChevronDown } from "lucide-react";
import type { PipelineResultProps } from "@/lib/pipeline-renderers";

// ---------- Metadata header (shared with TextResult) ----------

export function ResultMetadataHeader({
  pipelineName,
  runId,
  metadata,
}: Pick<PipelineResultProps, "pipelineName" | "runId" | "metadata">) {
  const statusColor: Record<string, string> = {
    completed:
      "bg-green-500/15 text-green-600 border-green-500/30 hover:bg-green-500/20",
    failed:
      "bg-red-500/15 text-red-600 border-red-500/30 hover:bg-red-500/20",
    running:
      "bg-blue-500/15 text-blue-600 border-blue-500/30 hover:bg-blue-500/20",
  };

  return (
    <div className="flex flex-wrap items-center gap-2 mb-4">
      <h2 className="text-lg font-semibold">{pipelineName}</h2>
      {metadata?.status && (
        <Badge className={statusColor[metadata.status] ?? ""}>
          {metadata.status}
        </Badge>
      )}
      {metadata?.duration_ms != null && (
        <Badge variant="outline" className="text-xs font-mono">
          {metadata.duration_ms}ms
        </Badge>
      )}
      <span className="text-xs text-muted-foreground font-mono ml-auto">
        {runId.slice(0, 12)}…
      </span>
      {metadata?.started_at && (
        <span className="text-xs text-muted-foreground">
          {new Date(metadata.started_at).toLocaleString()}
        </span>
      )}
    </div>
  );
}

// ---------- Recursive JSON tree ----------

function JsonValue({ value, defaultOpen = false }: { value: unknown; defaultOpen?: boolean }) {
  if (value === null) return <span className="text-muted-foreground">null</span>;
  if (value === undefined) return <span className="text-muted-foreground">undefined</span>;

  if (typeof value === "boolean")
    return <span className="text-blue-600">{String(value)}</span>;
  if (typeof value === "number")
    return <span className="text-amber-600">{String(value)}</span>;
  if (typeof value === "string")
    return <span className="text-green-700">&quot;{value}&quot;</span>;

  if (Array.isArray(value)) return <JsonArray items={value} defaultOpen={defaultOpen} />;
  if (typeof value === "object") return <JsonObject obj={value as Record<string, unknown>} defaultOpen={defaultOpen} />;

  return <span>{String(value)}</span>;
}

function JsonObject({ obj, defaultOpen = false }: { obj: Record<string, unknown>; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const keys = Object.keys(obj);
  if (keys.length === 0) return <span className="text-muted-foreground">{"{}"}</span>;

  return (
    <div className="ml-3">
      <button
        onClick={() => setOpen(!open)}
        className="inline-flex items-center gap-0.5 text-xs text-muted-foreground hover:text-foreground"
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        <span className="font-mono">{"{"}…{"}"}</span>
        <span className="ml-1 text-muted-foreground">{keys.length} keys</span>
      </button>
      {open && (
        <div className="ml-4 border-l border-border pl-2 space-y-0.5 mt-0.5">
          {keys.map((k) => (
            <div key={k} className="flex gap-1 items-start text-xs">
              <span className="text-purple-600 font-mono shrink-0">{k}:</span>
              <JsonValue value={obj[k]} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function JsonArray({ items, defaultOpen = false }: { items: unknown[]; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  if (items.length === 0) return <span className="text-muted-foreground">[]</span>;

  return (
    <div className="ml-3">
      <button
        onClick={() => setOpen(!open)}
        className="inline-flex items-center gap-0.5 text-xs text-muted-foreground hover:text-foreground"
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        <span className="font-mono">[…]</span>
        <span className="ml-1 text-muted-foreground">{items.length} items</span>
      </button>
      {open && (
        <div className="ml-4 border-l border-border pl-2 space-y-1 mt-0.5">
          {items.map((item, i) => (
            <div key={i} className="text-xs">
              <span className="text-muted-foreground font-mono mr-1">{i}:</span>
              <JsonValue value={item} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------- Array item cards ----------

function ArrayResultCards({ items }: { items: unknown[] }) {
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">{items.length} items</p>
      {items.map((item, i) => (
        <Card key={i}>
          <CardContent className="pt-4">
            <div className="text-xs">
              <JsonValue value={item} defaultOpen />
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

// ---------- Main component ----------

export function GenericResult({ pipelineName, runId, result, metadata }: PipelineResultProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(JSON.stringify(result, null, 2)).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [result]);

  const isArray = Array.isArray(result);

  return (
    <div className="space-y-4">
      <ResultMetadataHeader
        pipelineName={pipelineName}
        runId={runId}
        metadata={metadata}
      />

      <Card>
        <CardHeader className="pb-2 flex flex-row items-center justify-between">
          <CardTitle className="text-base">
            {isArray ? "Result (Array)" : "Result"}
          </CardTitle>
          <Button variant="ghost" size="sm" onClick={handleCopy}>
            {copied ? (
              <Check className="h-4 w-4 text-green-500" />
            ) : (
              <Copy className="h-4 w-4" />
            )}
            <span className="ml-1 text-xs">{copied ? "Copied" : "Copy JSON"}</span>
          </Button>
        </CardHeader>
        <CardContent>
          {isArray ? (
            <ArrayResultCards items={result} />
          ) : (
            <div className="text-xs">
              <JsonValue value={result} defaultOpen />
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
