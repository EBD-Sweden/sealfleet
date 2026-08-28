"""
Sealfleet Core Agent — natural language → MCP pipeline execution.
FastAPI service on port 8050.

Flow (optimised — single LLM call):
  1. Fetch pipeline tools from router
  2. ONE LLM call: tool selection + argument extraction (5s timeout → keyword fallback)
  3. Execute pipeline via router
  4. Template-based answer format (no extra LLM call)
  5. Return AskResponse
"""

import asyncio
import json
import os
import re
import time

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ROUTER_URL = os.getenv("ROUTER_URL", "http://localhost:8040")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://localhost:3456/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "claude-sonnet-4")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "not-needed")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "8"))  # seconds
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "150"))
# A local model is slower to first token than a hosted one, and a *reasoning*
# model spends much of its budget thinking before it emits any content — with
# cloud-sized limits it returns content=None and every question silently falls
# back to keyword matching. Give pinned-local requests their own budgets.
LOCAL_LLM_TIMEOUT = float(os.getenv("LOCAL_LLM_TIMEOUT", "120"))
LOCAL_LLM_MAX_TOKENS = int(os.getenv("LOCAL_LLM_MAX_TOKENS", "1024"))
# Router runs with REQUIRE_AUTH=true; present the operator key so /pipelines/*
# calls are authorized and audited. Compose/k8s inject the per-deployment value.
ROUTER_API_KEY = os.getenv("ROUTER_API_KEY", "")

llm = AsyncOpenAI(
    base_url=OPENAI_BASE_URL,
    api_key=OPENAI_API_KEY,
    timeout=httpx.Timeout(max(LLM_TIMEOUT, LOCAL_LLM_TIMEOUT), connect=3.0),
)
http = httpx.AsyncClient(
    timeout=20.0,
    headers={"X-API-Key": ROUTER_API_KEY} if ROUTER_API_KEY else {},
)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class AskRequest(BaseModel):
    question: str
    # Which brain to plan with, chosen per request by the portal's chat.
    # Prefix "local/" pins the on-box model (the llm-proxy will not fall back
    # to a cloud backend). None → the deployment default (OPENAI_MODEL).
    model: str | None = None


class AskResponse(BaseModel):
    question: str
    output_type: str
    resolved_chain: list[str]
    inputs_used: dict
    answer: str
    raw_result: dict
    reasoning: str


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Sealfleet Core Agent", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def fetch_pipeline_tools() -> list[dict]:
    """Tools the agent can call: named pipelines plus v2 pipelines.

    Both APIs coexist in the router; a deployment may have either or both, so
    merge them and remember which endpoint each tool runs through.
    """
    tools: list[dict] = []

    try:
        resp = await http.get(f"{ROUTER_URL}/pipelines/tools")
        resp.raise_for_status()
        for t in resp.json().get("tools", []):
            tools.append({**t, "_api": "named"})
    except Exception as e:
        print(f"[core-agent] named pipeline listing unavailable: {e}")

    try:
        resp = await http.get(f"{ROUTER_URL}/v2/pipelines")
        resp.raise_for_status()
        for p in resp.json().get("pipelines", []):
            tools.append({
                "name": p["name"],
                "description": p.get("description") or p["name"],
                "inputSchema": p.get("input_schema")
                or {"type": "object", "properties": {}, "required": []},
                "_api": "v2",
            })
    except Exception as e:
        print(f"[core-agent] v2 pipeline listing unavailable: {e}")

    return tools


def extract_location(question: str) -> str | None:
    """Extract city/location from a natural language question."""
    match = re.search(
        r"\b(?:in|for|at|to|from)\s+([A-Z][a-zA-Z\s]+?)(?:\s+today|\s+tomorrow|\s+now|[?,!.]|$)",
        question,
    )
    if match:
        return match.group(1).strip()
    skip = {"What", "The", "Can", "How", "Tell", "Give", "Show", "Do", "Is", "Are", "Should", "I", "Wear"}
    for word in question.split():
        if word[0].isupper() and len(word) > 2 and word.rstrip("?!.,") not in skip:
            return word.rstrip("?!.,")
    return None


def detect_pipeline(question: str) -> str | None:
    """Instant keyword-based pipeline detection.

    No built-in shortcuts in the public example set — the LLM selects from the
    registered named pipelines (`/pipelines/tools`). Add keyword rules here to
    fast-path your own pipelines without an LLM round-trip.
    """
    return None


def template_answer(pipeline_name: str, raw_result: dict, location: str) -> str:
    """Last-resort answer when no LLM is available to write one."""
    return json.dumps(raw_result, indent=2)[:300]


async def summarize_result(
    question: str, pipeline_name: str, raw_result: dict, model: str | None
) -> str:
    """Turn a pipeline result into prose. Falls back to the raw dump on any failure.

    Kept to one short call so a failure here costs latency, never the answer.
    """
    selected = model or OPENAI_MODEL
    is_local = selected.startswith("local/")
    payload = json.dumps(raw_result)[:6000]
    prompt = (
        f'Question: "{question}"\n\n'
        f"Result from the `{pipeline_name}` pipeline:\n{payload}\n\n"
        "Answer the question from this data in 2-3 sentences. Be specific with "
        "numbers and names. Do not mention the pipeline or the JSON."
    )
    try:
        resp = await asyncio.wait_for(
            llm.chat.completions.create(
                model=selected,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=LOCAL_LLM_MAX_TOKENS if is_local else LLM_MAX_TOKENS * 2,
            ),
            timeout=LOCAL_LLM_TIMEOUT if is_local else LLM_TIMEOUT,
        )
        content = resp.choices[0].message.content
        if content and content.strip():
            return content.strip()
    except Exception as e:
        print(f"[core-agent] answer formatting failed: {e}")
    return template_answer(pipeline_name, raw_result, "")


async def llm_select_pipeline(question: str, pipeline_tools: list[dict], model: str | None = None) -> tuple[str | None, dict, str]:
    """Single LLM call to select pipeline and extract arguments.
    Returns (pipeline_name, arguments, reasoning).
    Times out after LLM_TIMEOUT seconds → returns (None, {}, '').
    """
    tools_desc = "\n".join(
        f"- {t['name']}: {t['description']}"
        f" (args: {list(t.get('inputSchema', {}).get('properties', {}).keys())})"
        for t in pipeline_tools
    )
    prompt = f"""Available pipeline tools:
{tools_desc}

User question: "{question}"

Respond with ONLY valid JSON (no markdown):
{{"tool": "<tool_name>", "arguments": {{"<param>": "<value>"}}}}

If no tool matches: {{"tool": null, "arguments": {{}}}}"""

    selected = model or OPENAI_MODEL
    is_local = selected.startswith("local/")
    budget_timeout = LOCAL_LLM_TIMEOUT if is_local else LLM_TIMEOUT
    budget_tokens = LOCAL_LLM_MAX_TOKENS if is_local else LLM_MAX_TOKENS

    try:
        resp = await asyncio.wait_for(
            llm.chat.completions.create(
                model=selected,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=budget_tokens,
            ),
            timeout=budget_timeout,
        )
        content = resp.choices[0].message.content
        if not content:
            # Reasoning models return content=None when the token budget was
            # consumed before any answer text — raise LOCAL_LLM_MAX_TOKENS.
            return None, {}, (
                f"LLM returned no content (finish_reason="
                f"{resp.choices[0].finish_reason}) — using keyword fallback."
            )
        text = content.strip()
        # Strip code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0]
        result = json.loads(text.strip())
        name = result.get("tool")
        args = result.get("arguments", {})
        if name:
            return name, args, f"LLM selected '{name}' with args {args}."
        return None, {}, "LLM returned null tool."
    except asyncio.TimeoutError:
        return None, {}, f"LLM timed out after {budget_timeout}s — using keyword fallback."
    except Exception as e:
        return None, {}, f"LLM error ({e}) — using keyword fallback."


async def run_pipeline(name: str, arguments: dict, api: str = "named") -> dict:
    """Run a pipeline through whichever router API it came from."""
    if api == "v2":
        resp = await http.post(
            f"{ROUTER_URL}/v2/pipelines/run",
            json={"pipeline": name, "inputs": arguments},
        )
    else:
        resp = await http.post(
            f"{ROUTER_URL}/pipelines/{name}/run",
            json={"inputs": arguments},
        )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    return {"status": "ok", "service": "core-agent", "version": "0.3.0"}


@app.get("/models")
async def models():
    """Which brains this deployment can plan with, for the portal's chat picker.

    Asks the llm-proxy what it has configured rather than guessing, so the list
    reflects the actual deployment. Returns `default` plus one entry per brain.
    """
    brains: list[dict] = []
    proxy_health: dict = {}

    # OPENAI_BASE_URL points at <proxy>/v1; /health sits at the proxy root.
    proxy_root = OPENAI_BASE_URL.rsplit("/v1", 1)[0]
    try:
        resp = await http.get(f"{proxy_root}/health", timeout=5.0)
        resp.raise_for_status()
        proxy_health = resp.json()
    except Exception as e:
        print(f"[core-agent] llm-proxy health unavailable: {e}")

    local = proxy_health.get("local") or {}
    if local.get("enabled"):
        local_model = local.get("model") or ""
        brains.append({
            "id": f"local/{local_model}" if local_model else "local/",
            "label": local_model.split("/")[-1] or "On-box model",
            "kind": "local",
            "description": "Runs on this machine. Prompts never leave the host.",
        })

    mode = proxy_health.get("mode")
    if mode and mode not in ("local", "no-backend"):
        brains.append({
            "id": OPENAI_MODEL,
            "label": OPENAI_MODEL.split("/")[-1],
            "kind": "cloud",
            "description": f"Cloud provider via {mode}.",
        })

    return {
        "default": OPENAI_MODEL,
        "proxy_mode": mode or "unknown",
        "models": brains,
    }


@app.get("/tools")
async def tools():
    try:
        pipeline_tools = await fetch_pipeline_tools()
        return {"tools": pipeline_tools}
    except Exception as e:
        raise HTTPException(502, f"Failed to fetch tools from router: {e}")


@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest):
    start = time.time()

    # 1. Fetch available pipeline tools
    try:
        pipeline_tools = await fetch_pipeline_tools()
    except Exception as e:
        raise HTTPException(502, f"Router unavailable: {e}")

    if not pipeline_tools:
        return AskResponse(
            question=req.question, output_type="none", resolved_chain=[],
            inputs_used={}, answer="No pipeline tools available.", raw_result={},
            reasoning="Router returned zero pipeline tools.",
        )

    # 2. Single LLM call to select pipeline + extract args
    pipeline_name, tool_arguments, reasoning = await llm_select_pipeline(
        req.question, pipeline_tools, req.model
    )

    # 3. Keyword fallback if LLM didn't resolve
    if not pipeline_name:
        pipeline_name = detect_pipeline(req.question)
        if pipeline_name:
            location = extract_location(req.question)
            if location:
                tool_arguments = {"location": location}
                reasoning += f" Keyword matched '{pipeline_name}', location='{location}'."
            else:
                return AskResponse(
                    question=req.question, output_type="unknown", resolved_chain=[],
                    inputs_used={},
                    answer="I think you're asking about outfit/weather — which city? Please include a city name.",
                    raw_result={}, reasoning=reasoning + " No location found.",
                )
        else:
            return AskResponse(
                question=req.question, output_type="none", resolved_chain=[],
                inputs_used={}, answer="I don't have a pipeline for that question yet.",
                raw_result={}, reasoning=reasoning,
            )

    # 4. Execute pipeline via the API it was listed from
    pipeline_api = next(
        (t.get("_api", "named") for t in pipeline_tools if t.get("name") == pipeline_name),
        "named",
    )
    try:
        result = await run_pipeline(pipeline_name, tool_arguments, pipeline_api)
    except Exception as e:
        raise HTTPException(502, f"Pipeline '{pipeline_name}' failed: {e}")

    # 5. Build response. Named pipelines return a `steps` list; v2 returns a
    #    `results` map keyed by step id plus a final `output`.
    if pipeline_api == "v2":
        results = result.get("results") or {}
        resolved_chain = [
            f"{r['mcp']}.{r['tool']}" if r.get("mcp") else (r.get("pipeline") or step_id)
            for step_id, r in results.items()
            if isinstance(r, dict)
        ]
        raw_result = result.get("output") or result
    else:
        steps = result.get("steps", [])
        resolved_chain = [f"{s['mcp']}.{s['tool']}" for s in steps]
        raw_result = result.get("final", result)

    answer = await summarize_result(req.question, pipeline_name, raw_result, req.model)

    elapsed = round(time.time() - start, 2)
    reasoning += f" Completed in {elapsed}s."

    return AskResponse(
        question=req.question,
        output_type=result.get("pipeline_name", pipeline_name),
        resolved_chain=resolved_chain,
        inputs_used=tool_arguments,
        answer=answer,
        raw_result=raw_result if isinstance(raw_result, dict) else {"result": raw_result},
        reasoning=reasoning,
    )


@app.on_event("shutdown")
async def shutdown():
    await http.aclose()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8050)
