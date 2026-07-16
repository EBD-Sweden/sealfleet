"use client";

import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { AdminUser, Role, McpPermission } from "@/types/admin";

interface ManageUserSheetProps {
  user: AdminUser | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
  allRoles: Role[];
}

export function ManageUserSheet({
  user,
  open,
  onOpenChange,
  onSaved,
  allRoles,
}: ManageUserSheetProps) {
  const [userRoleIds, setUserRoleIds] = useState<string[]>([]);
  const [addRoleId, setAddRoleId] = useState("");
  const [saving, setSaving] = useState(false);
  const [mcpAccess, setMcpAccess] = useState<McpPermission[]>([]);
  const [loadingAccess, setLoadingAccess] = useState(false);

  useEffect(() => {
    if (user && open) {
      setUserRoleIds(user.roles.map((r) => r.id));
      setAddRoleId("");
      // Load MCP access based on user's roles
      setLoadingAccess(true);
      const loadAccess = async () => {
        try {
          const perms: McpPermission[] = [];
          for (const role of user.roles) {
            const res = await fetch(`/api/admin/roles/${role.id}`);
            if (res.ok) {
              const data = await res.json() as { permissions?: McpPermission[] };
              if (data.permissions) {
                perms.push(...data.permissions);
              }
            }
          }
          setMcpAccess(perms);
        } finally {
          setLoadingAccess(false);
        }
      };
      loadAccess();
    }
  }, [user, open]);

  const removeRole = (roleId: string) => {
    setUserRoleIds((prev) => prev.filter((id) => id !== roleId));
  };

  const handleAddRole = () => {
    if (addRoleId && !userRoleIds.includes(addRoleId)) {
      setUserRoleIds((prev) => [...prev, addRoleId]);
      setAddRoleId("");
    }
  };

  const handleSave = async () => {
    if (!user) return;
    setSaving(true);
    try {
      const res = await fetch(`/api/admin/users/${user.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role_ids: userRoleIds }),
      });
      if (!res.ok) throw new Error("Failed to update user");
      onSaved();
      onOpenChange(false);
    } finally {
      setSaving(false);
    }
  };

  if (!user) return null;

  const availableRoles = allRoles.filter((r) => !userRoleIds.includes(r.id));

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-[480px] sm:max-w-[480px] overflow-y-auto">
        <SheetHeader>
          <SheetTitle>{user.name || user.email}</SheetTitle>
          <SheetDescription className="flex items-center gap-2">
            {user.email}
            <Badge variant={user.auth_provider === "sso" ? "default" : "secondary"} className="text-xs">
              {user.auth_provider === "sso" ? "SSO" : "Native"}
            </Badge>
          </SheetDescription>
        </SheetHeader>

        <Tabs defaultValue="roles" className="mt-6">
          <TabsList>
            <TabsTrigger value="roles">Roles</TabsTrigger>
            <TabsTrigger value="mcp-access">MCP Access</TabsTrigger>
          </TabsList>

          <TabsContent value="roles" className="space-y-4 mt-4">
            <div className="space-y-2">
              <Label className="text-sm font-medium">Current Roles</Label>
              {userRoleIds.length === 0 ? (
                <p className="text-sm text-muted-foreground">No roles assigned.</p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {userRoleIds.map((roleId) => {
                    const role = allRoles.find((r) => r.id === roleId);
                    return (
                      <Badge key={roleId} variant="secondary" className="flex items-center gap-1">
                        {role?.name || roleId}
                        <button onClick={() => removeRole(roleId)} className="ml-1 hover:text-destructive">
                          <X className="h-3 w-3" />
                        </button>
                      </Badge>
                    );
                  })}
                </div>
              )}
            </div>

            <Separator />

            <div className="flex items-end gap-2">
              <div className="flex-1 space-y-1">
                <Label className="text-xs">Add Role</Label>
                <Select value={addRoleId} onValueChange={setAddRoleId}>
                  <SelectTrigger className="h-8">
                    <SelectValue placeholder="Select role" />
                  </SelectTrigger>
                  <SelectContent>
                    {availableRoles.map((r) => (
                      <SelectItem key={r.id} value={r.id}>
                        {r.name} {r.tenant_name ? `(${r.tenant_name})` : ""}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Button size="sm" variant="outline" onClick={handleAddRole} disabled={!addRoleId}>
                Add
              </Button>
            </div>

            <div className="pt-4">
              <Button onClick={handleSave} disabled={saving} className="w-full">
                {saving ? "Saving..." : "Save Changes"}
              </Button>
            </div>
          </TabsContent>

          <TabsContent value="mcp-access" className="space-y-4 mt-4">
            <Label className="text-sm font-medium">MCP Server Access (from roles)</Label>
            {loadingAccess ? (
              <p className="text-sm text-muted-foreground">Loading...</p>
            ) : mcpAccess.length === 0 ? (
              <p className="text-sm text-muted-foreground">No MCP server access through current roles.</p>
            ) : (
              <div className="space-y-3">
                {mcpAccess.map((perm) => (
                  <div key={perm.id} className="border rounded-md p-3 space-y-1">
                    <div className="font-medium text-sm">{perm.server_name || perm.server_id}</div>
                    <div className="text-xs text-muted-foreground">
                      Tools: {perm.allowed_tools?.length ? perm.allowed_tools.join(", ") : "All"}
                    </div>
                    <div className="flex gap-1">
                      {perm.scopes.map((s) => (
                        <Badge key={s} variant="outline" className="text-xs">{s}</Badge>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </TabsContent>
        </Tabs>
      </SheetContent>
    </Sheet>
  );
}
