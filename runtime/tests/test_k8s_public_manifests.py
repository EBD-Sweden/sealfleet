from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_MANIFESTS = sorted(
    path
    for path in REPO_ROOT.joinpath("k8s").rglob("*.yaml")
    if "dev-local" not in path.parts
)
SECRET_LIKE_ENV_NAMES = {
    "DATABASE" + "_URL",
    "DB" + "_URL",
    "INVESTDB" + "_URL",
    "NEXTAUTH" + "_SECRET",
    "LLM" + "_API" + "_KEY",
    "FMP" + "_API" + "_KEY",
    "OPENAI" + "_API" + "_KEY",
    "ANTHROPIC" + "_API" + "_KEY",
    "TELEGRAM" + "_BOT" + "_TOKEN",
    "OPENCLAW" + "_HOOKS" + "_TOKEN",
    "ENCRYPTION" + "_KEY",
}
FORBIDDEN_TEXT = (
    "postgres" + "ql://",
    "postgres" + "://",
    "mcpfinder" + "-secret-change-in-prod",
    "not" + "-needed",
)


def _documents(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return [doc for doc in yaml.safe_load_all(handle) if doc]


def _pod_specs(doc: dict):
    spec = doc.get("spec", {})
    direct_pod_spec = spec.get("template", {}).get("spec")
    if direct_pod_spec:
        yield direct_pod_spec

    cronjob_pod_spec = (
        spec.get("jobTemplate", {})
        .get("spec", {})
        .get("template", {})
        .get("spec")
    )
    if cronjob_pod_spec:
        yield cronjob_pod_spec


POD_CONTAINER_FIELDS = ("containers", "initContainers", "ephemeralContainers")


def _containers(doc: dict):
    return [
        container
        for pod_spec in _pod_specs(doc)
        for field in POD_CONTAINER_FIELDS
        for container in pod_spec.get(field, [])
    ]


def _volumes(doc: dict):
    return [
        volume
        for pod_spec in _pod_specs(doc)
        for volume in pod_spec.get("volumes", [])
    ]


def test_manifest_helpers_inspect_cronjob_plaintext_secret_env_values():
    cronjob = {
        "kind": "CronJob",
        "spec": {
            "jobTemplate": {
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "bad-cron",
                                    "env": [
                                        {
                                            "name": "DATABASE_URL",
                                            "value": "postgres://example",
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                }
            }
        },
    }

    env_names = [
        env.get("name")
        for container in _containers(cronjob)
        for env in container.get("env", [])
        if "value" in env
    ]

    assert "DATABASE_URL" in env_names


def test_manifest_helpers_inspect_cronjob_docker_socket_mounts():
    cronjob = {
        "kind": "CronJob",
        "spec": {
            "jobTemplate": {
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [{"name": "bad-cron"}],
                            "volumes": [
                                {
                                    "name": "docker-sock",
                                    "hostPath": {"path": "/var/run/docker.sock"},
                                }
                            ],
                        }
                    }
                }
            }
        },
    }

    docker_host_paths = [
        volume.get("hostPath", {}).get("path")
        for volume in _volumes(cronjob)
        if volume.get("hostPath")
    ]

    assert "/var/run/docker.sock" in docker_host_paths


def test_manifest_helpers_inspect_init_container_plaintext_secret_env_values():
    deployment = {
        "kind": "Deployment",
        "spec": {
            "template": {
                "spec": {
                    "containers": [{"name": "app"}],
                    "initContainers": [
                        {
                            "name": "bad-init",
                            "env": [
                                {"name": "DATABASE_URL", "value": "postgres://example"}
                            ],
                        }
                    ],
                }
            }
        },
    }

    env_names = [
        env.get("name")
        for container in _containers(deployment)
        for env in container.get("env", [])
        if "value" in env
    ]

    assert "DATABASE_URL" in env_names


def test_manifest_helpers_inspect_ephemeral_container_plaintext_secret_env_values():
    job = {
        "kind": "Job",
        "spec": {
            "template": {
                "spec": {
                    "containers": [{"name": "app"}],
                    "ephemeralContainers": [
                        {
                            "name": "bad-debug",
                            "env": [
                                {"name": "OPENAI_API_KEY", "value": "sk-example"}
                            ],
                        }
                    ],
                }
            }
        },
    }

    env_names = [
        env.get("name")
        for container in _containers(job)
        for env in container.get("env", [])
        if "value" in env
    ]

    assert "OPENAI_API_KEY" in env_names


def test_public_k8s_manifests_do_not_embed_plaintext_secret_values():
    assert PUBLIC_MANIFESTS, "expected top-level k8s public-test manifests"

    findings = []
    for path in PUBLIC_MANIFESTS:
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_TEXT:
            if forbidden in text:
                findings.append(f"{path.relative_to(REPO_ROOT)} contains {forbidden!r}")

        for doc in _documents(path):
            for container in _containers(doc):
                for env in container.get("env", []):
                    name = env.get("name")
                    if name in SECRET_LIKE_ENV_NAMES and "value" in env:
                        findings.append(
                            f"{path.relative_to(REPO_ROOT)} sets {name} with plaintext value"
                        )

    assert findings == []


def test_public_k8s_manifests_do_not_commit_secret_payloads():
    findings = []
    for path in PUBLIC_MANIFESTS:
        for doc in _documents(path):
            if doc.get("kind") != "Secret":
                continue
            if doc.get("data") or doc.get("stringData"):
                findings.append(
                    f"{path.relative_to(REPO_ROOT)} commits Secret data/stringData payloads"
                )

    assert findings == []


def test_public_k8s_manifests_do_not_mount_docker_socket():
    findings = []
    for path in PUBLIC_MANIFESTS:
        for doc in _documents(path):
            for volume in _volumes(doc):
                host_path = volume.get("hostPath", {}).get("path")
                if host_path == "/var/run/docker.sock":
                    findings.append(
                        f"{path.relative_to(REPO_ROOT)} mounts /var/run/docker.sock"
                    )

    assert findings == []


def _service(path: Path, name: str):
    services = [
        doc
        for doc in _documents(path)
        if doc.get("kind") == "Service" and doc.get("metadata", {}).get("name") == name
    ]
    assert len(services) == 1
    return services[0]


def test_public_test_externally_smoked_services_expose_documented_nodeports():
    expected = {
        "mcp-registry": (REPO_ROOT / "k8s" / "mcp-registry.yaml", 8010, 30010),
        "mcp-deploy": (REPO_ROOT / "k8s" / "mcp-deploy.yaml", 8030, 30030),
        "mcp-router": (REPO_ROOT / "k8s" / "mcp-router.yaml", 8040, 30040),
        "mcpfinder-portal": (REPO_ROOT / "k8s" / "mcpfinder-portal.yaml", 3004, 30004),
    }

    findings = []
    for service_name, (path, port, node_port) in expected.items():
        service = _service(path, service_name)
        spec = service.get("spec", {})
        ports = spec.get("ports", [])
        if spec.get("type") != "NodePort":
            findings.append(f"{service_name} must be NodePort for local public-test smoke")
            continue
        if not any(item.get("port") == port and item.get("nodePort") == node_port for item in ports):
            findings.append(f"{service_name} must expose {port}:{node_port}")

    assert findings == []


def test_public_test_scale_from_zero_docs_require_each_schedulable_k3d_node_cache():
    quickstart = (REPO_ROOT / "docs" / "EXTERNAL_DEMO_QUICKSTART.md").read_text(encoding="utf-8")

    assert "each schedulable k3d node" in quickstart
    assert "scripts/k3d-cache-image.sh" in quickstart
    assert "ctr -n k8s.io images import" in quickstart
    assert "crictl images" in quickstart


def test_public_test_scale_from_zero_cache_script_imports_exact_image_to_schedulable_k3d_nodes():
    script = (REPO_ROOT / "scripts" / "k3d-cache-image.sh").read_text(encoding="utf-8")

    assert "set -euo pipefail" in script
    assert "docker image save" in script
    assert "kubectl get nodes" in script
    assert "docker exec \"${node_container}\" ctr -n k8s.io images import" in script
    assert "docker exec \"${node_container}\" crictl images" in script
    assert "ImagePullPolicy" not in script


def test_public_test_cache_script_guards_root_disk_headroom_before_importing_images():
    script = (REPO_ROOT / "scripts" / "k3d-cache-image.sh").read_text(encoding="utf-8")

    assert "K3D_CACHE_MIN_ROOT_FREE_GB" in script
    assert "df -Pk /" in script
    assert "root disk free" in script
    assert "DiskPressure" in script


def test_observability_docs_include_self_hosted_runtime_api_key_lifecycle():
    docs = (REPO_ROOT / "docs" / "OBSERVABILITY.md").read_text(encoding="utf-8")

    assert "Self-hosted temporary audit key lifecycle" in docs
    assert "INSERT INTO api_keys" in docs
    assert "UPDATE api_keys SET is_active = false" in docs
    assert "audit.read" in docs
    assert "RUNTIME_URL=http://localhost:8040" in docs


def test_public_router_disables_dev_only_ephemeral_keys_and_docker_stdio():
    router_path = REPO_ROOT / "k8s" / "mcp-router.yaml"
    router_docs = _documents(router_path)
    deployments = [doc for doc in router_docs if doc.get("kind") == "Deployment"]
    assert len(deployments) == 1
    deployment = deployments[0]

    containers = _containers(deployment)
    assert len(containers) == 1
    assert containers[0].get("imagePullPolicy") == "IfNotPresent"

    env = {}
    for container in containers:
        for item in container.get("env", []):
            if "value" in item:
                env[item["name"]] = item["value"]

    assert env.get("AUTH_ALLOW_EPHEMERAL_KEYS") == "false"
    assert env.get("DOCKER_STDIO_ENABLED") == "false"

    docker_host_paths = [
        volume.get("hostPath", {}).get("path")
        for volume in _volumes(deployment)
        if volume.get("hostPath")
    ]
    assert "/var/run/docker.sock" not in docker_host_paths
