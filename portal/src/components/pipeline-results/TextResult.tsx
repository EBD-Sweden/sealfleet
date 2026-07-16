"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ResultMetadataHeader } from "./GenericResult";
import type { PipelineResultProps } from "@/lib/pipeline-renderers";

/** Extract the text content from various result shapes */
function extractText(result: unknown): string | null {
  if (typeof result === "string") return result;
  if (result && typeof result === "object") {
    const obj = result as Record<string, unknown>;
    if (typeof obj.text === "string") return obj.text;
    if (typeof obj.markdown === "string") return obj.markdown;
    if (typeof obj.content === "string") return obj.content;
  }
  return null;
}

/** Very light markdown-ish rendering: headers, bold, bullet lists */
function renderSimpleMarkdown(text: string) {
  const lines = text.split("\n");

  return lines.map((line, i) => {
    // Headers
    const h1 = line.match(/^# (.+)$/);
    if (h1) return <h1 key={i} className="text-xl font-bold mt-4 mb-1">{h1[1]}</h1>;
    const h2 = line.match(/^## (.+)$/);
    if (h2) return <h2 key={i} className="text-lg font-semibold mt-3 mb-1">{h2[1]}</h2>;
    const h3 = line.match(/^### (.+)$/);
    if (h3) return <h3 key={i} className="text-base font-semibold mt-2 mb-1">{h3[1]}</h3>;

    // Bullet list
    const bullet = line.match(/^[-*] (.+)$/);
    if (bullet) {
      return (
        <div key={i} className="flex items-start gap-2 ml-2">
          <span className="h-1.5 w-1.5 rounded-full bg-primary shrink-0 mt-1.5" />
          <span>{renderInline(bullet[1])}</span>
        </div>
      );
    }

    // Empty line
    if (line.trim() === "") return <div key={i} className="h-2" />;

    // Normal paragraph
    return <p key={i}>{renderInline(line)}</p>;
  });
}

/** Inline bold rendering */
function renderInline(text: string) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) => {
    const bold = part.match(/^\*\*(.+)\*\*$/);
    if (bold) return <strong key={i}>{bold[1]}</strong>;
    return <span key={i}>{part}</span>;
  });
}

export function TextResult({ pipelineName, runId, result, metadata }: PipelineResultProps) {
  const text = extractText(result);

  if (!text) {
    // Fallback: shouldn't normally happen
    return (
      <div>
        <ResultMetadataHeader pipelineName={pipelineName} runId={runId} metadata={metadata} />
        <Card>
          <CardContent className="pt-4">
            <pre className="text-xs whitespace-pre-wrap">{JSON.stringify(result, null, 2)}</pre>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <ResultMetadataHeader pipelineName={pipelineName} runId={runId} metadata={metadata} />
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Result</CardTitle>
        </CardHeader>
        <CardContent className="prose prose-sm dark:prose-invert max-w-none">
          {renderSimpleMarkdown(text)}
        </CardContent>
      </Card>
    </div>
  );
}
