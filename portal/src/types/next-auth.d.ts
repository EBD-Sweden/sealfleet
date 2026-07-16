import "next-auth";

declare module "next-auth" {
  interface Session {
    user: {
      id: string;
      email: string;
      name?: string | null;
      tenant_id: string;
      is_admin: boolean;
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
    email: string;
    groups?: string[];
  }
}
