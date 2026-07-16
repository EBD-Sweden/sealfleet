"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Checkbox } from "@/components/ui/checkbox";
import { Separator } from "@/components/ui/separator";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { Role, Tenant, McpServer, McpPermission } from "@/types/admin";

export interface RoleWithPermissions extends Role {
  permissions?: McpPermission[];
}

interface ServerPermission {
  server_id: string;
  server_name: string;
  enabled: boolean;
  allowed_tools: string;
  scopes: string[];
}

const ALL_SCOPES = ["read", "write", "execute", "admin"];

interface CreateEditRoleDialogProps {
  role: RoleWithPermissions | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
  tenants: Tenant[];
  servers: McpServer[];
}

export function CreateEditRoleDialog({
  role,
  open,
  onOpenChange,
  onSaved,
  tenants,
  servers,
}: CreateEditRoleDialogProps) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tenantId, setTenantId] = useState<string>("");
  const [serverPerms, setServerPerms] = useState<ServerPermission[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) {
      if (role) {
        setName(role.name);
        setDescription(role.description || "");
        setTenantId(role.tenant_id || "");
        // Build server permissions from role data
        const perms = servers.map((s) => {
          const existing = role.permissions?.find((p) => p.server_id === s.id);
          return {
            server_id: s.id,
            server_name: s.name,
            enabled: !!existing,
            allowed_tools: existing?.allowed_tools?.join(", ") || "",
            scopes: existing?.scopes || ["read"],
          };
        });
        setServerPerms(perms);
      } else {
        setName("");
        setDescription("");
        setTenantId("");
        setServerPerms(
          servers.map((s) => ({
            server_id: s.id,
            server_name: s.name,
            enabled: false,
            allowed_tools: "",
            scopes: ["read"],
          }))
        );
      }
    }
  }, [role, open, servers]);

  const updateServerPerm = (index: number, updates: Partial<ServerPermission>) => {
    setServerPerms((prev) => prev.map((p, i) => (i === index ? { ...p, ...updates } : p)));
  };

  const toggleScope = (index: number, scope: string) => {
    setServerPerms((prev) =>
      prev.map((p, i) => {
        if (i !== index) return p;
        const scopes = p.scopes.includes(scope) ? p.scopes.filter((s) => s !== scope) : [...p.scopes, scope];
        return { ...p, scopes };
      })
    );
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const permissions = serverPerms
        .filter((p) => p.enabled)
        .map((p) => ({
          server_id: p.server_id,
          allowed_tools: p.allowed_tools
            ? p.allowed_tools.split(",").map((t) => t.trim()).filter(Boolean)
            : [],
          scopes: p.scopes,
        }));

      const body = {
        name,
        description: description || undefined,
        tenant_id: tenantId || undefined,
        permissions,
      };

      const url = role ? `/api/admin/roles/${role.id}` : "/api/admin/roles";
      const method = role ? "PUT" : "POST";

      const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error("Failed to save role");
      onSaved();
      onOpenChange(false);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{role ? "Edit Role" : "Create Role"}</DialogTitle>
          <DialogDescription>
            {role ? "Update role configuration and permissions." : "Define a new role with MCP permissions."}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="role-name">Role Name</Label>
            <Input id="role-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="viewer" />
          </div>
          <div className="space-y-2">
            <Label htmlFor="role-desc">Description</Label>
            <Input id="role-desc" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Read-only access" />
          </div>
          <div className="space-y-2">
            <Label>Tenant</Label>
            <Select value={tenantId} onValueChange={setTenantId}>
              <SelectTrigger>
                <SelectValue placeholder="Global (no tenant)" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__">Global (no tenant)</SelectItem>
                {tenants.map((t) => (
                  <SelectItem key={t.id} value={t.id}>{t.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Separator />
          <div className="space-y-3">
            <Label className="text-base font-medium">MCP Server Permissions</Label>
            {serverPerms.length === 0 ? (
              <p className="text-sm text-muted-foreground">No MCP servers registered.</p>
            ) : (
              serverPerms.map((sp, idx) => (
                <div key={sp.server_id} className="border rounded-md p-3 space-y-3">
                  <div className="flex items-center gap-3">
                    <Switch
                      checked={sp.enabled}
                      onCheckedChange={(checked) => updateServerPerm(idx, { enabled: checked })}
                    />
                    <span className="font-medium text-sm">{sp.server_name}</span>
                  </div>
                  {sp.enabled && (
                    <div className="ml-10 space-y-3">
                      <div className="space-y-1">
                        <Label className="text-xs">Allowed Tools (comma-separated, empty = all)</Label>
                        <Input
                          value={sp.allowed_tools}
                          onChange={(e) => updateServerPerm(idx, { allowed_tools: e.target.value })}
                          placeholder="tool1, tool2"
                          className="h-8"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">Scopes</Label>
                        <div className="flex gap-4">
                          {ALL_SCOPES.map((scope) => (
                            <label key={scope} className="flex items-center gap-1.5 text-sm">
                              <Checkbox
                                checked={sp.scopes.includes(scope)}
                                onCheckedChange={() => toggleScope(idx, scope)}
                              />
                              {scope}
                            </label>
                          ))}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={handleSave} disabled={saving || !name}>
            {saving ? "Saving..." : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
