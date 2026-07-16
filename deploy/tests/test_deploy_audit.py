import json
import logging
import sys
import types
from pathlib import Path

import pytest

psycopg2_stub = types.ModuleType("psycopg2")
setattr(psycopg2_stub, "connect", lambda *_args, **_kwargs: None)
psycopg2_extras_stub = types.ModuleType("psycopg2.extras")
setattr(psycopg2_extras_stub, "RealDictCursor", object)
setattr(psycopg2_stub, "extras", psycopg2_extras_stub)
sys.modules.setdefault("psycopg2", psycopg2_stub)
sys.modules.setdefault("psycopg2.extras", psycopg2_extras_stub)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server  # noqa: E402


def test_write_deploy_audit_event_redacts_env_secrets_and_sets_required_receipt_fields(monkeypatch):
    executed = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=None):
            executed.append((sql, params))

    class Conn:
        def cursor(self):
            return Cursor()

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(server, "get_db", lambda: Conn())

    server.write_deploy_audit_event(
        action="deploy.register",
        resource="demo-mcp",
        result="ok",
        trace_id="trace-deploy",
        payload={
            "env_vars": {"API_KEY": "sk-live-abc", "SAFE_FLAG": "true"},
            "repo_url": "https://example.invalid/demo.git",
        },
        user_id="operator",
        tenant_id="tenant-a",
    )

    params = executed[0][1]
    assert params[:8] == (
        "tenant-a",
        "operator",
        "deploy.register",
        "demo-mcp",
        "mcpfinder-deploy",
        "ok",
        "trace-deploy",
        0,
    )
    payload = json.loads(params[8])
    assert payload["env_vars"]["API_KEY"] == "[REDACTED]"
    assert payload["env_vars"]["SAFE_FLAG"] == "true"
    assert "sk-live-abc" not in params[8]


def test_redact_audit_payload_treats_secret_key_separators_like_underscores():
    payload = {
        "env_vars": {
            "API-KEY": "api-hyphen-value",
            "PRIVATE.KEY": "private-dot-value",
            "ACCESS KEY": "access-space-value",
            "SAFE-FLAG": "true",
        },
        "diagnostics": {"safe.detail": "preserved"},
    }

    redacted = server.redact_audit_payload(payload)

    assert redacted["env_vars"]["API-KEY"] == "[REDACTED]"
    assert redacted["env_vars"]["PRIVATE.KEY"] == "[REDACTED]"
    assert redacted["env_vars"]["ACCESS KEY"] == "[REDACTED]"
    assert redacted["env_vars"]["SAFE-FLAG"] == "true"
    assert redacted["diagnostics"]["safe.detail"] == "preserved"


async def _drain_async_events(generator):
    events = []
    async for event in generator:
        events.append(json.loads(event["data"]))
    return events


class _DeployCursor:
    def __init__(self, *, fail_register=False, executed=None):
        self.fail_register = fail_register
        self.executed = executed if executed is not None else []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if self.fail_register and "INSERT INTO servers" in sql:
            raise RuntimeError("catalog insert failed with secret sk-live-abc")

    def fetchone(self):
        return ("server-1",)


class _DeployConn:
    def __init__(self, *, fail_register=False, executed=None):
        self.fail_register = fail_register
        self.executed = executed if executed is not None else []
        self.rolled_back = False

    def cursor(self):
        return _DeployCursor(fail_register=self.fail_register, executed=self.executed)

    def commit(self):
        pass

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


class _FakeAsyncClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, *_args, **_kwargs):
        return types.SimpleNamespace(status_code=201)


def _install_successful_deploy_fakes(monkeypatch, tmp_path, *, popen=None, run_cmd=None):
    monkeypatch.setattr(server, "BUILDS_DIR", tmp_path)
    monkeypatch.setattr(server.httpx, "AsyncClient", lambda *args, **kwargs: _FakeAsyncClient())
    monkeypatch.setattr(
        server.subprocess,
        "Popen",
        popen or (lambda *args, **kwargs: types.SimpleNamespace(stdout=[], wait=lambda: None, returncode=0)),
    )
    monkeypatch.setattr(server, "run_cmd", run_cmd or (lambda *_args, **_kwargs: (0, "ok")))


@pytest.mark.asyncio
async def test_deploy_pipeline_register_failure_emits_redacted_audit_event(monkeypatch, tmp_path):
    audit_calls = []
    db_calls = [_DeployConn(), _DeployConn(fail_register=True)]

    def fake_get_db():
        if db_calls:
            return db_calls.pop(0)
        return _DeployConn()

    def capture_audit(**kwargs):
        audit_calls.append(kwargs)

    monkeypatch.setattr(server, "get_db", fake_get_db)
    monkeypatch.setattr(server, "write_deploy_audit_event", capture_audit)
    _install_successful_deploy_fakes(monkeypatch, tmp_path)

    req = server.DeployRequest(
        name="demo-mcp",
        repo_url="https://example.invalid/demo.git",
        branch="main",
        env_vars={"API_KEY": "sk-live-abc", "SAFE_FLAG": "true"},
    )

    events = await _drain_async_events(server.deploy_pipeline(req))

    register_error = next(event for event in events if event["step"] == "register" and event["status"] == "error")
    assert register_error["msg"] == "DB registration failed; see deploy audit receipt for redacted details"
    assert "sk-live-abc" not in str(events)
    assert "catalog insert failed" not in str(events)
    failure_audit = next(event for event in audit_calls if event["action"] == "deploy.register" and event["result"] == "error")
    assert failure_audit["resource"] == "demo-mcp"
    assert failure_audit["trace_id"]
    assert failure_audit["payload"]["env_vars"]["API_KEY"] == "[REDACTED]"
    assert failure_audit["payload"]["env_vars"]["SAFE_FLAG"] == "true"
    assert failure_audit["payload"]["error"] == "catalog_registration_failed"
    assert "sk-live-abc" not in str(failure_audit)


@pytest.mark.asyncio
async def test_deploy_pipeline_register_failure_does_not_leak_when_audit_sink_fails(monkeypatch, tmp_path):
    db_calls = [_DeployConn(), _DeployConn(fail_register=True)]

    def fake_get_db():
        if db_calls:
            return db_calls.pop(0)
        return _DeployConn()

    def failing_audit(**_kwargs):
        raise RuntimeError("audit sink failed with secret sk-live-abc")

    monkeypatch.setattr(server, "get_db", fake_get_db)
    monkeypatch.setattr(server, "write_deploy_audit_event", failing_audit)
    _install_successful_deploy_fakes(monkeypatch, tmp_path)

    req = server.DeployRequest(
        name="demo-mcp",
        repo_url="https://example.invalid/demo.git",
        branch="main",
        env_vars={"API_KEY": "sk-live-abc", "SAFE_FLAG": "true"},
    )

    events = await _drain_async_events(server.deploy_pipeline(req))

    register_errors = [event for event in events if event["step"] == "register" and event["status"] == "error"]
    assert len(register_errors) == 1
    assert register_errors[0]["msg"] == "DB registration failed; see deploy audit receipt for redacted details"
    assert "sk-live-abc" not in str(events)
    assert "audit sink failed" not in str(events)
    assert "Unexpected error" not in str(events)



@pytest.mark.asyncio
async def test_deploy_pipeline_yaml_parse_error_does_not_leak_raw_details(monkeypatch, tmp_path, caplog):
    db_calls = [_DeployConn(), _DeployConn()]

    def fake_get_db():
        if db_calls:
            return db_calls.pop(0)
        return _DeployConn()

    def fake_run_cmd(cmd, *_args, **_kwargs):
        if cmd[:2] == ["git", "clone"]:
            build_dir = Path(cmd[-1])
            build_dir.mkdir(parents=True, exist_ok=True)
            (build_dir / "mcp.yaml").write_text("token: [sk-live-yaml\n", encoding="utf-8")
        return 0, "ok"

    monkeypatch.setattr(server, "get_db", fake_get_db)
    _install_successful_deploy_fakes(monkeypatch, tmp_path, run_cmd=fake_run_cmd)

    req = server.DeployRequest(name="yaml-mcp", repo_url="https://example.invalid/demo.git")

    events = await _drain_async_events(server.deploy_pipeline(req))

    detect_infos = [event for event in events if event["step"] == "detect" and event["status"] == "info"]
    assert any(event["msg"] == "Failed to parse mcp.yaml; see server logs for details" for event in detect_infos)
    assert "sk-live-yaml" not in str(events)
    assert "ParserError" in caplog.text


@pytest.mark.asyncio
async def test_deploy_pipeline_docker_build_output_is_redacted_for_sse_clients(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger=server.logger.name)
    db_calls = [_DeployConn(), _DeployConn()]

    def fake_get_db():
        if db_calls:
            return db_calls.pop(0)
        return _DeployConn()

    monkeypatch.setattr(server, "get_db", fake_get_db)
    def popen(*_args, **_kwargs):
        return types.SimpleNamespace(
            stdout=[
                "Step 1/3 : RUN echo token=leaky-build-value\n",
                "Step 2/3 : RUN curl -H 'Authorization: Bearer leaky-bearer-value' https://example.invalid\n",
                "Step 3/3 : RUN export credential: Basic leaky-basic-value\n",
            ],
            wait=lambda: None,
            returncode=0,
        )
    _install_successful_deploy_fakes(monkeypatch, tmp_path, popen=popen)

    req = server.DeployRequest(name="build-mcp", repo_url="https://example.invalid/demo.git")

    events = await _drain_async_events(server.deploy_pipeline(req))

    assert "leaky-build-value" not in str(events)
    assert "leaky-bearer-value" not in str(events)
    assert "leaky-basic-value" not in str(events)
    assert "[REDACTED]" in str(events)
    assert "leaky-build-value" in caplog.text
    assert "leaky-bearer-value" in caplog.text
    assert "leaky-basic-value" in caplog.text


@pytest.mark.asyncio
async def test_deploy_pipeline_docker_build_popen_exception_is_generic_for_sse_clients(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(server, "get_db", lambda: _DeployConn())

    def popen(*_args, **_kwargs):
        raise RuntimeError("docker unavailable token=leaky-popen-value")

    _install_successful_deploy_fakes(monkeypatch, tmp_path, popen=popen)

    req = server.DeployRequest(name="popen-mcp", repo_url="https://example.invalid/demo.git")

    events = await _drain_async_events(server.deploy_pipeline(req))

    build_error = next(event for event in events if event["step"] == "build" and event["status"] == "error")
    assert build_error["msg"] == "Docker build could not be started; see server logs for details"
    assert "leaky-popen-value" not in str(events)
    assert "docker unavailable" in caplog.text
    assert "leaky-popen-value" in caplog.text
