"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useSession } from "next-auth/react";
import { SidebarProvider, SidebarInset, SidebarTrigger } from "@/components/ui/sidebar";
import { AppSidebar } from "@/components/app-sidebar";
import { Input } from "@/components/ui/input";
import { Search } from "lucide-react";
import { UserMenu } from "@/components/user-menu";

const PUBLIC_PATHS = ["/login", "/signup"];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { status } = useSession();

  const isPublicPath = PUBLIC_PATHS.some((p) => pathname.startsWith(p));
  // The root path is the public product landing for logged-out visitors.
  const isLanding = pathname === "/";

  useEffect(() => {
    if (status === "unauthenticated" && !isPublicPath && !isLanding) {
      router.push("/login");
    }
  }, [status, isPublicPath, isLanding, router]);

  // Auth pages render without the shell.
  if (isPublicPath) {
    return <>{children}</>;
  }

  // The landing renders full-bleed (no app chrome) for anyone not signed in.
  if (isLanding && status !== "authenticated") {
    return <>{children}</>;
  }

  if (status === "unauthenticated") {
    return null;
  }

  if (status === "loading") {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="text-muted-foreground">Loading...</div>
      </div>
    );
  }

  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset>
        <header className="sticky top-0 z-10 flex h-14 items-center gap-4 border-b bg-background px-4">
          <SidebarTrigger />
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search servers, tools..."
              className="pl-8 h-9"
            />
          </div>
          <div className="ml-auto flex items-center gap-2">
            <UserMenu />
          </div>
        </header>
        <div className="flex-1 p-6">{children}</div>
      </SidebarInset>
    </SidebarProvider>
  );
}
