"""Runtime-authored v2 pipelines are stored in the database when one exists.

Files under PIPELINES_DATA_DIR remain the fallback (dev, offline). Loading
order: shipped seeds → data dir → database, later winning.
"""

import json

import pytest

import router


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 0
        self._rows = []

    def execute(self, sql, params=()):
        self.conn.log.append((sql.strip().split()[0].upper(), params))
        if sql.lstrip().upper().startswith("SELECT"):
            self._rows = [(n, json.dumps(d)) for n, d in self.conn.rows.items()]
        elif sql.lstrip().upper().startswith("INSERT"):
            self.conn.rows[params[0]] = json.loads(params[1])
        elif sql.lstrip().upper().startswith("DELETE"):
            self.rowcount = 1 if self.conn.rows.pop(params[0], None) is not None else 0

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class FakeConn:
    def __init__(self, rows=None):
        self.rows = dict(rows or {})
        self.log = []

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.log.append(("COMMIT", ()))

    def rollback(self):
        self.log.append(("ROLLBACK", ()))


DEF = {"name": "db_authored", "version": 2,
       "steps": [{"id": "s", "mcp": "demo-sandbox-mcp", "tool": "score_fake_vendor"}]}


@pytest.fixture()
def db(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(router, "_get_db", lambda: conn)
    return conn


def _auth(monkeypatch):
    monkeypatch.setattr(
        router.api_key_manager, "validate",
        lambda key: {"tenant_id": "qa", "name": "qa", "actions": ["*"]} if key == "qa-key" else None,
    )
    return {"X-API-Key": "qa-key"}


def test_loader_reads_db_after_files_and_db_wins(monkeypatch, db):
    seed = next(iter(p.stem for p in router._V2_SEED_DIR.glob("*.yaml")))
    db.rows = {"db_authored": DEF, seed: {**DEF, "name": seed, "description": "FROM DB"}}
    monkeypatch.setattr(router, "v2_pipelines", {})
    router._load_yaml_pipeline_v2()
    assert "db_authored" in router.v2_pipelines
    assert router.v2_pipelines[seed]["description"] == "FROM DB"


def test_loader_survives_a_missing_table(monkeypatch):
    class Broken(FakeConn):
        def cursor(self):
            raise RuntimeError('relation "pipelines_v2" does not exist')
    monkeypatch.setattr(router, "_get_db", lambda: Broken())
    monkeypatch.setattr(router, "v2_pipelines", {})
    router._load_yaml_pipeline_v2()          # must not raise
    assert len(router.v2_pipelines) >= 1     # seeds still loaded


async def test_deploy_writes_to_db_not_file_when_db_present(monkeypatch, tmp_path, db, client):
    monkeypatch.setattr(router, "_get_db", lambda: db)   # after test_app's patch
    monkeypatch.setenv("PIPELINES_DATA_DIR", str(tmp_path))
    headers = _auth(monkeypatch)
    monkeypatch.setattr(router, "v2_pipelines", {})
    r = await client.post("/v2/pipelines/deploy", json={"pipeline": DEF}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["store"] == "database"
    assert "db_authored" in db.rows
    assert not (tmp_path / "db_authored.yaml").exists()


async def test_deploy_falls_back_to_file_without_db(monkeypatch, tmp_path, client):
    monkeypatch.setattr(router, "_get_db", lambda: None)
    monkeypatch.setenv("PIPELINES_DATA_DIR", str(tmp_path))
    headers = _auth(monkeypatch)
    monkeypatch.setattr(router, "v2_pipelines", {})
    r = await client.post("/v2/pipelines/deploy", json={"pipeline": DEF}, headers=headers)
    assert r.status_code == 200 and r.json()["store"] == "file"
    assert (tmp_path / "db_authored.yaml").exists()


async def test_delete_removes_db_row_and_protects_seeds(monkeypatch, tmp_path, db, client):
    monkeypatch.setattr(router, "_get_db", lambda: db)   # after test_app's patch
    monkeypatch.setenv("PIPELINES_DATA_DIR", str(tmp_path))
    headers = _auth(monkeypatch)
    db.rows = {"db_authored": DEF}
    monkeypatch.setattr(router, "v2_pipelines", {})
    router._load_yaml_pipeline_v2()

    r = await client.delete("/v2/pipelines/db_authored", headers=headers)
    assert r.status_code == 204 and "db_authored" not in db.rows

    seed = next(iter(p.stem for p in router._V2_SEED_DIR.glob("*.yaml")))
    r = await client.delete(f"/v2/pipelines/{seed}", headers=headers)
    assert r.status_code == 409
