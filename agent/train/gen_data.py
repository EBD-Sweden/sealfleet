"""Generate LoRA SFT data for the Sealfleet chat planner.

Every example is rendered with the agent's own prompt builders
(agent._selection_prompt, agent._platform_messages) and the live MCP control
tool schemas from catalog.json (snapshot_catalog.py), so the model is trained
on exactly what production sends. Labels are programmatic — no teacher model.

Tasks:
  select    — selection prompt -> {"tool": ..., "arguments": {...}} JSON.
              Pipeline catalogs are randomised (real + synthetic pipelines) so
              the model learns to route, not to memorise two pipelines.
  platform  — platform loop: first tool call, and full trajectories
              (tool call -> tool result -> ... -> final answer). Read-only
              results are real router outputs; write results use the router's
              real response shapes.

Weighted toward the weak spots measured by agent/eval: "run X as a background
job", job follow-ups ("is it done?"), create-vs-run, and argument fidelity.
Benchmark questions (agent/eval/cases.yaml) are excluded.

    python agent/train/gen_data.py --catalog agent/train/catalog.json \
        --out /srv/spark-share/datasets/sealfleet-tools --n-select 3000 --n-platform 2500

Output: train.jsonl / val.jsonl, one {"messages", "tools", "meta"} per line
(OpenAI chat format; assistant tool calls as `tool_calls`).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import secrets
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agent/
import server  # noqa: E402  (agent/server.py — prompt builders only)

PLATFORM = server.PLATFORM_TOOL
CREATE = server.CREATE_PIPELINE_TOOL

# ---------------------------------------------------------------------------
# Entity pools
# ---------------------------------------------------------------------------

CITIES = ["Stockholm", "Gothenburg", "Malmö", "Uppsala", "Oslo", "Bergen", "Copenhagen", "Aarhus",
          "Helsinki", "Tampere", "Reykjavik", "Tallinn", "Riga", "Vilnius", "Berlin", "Munich",
          "Hamburg", "Paris", "Lyon", "Nice", "Marseille", "Madrid", "Barcelona", "Seville",
          "Valencia", "Lisbon", "Porto", "Rome", "Milan", "Naples", "Florence", "Athens",
          "Thessaloniki", "Vienna", "Prague", "Budapest", "Warsaw", "Krakow", "Amsterdam",
          "Brussels", "Dublin", "London", "Edinburgh", "Zurich", "Geneva", "Split", "Dubrovnik",
          "Istanbul", "Antalya", "Valletta", "Palermo", "Malaga", "Faro", "Bordeaux"]
SYMBOLS = ["BTC", "ETH", "SOL", "ADA", "XRP", "DOGE", "AVAX", "DOT", "LINK", "MATIC", "ATOM", "LTC"]
QUOTES = ["USDT", "USD", "EUR", "SEK"]
EXCHANGES = ["binance", "kraken", "coinbase", "bitstamp", "okx"]
CURRENCIES = ["USD", "EUR", "SEK", "NOK", "DKK", "GBP", "CHF", "JPY"]
TICKERS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "ERIC", "VOLV-B", "SAND", "ASML", "SAP"]
COMPANIES = ["Acme Ltd", "Nordic Parts AB", "Globex", "Initech", "Umbrella Corp", "Stark Supplies",
             "Wayne Logistics", "Hooli", "Vandelay Imports", "Soylent AB", "Tyrell GmbH"]
COUNTRIES = ["SE", "NO", "DK", "FI", "DE", "FR", "US", "GB", "NL", "PL"]
TOPICS = ["AI regulation", "Nordic energy prices", "bitcoin ETFs", "EU fintech", "chip supply",
          "interest rates", "shipping costs", "carbon credits"]
REAL_MCPS_FALLBACK = ["weather-trip-mcp", "demo-sandbox-mcp"]
SYNTH_MCPS = ["fx-mcp", "binance-mcp", "kraken-mcp", "sec-search-mcp", "news-mcp", "ledger-mcp",
              "crm-mcp", "github-mcp", "slack-notify-mcp", "pricing-mcp", "risk-mcp", "docs-mcp"]
SYNTH_PIPELINES = ["crypto_price_lookup", "fx_convert", "portfolio_summary", "news_digest",
                   "sec_filing_search", "vendor_risk_score", "arbitrage_scan", "nordic_weather",
                   "daily_market_brief", "invoice_batch_check"]
REPOS = ["https://github.com/acme/{n}", "https://github.com/ebd-labs/{n}", "https://gitlab.com/tools/{n}",
         "https://github.com/example-org/{n}"]


def hexid(n: int = 16) -> str:
    return secrets.token_hex(n // 2)


def pick(xs, k=None):
    return random.choice(xs) if k is None else random.sample(xs, k)


def city_list(k: int) -> list[str]:
    return random.sample(CITIES, k)


def join_and(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + random.choice([" and ", " or ", " & ", ", "]) + xs[-1]


# ---------------------------------------------------------------------------
# Pipeline catalog: real pipelines (with templates) + synthetic ones
# Each spec: name, description, inputSchema, questions(): -> [(question, args)]
# ---------------------------------------------------------------------------

def _schema(**props) -> dict:
    return {"type": "object", "properties": props}


def q_weather():
    k = random.choice([1, 1, 2, 2, 3])
    cs = city_list(k)
    c = join_and(cs)
    args = {"cities": cs}
    opts = []
    if k == 1:
        opts += [f"What's the weather like in {c}?", f"weather forecast for {c}", f"Will it be sunny in {c} this week?",
                 f"How warm is it in {c} at the moment?", f"Give me the {c} forecast for the coming days",
                 f"Is it raining in {c}?"]
    else:
        opts += [f"Compare the weather in {c}", f"Where is it warmer this week: {c}?", f"Rank {c} for a trip by weather",
                 f"Which city has the best weather, {c}?", f"I'm choosing between {c} for a holiday — which has nicer weather?",
                 f"weather {c} compare"]
    q = random.choice(opts)
    r = random.random()
    if r < 0.2:
        t = random.choice([18, 20, 22, 24, 25, 26, 28, 30])
        q += random.choice([f", I'd like around {t} degrees", f" — target {t}°C", f" (ideal temperature {t}C)"])
        args["target_temp_c"] = t
    elif r < 0.33:
        w = random.choice([10, 12, 15, 20, 25, 30])
        q += random.choice([f", wind below {w} km/h", f" but max wind {w} kph", f" with wind under {w} km/h please"])
        args["max_wind_kph"] = w
    return q, args


def q_invoice():
    inv = f"INV-{random.randint(1000, 9999)}"
    amt = random.choice([120, 950, 1800, 4200, 7300, 12500, 64000])
    v = pick(COMPANIES)
    args = {"invoice_id": inv}
    q = random.choice([f"Check invoice {inv}", f"Review the fake invoice {inv}", f"Summarize sandbox invoice {inv}"])
    if random.random() < 0.7:
        q += random.choice([f" for {amt} USD", f" of ${amt}", f" amounting to {amt} dollars"])
        args["amount_usd"] = amt
    if random.random() < 0.7:
        q += random.choice([f" from {v}", f" issued by {v}", f", vendor {v}"])
        args["vendor_name"] = v
    if random.random() < 0.25:
        c = pick(COUNTRIES)
        q += f" ({c})"
        args["country"] = c
    return q, args


def q_crypto():
    sym, x, quote = pick(SYMBOLS), pick(EXCHANGES), pick(QUOTES)
    return random.choice([
        (f"What's the {sym} price right now?", {"symbol": sym}),
        (f"{sym} price on {x}", {"symbol": sym, "exchange": x}),
        (f"How much is {sym} in {quote} today?", {"symbol": sym, "quote": quote}),
    ])


def q_portfolio():
    period, account = pick(["7d", "30d", "90d", "1y"]), pick(["main", "hedge", "savings", "bot-1"])
    return random.choice([
        ("How is my portfolio doing?", {}),
        (f"Show my {period} portfolio performance", {"period": period}),
        (f"Summarize the {account} account", {"account": account}),
    ])


SYNTH_SPECS = [
    {"name": "crypto_price_lookup", "description": "Current price and 24h change for a crypto asset on one exchange.",
     "inputSchema": _schema(symbol={"type": "string"}, quote={"type": "string", "default": "USDT"},
                            exchange={"type": "string", "default": "binance"}),
     "q": lambda: q_crypto()},
    {"name": "fx_convert", "description": "Convert an amount between two fiat currencies at the latest rate.",
     "inputSchema": _schema(amount={"type": "number"}, from_currency={"type": "string"}, to_currency={"type": "string"}),
     "q": lambda: (lambda a, f, t: random.choice([
         (f"Convert {a} {f} to {t}", {"amount": a, "from_currency": f, "to_currency": t}),
         (f"How many {t} is {a} {f}?", {"amount": a, "from_currency": f, "to_currency": t}),
         (f"{a} {f} in {t}?", {"amount": a, "from_currency": f, "to_currency": t})]))(
         random.choice([50, 100, 250, 1000, 1500, 9999]), *random.sample(CURRENCIES, 2))},
    {"name": "portfolio_summary", "description": "Balances, allocation and P&L for a trading account.",
     "inputSchema": _schema(account={"type": "string", "default": "main"}, period={"type": "string", "default": "30d"}),
     "q": lambda: q_portfolio()},
    {"name": "news_digest", "description": "Summarize recent news articles on a topic.",
     "inputSchema": _schema(topic={"type": "string"}, max_items={"type": "integer", "default": 5}),
     "q": lambda: (lambda t, n: random.choice([
         (f"What's the latest news on {t}?", {"topic": t}),
         (f"Give me the top {n} headlines about {t}", {"topic": t, "max_items": n}),
         (f"news digest {t}", {"topic": t})]))(pick(TOPICS), random.choice([3, 5, 10]))},
    {"name": "sec_filing_search", "description": "Search SEC filings and return cited passages.",
     "inputSchema": _schema(query={"type": "string"}, limit={"type": "integer", "default": 10}),
     "q": lambda: (lambda t, n: random.choice([
         (f"Find SEC filings mentioning {t}", {"query": t}),
         (f"Search 10-Ks for {t}, top {n}", {"query": t, "limit": n}),
         (f"What do SEC filings say about {t}?", {"query": t})]))(pick(["supply chain risk", "share buybacks",
                                                                    "AI capex", "going concern", "Nvidia exposure"]),
                                                               random.choice([3, 5, 20]))},
    {"name": "vendor_risk_score", "description": "Score a supplier's risk from registry and sanctions data.",
     "inputSchema": _schema(vendor_name={"type": "string"}, country={"type": "string", "default": "SE"}),
     "q": lambda: (lambda v, c: random.choice([
         (f"How risky is {v} as a supplier?", {"vendor_name": v}),
         (f"Risk score for vendor {v} in {c}", {"vendor_name": v, "country": c})]))(pick(COMPANIES), pick(COUNTRIES))},
    {"name": "arbitrage_scan", "description": "Compare a crypto asset's price across exchanges for arbitrage gaps.",
     "inputSchema": _schema(symbol={"type": "string"}, exchanges={"type": "array", "items": {"type": "string"}}),
     "q": lambda: (lambda s, xs: random.choice([
         (f"Any arbitrage on {s} between {join_and(xs)}?", {"symbol": s, "exchanges": xs}),
         (f"Scan {s} prices across exchanges", {"symbol": s})]))(pick(SYMBOLS), random.sample(EXCHANGES, 2))},
    {"name": "stock_quote", "description": "Latest quote and day range for a listed stock.",
     "inputSchema": _schema(ticker={"type": "string"}),
     "q": lambda: (lambda t: random.choice([(f"Quote for {t}", {"ticker": t}), (f"How is {t} trading today?", {"ticker": t}),
                                            (f"{t} share price?", {"ticker": t})]))(pick(TICKERS))},
    {"name": "flight_status", "description": "Live status of a flight by flight number.",
     "inputSchema": _schema(flight_number={"type": "string"}),
     "q": lambda: (lambda f: random.choice([(f"Is flight {f} on time?", {"flight_number": f}),
                                            (f"status of {f}", {"flight_number": f})]))(
         f"{pick(['SK', 'DY', 'LH', 'AY', 'BA'])}{random.randint(100, 2999)}")},
    {"name": "translate_text", "description": "Translate a short text to a target language.",
     "inputSchema": _schema(text={"type": "string"}, target_lang={"type": "string"}),
     "q": lambda: (lambda t, l: random.choice([(f"Translate '{t}' to {l}", {"text": t, "target_lang": l}),
                                               (f"How do you say '{t}' in {l}?", {"text": t, "target_lang": l})]))(
         pick(["good morning", "the invoice is overdue", "see you tomorrow", "thank you for your order"]),
         pick(["Swedish", "German", "French", "Spanish", "Finnish"]))},
]

REAL_SPECS = {"weather_trip_planner": q_weather, "demo_sandbox_invoice_review": q_invoice}


def build_catalog(real_tools: list[dict]) -> tuple[list[dict], dict]:
    """A randomised pipeline catalog for one selection example."""
    tools, gens = [], {}
    for t in real_tools:
        if t["name"] in REAL_SPECS and random.random() < 0.85:
            tools.append(t)
            gens[t["name"]] = REAL_SPECS[t["name"]]
    for s in random.sample(SYNTH_SPECS, random.randint(0, 5)):
        tools.append({"name": s["name"], "description": s["description"], "inputSchema": s["inputSchema"]})
        gens[s["name"]] = s["q"]
    if not tools:
        return build_catalog(real_tools)
    random.shuffle(tools)
    return tools, gens


def gen_pipeline_question(gens: dict) -> tuple[str, str, dict]:
    name = random.choice(list(gens))
    while True:
        out = gens[name]()
        if out:
            q, args = out
            return name, q, args


# ---------------------------------------------------------------------------
# Selection examples
# ---------------------------------------------------------------------------

CREATE_TEMPLATES = [
    "Create a pipeline that {does}", "Build a new workflow that {does}", "Add a pipeline called {name} that {does}",
    "Make me a pipeline to {do}", "Define a workflow which {does}", "Can you set up a new pipeline that {does}?",
    "I need a new pipeline: {do}", "Write a pipeline named {name} to {do}", "create workflow: {do}",
    "Compose a pipeline that chains tools to {do}",
]
CREATE_TASKS = [
    ("fetch the weather for {c} and rank them", "fetches the weather for {c} and ranks them"),
    ("check an invoice and then score the vendor", "checks an invoice and then scores the vendor"),
    ("get the {s} price and convert it to SEK", "gets the {s} price and converts it to SEK"),
    ("summarize news about {t} every morning", "summarizes news about {t} every morning"),
    ("compare {c} weather and post the best city to Slack", "compares {c} weather and posts the best city to Slack"),
    ("look up SEC filings for {k} and summarize risks", "looks up SEC filings for {k} and summarizes risks"),
]
NONE_QUESTIONS = [
    "Write me a haiku about autumn", "Tell me a joke", "What is 17 times 23?", "Who wrote Pride and Prejudice?",
    "How do I boil an egg?", "Explain quantum entanglement simply", "What's your favourite colour?",
    "Translate nothing, just say hi", "What year did the Berlin Wall fall?", "Recommend a good sci-fi book",
    "How tall is Mount Everest?", "hello!", "thanks, that's all", "What does HTTP 418 mean?",
]
PLATFORM_Q = {
    "job": ["Run {p} for {x} as a background job", "Start {p} in the background for {x}", "Queue a job running {p} for {x}",
            "Kick off {p} asynchronously for {x}", "Launch {p} as a job for {x} and give me the job id",
            "Run {p} for {x} in the background, I'll check later", "Schedule {p} to run now as a background job for {x}",
            "async run of {p} for {x} please", "Can you run {p} for {x} as a job?"],
    "job_status": ["What's the status of job {j}?", "Is job {j} finished?", "check job {j}", "Did job {j} fail?",
                   "Show me the result of job {j}"],
    "job_list": ["Which jobs are running?", "List my failed jobs", "Show recent jobs", "Any jobs still queued?"],
    "job_cancel": ["Cancel job {j}", "Stop job {j}", "Kill the running job {j}"],
    "mcps": ["Which MCP servers are registered?", "List all MCPs", "What MCP servers do we have?",
             "Show me the registered MCP servers and their tools", "what mcps are available"],
    "mcp_get": ["Show the manifest of {m}", "What tools does {m} expose?", "Details for MCP {m}"],
    "deploy": ["Deploy an MCP from {r}", "Deploy {r} as an MCP called {m}", "Build and register the MCP at {r}"],
    "register": ["Register the MCP running at http://{m}:8080 as {m}"],
    "scale": ["Scale {m} down to zero", "Wake up {m}", "Is {m} scaled to zero?", "Scale up {m}"],
    "creds": ["Which credentials do I have?", "List my stored secrets", "Store my {e} API key as {e}-key, value {v}",
              "Delete credential {j}"],
    "policy": ["What policy rules are active?", "Would calling {tool} on {m} be allowed?", "Reload the policies"],
    "audit": ["Show recent audit events", "What happened on the platform today?", "Show the last 10 audit log entries"],
    "health": ["Is the platform up?", "Platform status", "Is everything healthy?", "How is the runtime doing?"],
    "pipelines": ["Which pipelines exist?", "List the v2 pipelines", "Show the definition of the {p} pipeline",
                  "Delete the pipeline {p}", "Reload pipelines from disk"],
    "registry": ["Export the registry bundle"],
}
SELECT_WEIGHTS = {"run": 0.36, "create": 0.12, "platform": 0.34, "none": 0.06, "followup": 0.12}


def wpick(weights: dict) -> str:
    return random.choices(list(weights), weights=list(weights.values()))[0]


def fill(t: str, ctx: dict) -> str:
    return t.format(**ctx)


def entity_ctx(gens: dict | None = None, pipeline_names: list[str] | None = None, mcps: list[str] | None = None) -> dict:
    p = pick(pipeline_names or (list(gens) if gens else SYNTH_PIPELINES))
    return {"p": p, "x": join_and(city_list(random.randint(1, 2))), "j": hexid(), "m": pick(mcps or SYNTH_MCPS),
            "r": pick(REPOS).format(n=pick(SYNTH_MCPS)), "e": pick(["binance", "kraken", "openai", "slack"]),
            "v": "sk_" + hexid(12), "tool": pick(["fetch_cities_weather", "rank_cities", "get_ticker", "search"]),
            "c": join_and(city_list(2)), "s": pick(SYMBOLS), "t": pick(TOPICS), "k": pick(TICKERS),
            "name": pick(SYNTH_PIPELINES) + "_" + pick(["v2", "daily", "eu", "test"])}


def selection_target(tool, args) -> str:
    return json.dumps({"tool": tool, "arguments": args or {}}, ensure_ascii=False)


def gen_select(real_tools: list[dict], mcps: list[str]) -> dict:
    tools, gens = build_catalog(real_tools)
    kind = wpick(SELECT_WEIGHTS)
    history = None
    if kind == "run":
        name, q, args = gen_pipeline_question(gens)
        target = (name, args)
    elif kind == "create":
        ctx = entity_ctx(gens)
        do, does = pick(CREATE_TASKS)
        ctx2 = {**ctx, "do": fill(do, ctx), "does": fill(does, ctx)}
        q = fill(pick(CREATE_TEMPLATES), ctx2)
        target = (CREATE, {})
    elif kind == "platform":
        # Not "pipeline_run" (running a pipeline now routes to the pipeline itself)
        # and not bare follow-ups (ambiguous without history).
        pk = random.choice(list(PLATFORM_Q) + [k for k in PHRASES
                                               if k not in ("deploy_check", "pipeline_run", "job_status_hist")])
        bank = PLATFORM_Q.get(pk) or PHRASES[pk]
        ctx = entity_ctx(gens, mcps=mcps)
        ctx.update(mm=ctx["m"], sp=pick(SYNTH_PIPELINES), n="adhoc")
        q = fill(pick(bank), ctx)
        target = (PLATFORM, {})
    elif kind == "none":
        q, target = pick(NONE_QUESTIONS), (None, {})
    else:  # follow-up with history
        name, q0, args0 = gen_pipeline_question(gens)
        sub = random.random()
        if sub < 0.4 and "cities" in args0:
            new = [c for c in city_list(3) if c not in args0["cities"]][:1]
            history = [{"role": "user", "content": q0},
                       {"role": "agent", "content": f"Here is the forecast for {join_and(args0['cities'])}: mostly mild."}]
            q = random.choice([f"and {new[0]}?", f"what about {new[0]}?", f"same for {new[0]}", f"now {new[0]}"])
            target = (name, {"cities": new})
        elif sub < 0.7:
            j = hexid()
            history = [{"role": "user", "content": f"Run {name} as a background job"},
                       {"role": "agent", "content": f"Started job {j} for {name}."}]
            q = random.choice(["is it done?", "is it done yet?", "status?", "did it finish?", "check on that job",
                               "what was the result?", "any update on the job?"])
            target = (PLATFORM, {})
        else:
            history = [{"role": "user", "content": q0}, {"role": "agent", "content": f"Done — ran {name}."}]
            q = random.choice(["run it again as a background job", "do that again but in the background",
                               "now run the same thing as a job", "queue that as a job instead"])
            target = (PLATFORM, {})
    prompt = server._selection_prompt(q, tools, history)
    return {"messages": [{"role": "user", "content": prompt},
                         {"role": "assistant", "content": selection_target(*target)}],
            "tools": None, "meta": {"task": "select", "kind": kind, "q": q}}


# ---------------------------------------------------------------------------
# Platform-loop examples
# ---------------------------------------------------------------------------

def tool_call(name: str, args: dict) -> dict:
    return {"id": "call_" + hexid(12), "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def tool_result_msg(call: dict, content: str) -> dict:
    return {"role": "tool", "tool_call_id": call["id"], "content": content}


def obs(catalog: dict, tool: str, args: dict | None = None) -> dict | None:
    for o in catalog["observations"]:
        if o["tool"] == tool and (args is None or o["arguments"] == args):
            return o
    return None


def _json(o) -> str:
    return json.dumps(o, indent=2, ensure_ascii=False)


def summarize(tool: str, args: dict, result: str, is_error: bool) -> str:
    """Final answer after the last tool result: concise, concrete, no secrets."""
    if is_error:
        m = re.search(r'"detail":\s*"([^"]+)"', result)
        why = m.group(1) if m else result.strip()[:160]
        return random.choice([f"I couldn't do that — the platform said: {why}.",
                              f"That failed: {why}. You may need an admin to grant the missing permission."])
    try:
        data = json.loads(result)
    except json.JSONDecodeError:
        data = None
    if tool == "mcp_list" and isinstance(data, list):
        names = [m.get("name") for m in data if isinstance(m, dict)]
        return f"There are {len(names)} MCP servers registered: {', '.join(names)}."
    if tool == "mcp_tools" and isinstance(data, dict):
        parts = [f"{m['mcp']} ({', '.join(t['name'] for t in m.get('tools', []))})" for m in data.get("mcps", [])]
        return "Callable MCP tools: " + "; ".join(parts) + "."
    if tool == "mcp_get" and isinstance(data, dict):
        tools = [t.get("name") for t in data.get("tools", []) if isinstance(t, dict)]
        return f"{data.get('name')} is at {data.get('endpoint')} and exposes: {', '.join(tools) or 'no tools'}."
    if tool == "pipeline_list" and isinstance(data, dict):
        names = [p.get("name") for p in data.get("pipelines", [])]
        return f"{len(names)} v2 pipelines: {', '.join(names)}."
    if tool == "pipeline_get" and isinstance(data, dict):
        steps = [f"{s.get('mcp')}.{s.get('tool')}" for s in data.get("steps", [])]
        return f"{data.get('name')}: {data.get('description', '')} Steps: {' → '.join(steps)}."
    if tool == "platform_status" and isinstance(data, dict):
        return (f"The platform is {data.get('status')}: {data.get('manifests')} MCP manifests and "
                f"{data.get('named_pipelines', data.get('pipelines', '?'))} named pipelines loaded.")
    if tool == "job_create" and isinstance(data, dict):
        return f"Started job {data['job_id']} ({data.get('status')}). Ask me to check on it any time."
    if tool == "job_get" and isinstance(data, dict):
        st = data.get("status")
        if st == "completed":
            return f"Job {data['job_id']} completed. Result: {json.dumps(data.get('result'))[:200]}"
        if st == "failed":
            return f"Job {data['job_id']} failed: {data.get('error')}."
        return f"Job {data['job_id']} is still {st}."
    if tool == "job_list" and isinstance(data, dict):
        n = data.get("count", len(data.get("jobs", [])))
        return f"There are {n} matching jobs." if n else "No matching jobs."
    if tool == "job_cancel" and isinstance(data, dict):
        return f"Job {data['job_id']} is {data.get('status')}."
    if tool == "scale_status" and isinstance(data, dict):
        return "Scale-to-zero is disabled on this deployment." if not data.get("enabled") else "Scale-to-zero is enabled."
    if tool in ("scale_up", "scale_down"):
        return f"{args.get('mcp')} is now scaled {'up' if tool == 'scale_up' else 'down to zero'}."
    if tool == "mcp_deploy" and isinstance(data, dict):
        return f"Deployment of {args.get('name')} started (status: {data.get('status')})."
    if tool == "credential_create" and isinstance(data, dict):
        return f"Stored the credential {args.get('name')} (encrypted). I won't repeat its value."
    if tool == "pipeline_delete":
        return f"Deleted the pipeline {args.get('name')}."
    if tool == "pipeline_run" and isinstance(data, dict):
        return f"Ran {args.get('name')}: {json.dumps(data.get('result', data))[:220]}"
    if tool == "tool_invoke" and isinstance(data, dict):
        return f"{args.get('mcp')}.{args.get('tool')} returned: {json.dumps(data)[:220]}"
    if tool == "registry_export" and isinstance(data, dict):
        return f"Exported the registry: {len(data.get('mcps', []))} MCPs and {len(data.get('pipelines', []))} pipelines."
    if tool in ("pipeline_reload", "policy_reload") and isinstance(data, dict):
        return f"Reloaded {'pipelines' if tool == 'pipeline_reload' else 'policy rules'} from disk ({data.get('count', 'ok')})."
    if tool == "credential_delete":
        return f"Deleted credential {args.get('credential_id')}."
    if tool == "credential_update":
        return f"Credential {args.get('credential_id')} is now {'active' if args.get('is_active') else 'inactive'}."
    if tool == "mcp_register":
        return f"Registered {args.get('name')} at {args.get('endpoint')}."
    if tool == "policy_check" and isinstance(data, dict):
        return f"Policy {'allows' if data.get('allowed', True) else 'denies'} {args.get('tool')} on {args.get('mcp')}."
    return "Done."


def synth_result(tool: str, args: dict) -> tuple[str, bool]:
    """Router-shaped results for write tools and synthetic entities."""
    if tool == "job_create":
        return _json({"job_id": hexid(), "status": "queued", "name": args.get("name") or args.get("pipeline")}), False
    if tool == "job_get":
        st = random.choice(["completed", "completed", "running", "queued", "failed"])
        d = {"job_id": args["job_id"], "status": st, "pipeline": pick(SYNTH_PIPELINES + list(REAL_SPECS))}
        if st == "completed":
            d["result"] = {"ranking": [{"city": c, "score": round(random.random(), 3)} for c in city_list(2)]}
        if st == "failed":
            d["error"] = random.choice(["MCP weather-trip-mcp timed out", "upstream 502 from fx-mcp", "invalid input: cities"])
        return _json(d), False
    if tool == "job_cancel":
        return _json({"job_id": args["job_id"], "status": "cancelled"}), False
    if tool == "job_list":
        return _json({"jobs": [], "count": 0}), False
    if tool in ("scale_up", "scale_down"):
        return _json({"mcp": args.get("mcp"), "replicas": 1 if tool == "scale_up" else 0}), False
    if tool == "mcp_deploy":
        return _json({"name": args.get("name"), "status": "building", "deployment_id": hexid(8)}), False
    if tool == "credential_create":
        return _json({"credential_id": hexid(), "name": args.get("name"), "status": "stored"}), False
    if tool == "pipeline_delete":
        return _json({"deleted": args.get("name")}), False
    if tool == "pipeline_run":
        return _json({"pipeline": args.get("name"), "status": "completed",
                      "result": {"ranking": [{"city": c, "score": round(random.random(), 3)}
                                             for c in (args.get("inputs") or {}).get("cities", city_list(2))]}}), False
    if tool == "tool_invoke":
        return _json({"mcp": args.get("mcp"), "tool": args.get("tool"), "output": {"ok": True, "items": random.randint(1, 9)}}), False
    if tool == "registry_export":
        if random.random() < 0.5:
            return _json({"detail": "Forbidden: missing permission registry.export"}), True
        return _json({"mcps": [{"name": n} for n in REAL_MCPS_FALLBACK], "pipelines": [{"name": "weather_trip_planner"}]}), False
    if tool in ("pipeline_reload", "policy_reload"):
        if tool == "policy_reload" and random.random() < 0.5:
            return _json({"detail": "Forbidden: missing permission policy.admin"}), True
        return _json({"reloaded": True, "count": random.randint(2, 9)}), False
    if tool in ("credential_delete", "credential_update"):
        return _json({"credential_id": args.get("credential_id"), "status": "deleted" if tool == "credential_delete" else "updated"}), False
    if tool == "mcp_get":
        return _json({"name": args.get("name"), "endpoint": f"http://{args.get('name')}:8080",
                      "tools": [{"name": "query"}, {"name": "status"}]}), False
    return _json({"ok": True}), False


PHRASES = {
    "job": ["Run {p} for {x} as a background job", "Start {p} in the background for {x}", "Queue a job running {p} for {x}",
            "Kick off {p} asynchronously for {x}", "Launch {p} as a job for {x} and give me the job id",
            "Run {p} for {x} in the background, I'll check later", "please start a background run of {p} for {x}",
            "async run of {p} for {x}", "Can you run {p} for {x} as a job?", "Fire off {p} for {x} as a job",
            "I don't want to wait — run {p} for {x} in the background", "Submit {p} ({x}) as a background job",
            "Create a job for {p} with {x}", "Background job: {p}, {x}", "Run {p} as a job for {x}, name it {n}"],
    "job_status_hist": ["is it done?", "is it done yet?", "did it finish?", "what's the status?", "any result yet?",
                        "has that job completed?", "how's the job going?", "check on it", "status of that job?",
                        "did it work?", "is the job still running?", "give me the result"],
    "job_status": ["What's the status of job {j}?", "Is job {j} finished?", "check job {j}", "Did job {j} fail?",
                   "Show me the result of job {j}", "job {j} status", "Has {j} completed?", "What happened to job {j}?",
                   "Get the output of job {j}", "Is {j} still running?"],
    "job_cancel": ["Cancel job {j}", "Stop job {j}", "Kill the running job {j}", "abort {j}", "Please cancel {j}",
                   "Terminate job {j} now"],
    "mcp_list": ["Which MCP servers are registered?", "List all MCPs", "What MCP servers do we have?", "what mcps are available",
                 "Show me every registered MCP", "Which MCPs are connected to the platform?", "list mcp servers",
                 "How many MCP servers are there?", "What integrations are registered?"],
    "mcp_tools": ["Which tools can pipelines use?", "List every MCP tool", "What tools are available across all MCPs?",
                  "Show all callable tools", "Which tools could I build a workflow from?"],
    "mcp_get": ["Show the manifest of {m}", "What tools does {m} expose?", "Details for MCP {m}", "Describe {m}",
                "What's the endpoint of {m}?", "Get the {m} manifest", "Which tools are on {m}?"],
    "deploy": ["Deploy an MCP from {r}", "Deploy {r} as an MCP called {mm}", "Build and register the MCP at {r}",
               "Please deploy {r} (name: {mm})", "Ship {r} to the platform as {mm}", "Install the MCP from {r} as {mm}"],
    "deploy_check": [", then check it's registered", " and confirm it shows up", ", then show its manifest",
                     " and tell me which tools it has"],
    "scale_down": ["Scale {m} down to zero", "Put {m} to sleep", "Scale {m} to 0 replicas", "Shut down {m} until needed"],
    "scale_up": ["Wake up {m}", "Scale up {m}", "Bring {m} back up", "Start {m} again"],
    "scale_status": ["Which MCPs are scaled to zero?", "Scale-to-zero status", "Is anything sleeping right now?",
                     "Show the scaling state of the MCPs"],
    "cred_list": ["Which credentials do I have?", "List my stored secrets", "Show my credentials", "What API keys are saved?",
                  "list credentials"],
    "cred_create": ["Store my {e} API key as {e}-key, the value is {v}", "Save a credential named {e}-key with value {v}",
                    "Add my {e} secret ({v}) under the name {e}-key"],
    "cred_delete": ["Delete credential {j}", "Remove the credential with id {j}"],
    "cred_update": ["Deactivate credential {j}", "Disable the credential {j}"],
    "policy_rules": ["What policy rules are active?", "Show the policy rules", "List the access policies",
                     "Which policies are in force?"],
    "policy_check": ["Would calling {tool} on {m} be allowed by policy?", "Am I allowed to call {tool} on {m}?",
                     "Check policy for {m}.{tool}", "Is {tool} on {m} permitted?"],
    "policy_reload": ["Reload the policies", "Reload policy rules from disk", "Re-read the policy files"],
    "audit": ["Show the last {k} audit events", "Give me the {k} most recent audit log entries",
              "Show {k} audit events"],
    "audit_any": ["What happened on the platform recently?", "Show recent audit events", "Show the audit log"],
    "audit_server": ["Show audit events for {m}", "What calls were made to {m} recently?"],
    "health": ["Is the platform up?", "Platform status", "Is everything healthy?", "How is the runtime doing?",
               "health check", "Is Sealfleet running OK?", "Give me the runtime status"],
    "pipeline_list": ["Which pipelines exist?", "List the v2 pipelines", "What pipelines are deployed?", "Show all pipelines",
                      "list pipelines"],
    "pipeline_get": ["Show the definition of the {p} pipeline", "What steps does {p} have?", "Describe the {p} pipeline",
                     "Get pipeline {p}"],
    "pipeline_delete": ["Delete the pipeline {sp}", "Remove pipeline {sp}", "Get rid of the {sp} pipeline"],
    "pipeline_reload": ["Reload pipelines from disk", "Rescan the pipeline definitions", "Refresh the pipeline list from files"],
    "pipeline_run": ["Run the {p} pipeline for {x} now and give me the result", "Execute {p} synchronously for {x}",
                     "Run {p} for {x} right away and wait for it"],
    "tool_invoke": ["Call {tool} on {m} directly", "Invoke {m}.{tool} with no inputs", "Directly run the {tool} tool on {m}",
                    "Call the {tool} tool of {m}"],
    "registry": ["Export the registry", "Give me a registry export", "Dump the registry bundle", "Export all registry data"],
}
PLATFORM_KIND_WEIGHTS = {"job": 0.24, "job_status": 0.12, "job_list": 0.04, "job_cancel": 0.03, "mcps": 0.07,
                         "mcp_get": 0.05, "deploy": 0.06, "register": 0.02, "scale": 0.05, "creds": 0.05,
                         "policy": 0.04, "audit": 0.04, "health": 0.03, "pipelines": 0.07, "pipeline_run": 0.03,
                         "tool_invoke": 0.04, "registry": 0.02}
MCP_TOOLS = {"weather-trip-mcp": ["fetch_cities_weather", "rank_cities"],
             "demo-sandbox-mcp": ["summarize_fake_invoice", "score_fake_vendor"]}


def platform_case(catalog: dict, pipeline_names: list[str], mcps: list[str]) -> tuple[str, list[tuple[str, dict]], list | None]:
    """(question, [(tool, args), ...], history) for one platform request."""
    p, m, j = pick(pipeline_names), pick(mcps), hexid()
    cs = city_list(random.randint(1, 2))
    ctx = {"p": p, "m": m, "j": j, "x": join_and(cs), "k": random.choice([3, 5, 10, 20]),
           "e": pick(["binance", "kraken", "openai", "slack", "github"]), "v": "sk_" + hexid(12),
           "tool": pick(MCP_TOOLS.get(m, ["query", "search", "status"])), "sp": pick(SYNTH_PIPELINES),
           "n": f"{p}-{pick(['nightly', 'adhoc', 'test'])}"}
    say = lambda key: fill(pick(PHRASES[key]), ctx)
    kind = wpick(PLATFORM_KIND_WEIGHTS)
    if kind in ("job", "pipeline_run"):
        inputs = {"cities": cs} if p == "weather_trip_planner" else {}
        tmpl = pick(PHRASES[kind])
        if not inputs:
            tmpl = tmpl.replace(" for {x}", "").replace(", {x}", "").replace(" with {x}", "").replace(" ({x})", "")
        q = fill(tmpl, ctx)
        if kind == "job":
            a = {"pipeline": p, **({"inputs": inputs} if inputs else {})}
            if "name it" in tmpl:
                a["name"] = ctx["n"]
            return q, [("job_create", a)], None
        return q, [("pipeline_run", {"name": p, **({"inputs": inputs} if inputs else {})})], None
    if kind == "job_status":
        if random.random() < 0.5:
            hist = [{"role": "user", "content": fill(pick(PHRASES["job"]), ctx)},
                    {"role": "assistant", "content": f"Started job {j} ({p}, queued)."}]
            return say("job_status_hist"), [("job_get", {"job_id": j})], hist
        return say("job_status"), [("job_get", {"job_id": j})], None
    if kind == "job_list":
        st = pick(["failed", "running", "queued", "completed", None])
        q = {"failed": "List my failed jobs", "running": "Which jobs are running right now?", "queued": "Any jobs still queued?",
             "completed": "Show completed jobs", None: pick(["Show recent jobs", "List jobs", "What jobs have run?"])}[st]
        if st and random.random() < 0.3:
            q, a = q + f" for {p}", {"status": st, "pipeline": p}
        else:
            a = {"status": st} if st else {}
        return q, [("job_list", a)], None
    if kind == "job_cancel":
        return say("job_cancel"), [("job_cancel", {"job_id": j})], None
    if kind == "mcps":
        return (say("mcp_tools"), [("mcp_tools", {})], None) if random.random() < 0.3 else (say("mcp_list"), [("mcp_list", {})], None)
    if kind == "mcp_get":
        return say("mcp_get"), [("mcp_get", {"name": m})], None
    if kind == "deploy":
        name = pick(SYNTH_MCPS)
        ctx.update(r=pick(REPOS).format(n=name), mm=name)
        tmpl = pick(PHRASES["deploy"])
        q = fill(tmpl, ctx)
        a = {"repo_url": ctx["r"], "name": name}   # mcp_deploy requires a name; derive it from the repo
        steps = [("mcp_deploy", a)]
        if random.random() < 0.4:
            q += pick(PHRASES["deploy_check"])
            steps.append(("mcp_get", {"name": name}))
        return q, steps, None
    if kind == "register":
        name = pick(SYNTH_MCPS)
        return (f"Register the already-running MCP at http://{name}:8080 as {name} with tool query",
                [("mcp_register", {"name": name, "endpoint": f"http://{name}:8080", "tools": [{"name": "query"}]})], None)
    if kind == "scale":
        t = pick(["scale_down", "scale_up", "scale_status"])
        return say(t), [(t, {} if t == "scale_status" else {"mcp": m})], None
    if kind == "creds":
        t = random.choices(["cred_list", "cred_create", "cred_delete", "cred_update"], [0.45, 0.3, 0.15, 0.1])[0]
        if t == "cred_list":
            return say(t), [("credential_list", {})], None
        if t == "cred_create":
            return say(t), [("credential_create", {"name": f"{ctx['e']}-key", "value": ctx["v"]})], None
        if t == "cred_delete":
            return say(t), [("credential_delete", {"credential_id": j})], None
        return say(t), [("credential_update", {"credential_id": j, "is_active": False})], None
    if kind == "policy":
        t = random.choices(["policy_rules", "policy_check", "policy_reload"], [0.5, 0.35, 0.15])[0]
        return say(t), [(t, {"mcp": m, "tool": ctx["tool"]} if t == "policy_check" else {})], None
    if kind == "audit":
        t = random.choice(["audit", "audit_any", "audit_server"])
        a = {"audit": {"limit": ctx["k"]}, "audit_any": {}, "audit_server": {"server": m}}[t]
        return say(t), [("audit_events", a)], None
    if kind == "health":
        return say("health"), [("platform_status", {})], None
    if kind == "pipelines":
        t = random.choices(["pipeline_list", "pipeline_get", "pipeline_delete", "pipeline_reload"], [0.35, 0.3, 0.2, 0.15])[0]
        a = {"pipeline_get": {"name": p}, "pipeline_delete": {"name": ctx["sp"]}}.get(t, {})
        return say(t), [(t, a)], None
    if kind == "tool_invoke":
        inputs = {"cities": cs} if ctx["tool"] == "fetch_cities_weather" else {}
        q = say("tool_invoke") + (f" for {join_and(cs)}" if inputs else "")
        return q, [("tool_invoke", {"mcp": m, "tool": ctx["tool"], **({"inputs": inputs} if inputs else {})})], None
    return say("registry"), [("registry_export", {})], None


# ---------------------------------------------------------------------------
# Guarded platform behaviour: ask when a write lacks its target, explain when
# the user asks a question, act when history already names the target.
# Without these the fine-tune learned "always call a tool" and invented
# arguments for vague deletes/deploys (agent/eval/cases_hard.yaml).
# ---------------------------------------------------------------------------

VAGUE = {
    "pipeline_delete": (["remove the pipeline I made yesterday", "get rid of that test pipeline", "delete my pipeline",
                         "drop the old workflow", "can you clean up the pipeline I don't use anymore",
                         "nuke the broken pipeline", "please delete the duplicate workflow", "remove one of my pipelines",
                         "the pipeline from last week isn't needed, delete it", "trash the experimental pipeline",
                         "Ta bort min gamla pipeline"],
                        "pipeline_list", "Which pipeline should I delete? I can see: {opts}. Shipped examples are protected."),
    "job_cancel": (["stop my job", "kill the running one", "cancel that background run", "abort what's running",
                    "something is stuck, cancel it", "halt the job that's been running forever", "stop the background thing",
                    "please cancel my run", "Avbryt jobbet som kör"],
                   "job_list", "Which job should I cancel? Running jobs: {opts}."),
    "scale_down": (["scale it down", "put the server to sleep", "turn off the MCP we aren't using",
                    "save some resources, scale something down", "shut down the idle one", "sleep the unused server"],
                   "mcp_list", "Which MCP should I scale down to zero? Registered: {opts}."),
    "mcp_deploy": (["push my new MCP live", "deploy the server I wrote", "ship my integration to the platform",
                    "get my MCP deployed please", "I finished my MCP, put it on the platform", "deploy the new tool server",
                    "can you roll out my MCP?", "launch my integration", "Driftsätt min MCP"], None,
                   "Sure — what's the git repository URL, and what name should the MCP get?"),
    "mcp_register": (["register my server", "hook up the MCP I'm running locally", "add my running service to the router",
                      "connect the MCP that's already up", "make the platform aware of my server"], None,
                     "What's its endpoint URL, the name to register it under, and which tools it exposes?"),
    "credential_create": (["save my exchange key", "add my API token", "store a secret for the binance MCP",
                           "I need to give the kraken MCP my key", "set up credentials for my trading bot",
                           "add a password for the crm integration", "keep my OpenAI key somewhere safe"], None,
                          "Happy to — what name should the credential have? For the value, the portal's credential "
                          "form keeps the secret out of this chat; if you paste it here I'll store it encrypted and "
                          "won't repeat it."),
}
EXPLAIN = [
    ("what happens to running calls if {m} gets scaled to zero?",
     "Calls already in flight finish; afterwards {m} stops, and the next call wakes it with a short cold start. "
     "Nothing is deleted. I haven't scaled it — ask me to if you want that."),
    ("Would deleting {sp} affect jobs that are already running?",
     "Running jobs keep their own copy of the steps, but new runs of {sp} would fail once it's deleted, and only a "
     "re-deploy brings it back. I haven't deleted anything."),
    ("can you tell me what cancelling a job does before I decide?",
     "Cancelling stops a queued or running job; steps already done aren't rolled back and the job ends as cancelled. "
     "Tell me the job id when you want me to cancel one."),
    ("I'm thinking about registering an MCP, what do you need from me?",
     "Its endpoint URL, the name to register it under, and the tools it exposes. If it isn't running yet, give me a "
     "git repo instead and I can deploy it. I won't register anything until you confirm."),
    ("Hur fungerar scale-to-zero här?",
     "En MCP som skalas till noll stoppar sina poddar men raderas inte; nästa anrop väcker den igen med en kort "
     "kallstart. Jag har inte ändrat något."),
    ("before you do anything — what's a policy rule?",
     "A policy rule decides who may call which MCP tool, on what data and when; every call is checked against them "
     "and audited. I haven't changed or run anything."),
    ("Just curious, how do background jobs report results?",
     "A job gets an id when it starts; its status moves through queued, running and completed or failed, and the "
     "result is stored with it. Ask me for a job's status or result whenever you like."),
    ("No action needed, but what is a pipeline exactly?",
     "A pipeline is a named chain of MCP tool calls with typed inputs and an output; running it executes the steps in "
     "order through the router. I can list the ones you have if that helps."),
    ("If I put {m} to sleep, what breaks?",
     "Scaling {m} to zero stops its pods; nothing is deleted. The next call to one of its tools wakes it again, with a "
     "cold start of a few seconds. I haven't changed anything — say \"scale {m} down\" if you want me to do it."),
    ("Is it safe to scale {m} down to zero right now?",
     "It's safe in the sense that nothing is lost: {m} just stops until the next call wakes it (a few seconds of cold "
     "start). If something calls it constantly it will keep waking up. I haven't scaled anything."),
    ("What's the safest way to give Sealfleet my API key?",
     "Store it as a credential: it's encrypted at rest, scoped to the MCP that needs it, and injected at call time so "
     "the model never sees the value. The portal's credential form keeps the secret out of the chat entirely."),
    ("how are secrets stored on this platform?",
     "Credentials are encrypted at rest and only decrypted inside the broker when a tool call needs them; listings "
     "show names and metadata, never values, and the model never receives the secret itself."),
    ("How does deploying an MCP from git work here?",
     "Give me a repository URL and a name: the deploy service builds the image, runs it, and registers the MCP in "
     "the router so its tools show up. Nothing is deployed until you ask me to."),
    ("What's the difference between running a pipeline and starting a background job?",
     "Running a pipeline waits and returns the result in this chat. A background job returns a job id immediately "
     "and runs on its own; you can ask me for its status or result later."),
    ("If I delete a pipeline, can I get it back?",
     "Deleting removes a runtime-authored pipeline's definition; it can only come back if you re-deploy the "
     "definition. Shipped example pipelines are protected and can't be deleted."),
    ("explain what a policy check does, but don't run one",
     "A policy check evaluates whether a specific MCP tool call would be allowed for you — identity, action and "
     "data scope — without executing it. I haven't run one; give me the MCP and tool if you want me to."),
]
RESOLVE = [
    ("job_cancel", "Run {p} as a background job", "Started job {j} ({p}, queued).",
     ["cancel it", "actually, stop that job", "never mind, kill it"], lambda c: {"job_id": c["j"]}),
    ("scale_down", "Is {m} scaled to zero?", "{m} is running with 1 replica.",
     ["ok, scale it down then", "put it to sleep", "scale that one down"], lambda c: {"mcp": c["m"]}),
    ("pipeline_delete", "Show the definition of the {sp} pipeline", "{sp}: a runtime-authored test pipeline with 2 steps.",
     ["delete it", "remove that one", "ok get rid of it"], lambda c: {"name": c["sp"]}),
]


def gen_platform_guarded(catalog: dict) -> dict:
    mcps = (catalog["mcps"] or REAL_MCPS_FALLBACK) + random.sample(SYNTH_MCPS, 1)
    ctx = {"m": pick(mcps), "j": hexid(), "p": pick([t["name"] for t in catalog["pipeline_tools"]] or SYNTH_PIPELINES),
           "sp": pick(SYNTH_PIPELINES)}
    kind = random.choices(["clarify", "clarify_read", "explain", "resolve"], [0.3, 0.3, 0.25, 0.15])[0]
    if kind in ("clarify", "clarify_read"):
        tool = pick([k for k, v in VAGUE.items() if (v[1] is not None) == (kind == "clarify_read")])
        qs, read_tool, ask = VAGUE[tool]
        q = pick(qs)
        messages = server._platform_messages(q)
        if read_tool:
            call = tool_call(read_tool, {"status": "running"} if read_tool == "job_list" else {})
            if read_tool == "job_list":
                ids = [hexid() for _ in range(random.randint(1, 3))]
                result = _json({"jobs": [{"job_id": i, "status": "running", "pipeline": pick(SYNTH_PIPELINES)} for i in ids],
                                "count": len(ids)})
                opts = ", ".join(ids)
            elif read_tool == "pipeline_list":
                names = random.sample(SYNTH_PIPELINES, 3) + [t["name"] for t in catalog["pipeline_tools"]]
                result = _json({"pipelines": [{"name": n, "version": 2} for n in names]})
                opts = ", ".join(names)
            else:
                o = obs(catalog, "mcp_list")
                result = o["result"] if o else _json([{"name": n} for n in mcps])
                opts = ", ".join(mcps)
            messages += [{"role": "assistant", "content": "", "tool_calls": [call]}, tool_result_msg(call, result)]
            answer = ask.format(opts=opts)
        else:
            answer = ask
        messages.append({"role": "assistant", "content": answer})
    elif kind == "explain":
        q_t, a_t = pick(EXPLAIN)
        q = fill(q_t, ctx)
        messages = server._platform_messages(q) + [{"role": "assistant", "content": fill(a_t, ctx)}]
    else:
        tool, u0, a0, follow, args = pick(RESOLVE)
        history = [{"role": "user", "content": fill(u0, ctx)}, {"role": "assistant", "content": fill(a0, ctx)}]
        q = pick(follow)
        messages = server._platform_messages(q, history) + [
            {"role": "assistant", "content": "", "tool_calls": [tool_call(tool, args(ctx))]}]
    return {"messages": messages, "tools": catalog["_openai_tools"], "meta": {"task": "platform_guard", "kind": kind, "q": q}}


def gen_platform(catalog: dict, trajectory: bool) -> dict:
    mcps = catalog["mcps"] or REAL_MCPS_FALLBACK
    pipeline_names = [t["name"] for t in catalog["pipeline_tools"]] + random.sample(SYNTH_PIPELINES, 2)
    q, steps, history = platform_case(catalog, pipeline_names, mcps + random.sample(SYNTH_MCPS, 1))
    # Drop None-valued args (no filter given).
    steps = [(t, {k: v for k, v in a.items() if v is not None}) for t, a in steps]
    messages = server._platform_messages(q, history)
    if not trajectory:
        t, a = steps[0]
        messages.append({"role": "assistant", "content": "", "tool_calls": [tool_call(t, a)]})
        return {"messages": messages, "tools": catalog["_openai_tools"], "meta": {"task": "platform_first", "q": q}}
    done = []
    for t, a in steps:
        call = tool_call(t, a)
        messages.append({"role": "assistant", "content": "", "tool_calls": [call]})
        o = obs(catalog, t, a) or (obs(catalog, t) if t in ("mcp_list", "mcp_tools", "pipeline_list",
                                                           "platform_status", "scale_status") else None)
        if o:
            result, is_error = o["result"], o["is_error"]
        else:
            result, is_error = synth_result(t, a)
        messages.append(tool_result_msg(call, result))
        done.append((t, a, result, is_error))
        if is_error:
            break
    # One sentence per step taken, so a multi-step answer reports every step.
    messages.append({"role": "assistant", "content": " ".join(summarize(*d) for d in done)})
    return {"messages": messages, "tools": catalog["_openai_tools"], "meta": {"task": "platform_traj", "q": q}}


# ---------------------------------------------------------------------------

def norm_q(q: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", q.lower()).strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default=str(Path(__file__).with_name("catalog.json")))
    ap.add_argument("--cases", nargs="+", default=[str(Path(__file__).resolve().parents[1] / "eval" / f)
                                                   for f in ("cases.yaml", "cases_hard.yaml")],
                    help="benchmark files whose questions are excluded from the data")
    ap.add_argument("--out", required=True)
    ap.add_argument("--n-select", type=int, default=3000)
    ap.add_argument("--n-platform", type=int, default=2500)
    ap.add_argument("--traj-frac", type=float, default=0.5, help="share of platform examples that are full trajectories")
    ap.add_argument("--guard-frac", type=float, default=0.22,
                    help="share of platform examples that ask/explain/resolve-from-history instead of acting")
    ap.add_argument("--val-frac", type=float, default=0.05)
    ap.add_argument("--system-prefix", default="/no_think",
                    help='prepended to the system turn (Nemotron reasoning off); "" to disable')
    ap.add_argument("--args-format", choices=["string", "dict"], default="string",
                    help="tool_call arguments as an OpenAI JSON string, or a dict (chat templates that "
                         "iterate arguments|items, e.g. Nemotron-3-Nano)")
    ap.add_argument("--expand-turns", action="store_true",
                    help="split each trajectory into one example per assistant turn, for trainers "
                         "that only compute loss on the final assistant turn (nemo-automodel ChatDataset)")
    ap.add_argument("--seed", type=int, default=7)
    opts = ap.parse_args()

    random.seed(opts.seed)
    secrets.token_hex = lambda n=16: "".join(random.choice("0123456789abcdef") for _ in range(2 * n))  # reproducible ids
    catalog = json.loads(Path(opts.catalog).read_text())
    catalog["_openai_tools"] = server._as_openai_tools(catalog["platform_tools"])
    held_out = set()
    for path in opts.cases:
        for level in yaml.safe_load(Path(path).read_text()).values():
            held_out |= {norm_q(c["q"]) for c in level or [] if isinstance(c, dict) and "q" in c}

    rows, copies, leaked = [], {}, 0
    target = {"select": opts.n_select, "platform": opts.n_platform}
    made = {"select": 0, "platform": 0}
    attempts = 0
    while made["select"] < target["select"] or made["platform"] < target["platform"]:
        attempts += 1
        if attempts > 50 * (opts.n_select + opts.n_platform):
            break
        task = "select" if made["select"] < target["select"] and (
            made["platform"] >= target["platform"] or random.random() < 0.5) else "platform"
        if task == "select":
            ex = gen_select(catalog["pipeline_tools"], catalog["mcps"])
        elif random.random() < opts.guard_frac:
            ex = gen_platform_guarded(catalog)
        else:
            ex = gen_platform(catalog, trajectory=random.random() < opts.traj_frac)
        nq = norm_q(ex["meta"]["q"])
        if nq in held_out:
            leaked += 1
            continue
        key = json.dumps(ex["messages"], sort_keys=True)
        # Ask/explain rows have little natural variety; allow a few copies so the
        # behaviour carries weight next to thousands of act-immediately rows.
        cap = 4 if ex["meta"].get("kind") in ("explain", "clarify") else 1
        if copies.get(key, 0) >= cap:
            continue
        copies[key] = copies.get(key, 0) + 1
        if opts.system_prefix:
            if ex["messages"][0]["role"] == "system":
                ex["messages"][0]["content"] = opts.system_prefix + "\n" + ex["messages"][0]["content"]
            else:
                ex["messages"].insert(0, {"role": "system", "content": opts.system_prefix})
        if ex["tools"] is None:
            del ex["tools"]
        if opts.args_format == "dict":
            for m in ex["messages"]:
                for c in m.get("tool_calls") or []:
                    c["function"]["arguments"] = json.loads(c["function"]["arguments"])
        rows.append(ex)
        made[task] += 1

    random.shuffle(rows)
    n_val = max(1, int(len(rows) * opts.val_frac))
    parts = {"val.jsonl": rows[:n_val], "train.jsonl": rows[n_val:]}
    if opts.expand_turns:
        # After the split, so one conversation's turns never straddle train/val.
        for name, part in parts.items():
            expanded = []
            for r in part:
                # Only turns generated for THIS request: assistant messages in the
                # history are prompt context, not targets.
                last_user = max(i for i, m in enumerate(r["messages"]) if m["role"] == "user")
                ends = [i for i, m in enumerate(r["messages"]) if m["role"] == "assistant" and i > last_user]
                for k, i in enumerate(ends):
                    expanded.append({**r, "messages": r["messages"][:i + 1],
                                     "meta": {**r["meta"], "turn": k, "turns": len(ends)}})
            random.shuffle(expanded)
            parts[name] = expanded
    out = Path(opts.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, part in parts.items():
        with open(out / name, "w") as f:
            for r in part:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    from collections import Counter
    allrows = parts["train.jsonl"] + parts["val.jsonl"]
    stats = {"train": len(parts["train.jsonl"]), "val": len(parts["val.jsonl"]), "conversations": len(rows),
             "excluded_benchmark_questions": leaked,
             "by_task": Counter(r["meta"]["task"] for r in allrows),
             "select_kinds": Counter(r["meta"].get("kind") for r in allrows if r["meta"]["task"] == "select")}
    (out / "stats.json").write_text(json.dumps(stats, indent=2, default=dict))
    print(json.dumps(stats, indent=2, default=dict))


if __name__ == "__main__":
    main()
