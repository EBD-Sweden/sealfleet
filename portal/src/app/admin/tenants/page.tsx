"use client";

import { useCallback, useEffect, useState } from "react";
import { AdminGuard } from "@/components/admin-guard";
import { Building2, Plus, ChevronDown, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { Tenant } from "@/types/admin";
import { CreateEditTenantDialog } from "../components/CreateEditTenantDialog";
import { SsoMappingsSection } from "../components/SsoMappingsSection";

export default function TenantsAdminPage() {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingTenant, setEditingTenant] = useState<Tenant | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const loadTenants = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/admin/tenants");
      if (!res.ok) throw new Error("Failed to load tenants");
      setTenants(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadTenants();
  }, [loadTenants]);

  const handleEdit = (tenant: Tenant) => {
    setEditingTenant(tenant);
    setDialogOpen(true);
  };

  const handleAdd = () => {
    setEditingTenant(null);
    setDialogOpen(true);
  };

  return (
    <AdminGuard>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Building2 className="h-5 w-5" />
            <h1 className="text-2xl font-bold">Tenants</h1>
          </div>
          <Button onClick={handleAdd}>
            <Plus className="h-4 w-4 mr-2" />
            Add Tenant
          </Button>
        </div>

        {error && (
          <div className="rounded-md bg-destructive/10 p-4 text-destructive text-sm">{error}</div>
        )}

        {loading ? (
          <div className="text-muted-foreground p-8 text-center">Loading tenants...</div>
        ) : tenants.length === 0 ? (
          <div className="text-muted-foreground p-8 text-center">No tenants yet. Create your first tenant.</div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[40px]" />
                <TableHead>Name</TableHead>
                <TableHead>Slug</TableHead>
                <TableHead>SSO</TableHead>
                <TableHead>Allowed Domains</TableHead>
                <TableHead className="w-[100px]">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {tenants.map((t) => (
                <>
                  <TableRow key={t.id}>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 w-6 p-0"
                        onClick={() => setExpandedId(expandedId === t.id ? null : t.id)}
                      >
                        {expandedId === t.id ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                      </Button>
                    </TableCell>
                    <TableCell className="font-medium">{t.name}</TableCell>
                    <TableCell className="font-mono text-sm">{t.slug}</TableCell>
                    <TableCell>
                      <Badge variant={t.sso_enabled ? "default" : "secondary"}>
                        {t.sso_enabled ? "Enabled" : "Disabled"}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-sm">
                      {t.allowed_domains?.join(", ") || "—"}
                    </TableCell>
                    <TableCell>
                      <Button variant="outline" size="sm" onClick={() => handleEdit(t)}>Edit</Button>
                    </TableCell>
                  </TableRow>
                  {expandedId === t.id && (
                    <TableRow key={`${t.id}-mappings`}>
                      <TableCell colSpan={6}>
                        <SsoMappingsSection tenantId={t.id} />
                      </TableCell>
                    </TableRow>
                  )}
                </>
              ))}
            </TableBody>
          </Table>
        )}

        <CreateEditTenantDialog
          tenant={editingTenant}
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          onSaved={loadTenants}
        />
      </div>
    </AdminGuard>
  );
}
