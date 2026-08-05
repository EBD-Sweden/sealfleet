import "next-auth";

declare module "next-auth" {
  interface Session {
    user: {
      id: string;
      email: string;
      name?: string | null;
      tenant_id: string;
      is_admin: boolean;
      // Platform operator, not merely an admin of their own tenant. Every
      // self-serve signup is a tenant admin, so is_admin cannot gate
      // platform-only surfaces such as the tenant list.
      is_platform_admin: boolean;
      image?: string | null;
      groups?: string[];
    };
  }

  interface User {
    tenant_id: string;
    is_admin: boolean;
    auth_provider: string;
    groups?: string[];
  }
}

declare module "@auth/core/jwt" {
  interface JWT {
    user_id: string;
    tenant_id: string;
    is_admin: boolean;
    is_platform_admin: boolean;
    email: string;
    groups?: string[];
  }
}
