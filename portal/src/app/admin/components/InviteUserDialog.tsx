"use client";

import { useEffect, useState } from "react";
import { Copy, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
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
import type { Tenant, Role } from "@/types/admin";

interface InviteUserDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
  tenants: Tenant[];
  roles: Role[];
}

export function InviteUserDialog({
  open,
  onOpenChange,
  onSaved,
  tenants,
  roles,
}: InviteUserDialogProps) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [selectedRoleIds, setSelectedRoleIds] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [tempPassword, setTempPassword] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (open) {
      setEmail("");
      setName("");
      setTenantId("");
      setSelectedRoleIds([]);
      setTempPassword(null);
      setCopied(false);
    }
  }, [open]);

  const toggleRole = (roleId: string) => {
    setSelectedRoleIds((prev) =>
      prev.includes(roleId) ? prev.filter((id) => id !== roleId) : [...prev, roleId]
    );
  };

  const handleInvite = async () => {
    setSaving(true);
    try {
      const res = await fetch("/api/admin/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email,
          name,
          tenant_id: tenantId || undefined,
          role_ids: selectedRoleIds,
        }),
      });
      if (!res.ok) throw new Error("Failed to invite user");
      const data = await res.json() as { temp_password: string };
      setTempPassword(data.temp_password);
      onSaved();
    } finally {
      setSaving(false);
    }
  };

  const handleCopy = async () => {
    if (tempPassword) {
      await navigator.clipboard.writeText(tempPassword);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Invite User</DialogTitle>
          <DialogDescription>
            {tempPassword ? "User created. Share the temporary password." : "Create a new user account."}
          </DialogDescription>
        </DialogHeader>

        {tempPassword ? (
          <div className="space-y-4">
            <div className="rounded-md bg-muted p-4 space-y-2">
              <Label className="text-xs text-muted-foreground">Temporary Password</Label>
              <div className="flex items-center gap-2">
                <code className="flex-1 bg-background rounded px-3 py-2 font-mono text-sm border">{tempPassword}</code>
                <Button variant="outline" size="sm" onClick={handleCopy}>
                  {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">Share this securely. The user should change it on first login.</p>
            </div>
            <DialogFooter>
              <Button onClick={() => onOpenChange(false)}>Done</Button>
            </DialogFooter>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="user-email">Email</Label>
              <Input id="user-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="user@company.com" />
            </div>
            <div className="space-y-2">
              <Label htmlFor="user-name">Name</Label>
              <Input id="user-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Jane Doe" />
            </div>
            <div className="space-y-2">
              <Label>Tenant</Label>
              <Select value={tenantId} onValueChange={setTenantId}>
                <SelectTrigger>
                  <SelectValue placeholder="No tenant" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">No tenant</SelectItem>
                  {tenants.map((t) => (
                    <SelectItem key={t.id} value={t.id}>{t.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Roles</Label>
              <div className="space-y-2 max-h-40 overflow-y-auto">
                {roles.map((r) => (
                  <label key={r.id} className="flex items-center gap-2 text-sm">
                    <Checkbox
                      checked={selectedRoleIds.includes(r.id)}
                      onCheckedChange={() => toggleRole(r.id)}
                    />
                    {r.name}
                    {r.tenant_name && <span className="text-muted-foreground">({r.tenant_name})</span>}
                  </label>
                ))}
                {roles.length === 0 && <p className="text-sm text-muted-foreground">No roles available.</p>}
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
              <Button onClick={handleInvite} disabled={saving || !email || !name}>
                {saving ? "Creating..." : "Invite"}
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
