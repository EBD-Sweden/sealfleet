from datetime import datetime, timedelta, timezone

from mcpfinder_auth.enterprise import (
    AuditEventV1,
    AuthIntegration,
    AuthProviderKind,
    DeploymentMode,
    EnterpriseSubject,
    SealfleetResourceAdapter,
    MarketplaceIdentityHook,
    Organization,
    PolicyDecisionEnvelope,
    PrincipalType,
    SealedHandleDescriptor,
    ServiceIdentity,
    enterprise_contract_v1,
)


def test_contract_exposes_shared_identity_auth_policy_audit_secret_and_marketplace_sections():
    contract = enterprise_contract_v1()

    assert contract["version"] == "enterprise-auth-contract/v1"
    assert contract["deployment_modes"] == ["local_dev", "internal_pipeline", "public_platform"]
    for section in [
        "identity_core",
        "auth_integrations",
        "scim_lifecycle",
        "policy_primitives",
        "audit_event_schema",
        "sealed_secret_session_model",
        "marketplace_identity_hooks",
        "mcpfinder_adapter",
        "warehouse_adapter_design",
    ]:
        assert section in contract

    assert "mcp.tool.call" in contract["mcpfinder_adapter"]["actions"]
    assert "sql.query" in contract["warehouse_adapter_design"]["actions"]


def test_identity_and_integration_contract_keeps_org_membership_separate_from_product_access():
    org = Organization(
        organization_id="org_ebd",
        display_name="EBD Sweden",
        verified_domains=("example.com",),
        deployment_mode=DeploymentMode.INTERNAL_PIPELINE,
    )
    subject = EnterpriseSubject(
        subject_id="user_123",
        principal_type=PrincipalType.USER,
        organization_id=org.organization_id,
        email="user@example.com",
        lifecycle_status="active",
    )
    service = ServiceIdentity(
        service_identity_id="svc_runtime",
        organization_id=org.organization_id,
        display_name="Sealfleet Runtime",
        scopes=("mcp.tool.call", "credential.use"),
        token_ttl_seconds=900,
    )
    oidc = AuthIntegration(
        integration_id="idp_entra",
        organization_id=org.organization_id,
        provider_kind=AuthProviderKind.OIDC,
        issuer="https://login.microsoftonline.com/example/v2.0",
        audience="mcpfinder-runtime",
        domain_routing=("example.com",),
    )

    assert org.membership_model == "organization_membership_is_not_product_access"
    assert subject.organization_id == org.organization_id
    assert service.scopes == ("mcp.tool.call", "credential.use")
    assert oidc.requires_verified_domain_routing is True
    assert oidc.secret_material_model == "sealed_or_external_secret_ref_only"


def test_policy_audit_and_sealed_handle_shapes_redact_secrets_and_track_trace_ids():
    subject = EnterpriseSubject(
        subject_id="svc_agent",
        principal_type=PrincipalType.SERVICE,
        organization_id="org_ebd",
        lifecycle_status="active",
    )
    resource = SealfleetResourceAdapter.tool_call(
        organization_id="org_ebd",
        tenant_id="tenant_prod",
        server_id="weather-mcp",
        tool_name="get_weather",
        transport="http",
        input_classification="internal",
    )
    decision = PolicyDecisionEnvelope.allow(
        subject=subject,
        action="mcp.tool.call",
        resource=resource,
        trace_id="trace_123",
        policy_version="2026-05-28",
    )
    secret = SealedHandleDescriptor(
        handle_id="sealed_123",
        organization_id="org_ebd",
        tenant_id="tenant_prod",
        subject_id=subject.subject_id,
        purpose="credential.use",
        resource_scope=("mcp:weather-mcp",),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        single_use=True,
        resolver="local-dev",
    )
    event = AuditEventV1.from_policy_decision(
        event_id="evt_123",
        event_time=datetime(2026, 5, 28, tzinfo=timezone.utc),
        decision=decision,
        result="allowed",
        secret_handle_refs=(secret.handle_id,),
    )

    assert decision.decision == "allow"
    assert decision.trace_id == "trace_123"
    assert resource.resource_type == "mcp.tool"
    assert secret.redacted_dict()["value"] == "<redacted>"
    assert "plaintext" not in secret.redacted_dict()
    assert event.audit_schema_version == "audit-event/v1"
    assert event.trace_id == "trace_123"
    assert event.metadata_redacted is True
    assert event.secret_handle_refs == ("sealed_123",)


def test_marketplace_hook_is_authz_context_not_authz_replacement():
    hook = MarketplaceIdentityHook(
        organization_id="org_customer",
        marketplace="aws",
        buyer_account_id="123456789012",
        entitlement_ids=("ent_private_beta",),
        subscription_status="active",
    )

    assert hook.policy_context()["marketplace"] == "aws"
    assert hook.replaces_authorization is False
