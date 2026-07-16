"""Shared enterprise identity/compliance contracts for Sealfleet.

This module is intentionally product-neutral where possible and exposes typed,
serializable primitives that Sealfleet enforces now and that adapters for other
resource types (e.g. data warehouses) can consume through the same semantics.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal


class ContractEnum(str, Enum):
    """Enum that serializes as its value in dataclass payloads."""

    def __str__(self) -> str:
        return self.value


class DeploymentMode(ContractEnum):
    LOCAL_DEV = "local_dev"
    INTERNAL_PIPELINE = "internal_pipeline"
    PUBLIC_PLATFORM = "public_platform"


class PrincipalType(ContractEnum):
    USER = "user"
    GROUP = "group"
    SERVICE = "service"
    MARKETPLACE = "marketplace"
    SYSTEM = "system"


class AuthProviderKind(ContractEnum):
    LOCAL = "local"
    OIDC = "oidc"
    SAML = "saml"
    SCIM = "scim"
    API_KEY = "api_key"
    SERVICE_ACCOUNT = "service_account"


class LifecycleStatus(ContractEnum):
    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEACTIVATED = "deactivated"
    TOMBSTONED = "tombstoned"


@dataclass(frozen=True)
class Organization:
    organization_id: str
    display_name: str
    verified_domains: tuple[str, ...] = ()
    deployment_mode: DeploymentMode = DeploymentMode.LOCAL_DEV
    data_residency: str | None = None
    billing_account_id: str | None = None
    compliance_profile: str | None = None
    membership_model: str = "organization_membership_is_not_product_access"


@dataclass(frozen=True)
class Tenant:
    tenant_id: str
    organization_id: str
    product: str
    display_name: str
    environment: str = "dev"
    resource_boundary: str = "product_workspace"


@dataclass(frozen=True)
class EnterpriseSubject:
    subject_id: str
    principal_type: PrincipalType
    organization_id: str
    tenant_ids: tuple[str, ...] = ()
    email: str | None = None
    display_name: str | None = None
    idp_subject: str | None = None
    groups: tuple[str, ...] = ()
    lifecycle_status: str = LifecycleStatus.INVITED.value


@dataclass(frozen=True)
class TeamGroup:
    group_id: str
    organization_id: str
    display_name: str
    external_id: str | None = None
    source: Literal["local", "idp", "scim"] = "local"
    lifecycle_status: LifecycleStatus = LifecycleStatus.ACTIVE


@dataclass(frozen=True)
class RoleGrant:
    grant_id: str
    organization_id: str
    subject_id: str
    role: str
    resource_type: str
    resource_id: str = "*"
    actions: tuple[str, ...] = ()
    conditions: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ServiceIdentity:
    service_identity_id: str
    organization_id: str
    display_name: str
    scopes: tuple[str, ...]
    tenant_ids: tuple[str, ...] = ()
    token_ttl_seconds: int = 900
    key_rotation_required: bool = True
    owner_subject_id: str | None = None


@dataclass(frozen=True)
class AuthIntegration:
    integration_id: str
    organization_id: str
    provider_kind: AuthProviderKind
    issuer: str | None = None
    audience: str | None = None
    domain_routing: tuple[str, ...] = ()
    jwks_uri: str | None = None
    saml_metadata_url: str | None = None
    scim_base_url: str | None = None
    requires_verified_domain_routing: bool = True
    secret_material_model: str = "sealed_or_external_secret_ref_only"


@dataclass(frozen=True)
class ScimProvisioningContract:
    organization_id: str
    users_endpoint: str = "/scim/v2/Users"
    groups_endpoint: str = "/scim/v2/Groups"
    lifecycle_states: tuple[str, ...] = tuple(status.value for status in LifecycleStatus)
    deactivation_effects: tuple[str, ...] = (
        "disable_sessions",
        "block_future_execution",
        "optionally_revoke_user_owned_api_keys",
        "emit_audit_event",
    )


@dataclass(frozen=True)
class PolicyResource:
    organization_id: str
    resource_type: str
    resource_id: str
    tenant_id: str | None = None
    resource_name: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyDecisionEnvelope:
    subject: EnterpriseSubject
    action: str
    resource: PolicyResource
    context: dict[str, Any]
    decision: Literal["allow", "deny", "require_approval"]
    reason: str
    policy_version: str
    trace_id: str

    @classmethod
    def allow(
        cls,
        *,
        subject: EnterpriseSubject,
        action: str,
        resource: PolicyResource,
        trace_id: str,
        policy_version: str,
        context: dict[str, Any] | None = None,
        reason: str = "policy_allowed",
    ) -> "PolicyDecisionEnvelope":
        return cls(
            subject=subject,
            action=action,
            resource=resource,
            context=context or {},
            decision="allow",
            reason=reason,
            policy_version=policy_version,
            trace_id=trace_id,
        )

    @classmethod
    def deny(
        cls,
        *,
        subject: EnterpriseSubject,
        action: str,
        resource: PolicyResource,
        trace_id: str,
        policy_version: str,
        reason: str,
        context: dict[str, Any] | None = None,
    ) -> "PolicyDecisionEnvelope":
        return cls(
            subject=subject,
            action=action,
            resource=resource,
            context=context or {},
            decision="deny",
            reason=reason,
            policy_version=policy_version,
            trace_id=trace_id,
        )


@dataclass(frozen=True)
class AuditEventV1:
    event_id: str
    event_time: datetime
    organization_id: str
    action: str
    resource_type: str
    resource_id: str
    actor_type: str
    actor_id: str
    decision: str
    result: str
    reason_code: str
    trace_id: str
    audit_schema_version: str = "audit-event/v1"
    tenant_id: str | None = None
    actor_display: str | None = None
    actor_auth_method: str | None = None
    service_identity_id: str | None = None
    resource_name: str | None = None
    correlation_id: str | None = None
    request_id: str | None = None
    source_ip: str | None = None
    user_agent: str | None = None
    policy_version: str | None = None
    input_classification: str | None = None
    secret_handle_refs: tuple[str, ...] = ()
    metadata_redacted: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_policy_decision(
        cls,
        *,
        event_id: str,
        event_time: datetime,
        decision: PolicyDecisionEnvelope,
        result: str,
        secret_handle_refs: tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> "AuditEventV1":
        subject = decision.subject
        resource = decision.resource
        return cls(
            event_id=event_id,
            event_time=event_time,
            organization_id=resource.organization_id,
            tenant_id=resource.tenant_id,
            action=decision.action,
            resource_type=resource.resource_type,
            resource_id=resource.resource_id,
            resource_name=resource.resource_name,
            actor_type=str(subject.principal_type),
            actor_id=subject.subject_id,
            actor_display=subject.display_name or subject.email,
            service_identity_id=subject.subject_id if subject.principal_type == PrincipalType.SERVICE else None,
            decision=decision.decision,
            result=result,
            reason_code=decision.reason,
            trace_id=decision.trace_id,
            policy_version=decision.policy_version,
            input_classification=resource.attributes.get("input_classification"),
            secret_handle_refs=secret_handle_refs,
            metadata=metadata or {},
        )


@dataclass(frozen=True)
class SealedHandleDescriptor:
    handle_id: str
    organization_id: str
    tenant_id: str | None
    subject_id: str
    purpose: str
    resource_scope: tuple[str, ...]
    expires_at: datetime
    single_use: bool
    resolver: str
    status: Literal["active", "resolved", "expired", "revoked"] = "active"
    secret_type: str = "generic"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def redacted_dict(self) -> dict[str, Any]:
        data = _to_jsonable_dict(self)
        data["value"] = "<redacted>"
        data["secret_material"] = "<redacted>"
        return data


@dataclass(frozen=True)
class SessionDescriptor:
    session_id: str
    organization_id: str
    subject_id: str
    auth_method: AuthProviderKind
    issued_at: datetime
    expires_at: datetime
    tenant_ids: tuple[str, ...] = ()
    revoked: bool = False
    token_material_model: str = "opaque_or_signed_token_never_model_visible"


@dataclass(frozen=True)
class MarketplaceIdentityHook:
    organization_id: str
    marketplace: Literal["aws", "gcp", "azure", "license_key", "disabled"]
    buyer_account_id: str
    entitlement_ids: tuple[str, ...]
    subscription_status: str
    publisher_id: str | None = None
    replaces_authorization: bool = False

    def policy_context(self) -> dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "marketplace": self.marketplace,
            "buyer_account_id": self.buyer_account_id,
            "entitlement_ids": list(self.entitlement_ids),
            "subscription_status": self.subscription_status,
            "publisher_id": self.publisher_id,
        }


class SealfleetResourceAdapter:
    """Map Sealfleet runtime actions into the shared policy/audit resource shape."""

    @staticmethod
    def tool_call(
        *,
        organization_id: str,
        tenant_id: str,
        server_id: str,
        tool_name: str,
        transport: str,
        input_classification: str,
    ) -> PolicyResource:
        return PolicyResource(
            organization_id=organization_id,
            tenant_id=tenant_id,
            resource_type="mcp.tool",
            resource_id=f"{server_id}:{tool_name}",
            resource_name=tool_name,
            attributes={
                "server_id": server_id,
                "transport": transport,
                "input_classification": input_classification,
            },
        )

    @staticmethod
    def pipeline_run(*, organization_id: str, tenant_id: str, pipeline_id: str) -> PolicyResource:
        return PolicyResource(organization_id, "mcp.pipeline", pipeline_id, tenant_id=tenant_id)

    @staticmethod
    def credential_use(*, organization_id: str, tenant_id: str, credential_id: str) -> PolicyResource:
        return PolicyResource(organization_id, "mcp.credential", credential_id, tenant_id=tenant_id)

    @staticmethod
    def sealed_handle_resolve(*, organization_id: str, tenant_id: str, handle_id: str) -> PolicyResource:
        return PolicyResource(organization_id, "mcp.sealed_handle", handle_id, tenant_id=tenant_id)

    @staticmethod
    def deploy_create(*, organization_id: str, tenant_id: str, deploy_target: str) -> PolicyResource:
        return PolicyResource(organization_id, "mcp.deploy_target", deploy_target, tenant_id=tenant_id)

    @staticmethod
    def audit_read(*, organization_id: str, tenant_id: str) -> PolicyResource:
        return PolicyResource(organization_id, "mcp.audit", tenant_id, tenant_id=tenant_id)


def enterprise_contract_v1() -> dict[str, Any]:
    """Return the stable v1 enterprise auth/compliance contract summary."""

    return {
        "version": "enterprise-auth-contract/v1",
        "boundary": {
            "llm_sees": ["opaque_handles", "receipts", "trace_ids", "redacted_metadata"],
            "llm_never_sees": ["api_keys", "refresh_tokens", "private_keys", "raw_secrets", "unredacted_pii"],
            "enforcement_point": "product_adapter_to_policy_audit_secret_boundary",
        },
        "deployment_modes": [mode.value for mode in DeploymentMode],
        "identity_core": {
            "objects": [
                "organization",
                "tenant/workspace/project",
                "user",
                "group/team",
                "role_grant",
                "service_identity",
                "resource",
            ],
            "rule": "organization_membership_is_not_product_access",
        },
        "auth_integrations": {
            "provider_kinds": [kind.value for kind in AuthProviderKind],
            "oidc": "authorization_code_pkce_with_issuer_audience_jwks_key_rotation",
            "saml": "enterprise_saml2_metadata_and_assertion_mapping",
            "local": "bootstrap_or_developer_mode_only",
            "service_accounts": "scoped_expiring_rotatable_tokens",
            "routing": "verified_domain_or_explicit_idp_selection_required",
        },
        "scim_lifecycle": _to_jsonable_dict(ScimProvisioningContract(organization_id="org_example")),
        "policy_primitives": {
            "decisions": ["allow", "deny", "require_approval"],
            "envelope_fields": [
                "subject",
                "action",
                "resource",
                "context",
                "decision",
                "reason",
                "policy_version",
                "trace_id",
            ],
            "rbac": "role_grants_are_admin_friendly_defaults",
            "abac": "contextual_conditions_cover_tenant_environment_tags_data_sensitivity_ttl_approval_network",
        },
        "audit_event_schema": {
            "schema_version": "audit-event/v1",
            "append_only_in_production": True,
            "redaction_required": True,
            "fields": list(AuditEventV1.__dataclass_fields__.keys()),
        },
        "sealed_secret_session_model": {
            "handle_fields": list(SealedHandleDescriptor.__dataclass_fields__.keys()),
            "session_fields": list(SessionDescriptor.__dataclass_fields__.keys()),
            "storage": "local_dev_provider_or_vault_kms_cloud_secret_manager_in_production",
            "list_read_returns": "metadata_only_redacted",
        },
        "marketplace_identity_hooks": {
            "marketplaces": ["aws", "gcp", "azure", "license_key", "disabled"],
            "rule": "entitlements_feed_policy_context_but_do_not_replace_authorization",
            "fields": list(MarketplaceIdentityHook.__dataclass_fields__.keys()),
        },
        "mcpfinder_adapter": {
            "resources": [
                "mcp.server",
                "mcp.tool",
                "mcp.pipeline",
                "mcp.credential",
                "mcp.sealed_handle",
                "mcp.deploy_target",
                "mcp.audit",
            ],
            "actions": [
                "mcp.server.register",
                "mcp.tool.call",
                "mcp.pipeline.run",
                "credential.create",
                "credential.use",
                "sealed_handle.create",
                "sealed_handle.resolve",
                "deploy.create",
                "audit.read",
            ],
            "required_context": [
                "server_id",
                "tool_name",
                "transport",
                "input_classification",
                "credential_handle_id",
                "execution_environment",
                "approval_receipt",
                "ttl",
            ],
        },
        "warehouse_adapter_design": {
            "resources": ["warehouse", "database", "schema", "table", "stage", "catalog", "integration"],
            "actions": [
                "sql.query",
                "warehouse.use",
                "warehouse.resize",
                "database.create",
                "schema.use",
                "table.select",
                "table.insert",
                "stage.read",
                "stage.write",
                "integration.use",
                "policy.admin",
                "audit.read",
            ],
        },
    }


def _to_jsonable_dict(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return {k: _to_jsonable_dict(v) for k, v in asdict(value).items()}
    if isinstance(value, tuple):
        return [_to_jsonable_dict(v) for v in value]
    if isinstance(value, list):
        return [_to_jsonable_dict(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_jsonable_dict(v) for k, v in value.items()}
    return value
