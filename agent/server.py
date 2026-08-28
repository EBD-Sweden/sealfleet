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
from fastapi import FastAPI, HTTPException, Request
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

# Model-name prefixes the caller pinned deliberately. We cannot know whether the
# model behind one reasons before answering, so assume it might.
PINNED_PREFIXES = ("local/", "byo/")


def needs_generous_budget(model: str | None) -> bool:
    return (model or "").startswith(PINNED_PREFIXES)
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
    # Which stored credential backs a "byo/<provider>/<model>" choice. Not
    # trusted from the client: the router resolves it tenant-scoped as the
    # logged-in user, so a forged id from another tenant simply fails.
    credential_id: str | None = None
    # For a "byo/custom/…" model: the endpoint to call. Not a secret; the
    # credential it pairs with is still resolved server-side.
    base_url: str | None = None


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


# Identity the portal delegates for the logged-in user. The router trusts these
# only for API keys with allow_identity_delegation, so the agent's own key still
# gates the call — these decide *who* it is authorized as.
DELEGATION_HEADERS = (
    "X-McpFinder-User-Id",
    "X-McpFinder-Tenant-Id",
    "X-McpFinder-Groups",
)

# Reserved credential scope: assigned_mcp = "llm:<provider>[:<model>]".
LLM_CRED_PREFIX = "llm:"

# Used when a credential names a provider but no model.
PROVIDER_DEFAULT_MODEL = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-6",
    "groq": "llama-3.3-70b-versatile",
    "mistral": "mistral-large-latest",
    "deepseek": "deepseek-chat",
    "nvidia": "nvidia/llama-3.3-nemotron-super-49b-v1",
}


def delegated(request: Request) -> dict:
    """Pass the caller's identity through to the router, if the portal sent it."""
    return {
        h: request.headers[h]
        for h in DELEGATION_HEADERS
        if request.headers.get(h)
    }


async def fetch_llm_credentials(identity: dict) -> list[dict]:
    """The tenant's own LLM provider credentials, as selectable brains.

    Runs as the logged-in user: the agent's operator key is deliberately denied
    credential.* , so without delegated identity this returns nothing rather
    than escalating.
    """
    if not identity:
        return []
    try:
        resp = await http.get(f"{ROUTER_URL}/credentials", headers=identity)
        if resp.status_code == 403:
            return []  # user may not read credentials — not an error
        resp.raise_for_status()
        creds = resp.json().get("credentials", [])
    except Exception as e:
        print(f"[core-agent] credential listing unavailable: {e}")
        return []

    brains = []
    for c in creds:
        scope = (c.get("assigned_mcp") or "")
        if not scope.startswith(LLM_CRED_PREFIX) or not c.get("is_active", True):
            continue
        # Scope grammar:
        #   llm:<provider>                     — provider default model
        #   llm:<provider>:<model>
        #   llm:custom:<model>@<base_url>      — a private/self-hosted endpoint
        parts = scope[len(LLM_CRED_PREFIX):].split(":", 1)
        provider = parts[0].strip().lower()
        remainder = parts[1].strip() if len(parts) > 1 else ""

        base_url = ""
        if "@" in remainder:
            # Split on the LAST "@" so a model name may itself contain one.
            remainder, base_url = remainder.rsplit("@", 1)
            remainder, base_url = remainder.strip(), base_url.strip()

        model = remainder or PROVIDER_DEFAULT_MODEL.get(provider, "")
        if not provider or not model:
            continue
        if provider == "custom" and not base_url:
            print(f"[core-agent] credential {c.get('name')!r}: custom provider needs @<base_url>")
            continue

        brains.append({
            "id": f"byo/{provider}/{model}",
            "label": c.get("name") or provider,
            "kind": "byo",
            "description": (
                f"Your own endpoint ({model})." if provider == "custom"
                else f"Your own {provider} account ({model})."
            ),
            "credential_id": c.get("id"),
            **({"base_url": base_url} if base_url else {}),
        })
    return brains


async def resolve_llm_key(credential_id: str, identity: dict) -> str | None:
    """Unwrap a tenant LLM key, authorized as the logged-in user."""
    try:
        resp = await http.post(
            f"{ROUTER_URL}/credentials/{credential_id}/use", headers=identity
        )
        resp.raise_for_status()
        return resp.json().get("value")
    except Exception as e:
        # Never include the response body — it carries the secret on success.
        print(f"[core-agent] credential resolve failed: {type(e).__name__}")
        return None


# A pseudo-tool offered alongside real pipelines so the same selection call can
# route "build me a pipeline that…" without a separate intent classifier.
CREATE_PIPELINE_TOOL = "__create_pipeline__"


async def fetch_mcp_tools(identity: dict | None = None) -> list[dict]:
    """Callable MCP tools with their input shapes — the vocabulary for authoring."""
    try:
        resp = await http.get(f"{ROUTER_URL}/mcp-tools", headers=identity)
        resp.raise_for_status()
        return resp.json().get("mcps", [])
    except Exception as e:
        print(f"[core-agent] mcp tool catalog unavailable: {e}")
        return []


def _format_mcp_catalog(mcps: list[dict]) -> str:
    lines = []
    for m in mcps:
        for t in m.get("tools", []):
            detail = t.get("inputs") or t.get("description") or ""
            lines.append(f"- mcp: {m['mcp']}, tool: {t['name']}, inputs: {detail}")
    return "\n".join(lines) or "(none available)"


async def author_pipeline(
    question: str, mcps: list[dict], model: str | None, llm_headers: dict | None
) -> tuple[dict | None, str]:
    """Ask the model for a v2 pipeline definition. Returns (pipeline_def, note)."""
    catalog = _format_mcp_catalog(mcps)
    prompt = f"""You author McpFinder v2 pipelines. Build one for this request:
"{question}"

You may ONLY use these MCP tools:
{catalog}

Reply with ONLY a JSON object (no markdown fence) in this exact shape:
{{
  "name": "snake_case_name",
  "version": 2,
  "description": "one sentence describing when to use this pipeline",
  "inputs": {{"some_input": {{"type": "string|number|list", "default": <value>, "description": "..."}}}},
  "steps": [
    {{"id": "step_id", "mcp": "<mcp name>", "tool": "<tool name>",
     "inputs": {{"tool_arg": "{{{{inputs.some_input}}}}"}}}}
  ],
  "output": {{"result": "{{{{steps.step_id.output}}}}"}}
}}

Rules:
- name must match ^[A-Za-z0-9_-]{{1,128}}$
- every step needs id, mcp and tool, all from the list above
- reference a pipeline input as {{{{inputs.<name>}}}} and an earlier step as {{{{steps.<id>.output}}}}
- give every input a default so the pipeline can run with no arguments"""

    selected = model or OPENAI_MODEL
    generous = needs_generous_budget(selected)
    # A reasoning model spends part of this thinking before it writes the
    # definition, so the budget is well above the ~800 tokens a definition needs.
    max_tokens = (LOCAL_LLM_MAX_TOKENS * 4) if generous else 1500
    timeout = (LOCAL_LLM_TIMEOUT if generous else LLM_TIMEOUT) * 2

    async def _complete(messages: list[dict], json_mode: bool):
        kwargs = dict(
            model=selected, messages=messages, max_tokens=max_tokens,
            temperature=0, extra_headers=llm_headers or None,
        )
        # Guided decoding guarantees syntactically valid JSON where the server
        # supports it (vLLM/NIM, OpenAI, most OpenAI-compatible providers).
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = await asyncio.wait_for(llm.chat.completions.create(**kwargs), timeout=timeout)
        choice = resp.choices[0]
        return (
            (choice.message.content or "").strip(),
            choice.finish_reason,
            len(getattr(choice.message, "reasoning", None) or ""),
        )

    messages = [{"role": "user", "content": prompt}]
    try:
        try:
            text, finish, thought = await _complete(messages, json_mode=True)
        except Exception as e:
            # Not every provider accepts response_format; fall back rather than fail.
            print(f"[core-agent] json_mode rejected ({type(e).__name__}); retrying unconstrained")
            text, finish, thought = await _complete(messages, json_mode=False)
    except Exception as e:
        return None, f"Could not author a pipeline: {e}"

    def _extract(raw: str) -> dict | None:
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rsplit("```", 1)[0]
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            return None

    definition = _extract(text)
    if definition is not None:
        return definition, ""

    if not text or text.find("{") == -1:
        detail = f"finish_reason={finish}, content={len(text)} chars, reasoning={thought} chars"
        if finish == "length":
            detail += " — the token budget ran out before the definition; raise LOCAL_LLM_MAX_TOKENS"
        print(f"[core-agent] authoring produced no definition ({detail})")
        return None, f"The model did not return a pipeline definition ({detail})."

    # Near-JSON: one repair pass, handing the model its own output and the error.
    try:
        json.loads(text[text.find("{"):text.rfind("}") + 1])
        err = "unknown"
    except json.JSONDecodeError as e:
        err = str(e)
    repair = messages + [
        {"role": "assistant", "content": text},
        {"role": "user", "content": f"That JSON is invalid ({err}). Return the corrected JSON object only."},
    ]
    try:
        fixed, _, _ = await _complete(repair, json_mode=True)
    except Exception:
        try:
            fixed, _, _ = await _complete(repair, json_mode=False)
        except Exception as e:
            return None, f"The model returned invalid JSON ({err}) and the repair attempt failed: {e}"
    definition = _extract(fixed)
    if definition is not None:
        print("[core-agent] authoring succeeded after one JSON repair pass")
        return definition, ""
    return None, f"The model returned invalid JSON even after a repair attempt: {err}"


async def deploy_pipeline(pipeline_def: dict, identity: dict) -> tuple[bool, str]:
    """Register an authored pipeline. Needs pipeline.deploy — it writes to the
    server and joins the shared pipeline set, so it is deliberately privileged."""
    try:
        resp = await http.post(
            f"{ROUTER_URL}/v2/pipelines/deploy",
            json={"pipeline": pipeline_def},
            headers=identity,
        )
    except Exception as e:
        return False, f"Deploy request failed: {e}"

    if resp.status_code == 403:
        return False, "You do not have permission to create pipelines (pipeline.deploy)."
    if resp.status_code >= 400:
        # The router's validation errors are the useful part here.
        return False, f"Pipeline rejected: {resp.text[:300]}"
    return True, ""


def coerce_arguments(arguments: dict, input_schema: dict | None) -> dict:
    """Bend LLM-extracted arguments to the pipeline's declared JSON-schema types.

    Models routinely hand back `"Stockholm, Barcelona"` for an array input or
    `"27"` for a number. The MCP behind the pipeline then rejects the call with
    a 400, and the user is told "the request was invalid" for a question that
    was perfectly clear. Fixing the shape here is deterministic and cheap.
    """
    props = (input_schema or {}).get("properties") or {}
    out: dict = {}
    for key, value in (arguments or {}).items():
        want = (props.get(key) or {}).get("type")
        try:
            if want == "array" and isinstance(value, str):
                parts = re.split(r"\s*(?:,|;|\band\b|&|/)\s*", value.strip())
                value = [x.strip() for x in parts if x.strip()]
            elif want == "array" and not isinstance(value, list):
                value = [value]
            elif want in ("number", "integer") and isinstance(value, str):
                num = float(value.strip().rstrip("%°CcFf").strip())
                value = int(num) if want == "integer" or num.is_integer() else num
            elif want == "boolean" and isinstance(value, str):
                value = value.strip().lower() in ("true", "yes", "1", "on")
            elif want == "string" and isinstance(value, (int, float, bool)):
                value = str(value)
        except (ValueError, AttributeError):
            pass  # leave it; the pipeline will report a clear error
        out[key] = value
    return out


def step_errors(result: dict) -> list[str]:
    """Human-readable errors from a v2 run whose steps failed.

    The router records a failed step as {"error": ...} in its output and still
    returns 200, so a run can "succeed" while every step failed.
    """
    errs = []
    for step_id, r in (result.get("results") or {}).items():
        out = r.get("output") if isinstance(r, dict) else None
        if isinstance(out, dict) and out.get("error"):
            detail = out.get("detail") or ""
            try:  # MCPs return {"detail": "..."} JSON strings; unwrap for the user
                detail = json.loads(detail).get("detail", detail)
            except Exception:
                pass
            where = f"{r.get('mcp')}.{r.get('tool')}" if r.get("mcp") else step_id
            errs.append(f"{where}: {detail or out['error']}")
    return errs


async def fetch_pipeline_tools(identity: dict | None = None) -> list[dict]:
    """Tools the agent can call: named pipelines plus v2 pipelines.

    Both APIs coexist in the router; a deployment may have either or both, so
    merge them and remember which endpoint each tool runs through.
    """
    tools: list[dict] = []

    try:
        resp = await http.get(f"{ROUTER_URL}/pipelines/tools", headers=identity)
        resp.raise_for_status()
        for t in resp.json().get("tools", []):
            tools.append({**t, "_api": "named"})
    except Exception as e:
        print(f"[core-agent] named pipeline listing unavailable: {e}")

    try:
        resp = await http.get(f"{ROUTER_URL}/v2/pipelines", headers=identity)
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


# Budget for the pipeline result inside the answer-writing prompt. ~3k tokens:
# comfortably inside every model we route to, without starving the answer.
RESULT_PROMPT_CHARS = int(os.getenv("RESULT_PROMPT_CHARS", "12000"))


def _fit_result_for_prompt(raw_result, limit: int = RESULT_PROMPT_CHARS) -> str:
    """Serialise a pipeline result so it fits the prompt without losing the summary.

    Cutting the tail of a JSON dump mid-object hands the model mangled data and
    it concludes "no information" — even though the useful part (a ranking, a
    score) usually sits AFTER the bulky raw records. So instead, drop whole
    top-level keys largest-first, leaving a marker, until it fits.
    """
    compact = lambda v: json.dumps(v, separators=(",", ":"), default=str)  # noqa: E731
    text = compact(raw_result)
    if len(text) <= limit or not isinstance(raw_result, dict):
        return text[:limit]

    trimmed = dict(raw_result)
    by_size = sorted(trimmed, key=lambda k: len(compact(trimmed[k])), reverse=True)
    for key in by_size:
        size = len(compact(trimmed[key]))
        trimmed[key] = f"[omitted {size} chars of detail]"
        text = compact(trimmed)
        if len(text) <= limit:
            return text
    return text[:limit]


async def summarize_result(
    question: str, pipeline_name: str, raw_result: dict, model: str | None,
    llm_headers: dict | None = None,
) -> str:
    """Turn a pipeline result into prose. Falls back to the raw dump on any failure.

    Kept to one short call so a failure here costs latency, never the answer.
    """
    selected = model or OPENAI_MODEL
    generous = needs_generous_budget(selected)
    payload = _fit_result_for_prompt(raw_result)
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
                max_tokens=LOCAL_LLM_MAX_TOKENS if generous else LLM_MAX_TOKENS * 2,
                extra_headers=llm_headers or None,
            ),
            timeout=LOCAL_LLM_TIMEOUT if generous else LLM_TIMEOUT,
        )
        content = resp.choices[0].message.content
        if content and content.strip():
            return content.strip()
    except Exception as e:
        print(f"[core-agent] answer formatting failed: {e}")
    return template_answer(pipeline_name, raw_result, "")


async def llm_select_pipeline(question: str, pipeline_tools: list[dict], model: str | None = None, llm_headers: dict | None = None) -> tuple[str | None, dict, str]:
    """Single LLM call to select pipeline and extract arguments.
    Returns (pipeline_name, arguments, reasoning).
    Times out after LLM_TIMEOUT seconds → returns (None, {}, '').
    """
    def _args(t: dict) -> str:
        props = t.get("inputSchema", {}).get("properties", {}) or {}
        return ", ".join(f"{k}: {v.get('type', 'string')}" for k, v in props.items()) or "none"

    tools_desc = "\n".join(
        f"- {t['name']}: {t['description']} (args — {_args(t)})"
        for t in pipeline_tools
    )
    prompt = f"""Available pipeline tools:
{tools_desc}
- {CREATE_PIPELINE_TOOL}: build and register a NEW pipeline. Choose this only
  when the user asks to create, build, add or define a pipeline or workflow —
  never to answer a question an existing tool already covers.

User question: "{question}"

Respond with ONLY valid JSON (no markdown):
{{"tool": "<tool_name>", "arguments": {{"<param>": "<value>"}}}}

Pick the tool whose purpose covers the question's topic. Every argument has a
default, so never return null because the question omits one — pass only what
the question states.
Return null ONLY when no tool's purpose relates to the question at all:
{{"tool": null, "arguments": {{}}}}"""

    selected = model or OPENAI_MODEL
    generous = needs_generous_budget(selected)
    budget_timeout = LOCAL_LLM_TIMEOUT if generous else LLM_TIMEOUT
    budget_tokens = LOCAL_LLM_MAX_TOKENS if generous else LLM_MAX_TOKENS

    try:
        resp = await asyncio.wait_for(
            llm.chat.completions.create(
                model=selected,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=budget_tokens,
                # Tool choice must be reproducible: the same question should
                # pick the same pipeline every time, not depend on sampling.
                temperature=0,
                extra_headers=llm_headers or None,
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


async def run_pipeline(
    name: str, arguments: dict, api: str = "named", identity: dict | None = None
) -> dict:
    """Run a pipeline through whichever router API it came from.

    Carries the delegated identity so audit rows name the real user rather than
    the agent service.
    """
    if api == "v2":
        resp = await http.post(
            f"{ROUTER_URL}/v2/pipelines/run",
            json={"pipeline": name, "inputs": arguments},
            headers=identity,
        )
    else:
        resp = await http.post(
            f"{ROUTER_URL}/pipelines/{name}/run",
            json={"inputs": arguments},
            headers=identity,
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
async def models(request: Request):
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

    # The tenant's own providers, listed as the logged-in user.
    brains.extend(await fetch_llm_credentials(delegated(request)))

    return {
        "default": OPENAI_MODEL,
        "proxy_mode": mode or "unknown",
        "models": brains,
    }


@app.get("/tools")
async def tools(request: Request):
    try:
        pipeline_tools = await fetch_pipeline_tools(delegated(request))
        return {"tools": pipeline_tools}
    except Exception as e:
        raise HTTPException(502, f"Failed to fetch tools from router: {e}")


@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest, request: Request):
    start = time.time()
    identity = delegated(request)

    # A "byo/" model runs on the tenant's own provider, so unwrap their key
    # first — authorized as the logged-in user, never as the agent's own key.
    llm_headers: dict = {}
    if (req.model or "").startswith("byo/"):
        if not req.credential_id:
            raise HTTPException(400, "A bring-your-own model requires credential_id")
        key = await resolve_llm_key(req.credential_id, identity)
        if not key:
            raise HTTPException(
                403, "Could not use that provider credential. Check it is active "
                     "and that you have permission to use it."
            )
        llm_headers["X-McpFinder-LLM-Key"] = key
        if req.base_url:
            llm_headers["X-McpFinder-LLM-Base-Url"] = req.base_url

    # 1. Fetch available pipeline tools
    try:
        pipeline_tools = await fetch_pipeline_tools(identity)
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
        req.question, pipeline_tools, req.model, llm_headers
    )

    # 3a. Authoring request: build the pipeline, register it, then run it so the
    #     user sees it working rather than just being told it exists.
    if pipeline_name == CREATE_PIPELINE_TOOL:
        mcps = await fetch_mcp_tools(identity)
        if not mcps:
            raise HTTPException(502, "No MCP tools available to build a pipeline from")

        pipeline_def, note = await author_pipeline(
            req.question, mcps, req.model, llm_headers
        )
        if not pipeline_def:
            return AskResponse(
                question=req.question, output_type="pipeline.create", resolved_chain=[],
                inputs_used={}, answer=note, raw_result={},
                reasoning=reasoning + " " + note,
            )

        ok, err = await deploy_pipeline(pipeline_def, identity)
        if not ok:
            return AskResponse(
                question=req.question, output_type="pipeline.create", resolved_chain=[],
                inputs_used={}, answer=err,
                raw_result={"attempted": pipeline_def},
                reasoning=reasoning + f" Deploy failed: {err}",
            )

        new_name = pipeline_def["name"]
        try:
            result = await run_pipeline(new_name, {}, "v2", identity)
        except Exception as e:
            return AskResponse(
                question=req.question, output_type="pipeline.create",
                resolved_chain=[], inputs_used={},
                answer=f"Created pipeline '{new_name}', but the first run failed: {e}",
                raw_result={"pipeline": pipeline_def},
                reasoning=reasoning + " Created but first run failed.",
            )

        results = result.get("results") or {}
        chain = [
            f"{r['mcp']}.{r['tool']}" if r.get("mcp") else step_id
            for step_id, r in results.items() if isinstance(r, dict)
        ]
        steps = len(pipeline_def.get("steps", []))
        return AskResponse(
            question=req.question, output_type="pipeline.create",
            resolved_chain=chain, inputs_used=pipeline_def.get("inputs", {}),
            answer=(
                f"Created pipeline '{new_name}' with {steps} step(s) and ran it "
                f"successfully. You can now ask for it by name."
            ),
            raw_result={"pipeline": pipeline_def, "first_run": result.get("output") or result},
            reasoning=reasoning + f" Authored and deployed '{new_name}'.",
        )

    # 3b. Keyword fallback if LLM didn't resolve
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

    # 4. Execute pipeline via the API it was listed from, with arguments bent
    #    to its declared input types first.
    selected_tool = next((t for t in pipeline_tools if t.get("name") == pipeline_name), {})
    pipeline_api = selected_tool.get("_api", "named")
    tool_arguments = coerce_arguments(tool_arguments, selected_tool.get("inputSchema"))
    try:
        result = await run_pipeline(pipeline_name, tool_arguments, pipeline_api, identity)
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

    failures = step_errors(result) if pipeline_api == "v2" else []
    if failures:
        answer = (
            f"I ran '{pipeline_name}' but it could not complete:\n- "
            + "\n- ".join(failures)
        )
        reasoning += " Pipeline steps reported errors."
    else:
        answer = await summarize_result(
            req.question, pipeline_name, raw_result, req.model, llm_headers
        )

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
