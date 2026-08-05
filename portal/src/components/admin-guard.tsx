"use client";

import { useSession } from "next-auth/react";
import { ShieldAlert } from "lucide-react";

// `platformOnly` marks a page that belongs to the platform operator rather than
// to a customer org. Every self-serve signup is an admin of its own tenant, so
// is_admin alone would admit them to platform surfaces they cannot use.
export function AdminGuard({
  children,
  platformOnly = false,
}: {
  children: React.ReactNode;
  platformOnly?: boolean;
}) {
  const { data: session, status } = useSession();

  if (status === "loading") {
    return (
      <div className="flex items-center justify-center p-12">
        <div className="text-muted-foreground">Loading...</div>
      </div>
    );
  }

  const permitted = platformOnly
    ? session?.user?.is_platform_admin === true
    : session?.user?.is_admin === true;

  if (!permitted) {
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
