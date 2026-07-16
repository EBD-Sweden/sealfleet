"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSession } from "next-auth/react";
import {
  LayoutDashboard,
  MessageCircle,
  Server,
  FlaskConical,
  Rocket,
  Workflow,
  Cog,
  Compass,
  BookOpen,
  ShieldCheck,
  Shield,
  Zap,
  Building2,
  Users,
  Lock,
  Settings,
  CloudSun,
} from "lucide-react";

import { extraNavItems } from "@internal/nav-extra";

import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";

const navItems = [
  { title: "Dashboard", href: "/", icon: LayoutDashboard },
  { title: "Ask", href: "/ask", icon: MessageCircle },
  { title: "Catalog", href: "/catalog", icon: Server },
  { title: "Test Console", href: "/test", icon: FlaskConical },
  { title: "Deploy", href: "/deploy", icon: Rocket },
  { title: "🔌 Pipelines", href: "/pipelines", icon: Workflow },
  { title: "⚙️ Jobs", href: "/jobs", icon: Cog },
  { title: "Discover", href: "/discover", icon: Compass },
  { title: "Weather Example", href: "/weather-trip", icon: CloudSun },
  { title: "Docs", href: "/docs", icon: BookOpen },
  { title: "Agents", href: "/agents", icon: Users },
  { title: "🔐 Credentials", href: "/credentials", icon: Lock },
  { title: "Audit Log", href: "/audit", icon: ShieldCheck },
  { title: "Policy", href: "/policy", icon: Shield },
];

// Private deployments can extend the nav via the @internal overlay; the
// platform default contributes nothing.
const allNavItems = [...navItems, ...extraNavItems];

const adminItems = [
  { title: "Tenants", href: "/admin/tenants", icon: Building2 },
  { title: "Roles", href: "/admin/roles", icon: Shield },
  { title: "Users", href: "/admin/users", icon: Users },
];

export function AppSidebar() {
  const pathname = usePathname();
  const { data: session } = useSession();

  const isAdmin = session?.user?.is_admin === true;

  return (
    <Sidebar>
      <SidebarHeader className="px-4 py-5">
        <Link href="/" className="flex items-center gap-2 font-bold text-lg">
          <Zap className="h-5 w-5 text-primary" />
          <span>Sealfleet</span>
        </Link>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Navigation</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {allNavItems.map((item) => {
                const isActive =
                  item.href === "/"
                    ? pathname === "/"
                    : pathname.startsWith(item.href);
                return (
                  <SidebarMenuItem key={item.href}>
                    <SidebarMenuButton asChild isActive={isActive}>
                      <Link href={item.href}>
                        <item.icon className="h-4 w-4" />
                        <span>{item.title}</span>
                      </Link>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                );
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        {isAdmin && (
          <SidebarGroup>
            <SidebarGroupLabel>
              <Settings className="h-3 w-3 mr-1 inline" />
              Admin
            </SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {adminItems.map((item) => {
                  const isActive = pathname.startsWith(item.href);
                  return (
                    <SidebarMenuItem key={item.href}>
                      <SidebarMenuButton asChild isActive={isActive}>
                        <Link href={item.href}>
                          <item.icon className="h-4 w-4" />
                          <span>{item.title}</span>
                        </Link>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  );
                })}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        )}
      </SidebarContent>
    </Sidebar>
  );
}
