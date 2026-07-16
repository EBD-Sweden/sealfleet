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

llm = AsyncOpenAI(
    base_url=OPENAI_BASE_URL,
    api_key=OPENAI_API_KEY,
    timeout=httpx.Timeout(LLM_TIMEOUT, connect=3.0),
)
http = httpx.AsyncClient(timeout=20.0)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class AskRequest(BaseModel):
    question: str


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
    resp = await http.get(f"{ROUTER_URL}/pipelines/tools")
    resp.raise_for_status()
    return resp.json().get("tools", [])


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
    """Format pipeline result into human answer without LLM call."""
    return json.dumps(raw_result, indent=2)[:300]


async def llm_select_pipeline(question: str, pipeline_tools: list[dict]) -> tuple[str | None, dict, str]:
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

    try:
        resp = await asyncio.wait_for(
            llm.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150,
            ),
            timeout=LLM_TIMEOUT,
        )
        text = resp.choices[0].message.content.strip()
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
        return None, {}, "LLM timed out — using keyword fallback."
    except Exception as e:
        return None, {}, f"LLM error ({e}) — using keyword fallback."


async def run_pipeline(name: str, arguments: dict) -> dict:
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
    pipeline_name, tool_arguments, reasoning = await llm_select_pipeline(req.question, pipeline_tools)

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

    # 4. Execute pipeline
    try:
        result = await run_pipeline(pipeline_name, tool_arguments)
    except Exception as e:
        raise HTTPException(502, f"Pipeline '{pipeline_name}' failed: {e}")

    # 5. Build response
    steps = result.get("steps", [])
    resolved_chain = [f"{s['mcp']}.{s['tool']}" for s in steps]
    raw_result = result.get("final", result)
    location = tool_arguments.get("location", "the requested location")

    # Template-based answer (fast, no extra LLM call)
    answer = template_answer(pipeline_name, raw_result, location)

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
