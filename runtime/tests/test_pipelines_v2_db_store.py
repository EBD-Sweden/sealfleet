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
            self._rows = [(t, n, json.dumps(d)) for (t, n), d in self.conn.rows.items()]
        elif sql.lstrip().upper().startswith("INSERT"):
            # (name, definition, tenant_id, created_by)
            self.conn.rows[(params[2], params[0])] = json.loads(params[1])
        elif sql.lstrip().upper().startswith("DELETE"):
            # (name, tenant_id)
            self.rowcount = 1 if self.conn.rows.pop((params[1], params[0]), None) is not None else 0

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
    db.rows = {("t1", "db_authored"): DEF,
               ("", seed): {**DEF, "name": seed, "description": "FROM DB"}}
    monkeypatch.setattr(router, "v2_pipelines", {})
    monkeypatch.setattr(router, "v2_db_pipelines", {})
    router._load_yaml_pipeline_v2()
    assert router._resolve_v2_pipeline("db_authored", "t1") is not None
    assert router._resolve_v2_pipeline("db_authored", "t2") is None, "tenant-scoped"
    assert router._resolve_v2_pipeline(seed, "anyone")["description"] == "FROM DB"


def test_visibility_and_run_are_tenant_scoped(monkeypatch, db):
    monkeypatch.setattr(router, "v2_pipelines", {})
    monkeypatch.setattr(router, "v2_db_pipelines", {
        ("t1", "mine"): {**DEF, "name": "mine"},
        ("t2", "theirs"): {**DEF, "name": "theirs"},
        ("", "shared"): {**DEF, "name": "shared"},
    })
    visible = router._v2_visible_items("t1")
    assert set(visible) == {"mine", "shared"}
    assert "theirs" not in router._v2_visible_items("t1")


def test_same_name_in_two_tenants_does_not_collide(monkeypatch, db):
    monkeypatch.setattr(router, "v2_pipelines", {})
    monkeypatch.setattr(router, "v2_db_pipelines", {})
    a = {**DEF, "name": "report", "description": "tenant A"}
    b = {**DEF, "name": "report", "description": "tenant B"}
    router._db_upsert_v2_pipeline(a, tenant_id="tA")
    router._db_upsert_v2_pipeline(b, tenant_id="tB")
    assert db.rows[("tA", "report")]["description"] == "tenant A"
    assert db.rows[("tB", "report")]["description"] == "tenant B"


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
    monkeypatch.setattr(router, "v2_db_pipelines", {})
    r = await client.post("/v2/pipelines/deploy", json={"pipeline": DEF}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["store"] == "database"
    assert ("qa", "db_authored") in db.rows          # stored under the caller's tenant
    assert ("qa", "db_authored") in router.v2_db_pipelines
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
    db.rows = {("qa", "db_authored"): DEF}
    monkeypatch.setattr(router, "v2_pipelines", {})
    monkeypatch.setattr(router, "v2_db_pipelines", {})
    router._load_yaml_pipeline_v2()

    r = await client.delete("/v2/pipelines/db_authored", headers=headers)
    assert r.status_code == 204 and ("qa", "db_authored") not in db.rows

    seed = next(iter(p.stem for p in router._V2_SEED_DIR.glob("*.yaml")))
    r = await client.delete(f"/v2/pipelines/{seed}", headers=headers)
    assert r.status_code == 409
