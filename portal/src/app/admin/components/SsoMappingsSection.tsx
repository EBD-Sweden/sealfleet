"use client";

import { useCallback, useEffect, useState } from "react";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { Role, SsoRoleMapping } from "@/types/admin";

interface SsoMappingsSectionProps {
  tenantId: string;
}

export function SsoMappingsSection({ tenantId }: SsoMappingsSectionProps) {
  const [mappings, setMappings] = useState<SsoRoleMapping[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [loading, setLoading] = useState(true);
  const [claimKey, setClaimKey] = useState("groups");
  const [claimValue, setClaimValue] = useState("");
  const [selectedRoleId, setSelectedRoleId] = useState("");
  const [adding, setAdding] = useState(false);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [mappingsRes, rolesRes] = await Promise.all([
        fetch(`/api/admin/tenants/${tenantId}/sso-mappings`),
        fetch("/api/admin/roles"),
      ]);
      if (mappingsRes.ok) setMappings(await mappingsRes.json());
      if (rolesRes.ok) setRoles(await rolesRes.json());
    } finally {
      setLoading(false);
    }
  }, [tenantId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleAddMapping = async () => {
    if (!claimKey || !claimValue || !selectedRoleId) return;
    setAdding(true);
    try {
      const res = await fetch(`/api/admin/tenants/${tenantId}/sso-mappings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ idp_claim_key: claimKey, idp_claim_value: claimValue, role_id: selectedRoleId }),
      });
      if (res.ok) {
        setClaimValue("");
        setSelectedRoleId("");
        await loadData();
      }
    } finally {
      setAdding(false);
    }
  };

  const handleDeleteMapping = async (mappingId: string) => {
    await fetch(`/api/admin/tenants/${tenantId}/sso-mappings/${mappingId}`, { method: "DELETE" });
    await loadData();
  };

  if (loading) return <div className="p-4 text-sm text-muted-foreground">Loading mappings...</div>;

  return (
    <div className="p-4 space-y-4 bg-muted/30 rounded-md">
      <h4 className="font-medium text-sm">SSO Role Mappings</h4>
      {mappings.length > 0 ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Claim Key</TableHead>
              <TableHead>Claim Value</TableHead>
              <TableHead>→ Platform Role</TableHead>
              <TableHead className="w-[80px]">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {mappings.map((m) => (
              <TableRow key={m.id}>
                <TableCell className="font-mono text-sm">{m.idp_claim_key}</TableCell>
                <TableCell className="font-mono text-sm">{m.idp_claim_value}</TableCell>
                <TableCell><Badge variant="secondary">{m.role_name}</Badge></TableCell>
                <TableCell>
                  <Button variant="ghost" size="sm" onClick={() => handleDeleteMapping(m.id)}>
                    <Trash2 className="h-4 w-4 text-destructive" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <p className="text-sm text-muted-foreground">No mappings configured.</p>
      )}
      <Separator />
      <div className="flex items-end gap-2">
        <div className="space-y-1">
          <Label className="text-xs">Claim Key</Label>
          <Input value={claimKey} onChange={(e) => setClaimKey(e.target.value)} className="h-8 w-32" />
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Claim Value</Label>
          <Input value={claimValue} onChange={(e) => setClaimValue(e.target.value)} placeholder="engineering" className="h-8 w-40" />
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Role</Label>
          <Select value={selectedRoleId} onValueChange={setSelectedRoleId}>
            <SelectTrigger className="h-8 w-40">
              <SelectValue placeholder="Select role" />
            </SelectTrigger>
            <SelectContent>
              {roles.map((r) => (
                <SelectItem key={r.id} value={r.id}>{r.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button size="sm" onClick={handleAddMapping} disabled={adding || !claimValue || !selectedRoleId}>
          {adding ? "Adding..." : "Add Mapping"}
        </Button>
      </div>
    </div>
  );
}
