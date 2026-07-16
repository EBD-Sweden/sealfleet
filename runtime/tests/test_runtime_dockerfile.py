from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_runtime_dockerfile_packages_runtime_policy_hook_module():
    dockerfile = REPO_ROOT / "runtime" / "Dockerfile"
    text = dockerfile.read_text(encoding="utf-8")

    assert "COPY policy_hooks.py" in text
