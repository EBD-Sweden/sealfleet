"""
RFC 9728 Protected Resource Metadata helper.

Registers /.well-known/oauth-protected-resource on a FastAPI/Starlette app.

Usage:
    from mcpfinder_auth import register_resource_metadata

    register_resource_metadata(
        app,
        resource_url=os.getenv("MCP_SERVER_URL", "https://my-mcp-server.example.com"),
        authorization_servers=[os.getenv("ROUTER_ISSUER", "https://sealfleet.io/router")],
        scopes_supported=["mcp:call"],
    )
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.applications import Starlette

def register_resource_metadata(
    app,
    *,
    resource_url: str,
    authorization_servers: list[str],
    scopes_supported: list[str] | None = None,
    bearer_methods_supported: list[str] | None = None,
    resource_documentation: str | None = None,
) -> None:
    """Register the RFC 9728 well-known endpoint on the given ASGI app."""
    import json
    from starlette.requests import Request
    from starlette.responses import Response

    metadata = {
        "resource": resource_url,
        "authorization_servers": authorization_servers,
        "bearer_methods_supported": bearer_methods_supported or ["header"],
        "scopes_supported": scopes_supported or ["mcp:call"],
    }
    if resource_documentation:
        metadata["resource_documentation"] = resource_documentation

    metadata_json = json.dumps(metadata, indent=2)

    async def _well_known(request: Request) -> Response:
        return Response(
            metadata_json,
            media_type="application/json",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    # Works with FastAPI and Starlette
    app.add_route("/.well-known/oauth-protected-resource", _well_known, methods=["GET"], include_in_schema=False)
