"use client";

import { useState, useRef, useEffect } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Send, Loader2, ChevronDown, ChevronUp, Cpu, Cloud, KeyRound, Plus, MessageSquare, Trash2 } from "lucide-react";

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

interface BrainOption {
  id: string;
  label: string;
  kind: "local" | "cloud" | "byo";
  description: string;
  /** Present for "byo/…" brains: the stored credential backing the provider. */
  credential_id?: string;
  /** Present for a "byo/custom/…" brain: the tenant's own endpoint. */
  base_url?: string;
}

interface ChatSummary {
  id: string;
  title: string;
  updated_at: string;
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
  const [brains, setBrains] = useState<BrainOption[]>([]);
  const [model, setModel] = useState<string | null>(null);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [chatId, setChatId] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const refreshChats = async () => {
    try {
      const res = await fetch("/api/chats");
      if (res.ok) setChats((await res.json()).chats ?? []);
    } catch { /* sidebar is a convenience */ }
  };

  useEffect(() => { refreshChats(); }, []);

  const newChat = () => {
    setChatId(null);
    setMessages([]);
    setInput("");
  };

  const openChat = async (id: string) => {
    if (loading) return;
    try {
      const res = await fetch(`/api/chats/${id}`);
      if (!res.ok) return;
      const data: { messages: { role: "user" | "agent"; content: string; payload?: AskResponse; created_at: string }[] } = await res.json();
      setChatId(id);
      setMessages(
        (data.messages ?? []).map((m) => ({
          role: m.role,
          content: m.content,
          data: m.role === "agent" ? (m.payload as AskResponse | undefined) : undefined,
          timestamp: Date.parse(m.created_at) || Date.now(),
        })),
      );
    } catch { /* leave the current view */ }
  };

  const removeChat = async (id: string) => {
    try {
      const res = await fetch(`/api/chats/${id}`, { method: "DELETE" });
      if (res.ok || res.status === 404) {
        if (id === chatId) newChat();
        refreshChats();
      }
    } catch { /* ignore */ }
  };

  // Which brains this deployment offers. Restore the last choice, but only if
  // it is still on offer — an operator may have stopped the local model.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/ask/models");
        if (!res.ok) return;
        const data: { default: string | null; models: BrainOption[] } = await res.json();
        if (cancelled) return;

        setBrains(data.models ?? []);
        const saved = (() => {
          try { return localStorage.getItem("mcpfinder.ask.model"); } catch { return null; }
        })();
        const stillOffered = data.models?.some((m) => m.id === saved);
        setModel(stillOffered ? saved : (data.models?.[0]?.id ?? data.default ?? null));
      } catch {
        // Picker is a convenience; the chat still works on the server default.
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const selectBrain = (id: string) => {
    setModel(id);
    try { localStorage.setItem("mcpfinder.ask.model", id); } catch { /* private mode */ }
  };

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

    const selected = brains.find((b) => b.id === model);
    const start = Date.now();
    try {
      const res = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          model
            ? {
                question,
                model,
                ...(chatId ? { chat_id: chatId } : {}),
                ...(selected?.credential_id ? { credential_id: selected.credential_id } : {}),
                ...(selected?.base_url ? { base_url: selected.base_url } : {}),
              }
            : { question, ...(chatId ? { chat_id: chatId } : {}) },
        ),
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

      const data: AskResponse & { chat_id?: string | null } = await res.json();
      if (data.chat_id) {
        if (!chatId) setChatId(data.chat_id);
        refreshChats();
      }
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
    <div className="flex h-[calc(100vh-4rem)] gap-4">
      {/* Chat history sidebar */}
      <aside className="w-60 shrink-0 flex flex-col border-r pr-3">
        <button
          onClick={newChat}
          className="flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-accent transition-colors mb-3"
        >
          <Plus className="h-4 w-4" /> New chat
        </button>
        <div className="flex-1 overflow-y-auto space-y-1">
          {chats.length === 0 && (
            <p className="text-xs text-muted-foreground px-1">No chats yet.</p>
          )}
          {chats.map((c) => (
            <div
              key={c.id}
              className={`group flex items-center gap-2 rounded-md px-2 py-1.5 cursor-pointer text-sm transition-colors ${
                c.id === chatId ? "bg-accent" : "hover:bg-accent/50"
              }`}
              onClick={() => openChat(c.id)}
            >
              <MessageSquare className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="flex-1 truncate" title={c.title}>{c.title}</span>
              <button
                onClick={(e) => { e.stopPropagation(); removeChat(c.id); }}
                aria-label="Delete chat"
                className="opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-destructive transition-opacity"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      </aside>

      <div className="flex flex-col flex-1 min-w-0">
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
        {/* Brain picker — only worth showing when there is a real choice */}
        {brains.length > 1 && (
          <div className="flex items-center gap-2 pb-3">
            <span className="text-[11px] text-muted-foreground">Answer using</span>
            <div className="flex gap-1.5 flex-wrap">
              {brains.map((b) => {
                const active = b.id === model;
                const Icon = b.kind === "local" ? Cpu : b.kind === "byo" ? KeyRound : Cloud;
                return (
                  <button
                    key={b.id}
                    onClick={() => selectBrain(b.id)}
                    title={b.description}
                    aria-pressed={active}
                    className={`inline-flex items-center gap-1.5 text-[11px] border rounded-full px-2.5 py-1 transition-colors ${
                      active
                        ? "bg-primary text-primary-foreground border-primary"
                        : "hover:bg-accent"
                    }`}
                  >
                    <Icon className="h-3 w-3" />
                    {b.label}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* One local brain and nothing else: state the guarantee rather than
            offering a choice that isn't one. */}
        {brains.length === 1 && brains[0].kind === "local" && (
          <div className="flex items-center gap-1.5 pb-3 text-[11px] text-muted-foreground">
            <Cpu className="h-3 w-3" />
            <span>
              Answering with <span className="font-mono">{brains[0].label}</span> on this
              machine — prompts never leave the host.
            </span>
          </div>
        )}

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
