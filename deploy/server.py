"""
Sealfleet Deploy Service — Git-to-K8s deploy pipeline with SSE streaming.
FastAPI service on port 8030.
"""

import json
import logging
import os
import tempfile
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psycopg2
import psycopg2.extras
import yaml
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

app = FastAPI(title="Sealfleet Deploy Service")
logger = logging.getLogger(__name__)

# CORS: default-deny cross-origin. Set MCPFINDER_CORS_ALLOW_ORIGINS to a
# comma-separated allowlist (e.g. the portal origin) to permit specific sites;
# unset means no cross-origin access (safe default, replaces allow_origins=*).
_CORS_ORIGINS = [o.strip() for o in os.getenv("MCPFINDER_CORS_ALLOW_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=bool(_CORS_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)


def _deploy_auth_required() -> bool:
    return os.getenv("REQUIRE_AUTH", "true").lower() in ("1", "true", "yes")


def require_deploy_auth(x_api_key: str | None = Header(default=None)) -> str:
    """Authenticate deploy calls against an active api_keys row (same DB as the
    router). Fails closed unless REQUIRE_AUTH is explicitly disabled (dev)."""
    if not _deploy_auth_required():
        return "system"
    if not x_api_key:
        raise HTTPException(401, "X-API-Key required")
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tenant_id FROM api_keys WHERE api_key = %s AND is_active = TRUE LIMIT 1",
                (x_api_key,),
            )
            row = cur.fetchone()
        conn.close()
    except Exception as e:
        logger.error("deploy auth DB error: %s", e)
        raise HTTPException(503, "auth backend unavailable")
    if not row:
        raise HTTPException(401, "invalid API key")
    return str(row[0])


# Git URL hardening: only https(s) to allowlisted hosts (default: public forges).
# Blocks git transport-helper RCE (ext::, file:, scp-like host:path, -flags).
_ALLOWED_GIT_HOSTS = [
    h.strip().lower() for h in os.getenv(
        "MCPFINDER_GIT_HOST_ALLOWLIST",
        "github.com,gitlab.com,bitbucket.org",
    ).split(",") if h.strip()
]


def _validate_repo_url(repo_url: str) -> None:
    from urllib.parse import urlparse
    if not isinstance(repo_url, str) or repo_url != repo_url.strip() or repo_url.startswith("-"):
        raise HTTPException(400, "invalid repo_url")
    parsed = urlparse(repo_url)
    if parsed.scheme not in ("https", "http"):
        raise HTTPException(400, "repo_url must be an http(s) URL")
    host = (parsed.hostname or "").lower()
    if host not in _ALLOWED_GIT_HOSTS:
        raise HTTPException(400, f"repo_url host '{host}' is not allowlisted (MCPFINDER_GIT_HOST_ALLOWLIST)")


def _validate_git_ref(branch: str) -> None:
    if not re.match(r"^[A-Za-z0-9._/-]{1,200}$", branch or "") or branch.startswith("-"):
        raise HTTPException(400, "invalid branch/ref")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://admin:admin@localhost:54323/mcpfinder")
RUNTIME_URL = os.getenv("RUNTIME_URL", "http://localhost:8040")
BUILDS_DIR = Path(
    os.getenv("MCPFINDER_BUILDS_DIR")
    or os.path.join(tempfile.gettempdir(), "mcpfinder-builds")
)
REGISTRY_PREFIX = os.getenv("REGISTRY_PREFIX", "localhost:5050")
K8S_REGISTRY_PREFIX = os.getenv("K8S_REGISTRY_PREFIX", "mcpfinder-registry:5050")
HOST_DATABASE_URL = os.getenv(
    "HOST_DATABASE_URL",
    "postgresql://admin:admin@host.k3d.internal:54323/mcpfinder",
)


# --- Models ---

class DeployRequest(BaseModel):
    repo_url: str
    branch: str = "main"
    name: str
    description: str = ""
    tags: list[str] = []
    port: int = 8000
    is_public: bool = True
    env_vars: dict = {}


class DeploymentInfo(BaseModel):
    id: str
    name: str
    repo_url: str | None
    branch: str | None
    image: str | None
    endpoint: str | None
    node_port: int | None
    status: str
    server_id: str | None
    created_at: str | None
    updated_at: str | None


# --- DB helpers ---

def get_db():
    return psycopg2.connect(DATABASE_URL)


def ensure_tables():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS deployments (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    name TEXT UNIQUE NOT NULL,
                    repo_url TEXT,
                    branch TEXT DEFAULT 'main',
                    image TEXT,
                    endpoint TEXT,
                    node_port INT,
                    status TEXT DEFAULT 'deploying',
                    server_id TEXT,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    updated_at TIMESTAMPTZ DEFAULT now()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS audit_events (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    tenant_id TEXT NOT NULL DEFAULT 'system',
                    user_id TEXT,
                    action TEXT NOT NULL,
                    resource TEXT,
                    server_name TEXT,
                    result TEXT,
                    trace_id TEXT,
                    duration_ms INT DEFAULT 0,
                    payload JSONB,
                    created_at TIMESTAMPTZ DEFAULT now()
                );
            """)
            conn.commit()
    finally:
        conn.close()


K8S_NAMESPACE = os.getenv("K8S_NAMESPACE", "default")


def internal_service_url(name: str, port: int, namespace: str = K8S_NAMESPACE) -> str:
    return f"http://{name}.{namespace}.svc.cluster.local:{port}"




_SECRET_KEY_MARKERS = (
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "password",
    "credential",
    "private_key",
    "access_key",
)


def _normalized_audit_key_text(key: object) -> str:
    """Normalize audit payload keys so separator variants match secret markers."""
    return re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")


def redact_audit_payload(value):
    if isinstance(value, dict):
        redacted = {}
        for key, nested in value.items():
            key_text = _normalized_audit_key_text(key)
            if any(marker in key_text for marker in _SECRET_KEY_MARKERS):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_audit_payload(nested)
        return redacted
    if isinstance(value, list):
        return [redact_audit_payload(item) for item in value]
    return value


_SECRET_VALUE_PATTERNS = (
    (re.compile(r"(?i)(sk-[^\s'\"`,;)]{6,})"), "[REDACTED]"),
    (re.compile(r"(?i)(xox[baprs]-[^\s'\"`,;)]{6,})"), "[REDACTED]"),
    (re.compile(r"(?i)(gh[pousr]_[^\s'\"`,;)]{6,})"), "[REDACTED]"),
    (
        re.compile(
            r"(?i)((?:authorization|credential)\s*[=:]\s*(?:bearer|basic|token)\s+)"
            r"[^\s'\"`,;)]{3,}"
        ),
        r"\1[REDACTED]",
    ),
    (
        re.compile(r"(?i)((?:api[_-]?key|token|secret|password|passwd)\s*[=:]\s*)[^\s'\"`,;)]{3,}"),
        r"\1[REDACTED]",
    ),
    (
        re.compile(r"(?i)((?:authorization|credential)\s*[=:]\s*)(?!(?:bearer|basic|token)\s+)[^\s'\"`,;)]{3,}"),
        r"\1[REDACTED]",
    ),
)


def redact_sse_text(text: object) -> str:
    """Best-effort redaction for text that will be streamed to SSE clients."""
    value = str(text)
    for pattern, replacement in _SECRET_VALUE_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def new_trace_id() -> str:
    return uuid.uuid4().hex[:16]


def write_deploy_audit_event(*, action: str, resource: str, result: str, trace_id: str, payload: dict | None = None, user_id: str = "operator", tenant_id: str = "system", duration_ms: int = 0):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO audit_events
                   (tenant_id, user_id, action, resource, server_name, result, trace_id, duration_ms, payload)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    tenant_id,
                    user_id,
                    action,
                    resource,
                    "mcpfinder-deploy",
                    result,
                    trace_id,
                    duration_ms,
                    json.dumps(redact_audit_payload(payload)) if payload else None,
                ),
            )
            conn.commit()
    finally:
        conn.close()


# --- Deploy pipeline ---

def run_cmd(cmd: list[str], cwd: str | None = None) -> tuple[int, str]:
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=300
        )
        output = result.stdout + result.stderr
        return result.returncode, output.strip()
    except subprocess.TimeoutExpired:
        return 1, "Command timed out after 300s"
    except Exception as e:
        return 1, str(e)


def sse_event(step: str, status: str, msg: str, **extra) -> dict:
    data = {"step": step, "status": status, "msg": redact_sse_text(msg), "ts": datetime.now(timezone.utc).isoformat(), **extra}
    return {"event": "log", "data": json.dumps(data)}


async def deploy_pipeline(req: DeployRequest):
    trace_id = new_trace_id()
    build_id = str(uuid.uuid4())[:8]
    build_dir = str(BUILDS_DIR / f"{req.name}-{build_id}")
    detected_port = req.port
    tools_list = []
    server_id = None

    # Create deployment record
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO deployments (name, repo_url, branch, status, node_port)
                   VALUES (%s, %s, %s, 'deploying', NULL)
                   ON CONFLICT (name) DO UPDATE SET
                     repo_url = EXCLUDED.repo_url,
                     branch = EXCLUDED.branch,
                     status = 'deploying',
                     node_port = NULL,
                     updated_at = now()""",
                (req.name, req.repo_url, req.branch),
            )
            conn.commit()
    finally:
        conn.close()

    try:
        # --- Step 1: Clone ---
        yield sse_event("clone", "running", f"Cloning {req.repo_url} (branch: {req.branch})...")
        BUILDS_DIR.mkdir(parents=True, exist_ok=True)
        rc, out = run_cmd(["git", "clone", "--depth", "1", "--branch", req.branch, req.repo_url, build_dir])
        if rc != 0:
            logger.warning("Git clone failed for %s: %s", req.name, out)
            yield sse_event("clone", "error", f"Clone failed: {out}")
            return
        yield sse_event("clone", "done", "Repository cloned successfully")

        # --- Step 2: Detect config ---
        yield sse_event("detect", "running", "Detecting mcp.yaml and Dockerfile...")
        mcp_config = None
        for cfg_path in ["mcp.yaml", "config/mcp.yaml", "config/mcp-server.yaml"]:
            full = os.path.join(build_dir, cfg_path)
            if os.path.exists(full):
                try:
                    with open(full) as f:
                        mcp_config = yaml.safe_load(f)
                    yield sse_event("detect", "info", f"Found config at {cfg_path}")
                except Exception:
                    logger.exception("Failed to parse deploy config %s", cfg_path)
                    yield sse_event("detect", "info", f"Failed to parse {cfg_path}; see server logs for details")
                break

        if mcp_config and "tools" in mcp_config:
            tools_list = mcp_config["tools"]
            yield sse_event("detect", "info", f"Found {len(tools_list)} tools in config")
        else:
            yield sse_event("detect", "info", "No tools config found, proceeding without tools")

        # Detect port from Dockerfile
        dockerfile = os.path.join(build_dir, "Dockerfile")
        if os.path.exists(dockerfile):
            with open(dockerfile) as f:
                for line in f:
                    m = re.match(r"^\s*EXPOSE\s+(\d+)", line)
                    if m:
                        detected_port = int(m.group(1))
                        yield sse_event("detect", "info", f"Detected EXPOSE {detected_port} in Dockerfile")
                        break
        yield sse_event("detect", "done", "Config detection complete")

        # --- Step 3: Docker build + push ---
        image_local = f"{REGISTRY_PREFIX}/{req.name}:latest"
        image_k8s = f"{K8S_REGISTRY_PREFIX}/{req.name}:latest"

        yield sse_event("build", "running", f"Building Docker image {image_local}...")

        # Stream docker build output
        try:
            proc = subprocess.Popen(
                ["docker", "build", "-t", image_local, build_dir],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            if proc.stdout is not None:
                for line in proc.stdout:
                    line = line.rstrip()
                    if line:
                        logger.info("Docker build output for %s: %s", req.name, line)
                        yield sse_event("build", "running", line)
            proc.wait()
            if proc.returncode != 0:
                logger.warning("Docker build failed for %s", req.name)
                yield sse_event("build", "error", "Docker build failed")
                return
        except Exception:
            logger.exception("Docker build could not be started for %s", req.name)
            yield sse_event("build", "error", "Docker build could not be started; see server logs for details")
            return

        yield sse_event("build", "running", "Pushing image to local registry...")
        rc, out = run_cmd(["docker", "push", image_local])
        if rc != 0:
            logger.warning("Docker push failed for %s: %s", req.name, out)
            yield sse_event("build", "error", f"Docker push failed: {out}")
            return
        yield sse_event("build", "done", "Image built and pushed successfully")

        # --- Step 4: Generate k8s manifests ---
        yield sse_event("deploy", "running", "Generating Kubernetes manifests...")

        env_list = [{"name": k, "value": str(v)} for k, v in req.env_vars.items()]
        env_list.append({"name": "DATABASE_URL", "value": HOST_DATABASE_URL})

        service_endpoint = internal_service_url(req.name, detected_port)

        k8s_manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": req.name, "labels": {"app": req.name}},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": req.name}},
                "template": {
                    "metadata": {"labels": {"app": req.name}},
                    "spec": {
                        "containers": [{
                            "name": req.name,
                            "image": image_k8s,
                            "ports": [{"name": "http", "containerPort": detected_port}],
                            "env": env_list,
                        }]
                    },
                },
            },
        }

        svc_manifest = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": req.name, "labels": {"app": req.name}},
            "spec": {
                "type": "ClusterIP",
                "selector": {"app": req.name},
                "ports": [{
                    "name": "http",
                    "port": detected_port,
                    "targetPort": "http",
                }],
            },
        }

        manifest_dir = BUILDS_DIR / req.name
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / "k8s.yaml"
        with open(manifest_path, "w") as f:
            yaml.dump_all([k8s_manifest, svc_manifest], f, default_flow_style=False)

        yield sse_event("deploy", "info", f"Manifests written (ClusterIP service: {service_endpoint})")

        # --- Step 5: kubectl apply ---
        yield sse_event("deploy", "running", "Applying to Kubernetes cluster...")
        rc, out = run_cmd(["kubectl", "apply", "-f", str(manifest_path)])
        if rc != 0:
            logger.warning("kubectl apply failed for %s: %s", req.name, out)
            yield sse_event("deploy", "error", f"kubectl apply failed: {out}")
            return
        for line in out.split("\n"):
            if line.strip():
                yield sse_event("deploy", "info", line.strip())

        yield sse_event("deploy", "running", "Waiting for rollout...")
        rc, out = run_cmd(["kubectl", "rollout", "status", f"deployment/{req.name}", "--timeout=120s"])
        if rc != 0:
            logger.warning("kubectl rollout failed for %s: %s", req.name, out)
            yield sse_event("deploy", "error", f"Rollout failed: {out}")
            return
        yield sse_event("deploy", "done", "Deployment rolled out successfully")

        # --- Step 5.5: Auto-register typed manifest if present ---
        manifest_paths = [
            os.path.join(build_dir, "mcp.yaml"),
            os.path.join(build_dir, "runtime", "manifest.yaml"),
            os.path.join(build_dir, "config", "mcp-manifest.yaml"),
        ]
        for mp in manifest_paths:
            if os.path.exists(mp):
                with open(mp) as f:
                    manifest_data = yaml.safe_load(f)
                # Update endpoint to deployed k8s endpoint
                manifest_data["endpoint"] = service_endpoint
                manifest_data["name"] = req.name
                # POST to runtime router
                async with httpx.AsyncClient() as hclient:
                    resp = await hclient.post(
                        f"{RUNTIME_URL}/manifests/typed",
                        json=manifest_data,
                        timeout=10,
                    )
                yield sse_event("register_runtime", "done",
                                f"Typed manifest registered in runtime router: {resp.status_code}")
                break
        else:
            yield sse_event("register_runtime", "skip",
                            "No typed manifest found in repo (mcp.yaml) — skipping runtime registration")

        # --- Step 6: Register in mcpfinder DB ---
        yield sse_event("register", "running", "Registering in Sealfleet catalog...")
        endpoint = service_endpoint

        conn = get_db()
        try:
            with conn.cursor() as cur:
                # Upsert server
                cur.execute(
                    """INSERT INTO servers (name, endpoint, description, status, metadata, registered_at, updated_at)
                       VALUES (%s, %s, %s, 'active', %s, now(), now())
                       ON CONFLICT (name) DO UPDATE SET
                         endpoint = EXCLUDED.endpoint,
                         description = EXCLUDED.description,
                         status = 'active',
                         metadata = EXCLUDED.metadata,
                         updated_at = now()
                       RETURNING server_id""",
                    (req.name, endpoint, req.description,
                     json.dumps({"tags": req.tags, "is_public": req.is_public, "repo_url": req.repo_url})),
                )
                row = cur.fetchone()
                server_id = row[0] if row else None

                # Upsert tools from mcp.yaml
                if tools_list and server_id:
                    for tool in tools_list:
                        tool_name = tool.get("name", "")
                        tool_desc = tool.get("description", "")
                        tool_schema = json.dumps(tool.get("inputSchema", tool.get("input_schema", {})))
                        tool_tags = json.dumps(tool.get("tags", []))
                        cur.execute(
                            """INSERT INTO tools (server_id, name, description, input_schema, tags, registered_at, updated_at)
                               VALUES (%s, %s, %s, %s, %s, now(), now())
                               ON CONFLICT (server_id, name) DO UPDATE SET
                                 description = EXCLUDED.description,
                                 input_schema = EXCLUDED.input_schema,
                                 tags = EXCLUDED.tags,
                                 updated_at = now()""",
                            (server_id, tool_name, tool_desc, tool_schema, tool_tags),
                        )
                    yield sse_event("register", "info", f"Registered {len(tools_list)} tools")

                write_deploy_audit_event(
                    action="deploy.register",
                    resource=req.name,
                    result="ok",
                    trace_id=trace_id,
                    payload={
                        "repo_url": req.repo_url,
                        "tags": req.tags,
                        "is_public": req.is_public,
                        "tools_registered": len(tools_list),
                        "env_vars": req.env_vars,
                    },
                )

                # Update deployment record
                cur.execute(
                    """UPDATE deployments SET
                         image = %s, endpoint = %s, status = 'running',
                         server_id = %s, updated_at = now()
                       WHERE name = %s""",
                    (image_k8s, endpoint, str(server_id) if server_id else None, req.name),
                )
                conn.commit()
        except Exception:
            yield sse_event("register", "error", "DB registration failed; see deploy audit receipt for redacted details")
            failure_payload = redact_audit_payload({
                "repo_url": req.repo_url,
                "tags": req.tags,
                "is_public": req.is_public,
                "tools_registered": len(tools_list),
                "env_vars": req.env_vars,
                "error": "catalog_registration_failed",
                "error_detail": "redacted",
            })
            try:
                write_deploy_audit_event(
                    action="deploy.register",
                    resource=req.name,
                    result="error",
                    trace_id=trace_id,
                    payload=failure_payload if isinstance(failure_payload, dict) else None,
                )
            except Exception:
                pass
            try:
                conn.rollback()
            except Exception:
                pass
            return
        finally:
            conn.close()

        yield sse_event("register", "done", "Registered in Sealfleet catalog")

        # --- Step 7: Done ---
        yield sse_event("done", "success", f"Deployment complete! Endpoint: {endpoint}",
                        endpoint=endpoint, server_id=str(server_id) if server_id else None,
                        service_dns=endpoint, node_port=None)

    except Exception:
        yield sse_event("error", "error", "Unexpected deployment error; see server logs for details")
        # Update deployment status
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute("UPDATE deployments SET status = 'failed', updated_at = now() WHERE name = %s", (req.name,))
                conn.commit()
            conn.close()
        except Exception:
            pass
    finally:
        # Cleanup build dir
        if os.path.exists(build_dir):
            shutil.rmtree(build_dir, ignore_errors=True)


# --- Endpoints ---

@app.on_event("startup")
def startup():
    ensure_tables()


@app.get("/health")
def health():
    return {"status": "ok", "service": "mcpfinder-deploy"}


@app.get("/ready")
def ready():
    try:
        conn = get_db()
        conn.close()
        db_status = "ok"
    except Exception:
        db_status = "unavailable"
    return {
        "status": "ready" if db_status == "ok" else "degraded",
        "service": "mcpfinder-deploy",
        "checks": {"database": db_status, "builds_dir": str(BUILDS_DIR)},
    }


@app.post("/deploy")
async def deploy(req: DeployRequest, _tenant: str = Depends(require_deploy_auth)):
    _validate_repo_url(req.repo_url)
    _validate_git_ref(req.branch)
    return EventSourceResponse(deploy_pipeline(req))


@app.get("/deployments")
def list_deployments():
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM deployments ORDER BY created_at DESC")
            rows = cur.fetchall()
            return [
                {**row, "id": str(row["id"]),
                 "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                 "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None}
                for row in rows
            ]
    finally:
        conn.close()


@app.get("/deployments/{name}")
def get_deployment(name: str):
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM deployments WHERE name = %s", (name,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Deployment not found")
            return {**row, "id": str(row["id"]),
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None}
    finally:
        conn.close()
