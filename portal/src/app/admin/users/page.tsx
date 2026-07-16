"use client";

import { useCallback, useEffect, useState } from "react";
import { AdminGuard } from "@/components/admin-guard";
import { Users, Plus, Search } from "lucide-react";
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
import type { AdminUser, Role, Tenant } from "@/types/admin";
import { InviteUserDialog } from "../components/InviteUserDialog";
import { ManageUserSheet } from "../components/ManageUserSheet";

export default function UsersAdminPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [inviteOpen, setInviteOpen] = useState(false);
  const [manageUser, setManageUser] = useState<AdminUser | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [usersRes, rolesRes, tenantsRes] = await Promise.all([
        fetch("/api/admin/users"),
        fetch("/api/admin/roles"),
        fetch("/api/admin/tenants"),
      ]);
      if (!usersRes.ok) throw new Error("Failed to load users");
      setUsers(await usersRes.json());
      if (rolesRes.ok) setRoles(await rolesRes.json());
      if (tenantsRes.ok) setTenants(await tenantsRes.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleManage = (user: AdminUser) => {
    setManageUser(user);
    setSheetOpen(true);
  };

  const filteredUsers = search
    ? users.filter(
        (u) =>
          u.email.toLowerCase().includes(search.toLowerCase()) ||
          (u.name && u.name.toLowerCase().includes(search.toLowerCase()))
      )
    : users;

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return "Never";
    return new Date(dateStr).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  };

  return (
    <AdminGuard>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Users className="h-5 w-5" />
            <h1 className="text-2xl font-bold">Users</h1>
          </div>
          <Button onClick={() => setInviteOpen(true)}>
            <Plus className="h-4 w-4 mr-2" />
            Invite User
          </Button>
        </div>

        <div className="relative max-w-sm">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search users..."
            className="pl-8"
          />
        </div>

        {error && (
          <div className="rounded-md bg-destructive/10 p-4 text-destructive text-sm">{error}</div>
        )}

        {loading ? (
          <div className="text-muted-foreground p-8 text-center">Loading users...</div>
        ) : filteredUsers.length === 0 ? (
          <div className="text-muted-foreground p-8 text-center">
            {search ? "No users match your search." : "No users yet. Invite your first user."}
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Email</TableHead>
                <TableHead>Tenant</TableHead>
                <TableHead>Auth</TableHead>
                <TableHead>Roles</TableHead>
                <TableHead>Last Login</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="w-[100px]">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredUsers.map((u) => (
                <TableRow key={u.id}>
                  <TableCell className="font-medium">{u.name || "—"}</TableCell>
                  <TableCell className="text-sm">{u.email}</TableCell>
                  <TableCell className="text-sm">{u.tenant_name || <span className="text-muted-foreground">—</span>}</TableCell>
                  <TableCell>
                    <Badge variant={u.auth_provider === "sso" ? "default" : "secondary"} className="text-xs">
                      {u.auth_provider === "sso" ? "SSO" : "Native"}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {u.roles.length > 0
                        ? u.roles.map((r) => (
                            <Badge key={r.id} variant="outline" className="text-xs">{r.name}</Badge>
                          ))
                        : <span className="text-muted-foreground text-xs">None</span>}
                    </div>
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">{formatDate(u.last_login_at)}</TableCell>
                  <TableCell>
                    <Badge variant={u.is_active ? "default" : "destructive"} className="text-xs">
                      {u.is_active ? "Active" : "Inactive"}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <Button variant="outline" size="sm" onClick={() => handleManage(u)}>Manage</Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}

        <InviteUserDialog
          open={inviteOpen}
          onOpenChange={setInviteOpen}
          onSaved={loadData}
          tenants={tenants}
          roles={roles}
        />

        <ManageUserSheet
          user={manageUser}
          open={sheetOpen}
          onOpenChange={setSheetOpen}
          onSaved={loadData}
          allRoles={roles}
        />
      </div>
    </AdminGuard>
  );
}
