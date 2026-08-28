"""Runtime-authored pipelines persist in PIPELINES_DATA_DIR, not the image.

Seeds ship in the image and refresh on rebuild; pipelines authored through
chat or POST /v2/pipelines/deploy are written to a mounted data dir so they
survive container recreation. Unset, everything falls back to the seed dir.
"""

import yaml

import router


def _seed_names():
    return {p.stem for p in router._V2_SEED_DIR.glob("*.yaml")}


def test_without_data_dir_only_the_seed_dir_is_used(monkeypatch):
    monkeypatch.delenv("PIPELINES_DATA_DIR", raising=False)
    assert router._v2_pipeline_dirs() == [router._V2_SEED_DIR]
    assert router._v2_data_dir() == router._V2_SEED_DIR


def test_data_dir_pipelines_load_alongside_seeds(monkeypatch, tmp_path):
    monkeypatch.setenv("PIPELINES_DATA_DIR", str(tmp_path))
    (tmp_path / "authored_one.yaml").write_text(yaml.dump({
        "name": "authored_one", "version": 2,
        "steps": [{"id": "s", "mcp": "demo-sandbox-mcp", "tool": "score_fake_vendor"}],
    }))
    monkeypatch.setattr(router, "v2_pipelines", {})
    router._load_yaml_pipeline_v2()

    loaded = set(router.v2_pipelines)
    assert "authored_one" in loaded
    assert _seed_names() <= loaded, "seeds must still load when a data dir is set"


def test_data_dir_overrides_a_seed_of_the_same_name(monkeypatch, tmp_path):
    seed = next(iter(_seed_names()))
    monkeypatch.setenv("PIPELINES_DATA_DIR", str(tmp_path))
    (tmp_path / f"{seed}.yaml").write_text(yaml.dump({
        "name": seed, "version": 2, "description": "OVERRIDDEN",
        "steps": [{"id": "s", "mcp": "demo-sandbox-mcp", "tool": "score_fake_vendor"}],
    }))
    monkeypatch.setattr(router, "v2_pipelines", {})
    router._load_yaml_pipeline_v2()
    assert router.v2_pipelines[seed]["description"] == "OVERRIDDEN"


async def test_deploy_writes_into_the_data_dir_not_the_image(monkeypatch, tmp_path, client):
    monkeypatch.setenv("PIPELINES_DATA_DIR", str(tmp_path))
    # Pass the API-key middleware and the pipeline.deploy action gate.
    monkeypatch.setattr(
        router.api_key_manager, "validate",
        lambda key: {"tenant_id": "qa", "name": "qa", "actions": ["*"]} if key == "qa-key" else None,
    )
    monkeypatch.setattr(router, "v2_pipelines", {})

    body = {"pipeline": {
        "name": "deployed_via_api", "version": 2,
        "steps": [{"id": "s", "mcp": "demo-sandbox-mcp", "tool": "score_fake_vendor"}],
    }}
    r = await client.post("/v2/pipelines/deploy", json=body, headers={"X-API-Key": "qa-key"})
    assert r.status_code == 200, r.text

    assert (tmp_path / "deployed_via_api.yaml").exists()
    assert not (router._V2_SEED_DIR / "deployed_via_api.yaml").exists()
    assert "deployed_via_api" in router.v2_pipelines


def _qa_auth(monkeypatch):
    monkeypatch.setattr(
        router.api_key_manager, "validate",
        lambda key: {"tenant_id": "qa", "name": "qa", "actions": ["*"]} if key == "qa-key" else None,
    )
    return {"X-API-Key": "qa-key"}


async def test_delete_removes_an_authored_pipeline(monkeypatch, tmp_path, client):
    monkeypatch.setenv("PIPELINES_DATA_DIR", str(tmp_path))
    headers = _qa_auth(monkeypatch)
    monkeypatch.setattr(router, "v2_pipelines", {})
    body = {"pipeline": {"name": "to_delete", "version": 2,
            "steps": [{"id": "s", "mcp": "demo-sandbox-mcp", "tool": "score_fake_vendor"}]}}
    assert (await client.post("/v2/pipelines/deploy", json=body, headers=headers)).status_code == 200
    assert (tmp_path / "to_delete.yaml").exists()

    r = await client.delete("/v2/pipelines/to_delete", headers=headers)
    assert r.status_code == 204
    assert not (tmp_path / "to_delete.yaml").exists()
    assert "to_delete" not in router.v2_pipelines


async def test_delete_refuses_a_shipped_example(monkeypatch, tmp_path, client):
    monkeypatch.setenv("PIPELINES_DATA_DIR", str(tmp_path))
    headers = _qa_auth(monkeypatch)
    monkeypatch.setattr(router, "v2_pipelines", {})
    router._load_yaml_pipeline_v2()
    seed = next(iter(_seed_names()))

    r = await client.delete(f"/v2/pipelines/{seed}", headers=headers)
    assert r.status_code == 409
    assert (router._V2_SEED_DIR / f"{seed}.yaml").exists()
    assert seed in router.v2_pipelines


async def test_delete_requires_the_deploy_action(monkeypatch, tmp_path, client):
    monkeypatch.setenv("PIPELINES_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        router.api_key_manager, "validate",
        lambda key: {"tenant_id": "qa", "name": "ro", "actions": ["pipeline.invoke"]} if key == "ro" else None,
    )
    monkeypatch.setattr(router, "v2_pipelines", {"x": {"name": "x"}})
    r = await client.delete("/v2/pipelines/x", headers={"X-API-Key": "ro"})
    assert r.status_code == 403


async def test_deploy_refuses_to_shadow_a_shipped_example_unless_replace(monkeypatch, tmp_path, client):
    monkeypatch.setenv("PIPELINES_DATA_DIR", str(tmp_path))
    headers = _qa_auth(monkeypatch)
    monkeypatch.setattr(router, "v2_pipelines", {})
    seed = next(iter(_seed_names()))
    body = {"pipeline": {"name": seed, "version": 2,
            "steps": [{"id": "s", "mcp": "demo-sandbox-mcp", "tool": "score_fake_vendor"}]}}

    r = await client.post("/v2/pipelines/deploy", json=body, headers=headers)
    assert r.status_code == 409 and "replace" in r.json()["detail"]
    assert not (tmp_path / f"{seed}.yaml").exists()

    r = await client.post("/v2/pipelines/deploy", json={**body, "replace": True}, headers=headers)
    assert r.status_code == 200
    assert (tmp_path / f"{seed}.yaml").exists()
