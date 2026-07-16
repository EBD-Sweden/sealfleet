"use client";

import { useCallback, useEffect, useState } from "react";
import { AdminGuard } from "@/components/admin-guard";
import { Shield, Plus, Pencil } from "lucide-react";
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
import type { Role, Tenant, McpServer } from "@/types/admin";
import { CreateEditRoleDialog, type RoleWithPermissions } from "../components/CreateEditRoleDialog";

export default function RolesAdminPage() {
  const [roles, setRoles] = useState<Role[]>([]);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingRole, setEditingRole] = useState<RoleWithPermissions | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [rolesRes, tenantsRes, serversRes] = await Promise.all([
        fetch("/api/admin/roles"),
        fetch("/api/admin/tenants"),
        fetch("/api/admin/servers"),
      ]);
      if (!rolesRes.ok) throw new Error("Failed to load roles");
      setRoles(await rolesRes.json());
      if (tenantsRes.ok) setTenants(await tenantsRes.json());
      if (serversRes.ok) setServers(await serversRes.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleEdit = async (role: Role) => {
    const res = await fetch(`/api/admin/roles/${role.id}`);
    if (res.ok) {
      const data = await res.json() as RoleWithPermissions;
      setEditingRole(data);
      setDialogOpen(true);
    }
  };

  const handleCreate = () => {
    setEditingRole(null);
    setDialogOpen(true);
  };

  return (
    <AdminGuard>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Shield className="h-5 w-5" />
            <h1 className="text-2xl font-bold">Roles</h1>
          </div>
          <Button onClick={handleCreate}>
            <Plus className="h-4 w-4 mr-2" />
            Create Role
          </Button>
        </div>

        {error && (
          <div className="rounded-md bg-destructive/10 p-4 text-destructive text-sm">{error}</div>
        )}

        {loading ? (
          <div className="text-muted-foreground p-8 text-center">Loading roles...</div>
        ) : roles.length === 0 ? (
          <div className="text-muted-foreground p-8 text-center">No roles yet. Create your first role.</div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Role Name</TableHead>
                <TableHead>Tenant</TableHead>
                <TableHead>Description</TableHead>
                <TableHead>MCP Permissions</TableHead>
                <TableHead className="w-[100px]">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {roles.map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="font-medium">{r.name}</TableCell>
                  <TableCell>{r.tenant_name || <span className="text-muted-foreground">Global</span>}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">{r.description || "—"}</TableCell>
                  <TableCell>
                    <Badge variant="secondary">{r.permissions_count ?? 0}</Badge>
                  </TableCell>
                  <TableCell>
                    <Button variant="outline" size="sm" onClick={() => handleEdit(r)}>
                      <Pencil className="h-3 w-3 mr-1" />
                      Edit
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}

        <CreateEditRoleDialog
          role={editingRole}
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          onSaved={loadData}
          tenants={tenants}
          servers={servers}
        />
      </div>
    </AdminGuard>
  );
}
