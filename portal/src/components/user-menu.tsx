"use client";

import { useSession, signOut } from "next-auth/react";
import { CircleUser, LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";

export function UserMenu() {
  const { data: session } = useSession();

  if (!session?.user) {
    return null;
  }

  return (
    <div className="flex items-center gap-2">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <span className="hidden sm:inline">
          {session.user.name || session.user.email}
        </span>
        <CircleUser className="h-7 w-7" />
      </div>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => signOut({ callbackUrl: "/login" })}
        title="Sign out"
      >
        <LogOut className="h-4 w-4" />
      </Button>
    </div>
  );
}
