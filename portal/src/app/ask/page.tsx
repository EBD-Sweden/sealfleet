"use client";

import { useState, useRef, useEffect } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Send, Loader2, ChevronDown, ChevronUp } from "lucide-react";

// --- Types ---

interface AskResponse {
  question: string;
  output_type: string;
  resolved_chain: string[];
  inputs_used: Record<string, unknown>;
  answer: string;
  raw_result: Record<string, unknown>;
  reasoning: string;
}

interface ChatMessage {
  role: "user" | "agent";
  content: string;
  data?: AskResponse;
  timestamp: number;
  duration_ms?: number;
}

// --- Component ---

export default function AskPage() {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const handleSend = async () => {
    const question = input.trim();
    if (!question || loading) return;

    setInput("");
    setMessages((prev) => [
      ...prev,
      { role: "user", content: question, timestamp: Date.now() },
    ]);
    setLoading(true);

    const start = Date.now();
    try {
      const res = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });

      const elapsed = Date.now() - start;

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        setMessages((prev) => [
          ...prev,
          {
            role: "agent",
            content: err.error || err.detail || `Error ${res.status}`,
            timestamp: Date.now(),
            duration_ms: elapsed,
          },
        ]);
        return;
      }

      const data: AskResponse = await res.json();
      setMessages((prev) => [
        ...prev,
        {
          role: "agent",
          content: data.answer,
          data,
          timestamp: Date.now(),
          duration_ms: elapsed,
        },
      ]);
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          role: "agent",
          content: "Failed to reach the agent. Is the core-agent running on port 8050?",
          timestamp: Date.now(),
          duration_ms: Date.now() - start,
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)]">
      {/* Header */}
      <div className="pb-4">
        <h1 className="text-2xl font-bold">Ask</h1>
        <p className="text-sm text-muted-foreground">
          Ask a question. The agent discovers the right tool chain and answers.
        </p>
      </div>

      {/* Chat area */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto space-y-4 pb-4">
        {messages.length === 0 && (
          <div className="flex items-center justify-center h-full">
            <div className="text-center text-muted-foreground space-y-2">
              <p className="text-sm">Try asking something like:</p>
              <div className="flex flex-wrap gap-2 justify-center">
                {[
                  "What should I wear in Stockholm today?",
                  "What's the weather in London?",
                  "Recommend an outfit for Tokyo",
                ].map((q) => (
                  <button
                    key={q}
                    onClick={() => { setInput(q); }}
                    className="text-xs border rounded-full px-3 py-1.5 hover:bg-accent transition-colors"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {messages.map((msg, i) =>
          msg.role === "user" ? (
            <div key={i} className="flex justify-end">
              <div className="max-w-[70%] rounded-2xl rounded-br-sm bg-primary text-primary-foreground px-4 py-2.5">
                <p className="text-sm">{msg.content}</p>
              </div>
            </div>
          ) : (
            <AgentMessage key={i} msg={msg} />
          ),
        )}

        {loading && (
          <div className="flex justify-start">
            <div className="rounded-2xl rounded-bl-sm border bg-card px-4 py-3">
              <div className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-muted-foreground/40 animate-bounce [animation-delay:-0.3s]" />
                <span className="h-2 w-2 rounded-full bg-muted-foreground/40 animate-bounce [animation-delay:-0.15s]" />
                <span className="h-2 w-2 rounded-full bg-muted-foreground/40 animate-bounce" />
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Input bar */}
      <div className="border-t pt-4">
        <div className="flex gap-3">
          <Input
            placeholder="Ask a question..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSend()}
            disabled={loading}
            className="flex-1"
          />
          <Button onClick={handleSend} disabled={loading || !input.trim()} size="icon">
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}

// --- Agent message bubble ---

function AgentMessage({ msg }: { msg: ChatMessage }) {
  const [showRaw, setShowRaw] = useState(false);
  const data = msg.data;

  return (
    <div className="flex justify-start">
      <div className="max-w-[80%] space-y-2">
        {/* Chain badge */}
        {data && data.resolved_chain.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {data.resolved_chain.map((step, i) => (
              <span key={i} className="inline-flex items-center gap-1">
                <Badge variant="secondary" className="text-[10px] font-mono">
                  {step}
                </Badge>
                {i < data.resolved_chain.length - 1 && (
                  <span className="text-muted-foreground text-xs">&rarr;</span>
                )}
              </span>
            ))}
          </div>
        )}

        {/* Answer card */}
        <Card className="rounded-2xl rounded-bl-sm">
          <CardContent className="p-4">
            <p className="text-sm leading-relaxed">{msg.content}</p>

            {/* Footer */}
            <div className="flex items-center gap-3 mt-3 pt-2 border-t">
              {msg.duration_ms != null && (
                <span className="text-[10px] text-muted-foreground">
                  {(msg.duration_ms / 1000).toFixed(1)}s
                </span>
              )}
              {data && data.output_type !== "none" && (
                <Badge variant="outline" className="text-[10px]">
                  {data.output_type}
                </Badge>
              )}
              {data && data.raw_result && Object.keys(data.raw_result).length > 0 && (
                <button
                  onClick={() => setShowRaw(!showRaw)}
                  className="ml-auto flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground transition-colors"
                >
                  Raw result
                  {showRaw ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                </button>
              )}
            </div>

            {/* Raw result collapsible */}
            {showRaw && data?.raw_result && (
              <pre className="mt-2 p-3 rounded-md bg-muted text-[11px] font-mono overflow-x-auto max-h-64 overflow-y-auto">
                {JSON.stringify(data.raw_result, null, 2)}
              </pre>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
