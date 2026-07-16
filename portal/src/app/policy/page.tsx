"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { Shield, RefreshCw, Loader2, Search } from "lucide-react";

interface PolicyRule {
  id: string;
  match: { tool_pattern?: string; mcp_pattern?: string };
  action: string;
  reason?: string;
}

interface CheckResult {
  action: string;
  rule_id: string;
  reason: string;
}

function ActionBadge({ action }: { action: string }) {
  if (action === "allow") {
    return (
      <Badge className="bg-green-600 hover:bg-green-700 text-white text-xs">
        {action}
      </Badge>
    );
  }
  if (action === "deny") {
    return <Badge variant="destructive" className="text-xs">{action}</Badge>;
  }
  if (action === "require_confirm") {
    return (
      <Badge className="bg-amber-500 hover:bg-amber-600 text-white text-xs">
        {action}
      </Badge>
    );
  }
  return <Badge variant="secondary" className="text-xs">{action}</Badge>;
}

export default function PolicyPage() {
  const [rules, setRules] = useState<PolicyRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloading, setReloading] = useState(false);

  // Test policy form
  const [testMcp, setTestMcp] = useState("");
  const [testTool, setTestTool] = useState("");
  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState<CheckResult | null>(null);

  const fetchRules = async () => {
    try {
      const res = await fetch("/api/policy");
      if (!res.ok) throw new Error("Failed to load policy rules");
      const data = await res.json();
      setRules(data.rules ?? []);
      setError("");
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Failed to load policy rules";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchRules();
  }, []);

  const handleReload = async () => {
    setReloading(true);
    try {
      await fetch("/api/policy/reload", { method: "POST" });
      await fetchRules();
    } finally {
      setReloading(false);
    }
  };

  const handleCheck = async () => {
    if (!testMcp.trim() || !testTool.trim()) return;
    setChecking(true);
    setCheckResult(null);
    try {
      const res = await fetch("/api/policy/check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mcp: testMcp, tool: testTool }),
      });
      if (res.ok) {
        const data = await res.json();
        setCheckResult(data);
      }
    } finally {
      setChecking(false);
    }
  };

  // Stats
  const allowCount = rules.filter((r) => r.action === "allow").length;
  const denyCount = rules.filter((r) => r.action === "deny").length;
  const confirmCount = rules.filter((r) => r.action === "require_confirm").length;

  if (loading) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Shield className="h-6 w-6" />
          Policy Engine
        </h1>
        <Card>
          <CardContent className="space-y-3 pt-6">
            {[1, 2, 3].map((i) => (
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
          <Shield className="h-6 w-6" />
          Policy Engine
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
        <Shield className="h-6 w-6" />
        Policy Engine
      </h1>

      {/* Stats */}
      <div className="grid grid-cols-4 gap-4">
        <Card>
          <CardContent className="pt-4 pb-3 text-center">
            <p className="text-2xl font-bold">{rules.length}</p>
            <p className="text-xs text-muted-foreground">Total Rules</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-3 text-center">
            <p className="text-2xl font-bold text-green-600">{allowCount}</p>
            <p className="text-xs text-muted-foreground">Allow</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-3 text-center">
            <p className="text-2xl font-bold text-red-600">{denyCount}</p>
            <p className="text-xs text-muted-foreground">Deny</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-3 text-center">
            <p className="text-2xl font-bold text-amber-600">{confirmCount}</p>
            <p className="text-xs text-muted-foreground">Require Confirm</p>
          </CardContent>
        </Card>
      </div>

      {/* Rules Table */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            Policy Rules
            <Button
              size="sm"
              variant="outline"
              onClick={handleReload}
              disabled={reloading}
              className="ml-auto"
            >
              {reloading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
              <span className="ml-1">Reload Policy</span>
            </Button>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>ID</TableHead>
                  <TableHead>Tool Pattern</TableHead>
                  <TableHead>MCP Pattern</TableHead>
                  <TableHead>Action</TableHead>
                  <TableHead>Reason</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rules.map((rule) => (
                  <TableRow key={rule.id}>
                    <TableCell className="font-mono text-xs">
                      {rule.id}
                    </TableCell>
                    <TableCell className="font-mono text-sm">
                      {rule.match?.tool_pattern || "*"}
                    </TableCell>
                    <TableCell className="font-mono text-sm">
                      {rule.match?.mcp_pattern || "*"}
                    </TableCell>
                    <TableCell>
                      <ActionBadge action={rule.action} />
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {rule.reason || "-"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      {/* Test Policy */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Search className="h-4 w-4" />
            Test Policy
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex gap-2 flex-wrap items-end">
            <div className="space-y-1">
              <label className="text-xs font-medium">MCP Name</label>
              <Input
                placeholder="e.g. weather-mcp"
                value={testMcp}
                onChange={(e) => setTestMcp(e.target.value)}
                className="w-48"
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium">Tool Name</label>
              <Input
                placeholder="e.g. get_weather"
                value={testTool}
                onChange={(e) => setTestTool(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleCheck()}
                className="w-48"
              />
            </div>
            <Button
              size="sm"
              onClick={handleCheck}
              disabled={checking || !testMcp.trim() || !testTool.trim()}
            >
              {checking ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                "Check"
              )}
            </Button>
            {checkResult && (
              <div className="flex items-center gap-2">
                <ActionBadge action={checkResult.action} />
                <span className="text-xs text-muted-foreground">
                  rule: {checkResult.rule_id}
                  {checkResult.reason ? ` — ${checkResult.reason}` : ""}
                </span>
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
