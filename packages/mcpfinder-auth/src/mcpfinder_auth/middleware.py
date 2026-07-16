"""
FastAPI/Starlette middleware for Sealfleet JWT auth.

Usage:
    from mcpfinder_auth import JWTAuthMiddleware

    app.add_middleware(
        JWTAuthMiddleware,
        jwks_url="http://router:8000/.well-known/jwks.json",
        audience="https://my-mcp-server.example.com",  # optional
    )

After the middleware runs, on valid auth:
    request.state.user_id    = str
    request.state.tenant_id  = str
    request.state.email      = str
    request.state.is_admin   = bool
    request.state.sub        = str
    request.state.jwt_payload = dict  (full payload)
"""
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from .jwks import verify_token

logger = logging.getLogger(__name__)

_SKIP_PATHS = {"/health", "/tools", "/.well-known/oauth-protected-resource"}

class JWTAuthMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        jwks_url: str,
        audience: str | None = None,
        issuer: str | None = None,
        skip_paths: set[str] | None = None,
        require_auth: bool = True,
    ):
        super().__init__(app)
        self.jwks_url = jwks_url
        self.audience = audience
        self.issuer = issuer
        self.skip_paths = skip_paths or _SKIP_PATHS
        self.require_auth = require_auth

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path in self.skip_paths:
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            if self.require_auth:
                return JSONResponse({"error": "missing_token"}, status_code=401)
            return await call_next(request)

        token = auth_header[7:]
        try:
            payload = await verify_token(
                token,
                self.jwks_url,
                audience=self.audience,
                issuer=self.issuer,
            )
        except Exception as e:
            logger.warning(f"JWT validation failed: {e}")
            if self.require_auth:
                return JSONResponse({"error": "invalid_token", "detail": str(e)}, status_code=401)
            return await call_next(request)

        # Attach claims to request state
        request.state.jwt_payload = payload
        request.state.sub         = payload.get("sub", "")
        request.state.user_id     = payload.get("user_id") or payload.get("sub", "")
        request.state.tenant_id   = payload.get("tenant_id", "")
        request.state.email       = payload.get("email", "")
        request.state.is_admin    = payload.get("is_admin", False)

        return await call_next(request)
