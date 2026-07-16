"use client";

import { useSession } from "next-auth/react";
import { ShieldAlert } from "lucide-react";

export function AdminGuard({ children }: { children: React.ReactNode }) {
  const { data: session, status } = useSession();

  if (status === "loading") {
    return (
      <div className="flex items-center justify-center p-12">
        <div className="text-muted-foreground">Loading...</div>
      </div>
    );
  }

  if (!session?.user?.is_admin) {
    return (
      <div className="flex flex-col items-center justify-center p-12 gap-4">
        <ShieldAlert className="h-12 w-12 text-destructive" />
        <h1 className="text-2xl font-bold">Access Denied</h1>
        <p className="text-muted-foreground">
          You need administrator privileges to view this page.
        </p>
      </div>
    );
  }

  return <>{children}</>;
}
