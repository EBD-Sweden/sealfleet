export interface Tenant {
  id: string;
  slug: string;
  name: string;
  sso_enabled: boolean;
  oidc_issuer: string | null;
  oidc_client_id: string | null;
  oidc_client_secret: string | null;
  oidc_scopes: string | null;
  allowed_domains: string[] | null;
  created_at: string;
  updated_at: string;
}

export interface Role {
  id: string;
  tenant_id: string | null;
  name: string;
  description: string | null;
  tenant_name?: string;
  permissions_count?: number;
}

export interface McpPermission {
  id: string;
  tenant_id: string | null;
  grantee_type: string;
  grantee_id: string;
  server_id: string | null;
  allowed_tools: string[] | null;
  scopes: string[];
  server_name?: string;
}

export interface SsoRoleMapping {
  id: string;
  tenant_id: string;
  idp_claim_key: string;
  idp_claim_value: string;
  role_id: string;
  role_name?: string;
  created_at: string;
}

export interface AdminUser {
  id: string;
  tenant_id: string | null;
  email: string;
  name: string | null;
  auth_provider: string;
  is_active: boolean;
  is_admin: boolean;
  last_login_at: string | null;
  created_at: string;
  tenant_name?: string;
  roles: { id: string; name: string }[];
}

export interface McpServer {
  id: string;
  name: string;
  description: string | null;
  status: string;
}
