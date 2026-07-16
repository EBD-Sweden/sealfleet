"""Tests for run_docker_stdio() with mocked subprocess (no real Docker needed)."""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from router import run_docker_stdio


class FakeProcess:
    """Fake asyncio subprocess for testing."""

    def __init__(self, stdout=b"", stderr=b"", returncode=0, timeout=False):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._timeout = timeout
        self.stdin = MagicMock()
        self._killed = False

    async def communicate(self, input=None):
        if self._timeout:
            raise asyncio.TimeoutError()
        self._received_input = input
        return self._stdout, self._stderr

    def kill(self):
        self._killed = True


@pytest.mark.asyncio
async def test_run_docker_stdio_success(monkeypatch):
    """Mock process returns valid JSON result."""
    result_json = json.dumps({"result": "ok"}).encode()
    fake_proc = FakeProcess(stdout=result_json, returncode=0)

    async def fake_create(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    result = await run_docker_stdio("test-image:latest", "get_weather", {"location": "Berlin"})
    assert result == {"result": "ok"}


@pytest.mark.asyncio
async def test_run_docker_stdio_nonzero_exit(monkeypatch):
    """Mock process: returncode=1, stderr='container error'.
    Assert RuntimeError raised with 'docker run failed'."""
    fake_proc = FakeProcess(stdout=b"", stderr=b"container error", returncode=1)

    async def fake_create(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    with pytest.raises(RuntimeError, match="docker run failed"):
        await run_docker_stdio("test-image:latest", "get_weather", {})


@pytest.mark.asyncio
async def test_run_docker_stdio_timeout(monkeypatch):
    """Mock communicate() to raise asyncio.TimeoutError.
    Assert RuntimeError raised with 'timeout'."""
    fake_proc = FakeProcess(timeout=True)

    async def fake_create(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    with pytest.raises(RuntimeError, match="timeout"):
        await run_docker_stdio("test-image:latest", "get_weather", {})


@pytest.mark.asyncio
async def test_run_docker_stdio_sends_correct_payload(monkeypatch):
    """Capture what was written to stdin.
    Assert it is JSON with {"tool": "get_weather", "inputs": {"location": "Berlin"}}."""
    expected_payload = {"tool": "get_weather", "inputs": {"location": "Berlin"}}
    result_json = json.dumps({"result": "ok"}).encode()
    fake_proc = FakeProcess(stdout=result_json, returncode=0)

    async def fake_create(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    await run_docker_stdio("test-image:latest", "get_weather", {"location": "Berlin"})

    # The payload is sent via communicate(input=...)
    received = json.loads(fake_proc._received_input.decode())
    assert received == expected_payload
