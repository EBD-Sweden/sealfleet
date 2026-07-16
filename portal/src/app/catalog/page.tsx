"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Plus, Search } from "lucide-react";

interface Server {
  server_id: string;
  name: string;
  endpoint: string;
  description: string;
  auth_methods: string[];
  status: string;
  tool_count: number;
  metadata?: Record<string, unknown>;
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

const statusColor: Record<string, string> = {
  active: "bg-green-500",
  online: "bg-green-500",
  degraded: "bg-yellow-500",
  inactive: "bg-red-500",
  offline: "bg-red-500",
};

const categoryColor: Record<string, string> = {
  "finance": "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300",
  "crypto": "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300",
  "default": "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
};

export default function CatalogPage() {
  const [servers, setServers] = useState<Server[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState("");
  const [search, setSearch] = useState("");
  const [activeCategory, setActiveCategory] = useState("all");

  const fetchServers = async () => {
    try {
      setLoading(true);
      setError("");
      const res = await fetch("/api/servers");
      if (!res.ok) throw new Error("Failed to load servers");
      const data = await res.json();
      setServers(data.servers ?? []);
    } catch (e: unknown) {
      setError(errorMessage(e, "Failed to load servers"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchServers();
  }, []);

  const handleRegister = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setSubmitting(true);
    setFormError("");
    const form = e.currentTarget;
    const formData = new FormData(form);
    const body = {
      name: formData.get("name") as string,
      description: formData.get("description") as string,
      endpoint: formData.get("endpoint") as string,
      metadata: {
        tags: ((formData.get("tags") as string) || "")
          .split(",").map((t) => t.trim()).filter(Boolean),
      },
    };
    try {
      const res = await fetch("/api/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({})) as { error?: string };
        throw new Error(data.error || "Registration failed");
      }
      setDialogOpen(false);
      form.reset();
      fetchServers();
    } catch (e: unknown) {
      setFormError(errorMessage(e, "Registration failed"));
    } finally {
      setSubmitting(false);
    }
  };

  // Derive category list
  const categories = ["all", ...Array.from(new Set(
    servers.map(s => s.metadata?.category).filter(Boolean) as string[]
  ))];

  const filtered = servers.filter(s => {
    const matchesSearch = !search ||
      s.name.toLowerCase().includes(search.toLowerCase()) ||
      s.description?.toLowerCase().includes(search.toLowerCase());
    const matchesCat = activeCategory === "all" ||
      s.metadata?.category === activeCategory;
    return matchesSearch && matchesCat;
  });

  if (loading) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold">Server Catalog</h1>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <Card key={i} className="h-full">
              <CardHeader className="pb-2"><Skeleton className="h-5 w-2/3" /></CardHeader>
              <CardContent className="space-y-3">
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-1/2" />
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-bold">Server Catalog</h1>
        <Card><CardContent className="py-8 text-center text-destructive">{error}</CardContent></Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Server Catalog</h1>
          <p className="text-sm text-muted-foreground mt-1">{servers.length} MCP servers available</p>
        </div>
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogTrigger asChild>
            <Button><Plus className="mr-1 h-4 w-4" />Register MCP Server</Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Register MCP Server</DialogTitle>
              <DialogDescription>Add a new MCP server to the catalog.</DialogDescription>
            </DialogHeader>
            <form onSubmit={handleRegister} className="space-y-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">Name</label>
                <Input name="name" placeholder="My MCP Server" required />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">Description</label>
                <Input name="description" placeholder="What this server does" />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">Endpoint URL</label>
                <Input name="endpoint" type="url" placeholder="https://example.com/mcp" required />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">Tags (comma-separated)</label>
                <Input name="tags" placeholder="crypto, trading, finance" />
              </div>
              {formError && <p className="text-sm text-destructive">{formError}</p>}
              <DialogFooter>
                <Button type="submit" disabled={submitting}>
                  {submitting ? "Registering..." : "Register"}
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      {/* Search + category filter */}
      <div className="flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            className="pl-8"
            placeholder="Search servers..."
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <div className="flex gap-2 flex-wrap">
          {categories.map(cat => (
            <Button
              key={cat}
              variant={activeCategory === cat ? "default" : "outline"}
              size="sm"
              onClick={() => setActiveCategory(cat)}
              className="capitalize"
            >
              {cat === "all" ? `All (${servers.length})` : `${cat} (${servers.filter(s => s.metadata?.category === cat).length})`}
            </Button>
          ))}
        </div>
      </div>

      {/* Grid */}
      {filtered.length === 0 ? (
        <Card><CardContent className="py-8 text-center text-muted-foreground">No servers match your search.</CardContent></Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((s) => {
            const category = s.metadata?.category as string | undefined;
            const catClass = categoryColor[category ?? ""] ?? categoryColor.default;
            return (
              <Link key={s.server_id} href={`/catalog/${s.server_id}`}>
                <Card className="h-full transition-colors hover:border-primary/40 cursor-pointer">
                  <CardHeader className="pb-2">
                    <div className="flex items-start justify-between gap-2">
                      <CardTitle className="text-base leading-snug">{s.name}</CardTitle>
                      <span className="flex items-center gap-1.5 text-xs text-muted-foreground shrink-0">
                        <span className={`inline-block h-2 w-2 rounded-full ${statusColor[s.status] ?? "bg-gray-400"}`} />
                        {s.status}
                      </span>
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <p className="text-sm text-muted-foreground line-clamp-2">{s.description}</p>
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-muted-foreground">
                        {s.tool_count} {s.tool_count === 1 ? "tool" : "tools"}
                      </span>
                      {category && (
                        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${catClass}`}>
                          {category}
                        </span>
                      )}
                    </div>
                  </CardContent>
                </Card>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
