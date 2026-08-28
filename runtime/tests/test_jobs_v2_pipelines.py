"""Background jobs run v2 pipelines, not only named (v1) ones.

Every pipeline authored from chat is v2; before this, POST /jobs answered
404 for all of them.
"""

import asyncio
import json

import pytest

import router


V2 = {
    "name": "v2_job_probe", "version": 2,
    "inputs": {"city": {"type": "string", "default": "Oslo"}},
    "steps": [
        {"id": "fetch", "mcp": "weather-trip-mcp", "tool": "fetch_cities_weather", "inputs": {"cities": ["{{inputs.city}}"]}},
        {"id": "rank", "mcp": "weather-trip-mcp", "tool": "rank_cities", "inputs": {"weather": "{{steps.fetch.output}}"}},
    ],
    "output": {"ranking": "{{steps.rank.output}}"},
}


@pytest.fixture()
def job_db(monkeypatch):
    """Capture job table writes instead of needing Postgres."""
    writes: list[tuple[str, tuple]] = []
    monkeypatch.setattr(router, "_job_db_write", lambda sql, params=(): writes.append((sql, params)))
    return writes


def test_resolver_accepts_both_engines(monkeypatch):
    monkeypatch.setattr(router, "v2_pipelines", {"v2_job_probe": V2})
    kind, obj = router._resolve_job_pipeline("v2_job_probe")
    assert kind == "v2" and obj is V2
    with pytest.raises(router.HTTPException) as exc:
        router._resolve_job_pipeline("nope")
    assert exc.value.status_code == 404


async def test_v2_job_records_steps_and_completes(monkeypatch, job_db):
    async def fake_run(pipeline_def, inputs, **kw):
        return {"results": {
            "fetch": {"mcp": "weather-trip-mcp", "tool": "fetch_cities_weather", "output": {"cities": [{"name": "Oslo"}]}},
            "rank": {"mcp": "weather-trip-mcp", "tool": "rank_cities", "output": {"ranking": [{"city": "Oslo"}]}},
        }, "output": {"ranking": [{"city": "Oslo"}]}, "trace_id": "t1", "duration_ms": 5}

    monkeypatch.setattr(router, "_run_v2_pipeline", fake_run)
    monkeypatch.setattr(router, "v2_pipelines", {"v2_job_probe": V2})

    await router._execute_job("job1", "v2_job_probe", {"city": "Oslo"}, "tenant-1", subject_id="u1")

    sqls = [w[0] for w in job_db]
    assert sum("INSERT INTO pipeline_job_steps" in q for q in sqls) == 2
    step_updates = [w for w in job_db if "UPDATE pipeline_job_steps" in w[0]]
    assert [w[1][0] for w in step_updates] == ["completed", "completed"]
    final = [w for w in job_db if "UPDATE pipeline_jobs SET status=%s" in w[0]][-1]
    assert final[1][0] == "completed" and final[1][2] is None
    assert json.loads(final[1][1])["output"]["ranking"][0]["city"] == "Oslo"


async def test_v2_job_with_a_failed_step_is_marked_failed(monkeypatch, job_db):
    async def fake_run(pipeline_def, inputs, **kw):
        return {"results": {
            "fetch": {"output": {"error": "HTTP 400", "detail": "bad"}},
            "rank": {"output": {"error": "HTTP 400"}},
        }, "output": None}

    monkeypatch.setattr(router, "_run_v2_pipeline", fake_run)
    monkeypatch.setattr(router, "v2_pipelines", {"v2_job_probe": V2})
    await router._execute_job("job2", "v2_job_probe", {}, "tenant-1")

    final = [w for w in job_db if "UPDATE pipeline_jobs SET status=%s" in w[0]][-1]
    assert final[1][0] == "failed"
    step_updates = [w for w in job_db if "UPDATE pipeline_job_steps" in w[0]]
    assert all(w[1][0] == "failed" for w in step_updates)


async def test_post_jobs_accepts_a_v2_pipeline(monkeypatch, job_db, client):
    monkeypatch.setattr(
        router.api_key_manager, "validate",
        lambda key: {"tenant_id": "qa", "name": "qa", "actions": ["*"]} if key == "qa-key" else None,
    )
    monkeypatch.setattr(router, "v2_pipelines", {"v2_job_probe": V2})

    async def fake_run(pipeline_def, inputs, **kw):
        return {"results": {}, "output": {}}
    monkeypatch.setattr(router, "_run_v2_pipeline", fake_run)

    r = await client.post("/jobs", json={"pipeline": "v2_job_probe", "inputs": {"city": "Oslo"}},
                          headers={"X-API-Key": "qa-key"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued" and r.json()["job_id"]
    await asyncio.sleep(0.05)  # let the background task run
    assert any("UPDATE pipeline_jobs SET status=%s" in w[0] for w in job_db)


async def test_post_jobs_still_404s_for_unknown_pipeline(monkeypatch, client):
    monkeypatch.setattr(
        router.api_key_manager, "validate",
        lambda key: {"tenant_id": "qa", "name": "qa", "actions": ["*"]} if key == "qa-key" else None,
    )
    r = await client.post("/jobs", json={"pipeline": "ghost"}, headers={"X-API-Key": "qa-key"})
    assert r.status_code == 404
