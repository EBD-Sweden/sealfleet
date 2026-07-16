"use client";

import { useEffect, useState, useCallback } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { ShieldCheck, RefreshCw } from "lucide-react";

interface AuditEvent {
  event_id: string;
  user_id: string;
  action: string;
  resource: string;
  server_name: string;
  result: string;
  trace_id: string;
  duration_ms: number;
  created_at: string;
}

function ResultBadge({ result }: { result: string }) {
  if (result === "ok") {
    return (
      <Badge className="bg-green-600 hover:bg-green-700 text-white text-xs">
        {result}
      </Badge>
    );
  }
  if (result === "error") {
    return <Badge variant="destructive" className="text-xs">{result}</Badge>;
  }
  if (result === "denied") {
    return (
      <Badge className="bg-amber-500 hover:bg-amber-600 text-white text-xs">
        {result}
      </Badge>
    );
  }
  return <Badge variant="secondary" className="text-xs">{result}</Badge>;
}

export default function AuditPage() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [serverFilter, setServerFilter] = useState("");
  const [servers, setServers] = useState<string[]>([]);

  const fetchAudit = useCallback(async () => {
    try {
      const qs = new URLSearchParams({ limit: "100" });
      if (serverFilter) qs.set("server", serverFilter);
      const res = await fetch(`/api/audit?${qs}`);
      if (!res.ok) throw new Error("Failed to load audit events");
      const data = await res.json();
      const evts: AuditEvent[] = data.events ?? [];
      setEvents(evts);
      // Collect unique server names for filter
      const unique = [...new Set(evts.map((e) => e.server_name).filter(Boolean))];
      setServers((prev) => {
        const merged = [...new Set([...prev, ...unique])];
        return merged.sort();
      });
      setError("");
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Failed to load audit events";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [serverFilter]);

  useEffect(() => {
    fetchAudit();
    const interval = setInterval(fetchAudit, 10000);
    return () => clearInterval(interval);
  }, [fetchAudit]);

  if (loading) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <ShieldCheck className="h-6 w-6" />
          Audit Log
        </h1>
        <Card>
          <CardHeader>
            <CardTitle>Recent Events</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {[1, 2, 3, 4, 5].map((i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </CardContent>
        </Card>
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <ShieldCheck className="h-6 w-6" />
          Audit Log
        </h1>
        <Card>
          <CardContent className="py-8 text-center text-destructive">
            {error}
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold flex items-center gap-2">
        <ShieldCheck className="h-6 w-6" />
        Audit Log
      </h1>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-3">
            Recent Events
            <RefreshCw className="h-3 w-3 text-muted-foreground animate-spin" style={{ animationDuration: "10s" }} />
            <span className="text-xs font-normal text-muted-foreground">auto-refresh 10s</span>
            {servers.length > 0 && (
              <select
                value={serverFilter}
                onChange={(e) => setServerFilter(e.target.value)}
                className="ml-auto appearance-none bg-background border rounded-md px-2 py-1 text-xs cursor-pointer focus:outline-none focus:ring-1 focus:ring-ring"
              >
                <option value="">All servers</option>
                {servers.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {events.length === 0 ? (
            <p className="text-sm text-muted-foreground py-4 text-center">
              No audit events found. Run a pipeline to generate events.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Timestamp</TableHead>
                    <TableHead>User</TableHead>
                    <TableHead>Action</TableHead>
                    <TableHead>Resource</TableHead>
                    <TableHead>Server</TableHead>
                    <TableHead>Result</TableHead>
                    <TableHead>Trace ID</TableHead>
                    <TableHead className="text-right">Duration</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {events.map((evt) => (
                    <TableRow key={evt.event_id}>
                      <TableCell className="text-xs whitespace-nowrap">
                        {evt.created_at
                          ? new Date(evt.created_at).toLocaleString()
                          : "-"}
                      </TableCell>
                      <TableCell className="text-xs">{evt.user_id}</TableCell>
                      <TableCell className="font-mono text-xs">
                        {evt.action}
                      </TableCell>
                      <TableCell className="text-xs">{evt.resource}</TableCell>
                      <TableCell className="text-xs">
                        {evt.server_name && (
                          <Badge variant="secondary" className="text-xs font-mono">
                            {evt.server_name}
                          </Badge>
                        )}
                      </TableCell>
                      <TableCell>
                        <ResultBadge result={evt.result} />
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {evt.trace_id ? evt.trace_id.slice(0, 12) : "-"}
                      </TableCell>
                      <TableCell className="text-right text-xs">
                        {evt.duration_ms != null ? `${evt.duration_ms}ms` : "-"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
