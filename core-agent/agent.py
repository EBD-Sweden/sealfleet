"""Sealfleet Core Agent — LLM-powered agent that uses the runtime to answer questions.

FastAPI service on port 8050.
Discovers capabilities via /capabilities, uses LLM to map questions to output_type+inputs,
calls /resolve to execute the chain, and returns a formatted answer.
"""

import json
import os
import time

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

RUNTIME_URL = os.getenv("RUNTIME_URL", "http://localhost:8040")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:3456/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-6")
LLM_API_KEY = os.getenv("LLM_API_KEY", "not-needed")

llm = AsyncOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)

app = FastAPI(title="Sealfleet Core Agent", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class AskRequest(BaseModel):
    question: str
    context: dict = {}


class AskResponse(BaseModel):
    question: str
    output_type: str
    resolved_chain: list[str]
    inputs_used: dict
    answer: str
    raw_result: dict
    reasoning: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mcpfinder-core-agent"}


@app.get("/capabilities")
async def capabilities():
    """Proxy GET /capabilities from the runtime router."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(f"{RUNTIME_URL}/capabilities")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            raise HTTPException(502, f"Failed to reach runtime: {e}")


@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest):
    start = time.time()

    # 1. Fetch capabilities
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            caps_resp = await client.get(f"{RUNTIME_URL}/capabilities")
            caps_resp.raise_for_status()
            caps_data = caps_resp.json()
        except Exception as e:
            raise HTTPException(502, f"Failed to fetch capabilities: {e}")

    capabilities_map = caps_data.get("capabilities", {})
    if not capabilities_map:
        return AskResponse(
            question=req.question,
            output_type="none",
            resolved_chain=[],
            inputs_used={},
            answer="No capabilities are currently available in the runtime.",
            raw_result={},
            reasoning="Runtime returned zero capabilities.",
        )

    # 2. Format capabilities for the LLM
    caps_formatted = json.dumps(capabilities_map, indent=2)

    system_prompt = f"""You are an intelligent agent connected to the Sealfleet runtime.

Available capabilities (what you can produce):
{caps_formatted}

Your job:
1. Determine which output_type best answers the question
2. Extract the required inputs from the question
3. Respond with valid JSON only:
{{
  "output_type": "...",
  "inputs": {{}},
  "reasoning": "why you chose this"
}}

If no capability matches, respond with:
{{"output_type": null, "inputs": {{}}, "reasoning": "...why not..."}}"""

    # 3. Call LLM
    try:
        completion = await llm.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": req.question},
            ],
            max_tokens=500,
        )
        raw_text = completion.choices[0].message.content.strip()
    except Exception as e:
        raise HTTPException(502, f"LLM call failed: {e}")

    # 4. Parse JSON from LLM response
    try:
        # Handle markdown code blocks
        text = raw_text
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0]
        llm_result = json.loads(text.strip())
    except json.JSONDecodeError:
        raise HTTPException(
            502, f"LLM returned invalid JSON: {raw_text[:200]}"
        )

    output_type = llm_result.get("output_type")
    inputs_used = llm_result.get("inputs", {})
    reasoning = llm_result.get("reasoning", "")

    # 5. If no match, return friendly message
    if not output_type:
        return AskResponse(
            question=req.question,
            output_type="none",
            resolved_chain=[],
            inputs_used=inputs_used,
            answer=f"I don't have a capability that can answer that question. {reasoning}",
            raw_result={},
            reasoning=reasoning,
        )

    # 6. Call /resolve
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resolve_resp = await client.post(
                f"{RUNTIME_URL}/resolve",
                json={"output_type": output_type, "inputs": inputs_used},
            )
            resolve_resp.raise_for_status()
            raw_result = resolve_resp.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                502, f"Runtime /resolve failed ({e.response.status_code}): {e.response.text}"
            )
        except Exception as e:
            raise HTTPException(502, f"Runtime /resolve failed: {e}")

    resolved_chain = raw_result.get("resolved_chain", [])

    # 7. Format answer using second LLM call
    format_prompt = f"""Given this data: {json.dumps(raw_result.get("result", {}), indent=2)}
Write a clear, concise answer to: "{req.question}"
Be specific with numbers and details. Keep it to 2-3 sentences."""

    try:
        format_completion = await llm.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": format_prompt}],
            max_tokens=300,
        )
        answer = format_completion.choices[0].message.content.strip()
    except Exception:
        # Fallback: use raw result
        answer = json.dumps(raw_result.get("result", {}).get("final", {}), indent=2)

    elapsed = round(time.time() - start, 2)

    return AskResponse(
        question=req.question,
        output_type=output_type,
        resolved_chain=resolved_chain,
        inputs_used=inputs_used,
        answer=answer,
        raw_result=raw_result,
        reasoning=reasoning,
    )


# ---------------------------------------------------------------------------
# CLI mode
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import asyncio

    question = " ".join(sys.argv[1:]) or "What should I wear in Stockholm today?"

    async def main():
        print(f"Question: {question}\n")

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{RUNTIME_URL}/capabilities")
            caps = resp.json()

        caps_formatted = json.dumps(caps.get("capabilities", {}), indent=2)

        system_prompt = f"""You are an intelligent agent connected to the Sealfleet runtime.

Available capabilities (what you can produce):
{caps_formatted}

Your job:
1. Determine which output_type best answers the question
2. Extract the required inputs from the question
3. Respond with valid JSON only:
{{
  "output_type": "...",
  "inputs": {{}},
  "reasoning": "why you chose this"
}}

If no capability matches, respond with:
{{"output_type": null, "inputs": {{}}, "reasoning": "...why not..."}}"""

        client_llm = AsyncOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)

        completion = await client_llm.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
            max_tokens=500,
        )
        raw_text = completion.choices[0].message.content.strip()
        text = raw_text
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0]
        llm_result = json.loads(text.strip())

        print(f"LLM decided: {json.dumps(llm_result, indent=2)}\n")

        output_type = llm_result.get("output_type")
        if not output_type:
            print(f"No matching capability. Reasoning: {llm_result.get('reasoning')}")
            return

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{RUNTIME_URL}/resolve",
                json={"output_type": output_type, "inputs": llm_result.get("inputs", {})},
            )
            result = resp.json()

        print(f"Chain: {' → '.join(result.get('resolved_chain', []))}")
        print(f"\nResult: {json.dumps(result.get('result', {}).get('final', {}), indent=2)}")

        # Format with LLM
        format_prompt = f"""Given this data: {json.dumps(result.get("result", {}), indent=2)}
Write a clear, concise answer to: "{question}"
Be specific with numbers and details. Keep it to 2-3 sentences."""

        format_completion = await client_llm.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": format_prompt}],
            max_tokens=300,
        )
        answer = format_completion.choices[0].message.content.strip()
        print(f"\nAnswer: {answer}")

    asyncio.run(main())
