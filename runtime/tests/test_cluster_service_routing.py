from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[2]

# Host-only URLs are still valid for host-run dependencies (local LLM/search proxy,
# browser-facing public callbacks, and local DB/docs). MCP-to-MCP traffic must not
# use these when a Kubernetes Service exists.
HOST_ONLY_ENV_NAMES = {
    "LLM_BASE_URL",
    "OPENAI_BASE_URL",
    "SEARCH_PROXY_BASE_URL",
    "NEXTAUTH_URL",
    "AUTH_URL",
}


def _yaml_documents(path: Path):
    return [doc for doc in yaml.safe_load_all(path.read_text()) if doc]


def _k8s_service_names() -> set[str]:
    names: set[str] = set()
    for path in (ROOT / "k8s").rglob("*.yaml"):
        for doc in _yaml_documents(path):
            if doc.get("kind") == "Service":
                names.add(doc["metadata"]["name"])
    return names


def _endpoint_from_doc(doc: dict) -> str | None:
    if "endpoint" in doc:
        return doc["endpoint"]
    server = doc.get("server")
    if isinstance(server, dict):
        return server.get("endpoint")
    return None


def test_runtime_manifest_endpoints_use_kubernetes_service_dns():
    services = _k8s_service_names()
    offenders: list[str] = []

    for path in (ROOT / "runtime" / "manifests").glob("*.yaml"):
        doc = yaml.safe_load(path.read_text())
        endpoint = _endpoint_from_doc(doc)
        if not endpoint:
            continue
        parsed = urlparse(endpoint)
        host = parsed.hostname or ""
        name = doc.get("name") or doc.get("id") or path.stem
        if host in {"localhost", "127.0.0.1", "host.k3d.internal"}:
            offenders.append(f"{path.relative_to(ROOT)}: {name} -> {endpoint}")
        elif name in services and host != name and not host.startswith(f"{name}."):
            offenders.append(f"{path.relative_to(ROOT)}: {name} should route to http://{name}:..., got {endpoint}")

    assert offenders == []


def test_catalog_mcp_endpoint_yaml_uses_cluster_dns_not_localhost():
    offenders: list[str] = []
    paths = list((ROOT / "config").rglob("*.yaml")) + list((ROOT / "mcps").rglob("mcp.yaml"))
    for path in paths:
        doc = yaml.safe_load(path.read_text())
        endpoint = _endpoint_from_doc(doc)
        if not endpoint:
            continue
        host = urlparse(endpoint).hostname or ""
        if host in {"localhost", "127.0.0.1", "host.k3d.internal"}:
            offenders.append(f"{path.relative_to(ROOT)} -> {endpoint}")

    assert offenders == []


def test_k8s_mcp_service_urls_do_not_route_via_host_alias():
    services = _k8s_service_names()
    offenders: list[str] = []

    for path in (ROOT / "k8s").rglob("*.yaml"):
        for doc in _yaml_documents(path):
            spec = (((doc.get("spec") or {}).get("template") or {}).get("spec") or {})
            for container in spec.get("containers") or []:
                for env in container.get("env") or []:
                    name = env.get("name", "")
                    value = env.get("value")
                    if not isinstance(value, str) or not value.startswith(("http://", "https://")):
                        continue
                    host = urlparse(value).hostname or ""
                    if name in HOST_ONLY_ENV_NAMES:
                        continue
                    if host in {"localhost", "127.0.0.1", "host.k3d.internal"}:
                        offenders.append(f"{path.relative_to(ROOT)} {name}={value}")
                    elif host.endswith("-mcp") and host not in services:
                        offenders.append(f"{path.relative_to(ROOT)} {name}={value} has no matching Service")

    assert offenders == []


def test_scale_to_zero_map_covers_all_runtime_manifest_mcp_services():
    import router

    services = _k8s_service_names()
    missing: list[str] = []
    for path in (ROOT / "runtime" / "manifests").glob("*.yaml"):
        doc = yaml.safe_load(path.read_text())
        name = doc.get("name") or doc.get("id")
        endpoint = _endpoint_from_doc(doc) or ""
        host = urlparse(endpoint).hostname or ""
        if name in services and host in services and name not in router.MCP_DEPLOYMENT_MAP:
            missing.append(name)

    assert missing == []
