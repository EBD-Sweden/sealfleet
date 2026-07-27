from __future__ import annotations

import os
import re
import runpy
import stat
import subprocess
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
KEYGEN_SCRIPT = REPO_ROOT / "scripts" / "compose-keygen.py"
BROKEN_DATABASE_URL_SECRET = re.compile(r"postgres(?:ql)?://[^\s'\"`<>]*:\*\*\*@", re.IGNORECASE)


def _tracked_text_files() -> list[Path]:
    paths = subprocess.check_output(["git", "ls-files"], cwd=REPO_ROOT, text=True).splitlines()
    text_files: list[Path] = []
    for rel in paths:
        path = REPO_ROOT / rel
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        text_files.append(path)
    return text_files


def test_compose_database_url_uses_documented_dev_password_not_broken_mask():
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))

    assert compose["x-db-url"] == "postgresql://admin:admin@db:5432/mcpfinder"

    for service in ("registry", "deploy", "router", "portal"):
        assert compose["services"][service]["environment"]["DATABASE_URL"] == compose["x-db-url"]


def test_compose_keygen_is_repo_script_and_creates_readable_persistent_keys(tmp_path, monkeypatch):
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    assert compose["services"]["keygen"]["command"] == ["python", "/scripts/compose-keygen.py"]

    monkeypatch.setenv("MCPFINDER_KEY_DIR", str(tmp_path))
    runpy.run_path(str(KEYGEN_SCRIPT), run_name="__main__")

    expected_files = {
        "portal-rs256.pem",
        "router-rs256.pem",
        "nextauth-secret",
        "encryption-key",
        "runtime-api-key",
    }
    assert {path.name for path in tmp_path.iterdir()} == expected_files
    for path in tmp_path.iterdir():
        assert path.stat().st_size > 0
        assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_tracked_public_files_do_not_ship_broken_database_url_masks():
    offenders: list[str] = []
    for path in _tracked_text_files():
        source = path.read_text(encoding="utf-8")
        if BROKEN_DATABASE_URL_SECRET.search(source):
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert offenders == []
