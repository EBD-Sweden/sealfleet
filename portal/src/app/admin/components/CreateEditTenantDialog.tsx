"use client";

import { useEffect, useState } from "react";
import { Eye, EyeOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { Tenant } from "@/types/admin";

function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

interface CreateEditTenantDialogProps {
  tenant: Tenant | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}

export function CreateEditTenantDialog({
  tenant,
  open,
  onOpenChange,
  onSaved,
}: CreateEditTenantDialogProps) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [ssoEnabled, setSsoEnabled] = useState(false);
  const [oidcIssuer, setOidcIssuer] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [showSecret, setShowSecret] = useState(false);
  const [allowedDomains, setAllowedDomains] = useState("");
  const [scopes, setScopes] = useState("openid email profile");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (tenant) {
      setName(tenant.name);
      setSlug(tenant.slug);
      setSsoEnabled(tenant.sso_enabled);
      setOidcIssuer(tenant.oidc_issuer || "");
      setClientId(tenant.oidc_client_id || "");
      setClientSecret("");
      setAllowedDomains(tenant.allowed_domains?.join(", ") || "");
      setScopes(tenant.oidc_scopes || "openid email profile");
    } else {
      setName("");
      setSlug("");
      setSsoEnabled(false);
      setOidcIssuer("");
      setClientId("");
      setClientSecret("");
      setAllowedDomains("");
      setScopes("openid email profile");
    }
    setShowSecret(false);
  }, [tenant, open]);

  const handleNameChange = (value: string) => {
    setName(value);
    if (!tenant) {
      setSlug(slugify(value));
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const body: Record<string, unknown> = {
        name,
        slug,
        sso_enabled: ssoEnabled,
        oidc_issuer: oidcIssuer || undefined,
        oidc_client_id: clientId || undefined,
        oidc_client_secret: clientSecret || undefined,
        oidc_scopes: scopes || undefined,
        allowed_domains: allowedDomains
          ? allowedDomains.split(",").map((d) => d.trim()).filter(Boolean)
          : undefined,
      };
      if (tenant) {
        body.id = tenant.id;
      }
      const res = await fetch("/api/admin/tenants", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error("Failed to save tenant");
      onSaved();
      onOpenChange(false);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{tenant ? "Edit Tenant" : "Add Tenant"}</DialogTitle>
          <DialogDescription>
            {tenant ? "Update tenant configuration." : "Create a new tenant."}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="tenant-name">Tenant Name</Label>
            <Input id="tenant-name" value={name} onChange={(e) => handleNameChange(e.target.value)} placeholder="Acme Corp" />
          </div>
          <div className="space-y-2">
            <Label htmlFor="tenant-slug">Slug</Label>
            <Input id="tenant-slug" value={slug} onChange={(e) => setSlug(e.target.value)} placeholder="acme-corp" />
          </div>
          <div className="flex items-center gap-3">
            <Switch id="sso-enabled" checked={ssoEnabled} onCheckedChange={setSsoEnabled} />
            <Label htmlFor="sso-enabled">SSO Enabled</Label>
          </div>
          {ssoEnabled && (
            <>
              <Separator />
              <div className="space-y-2">
                <Label htmlFor="oidc-issuer">OIDC Issuer URL</Label>
                <Input
                  id="oidc-issuer"
                  value={oidcIssuer}
                  onChange={(e) => setOidcIssuer(e.target.value)}
                  placeholder="https://login.microsoftonline.com/{tenant-id}/v2.0"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="client-id">Client ID</Label>
                <Input id="client-id" value={clientId} onChange={(e) => setClientId(e.target.value)} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="client-secret">Client Secret</Label>
                <div className="relative">
                  <Input
                    id="client-secret"
                    type={showSecret ? "text" : "password"}
                    value={clientSecret}
                    onChange={(e) => setClientSecret(e.target.value)}
                    placeholder={tenant ? "Leave empty to keep current" : ""}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="absolute right-1 top-1 h-7 w-7 p-0"
                    onClick={() => setShowSecret(!showSecret)}
                  >
                    {showSecret ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </Button>
                </div>
              </div>
              <div className="space-y-2">
                <Label htmlFor="allowed-domains">Allowed Domains</Label>
                <Input
                  id="allowed-domains"
                  value={allowedDomains}
                  onChange={(e) => setAllowedDomains(e.target.value)}
                  placeholder="acme.com, acme.io"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="scopes">Scopes</Label>
                <Input id="scopes" value={scopes} onChange={(e) => setScopes(e.target.value)} />
              </div>
            </>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={handleSave} disabled={saving || !name || !slug}>
            {saving ? "Saving..." : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
