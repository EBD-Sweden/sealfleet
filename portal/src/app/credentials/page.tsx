"use client";

import { useEffect, useState, useCallback } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import { Skeleton } from "@/components/ui/skeleton";
import {
  Lock,
  Plus,
  Pencil,
  Trash2,
  Eye,
  EyeOff,
  X,
  Info,
  ShieldCheck,
  AlertTriangle,
} from "lucide-react";

/* ---------- Types ---------- */

type StorageMode = "platform" | "byok" | "k8s";
type CredentialType = "api_key" | "oauth_token" | "basic_auth" | "custom";

interface Credential {
  id: string;
  name: string;
  description: string;
  storage_mode: StorageMode;
  credential_type: CredentialType;
  assigned_mcp: string;
  active: boolean;
  last_used: string | null;
  created_at: string;
}

interface CreatePayload {
  name: string;
  description: string;
  storage_mode: StorageMode;
  credential_type: CredentialType;
  value?: string;
  encryption_key?: string;
  k8s_secret_name?: string;
  k8s_secret_key?: string;
  assigned_mcp: string;
}

interface EditPayload {
  description?: string;
  assigned_mcp?: string;
  active?: boolean;
  value?: string;
  encryption_key?: string;
}

/* ---------- Badge helpers ---------- */

function StorageModeBadge({ mode }: { mode: StorageMode }) {
  const config: Record<StorageMode, { label: string; cls: string }> = {
    platform: { label: "Platform", cls: "bg-gray-600 hover:bg-gray-700 text-white" },
    byok: { label: "BYOK", cls: "bg-purple-600 hover:bg-purple-700 text-white" },
    k8s: { label: "K8s Secret", cls: "bg-blue-600 hover:bg-blue-700 text-white" },
  };
  const c = config[mode] ?? config.platform;
  return <Badge className={`${c.cls} text-xs`}>{c.label}</Badge>;
}

function TypeBadge({ type }: { type: CredentialType }) {
  const config: Record<CredentialType, { label: string; cls: string }> = {
    api_key: { label: "API Key", cls: "bg-blue-600 hover:bg-blue-700 text-white" },
    oauth_token: { label: "OAuth", cls: "bg-purple-600 hover:bg-purple-700 text-white" },
    basic_auth: { label: "Basic Auth", cls: "bg-gray-600 hover:bg-gray-700 text-white" },
    custom: { label: "Custom", cls: "bg-orange-600 hover:bg-orange-700 text-white" },
  };
  const c = config[type] ?? config.custom;
  return <Badge className={`${c.cls} text-xs`}>{c.label}</Badge>;
}

function StatusBadge({ active }: { active: boolean }) {
  return active ? (
    <Badge className="bg-green-600 hover:bg-green-700 text-white text-xs">Active</Badge>
  ) : (
    <Badge className="bg-red-600 hover:bg-red-700 text-white text-xs">Inactive</Badge>
  );
}

/* ---------- Password Input ---------- */

function PasswordInput({
  value,
  onChange,
  placeholder,
  id,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  id?: string;
}) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative">
      <Input
        id={id}
        type={show ? "text" : "password"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="pr-10 bg-gray-800 border-gray-700 text-white"
      />
      <button
        type="button"
        onClick={() => setShow(!show)}
        className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-white"
      >
        {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
      </button>
    </div>
  );
}

/* ---------- Toast ---------- */

function Toast({ message, onClose }: { message: string; onClose: () => void }) {
  useEffect(() => {
    const t = setTimeout(onClose, 4000);
    return () => clearTimeout(t);
  }, [onClose]);
  return (
    <div className="fixed bottom-6 right-6 z-50 bg-green-700 text-white px-4 py-3 rounded-lg shadow-lg flex items-center gap-2">
      <span>{message}</span>
      <button onClick={onClose} className="text-white/70 hover:text-white">
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}

/* ---------- Add Modal ---------- */

function AddCredentialModal({
  open,
  onClose,
  onSuccess,
}: {
  open: boolean;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [mode, setMode] = useState<StorageMode>("platform");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [credType, setCredType] = useState<CredentialType>("api_key");
  const [value, setValue] = useState("");
  const [encryptionKey, setEncryptionKey] = useState("");
  const [k8sSecretName, setK8sSecretName] = useState("");
  const [k8sSecretKey, setK8sSecretKey] = useState("");
  const [assignedMcp, setAssignedMcp] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const reset = () => {
    setMode("platform");
    setName("");
    setDescription("");
    setCredType("api_key");
    setValue("");
    setEncryptionKey("");
    setK8sSecretName("");
    setK8sSecretKey("");
    setAssignedMcp("");
    setError("");
  };

  const handleClose = () => {
    reset();
    onClose();
  };

  const handleSubmit = async () => {
    if (!name.trim()) { setError("Name is required"); return; }
    if (mode !== "k8s" && !value.trim()) { setError("Value is required"); return; }
    if (mode === "byok" && !encryptionKey.trim()) { setError("Encryption key is required"); return; }
    if (mode === "k8s" && (!k8sSecretName.trim() || !k8sSecretKey.trim())) {
      setError("K8s Secret Name and Key are required");
      return;
    }

    setSubmitting(true);
    setError("");

    const payload: CreatePayload = {
      name: name.trim(),
      description: description.trim(),
      storage_mode: mode,
      credential_type: credType,
      assigned_mcp: assignedMcp.trim(),
    };

    if (mode === "platform" || mode === "byok") {
      payload.value = value;
    }
    if (mode === "byok") {
      payload.encryption_key = encryptionKey;
    }
    if (mode === "k8s") {
      payload.k8s_secret_name = k8sSecretName.trim();
      payload.k8s_secret_key = k8sSecretKey.trim();
    }

    try {
      const res = await fetch("/api/credentials", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const data = await res.json();
        setError(data.error || "Failed to create credential");
        return;
      }
      reset();
      onSuccess();
    } catch {
      setError("Network error");
    } finally {
      setSubmitting(false);
    }
  };

  if (!open) return null;

  const modeCards: { key: StorageMode; icon: string; title: string; desc: string }[] = [
    { key: "platform", icon: "🏠", title: "Platform", desc: "Sealfleet encrypts and stores. Easiest setup." },
    { key: "byok", icon: "🔑", title: "BYOK", desc: "You provide the encryption key. Sealfleet never holds your plaintext key." },
    { key: "k8s", icon: "☸️", title: "K8s Secret", desc: "Credential lives in your k8s Secret. Sealfleet only stores the reference." },
  ];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-gray-900 border border-gray-800 rounded-xl shadow-2xl w-full max-w-lg max-h-[90vh] overflow-y-auto p-6">
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-xl font-semibold text-white">Add Credential</h2>
          <button onClick={handleClose} className="text-gray-400 hover:text-white">
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Step 1: Storage Mode */}
        <div className="mb-6">
          <Label className="text-sm text-gray-400 mb-3 block">Storage Mode</Label>
          <div className="grid grid-cols-3 gap-3">
            {modeCards.map((m) => (
              <button
                key={m.key}
                onClick={() => setMode(m.key)}
                className={`p-3 rounded-lg border text-left transition-all ${
                  mode === m.key
                    ? "border-blue-500 bg-blue-500/10"
                    : "border-gray-700 bg-gray-800 hover:border-gray-600"
                }`}
              >
                <div className="text-lg mb-1">{m.icon}</div>
                <div className="text-sm font-medium text-white">{m.title}</div>
                <div className="text-xs text-gray-400 mt-1">{m.desc}</div>
              </button>
            ))}
          </div>
        </div>

        {/* Step 2: Fields */}
        <div className="space-y-4">
          <div>
            <Label htmlFor="cred-name" className="text-sm text-gray-400">Name *</Label>
            <Input
              id="cred-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="stripe-prod"
              className="bg-gray-800 border-gray-700 text-white mt-1"
            />
          </div>

          <div>
            <Label htmlFor="cred-desc" className="text-sm text-gray-400">Description</Label>
            <Input
              id="cred-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Production Stripe API key"
              className="bg-gray-800 border-gray-700 text-white mt-1"
            />
          </div>

          <div>
            <Label className="text-sm text-gray-400">Type</Label>
            <select
              value={credType}
              onChange={(e) => setCredType(e.target.value as CredentialType)}
              className="w-full mt-1 rounded-md bg-gray-800 border border-gray-700 text-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="api_key">API Key</option>
              <option value="oauth_token">OAuth Token</option>
              <option value="basic_auth">Basic Auth</option>
              <option value="custom">Custom</option>
            </select>
          </div>

          {(mode === "platform" || mode === "byok") && (
            <div>
              <Label htmlFor="cred-value" className="text-sm text-gray-400">Value *</Label>
              <PasswordInput
                id="cred-value"
                value={value}
                onChange={setValue}
                placeholder="sk-live-..."
              />
            </div>
          )}

          {mode === "byok" && (
            <>
              <div>
                <Label htmlFor="cred-key" className="text-sm text-gray-400">Your Encryption Key *</Label>
                <PasswordInput
                  id="cred-key"
                  value={encryptionKey}
                  onChange={setEncryptionKey}
                  placeholder="Fernet key..."
                />
              </div>
              <div className="flex items-start gap-2 p-3 rounded-lg bg-amber-500/10 border border-amber-500/30">
                <AlertTriangle className="h-4 w-4 text-amber-400 mt-0.5 shrink-0" />
                <span className="text-xs text-amber-300">
                  Store your key safely. If lost, the credential cannot be recovered.
                </span>
              </div>
            </>
          )}

          {mode === "k8s" && (
            <>
              <div>
                <Label htmlFor="k8s-secret" className="text-sm text-gray-400">K8s Secret Name *</Label>
                <Input
                  id="k8s-secret"
                  value={k8sSecretName}
                  onChange={(e) => setK8sSecretName(e.target.value)}
                  placeholder="app-secrets"
                  className="bg-gray-800 border-gray-700 text-white mt-1"
                />
              </div>
              <div>
                <Label htmlFor="k8s-key" className="text-sm text-gray-400">K8s Secret Key *</Label>
                <Input
                  id="k8s-key"
                  value={k8sSecretKey}
                  onChange={(e) => setK8sSecretKey(e.target.value)}
                  placeholder="STRIPE_KEY"
                  className="bg-gray-800 border-gray-700 text-white mt-1"
                />
              </div>
              <div className="flex items-start gap-2 p-3 rounded-lg bg-blue-500/10 border border-blue-500/30">
                <Info className="h-4 w-4 text-blue-400 mt-0.5 shrink-0" />
                <span className="text-xs text-blue-300">
                  Sealfleet will read from your cluster&apos;s Secret at runtime.
                </span>
              </div>
            </>
          )}

          <div>
            <Label htmlFor="cred-mcp" className="text-sm text-gray-400">Assign to MCP</Label>
            <Input
              id="cred-mcp"
              value={assignedMcp}
              onChange={(e) => setAssignedMcp(e.target.value)}
              placeholder="payment-mcp"
              className="bg-gray-800 border-gray-700 text-white mt-1"
            />
          </div>
        </div>

        {error && (
          <div className="mt-4 p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300 text-sm">
            {error}
          </div>
        )}

        <div className="flex justify-end gap-3 mt-6">
          <Button variant="outline" onClick={handleClose} className="border-gray-700 text-gray-300 hover:bg-gray-800">
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={submitting} className="bg-blue-600 hover:bg-blue-700 text-white">
            {submitting ? "Saving..." : "Save Credential"}
          </Button>
        </div>
      </div>
    </div>
  );
}

/* ---------- Edit Modal ---------- */

function EditCredentialModal({
  credential,
  onClose,
  onSuccess,
}: {
  credential: Credential | null;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [description, setDescription] = useState("");
  const [assignedMcp, setAssignedMcp] = useState("");
  const [active, setActive] = useState(true);
  const [rotateValue, setRotateValue] = useState("");
  const [rotateKey, setRotateKey] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (credential) {
      setDescription(credential.description || "");
      setAssignedMcp(credential.assigned_mcp || "");
      setActive(credential.active);
      setRotateValue("");
      setRotateKey("");
      setError("");
    }
  }, [credential]);

  if (!credential) return null;

  const handleSubmit = async () => {
    setSubmitting(true);
    setError("");

    const payload: EditPayload = {
      description: description.trim(),
      assigned_mcp: assignedMcp.trim(),
      active,
    };

    if (rotateValue.trim()) {
      payload.value = rotateValue;
    }
    if (credential.storage_mode === "byok" && rotateKey.trim()) {
      payload.encryption_key = rotateKey;
    }

    try {
      const res = await fetch(`/api/credentials/${credential.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const data = await res.json();
        setError(data.error || "Failed to update credential");
        return;
      }
      onSuccess();
    } catch {
      setError("Network error");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-gray-900 border border-gray-800 rounded-xl shadow-2xl w-full max-w-lg max-h-[90vh] overflow-y-auto p-6">
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-xl font-semibold text-white">Edit: {credential.name}</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-white">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <StorageModeBadge mode={credential.storage_mode} />
            <TypeBadge type={credential.credential_type} />
          </div>

          <div>
            <Label htmlFor="edit-desc" className="text-sm text-gray-400">Description</Label>
            <Input
              id="edit-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="bg-gray-800 border-gray-700 text-white mt-1"
            />
          </div>

          <div>
            <Label htmlFor="edit-mcp" className="text-sm text-gray-400">Assign to MCP</Label>
            <Input
              id="edit-mcp"
              value={assignedMcp}
              onChange={(e) => setAssignedMcp(e.target.value)}
              className="bg-gray-800 border-gray-700 text-white mt-1"
            />
          </div>

          <div className="flex items-center gap-3">
            <Label className="text-sm text-gray-400">Active</Label>
            <button
              onClick={() => setActive(!active)}
              className={`w-10 h-5 rounded-full transition-colors ${
                active ? "bg-green-600" : "bg-gray-600"
              } relative`}
            >
              <span
                className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                  active ? "translate-x-5" : ""
                }`}
              />
            </button>
          </div>

          {/* Rotate Secret */}
          {credential.storage_mode !== "k8s" && (
            <div className="border-t border-gray-800 pt-4">
              <h3 className="text-sm font-medium text-gray-300 mb-3">🔄 Rotate Secret</h3>
              <div className="space-y-3">
                <div>
                  <Label htmlFor="rotate-val" className="text-sm text-gray-400">New Value</Label>
                  <PasswordInput
                    id="rotate-val"
                    value={rotateValue}
                    onChange={setRotateValue}
                    placeholder="Leave empty to keep current"
                  />
                </div>
                {credential.storage_mode === "byok" && (
                  <div>
                    <Label htmlFor="rotate-key" className="text-sm text-gray-400">Encryption Key</Label>
                    <PasswordInput
                      id="rotate-key"
                      value={rotateKey}
                      onChange={setRotateKey}
                      placeholder="Your Fernet key"
                    />
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {error && (
          <div className="mt-4 p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300 text-sm">
            {error}
          </div>
        )}

        <div className="flex justify-end gap-3 mt-6">
          <Button variant="outline" onClick={onClose} className="border-gray-700 text-gray-300 hover:bg-gray-800">
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={submitting} className="bg-blue-600 hover:bg-blue-700 text-white">
            {submitting ? "Saving..." : "Update"}
          </Button>
        </div>
      </div>
    </div>
  );
}

/* ---------- Main Page ---------- */

export default function CredentialsPage() {
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [addOpen, setAddOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Credential | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [toast, setToast] = useState("");

  const fetchCredentials = useCallback(async () => {
    try {
      const res = await fetch("/api/credentials");
      if (!res.ok) throw new Error("Failed to load credentials");
      const data = await res.json();
      setCredentials(data.credentials ?? data ?? []);
      setError("");
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Failed to load credentials";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchCredentials();
  }, [fetchCredentials]);

  const handleDelete = async (id: string) => {
    try {
      await fetch(`/api/credentials/${id}`, { method: "DELETE" });
      setDeleteConfirm(null);
      setToast("Credential deleted");
      fetchCredentials();
    } catch {
      setError("Failed to delete credential");
    }
  };

  const formatDate = (d: string | null) => {
    if (!d) return "—";
    return new Date(d).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  };

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-3xl font-bold text-white flex items-center gap-2">
            🔐 Credentials
          </h1>
          <p className="text-gray-400 mt-1">
            Encrypted secrets for your MCPs. The LLM never sees plaintext values.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Badge className="bg-green-600/20 text-green-400 border border-green-600/30 text-xs">
            <ShieldCheck className="h-3 w-3 mr-1" />
            AES-256 Encrypted
          </Badge>
          <Button
            onClick={() => setAddOpen(true)}
            className="bg-blue-600 hover:bg-blue-700 text-white"
          >
            <Plus className="h-4 w-4 mr-1" />
            Add Credential
          </Button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300 text-sm">
          {error}
        </div>
      )}

      {/* Table or Empty State */}
      {loading ? (
        <Card className="bg-gray-900 border-gray-800">
          <CardContent className="p-6 space-y-3">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-12 w-full bg-gray-800" />
            ))}
          </CardContent>
        </Card>
      ) : credentials.length === 0 ? (
        <Card className="bg-gray-900 border-gray-800">
          <CardContent className="p-12 flex flex-col items-center text-center">
            <Lock className="h-12 w-12 text-gray-600 mb-4" />
            <h3 className="text-lg font-medium text-gray-300 mb-2">No credentials yet</h3>
            <p className="text-gray-500 mb-6 max-w-md">
              Add your first credential to connect an MCP securely.
            </p>
            <Button
              onClick={() => setAddOpen(true)}
              className="bg-blue-600 hover:bg-blue-700 text-white"
            >
              <Plus className="h-4 w-4 mr-1" />
              Add Credential
            </Button>
          </CardContent>
        </Card>
      ) : (
        <Card className="bg-gray-900 border-gray-800">
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow className="border-gray-800 hover:bg-transparent">
                  <TableHead className="text-gray-400">Name</TableHead>
                  <TableHead className="text-gray-400">Storage Mode</TableHead>
                  <TableHead className="text-gray-400">Type</TableHead>
                  <TableHead className="text-gray-400">Assigned MCP</TableHead>
                  <TableHead className="text-gray-400">Status</TableHead>
                  <TableHead className="text-gray-400">Last Used</TableHead>
                  <TableHead className="text-gray-400">Created</TableHead>
                  <TableHead className="text-gray-400 text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {credentials.map((cred) => (
                  <TableRow key={cred.id} className="border-gray-800 hover:bg-gray-800/50">
                    <TableCell className="font-medium text-white">{cred.name}</TableCell>
                    <TableCell><StorageModeBadge mode={cred.storage_mode} /></TableCell>
                    <TableCell><TypeBadge type={cred.credential_type} /></TableCell>
                    <TableCell className="text-gray-300">
                      {cred.assigned_mcp || <span className="text-gray-600">—</span>}
                    </TableCell>
                    <TableCell><StatusBadge active={cred.active} /></TableCell>
                    <TableCell className="text-gray-400 text-sm">{formatDate(cred.last_used)}</TableCell>
                    <TableCell className="text-gray-400 text-sm">{formatDate(cred.created_at)}</TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setEditTarget(cred)}
                          className="text-gray-400 hover:text-white hover:bg-gray-800 h-8 w-8 p-0"
                        >
                          <Pencil className="h-4 w-4" />
                        </Button>
                        {deleteConfirm === cred.id ? (
                          <div className="flex items-center gap-1">
                            <span className="text-xs text-red-400 mr-1">
                              Delete &apos;{cred.name}&apos;?
                            </span>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => setDeleteConfirm(null)}
                              className="text-gray-400 hover:text-white h-7 px-2 text-xs"
                            >
                              Cancel
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => handleDelete(cred.id)}
                              className="text-red-400 hover:text-red-300 hover:bg-red-500/10 h-7 px-2 text-xs"
                            >
                              Delete
                            </Button>
                          </div>
                        ) : (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setDeleteConfirm(cred.id)}
                            className="text-gray-400 hover:text-red-400 hover:bg-gray-800 h-8 w-8 p-0"
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {/* Token Helper */}
      <Card className="bg-gray-900/50 border-gray-800">
        <CardContent className="p-4">
          <div className="flex items-start gap-3">
            <Info className="h-5 w-5 text-blue-400 mt-0.5 shrink-0" />
            <div>
              <p className="text-sm text-gray-300">
                Use credentials in MCP inputs as{" "}
                <code className="text-blue-400 bg-blue-500/10 px-1.5 py-0.5 rounded text-xs">
                  {"{{credential:name}}"}
                </code>{" "}
                — the value is resolved at call time and never exposed to the LLM.
              </p>
              <pre className="mt-2 text-xs text-gray-400 bg-gray-800/50 rounded p-3 overflow-x-auto">
{`{"Authorization": "Bearer {{credential:stripe-key}}"}`}
              </pre>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Modals */}
      <AddCredentialModal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onSuccess={() => {
          setAddOpen(false);
          setToast("✅ Credential saved securely");
          fetchCredentials();
        }}
      />

      <EditCredentialModal
        credential={editTarget}
        onClose={() => setEditTarget(null)}
        onSuccess={() => {
          setEditTarget(null);
          setToast("✅ Credential updated");
          fetchCredentials();
        }}
      />

      {/* Toast */}
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </div>
  );
}
