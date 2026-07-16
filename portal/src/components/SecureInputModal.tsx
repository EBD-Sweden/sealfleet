"use client";

import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Lock, Loader2, ShieldCheck } from "lucide-react";

interface SecureInputModalProps {
  isOpen: boolean;
  onClose: () => void;
  fields: Array<{ name: string; label: string; placeholder?: string }>;
  onSubmit: (handles: Record<string, string>) => void;
  title?: string;
}

export function SecureInputModal({
  isOpen,
  onClose,
  fields,
  onSubmit,
  title = "Secure Input Required",
}: SecureInputModalProps) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const handles: Record<string, string> = {};
      for (const field of fields) {
        const val = values[field.name];
        if (!val?.trim()) continue;
        const res = await fetch("/api/sealed", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            label: field.name,
            value: val,
            expires_in_seconds: 300,
          }),
        });
        if (!res.ok) {
          throw new Error(`Failed to seal ${field.label}`);
        }
        const data = await res.json();
        handles[field.name] = data.handle_id;
      }
      onSubmit(handles);
      setValues({});
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create sealed handles");
    } finally {
      setSubmitting(false);
    }
  };

  const allFilled = fields.every((f) => values[f.name]?.trim());

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Lock className="h-5 w-5 text-primary" />
            {title}
          </DialogTitle>
          <DialogDescription>
            These values are stored securely and never exposed to the AI model.
          </DialogDescription>
        </DialogHeader>

        <div className="rounded-md bg-amber-500/10 border border-amber-500/30 p-3 text-sm text-amber-700 dark:text-amber-400 flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 shrink-0" />
          Values are sealed server-side. The LLM only sees opaque handle IDs.
        </div>

        <div className="space-y-3">
          {fields.map((field) => (
            <div key={field.name} className="space-y-1">
              <label className="text-sm font-medium">{field.label}</label>
              <Input
                type="password"
                placeholder={field.placeholder || `Enter ${field.label}`}
                value={values[field.name] || ""}
                onChange={(e) =>
                  setValues((prev) => ({ ...prev, [field.name]: e.target.value }))
                }
              />
            </div>
          ))}
        </div>

        {error && (
          <div className="rounded-md bg-destructive/10 border border-destructive/30 p-3 text-sm text-destructive">
            {error}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={submitting || !allFilled}>
            {submitting ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Sealing...
              </>
            ) : (
              <>
                <Lock className="mr-2 h-4 w-4" />
                Submit Securely
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
