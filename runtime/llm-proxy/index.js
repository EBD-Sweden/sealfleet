/**
 * McpFinder LLM Proxy
 *
 * OpenAI-compatible server that routes LLM calls through the configured
 * AI gateway first, with Anthropic direct paths kept as fallbacks.
 *
 * All MCPs call http://host.k3d.internal:3456/v1 — this is the single
 * LLM entry point for the entire McpFinder platform.
 *
 * Model selection:
 *   model "local/..."  → PINNED to the on-box model. Never falls back to a
 *                        cloud backend, so the request cannot leave the host.
 *                        This is the guarantee regulated tenants need.
 *   anything else      → the routing priority below.
 *
 * Routing priority:
 *   1. Claude CLI / Anthropic OAuth              → Anthropic API directly (preferred)
 *   2. AI_GATEWAY_API_KEY                        → AI Gateway (fallback)
 *   3. ANTHROPIC_API_KEY (sk-ant-api...)        → Anthropic API directly (fallback)
 *   4. LOCAL_LLM_BASE_URL                        → local OpenAI-compatible server (fallback)
 *   5. 503
 *
 * OAuth token sources, in order:
 *   - CLAUDE_CODE_OAUTH_TOKEN
 *   - ~/.claude/.credentials.json (claudeAiOauth.accessToken)
 *   - ANTHROPIC_OAUTH_TOKEN
 *
 * OAuth uses Authorization: Bearer + anthropic-beta: oauth-2025-04-20
 *
 * Usage (PM2):
 *   pm2 start runtime/ecosystem.config.js --only llm-proxy
 */

const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");
const https = require("https");
const url = require("url");

const PORT = parseInt(process.env.LLM_PROXY_PORT || "3456", 10);
const ANTHROPIC_OAUTH_TOKEN = process.env.ANTHROPIC_OAUTH_TOKEN || "";
const CLAUDE_CODE_OAUTH_TOKEN = process.env.CLAUDE_CODE_OAUTH_TOKEN || "";
const CLAUDE_CODE_CREDENTIALS_PATH = process.env.CLAUDE_CODE_CREDENTIALS_PATH || path.join(os.homedir(), ".claude", ".credentials.json");
const ANTHROPIC_API_KEY = process.env.ANTHROPIC_API_KEY || "";
const AI_GATEWAY_API_KEY = process.env.AI_GATEWAY_API_KEY || "";
const GATEWAY_BASE = process.env.GATEWAY_URL || "https://ai-gateway.vercel.sh/v1";

// Local, on-box model: any OpenAI-compatible server (NVIDIA NIM, vLLM,
// llama.cpp, Ollama). Empty base URL disables the local route entirely.
const LOCAL_LLM_BASE_URL = (process.env.LOCAL_LLM_BASE_URL || "").replace(/\/$/, "");
const LOCAL_LLM_MODEL = process.env.LOCAL_LLM_MODEL || "";
const LOCAL_LLM_TIMEOUT_MS = parseInt(process.env.LOCAL_LLM_TIMEOUT_MS || "300000", 10);
const LOCAL_PREFIX = "local/";

// Bring-your-own provider: "byo/<provider>/<model>". The caller supplies the
// key per request (resolved from the platform credential store upstream) —
// this proxy never stores or logs it.
const BYO_PREFIX = "byo/";
const BYO_KEY_HEADER = "x-mcpfinder-llm-key";
const BYO_BASE_URL_HEADER = "x-mcpfinder-llm-base-url";
const BYO_TIMEOUT_MS = parseInt(process.env.BYO_LLM_TIMEOUT_MS || "90000", 10);

// Known OpenAI-compatible providers. "custom" takes its base URL from the
// request header, so a tenant can point at a private or regional endpoint.
const BYO_PROVIDERS = {
  openai: "https://api.openai.com/v1",
  groq: "https://api.groq.com/openai/v1",
  mistral: "https://api.mistral.ai/v1",
  together: "https://api.together.xyz/v1",
  deepseek: "https://api.deepseek.com/v1",
  openrouter: "https://openrouter.ai/api/v1",
  nvidia: "https://integrate.api.nvidia.com/v1",
  custom: "",
};

// --- Map OpenAI model names to Anthropic model IDs ---
const MODEL_MAP = {
  "anthropic/claude-haiku-4-5": "claude-haiku-4-5-20251001",
  "anthropic/claude-haiku-4.5": "claude-haiku-4-5-20251001",
  "anthropic/claude-sonnet-4-5": "claude-sonnet-4-5-20251029",
  "anthropic/claude-sonnet-4.5": "claude-sonnet-4-5-20251029",
  "anthropic/claude-sonnet-4-6": "claude-sonnet-4-6",
  "anthropic/claude-opus-4-5": "claude-opus-4-5-20251101",
  "anthropic/claude-opus-4.5": "claude-opus-4-5-20251101",
  "anthropic/claude-opus-4-6": "claude-opus-4-6",
  "anthropic/claude-opus-4-7": "claude-opus-4-7",
  "anthropic/claude-opus-4.7": "claude-opus-4-7",
  "anthropic/claude-sonnet-4-6": "claude-sonnet-4-6",
};

function resolveAnthropicModel(openAiModel) {
  if (!openAiModel) return "claude-haiku-4-5-20251001";
  return MODEL_MAP[openAiModel] || openAiModel.replace(/^anthropic\//, "");
}

function readClaudeCliOauthToken() {
  try {
    const raw = fs.readFileSync(CLAUDE_CODE_CREDENTIALS_PATH, "utf8");
    const data = JSON.parse(raw);
    return data?.claudeAiOauth?.accessToken || "";
  } catch {
    return "";
  }
}

function getAnthropicOauthToken() {
  return CLAUDE_CODE_OAUTH_TOKEN || readClaudeCliOauthToken() || ANTHROPIC_OAUTH_TOKEN;
}

function getBackendMode() {
  const oauthToken = getAnthropicOauthToken();
  if (CLAUDE_CODE_OAUTH_TOKEN || readClaudeCliOauthToken()) return "claude-cli-oauth";
  if (oauthToken) return "anthropic-oauth";
  if (AI_GATEWAY_API_KEY) return "ai-gateway";
  if (ANTHROPIC_API_KEY) return "anthropic-api-key";
  if (LOCAL_LLM_BASE_URL) return "local";
  return "no-backend";
}

/** Convert OpenAI chat completion request → Anthropic Messages API request */
function toAnthropicBody(openAiBody) {
  const model = resolveAnthropicModel(openAiBody.model);
  const maxTokens = openAiBody.max_tokens || 4096;
  const messages = (openAiBody.messages || []).filter((m) => m.role !== "system");
  const system = (openAiBody.messages || [])
    .filter((m) => m.role === "system")
    .map((m) => m.content)
    .join("\n");
  return {
    model,
    max_tokens: maxTokens,
    ...(system ? { system } : {}),
    messages,
  };
}

/** Convert Anthropic Messages response → OpenAI chat completion response */
function toOpenAiResponse(anthropicResp, originalModel) {
  const content = anthropicResp.content?.[0]?.text || "";
  return {
    id: anthropicResp.id || `proxy-${Date.now()}`,
    object: "chat.completion",
    created: Math.floor(Date.now() / 1000),
    model: originalModel || "anthropic/claude-haiku-4-5",
    choices: [
      {
        index: 0,
        message: { role: "assistant", content },
        finish_reason: anthropicResp.stop_reason || "stop",
      },
    ],
    usage: {
      prompt_tokens: anthropicResp.usage?.input_tokens || 0,
      completion_tokens: anthropicResp.usage?.output_tokens || 0,
      total_tokens: (anthropicResp.usage?.input_tokens || 0) + (anthropicResp.usage?.output_tokens || 0),
    },
  };
}

/** Call Anthropic API using OAuth token or API key */
function callAnthropicDirect(openAiBody, token, isOauth) {
  return new Promise((resolve, reject) => {
    const body = Buffer.from(JSON.stringify(toAnthropicBody(openAiBody)));
    const headers = {
      "content-type": "application/json",
      "content-length": body.length,
      "anthropic-version": "2023-06-01",
    };
    if (isOauth) {
      headers["Authorization"] = `Bearer ${token}`;
      headers["anthropic-beta"] = "oauth-2025-04-20";
    } else {
      headers["x-api-key"] = token;
    }

    const opts = {
      hostname: "api.anthropic.com",
      port: 443,
      path: "/v1/messages",
      method: "POST",
      headers,
      timeout: 90000,
    };

    const req = https.request(opts, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        try {
          const data = JSON.parse(Buffer.concat(chunks).toString());
          if (data.type === "error") {
            return reject(new Error(`Anthropic API error: ${data.error?.message || JSON.stringify(data.error)}`));
          }
          resolve(toOpenAiResponse(data, openAiBody.model));
        } catch (e) {
          reject(new Error("Anthropic API returned non-JSON"));
        }
      });
    });
    req.on("error", reject);
    req.on("timeout", () => { req.destroy(); reject(new Error("Anthropic API timeout")); });
    req.write(body);
    req.end();
  });
}

/** Forward request to Vercel AI Gateway (OpenAI-compatible) */
function callVercelGateway(openAiBody) {
  return new Promise((resolve, reject) => {
    const parsedGateway = url.parse(GATEWAY_BASE);
    const payload = Buffer.from(JSON.stringify(openAiBody));
    const opts = {
      hostname: parsedGateway.hostname,
      port: parsedGateway.port || 443,
      path: (parsedGateway.path || "/v1").replace(/\/$/, "") + "/chat/completions",
      method: "POST",
      headers: {
        "content-type": "application/json",
        "content-length": payload.length,
        "authorization": `Bearer ${AI_GATEWAY_API_KEY}`,
      },
      timeout: 90000,
    };
    const req = https.request(opts, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        let data;
        try {
          data = JSON.parse(Buffer.concat(chunks).toString());
        } catch {
          reject(new Error("Gateway non-JSON"));
          return;
        }

        if ((res.statusCode || 500) >= 400) {
          const msg = data?.error?.message || `Gateway HTTP ${res.statusCode}`;
          reject(new Error(msg));
          return;
        }

        if (data?.error) {
          const msg = data.error?.message || JSON.stringify(data.error);
          reject(new Error(msg));
          return;
        }

        if (!Array.isArray(data?.choices)) {
          reject(new Error("Gateway response missing choices"));
          return;
        }

        resolve(data);
      });
    });
    req.on("error", reject);
    req.on("timeout", () => { req.destroy(); reject(new Error("Gateway timeout")); });
    req.write(payload);
    req.end();
  });
}

/**
 * POST an OpenAI-compatible chat completion to any base URL.
 * Shared by the local route and the bring-your-own-provider route.
 *
 * `label` only shapes error messages; `apiKey` is omitted for servers that
 * don't authenticate (a local NIM/vLLM on the host network).
 */
function callOpenAiCompatible({ baseUrl, model, body, apiKey, timeoutMs, label }) {
  return new Promise((resolve, reject) => {
    if (!baseUrl) return reject(new Error(`${label}: no base URL configured`));

    const parsed = url.parse(baseUrl);
    const isHttps = parsed.protocol === "https:";
    const transport = isHttps ? https : http;
    const payload = Buffer.from(JSON.stringify({ ...body, model }));

    const headers = {
      "content-type": "application/json",
      "content-length": payload.length,
    };
    if (apiKey) headers["authorization"] = `Bearer ${apiKey}`;

    const opts = {
      hostname: parsed.hostname,
      port: parsed.port || (isHttps ? 443 : 80),
      path: (parsed.path || "").replace(/\/$/, "") + "/chat/completions",
      method: "POST",
      headers,
      timeout: timeoutMs,
    };

    const req = transport.request(opts, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        const raw = Buffer.concat(chunks).toString();
        let data;
        try { data = JSON.parse(raw); }
        catch {
          // A NIM/vLLM server still building its engine answers with plain
          // text, so surface the status and a snippet, not a bare parse error.
          return reject(new Error(
            `${label} HTTP ${res.statusCode} (non-JSON): ${raw.slice(0, 120)}`
          ));
        }
        if ((res.statusCode || 500) >= 400) {
          return reject(new Error(data?.error?.message || `${label} HTTP ${res.statusCode}`));
        }
        if (!Array.isArray(data?.choices)) {
          return reject(new Error(`${label} response missing choices`));
        }
        resolve(data);
      });
    });
    req.on("error", reject);
    req.on("timeout", () => { req.destroy(); reject(new Error(`${label} timeout`)); });
    req.write(payload);
    req.end();
  });
}

/**
 * Forward to a tenant's own provider.
 *
 * Anthropic is not OpenAI-compatible, so it reuses the Messages translation
 * already in this file; everything else is a straight passthrough.
 */
function callByo(openAiBody, providerKey, model, apiKey, customBaseUrl) {
  if (!apiKey) {
    return Promise.reject(new Error("no API key supplied for the selected provider"));
  }
  if (providerKey === "anthropic") {
    return callAnthropicDirect({ ...openAiBody, model }, apiKey, false);
  }

  const baseUrl = providerKey === "custom"
    ? customBaseUrl
    : BYO_PROVIDERS[providerKey];
  if (!baseUrl) {
    return Promise.reject(new Error(
      providerKey === "custom"
        ? `custom provider needs a ${BYO_BASE_URL_HEADER} header`
        : `unknown provider "${providerKey}"`
    ));
  }

  return callOpenAiCompatible({
    baseUrl,
    model,
    body: openAiBody,
    apiKey,
    timeoutMs: BYO_TIMEOUT_MS,
    label: `Provider ${providerKey}`,
  });
}

/** Forward request to a local OpenAI-compatible server (NIM / vLLM / llama.cpp / Ollama) */
function callLocal(openAiBody) {
  if (!LOCAL_LLM_BASE_URL) return Promise.reject(new Error("LOCAL_LLM_BASE_URL not set"));

  // Pinned requests name the local model directly (minus the marker). Unpinned
  // ones arrive here as a fallback still carrying a cloud model name the local
  // server has never heard of, so serve them with the model it actually has.
  let model = openAiBody.model || "";
  if (model.startsWith(LOCAL_PREFIX)) {
    model = model.slice(LOCAL_PREFIX.length) || LOCAL_LLM_MODEL;
  } else {
    model = LOCAL_LLM_MODEL || model;
  }

  return callOpenAiCompatible({
    baseUrl: LOCAL_LLM_BASE_URL,
    model,
    body: openAiBody,
    timeoutMs: LOCAL_LLM_TIMEOUT_MS,
    label: "Local LLM",
  });
}

// --- HTTP server ---

const server = http.createServer((req, res) => {
  const sendJson = (code, obj) => {
    const body = JSON.stringify(obj);
    res.writeHead(code, { "content-type": "application/json", "content-length": Buffer.byteLength(body) });
    res.end(body);
  };

  // Health
  if (req.method === "GET" && (req.url === "/" || req.url === "/health")) {
    return sendJson(200, {
      status: "ok",
      service: "llm-proxy",
      mode: getBackendMode(),
      port: PORT,
      local: LOCAL_LLM_BASE_URL
        ? { enabled: true, base_url: LOCAL_LLM_BASE_URL, model: LOCAL_LLM_MODEL }
        : { enabled: false },
      byo: { providers: Object.keys(BYO_PROVIDERS).concat("anthropic").sort() },
    });
  }

  if (!req.url.includes("chat/completions")) {
    return sendJson(404, { error: { message: `Not found: ${req.url}` } });
  }

  const chunks = [];
  req.on("data", (c) => chunks.push(c));
  req.on("end", async () => {
    let body;
    try { body = JSON.parse(Buffer.concat(chunks).toString()); }
    catch { return sendJson(400, { error: { message: "Invalid JSON" } }); }

    const model = body.model || "anthropic/claude-haiku-4-5";
    console.log(`[llm-proxy] ${new Date().toISOString()} model=${model}`);

    // Route 0a: a tenant's own provider, pinned with "byo/<provider>/<model>".
    // Like the local pin this has NO fallback: a tenant that chose its own
    // provider must not be silently served by the platform's backend instead.
    if (model.startsWith(BYO_PREFIX)) {
      const rest = model.slice(BYO_PREFIX.length);
      const slash = rest.indexOf("/");
      const providerKey = (slash === -1 ? rest : rest.slice(0, slash)).toLowerCase();
      const providerModel = slash === -1 ? "" : rest.slice(slash + 1);

      if (!providerModel) {
        return sendJson(400, { error: { message: `Model must be "${BYO_PREFIX}<provider>/<model>"` } });
      }
      try {
        const result = await callByo(
          body,
          providerKey,
          providerModel,
          req.headers[BYO_KEY_HEADER],
          req.headers[BYO_BASE_URL_HEADER],
        );
        console.log(`[llm-proxy] OK via byo:${providerKey}`);
        return sendJson(200, result);
      } catch (err) {
        // Never echo the request back — it may carry the tenant's key header.
        console.error(`[llm-proxy] byo:${providerKey} failed: ${err.message}`);
        return sendJson(502, { error: { message: `Provider request failed: ${err.message}` } });
      }
    }

    // Route 0b: model pinned to the on-box server with a "local/" prefix.
    // Deliberately has NO fallback — a tenant that pins local must never have
    // its prompt silently sent to a cloud provider because the box was busy.
    if (model.startsWith(LOCAL_PREFIX)) {
      if (!LOCAL_LLM_BASE_URL) {
        return sendJson(503, { error: { message: "Local LLM requested but LOCAL_LLM_BASE_URL is not configured" } });
      }
      try {
        const result = await callLocal(body);
        console.log(`[llm-proxy] OK via local (pinned)`);
        return sendJson(200, result);
      } catch (err) {
        console.error(`[llm-proxy] local (pinned) failed: ${err.message}`);
        return sendJson(502, { error: { message: `Local LLM unavailable: ${err.message}` } });
      }
    }

    // Route 1: Claude CLI / Anthropic OAuth (preferred)
    const oauthToken = getAnthropicOauthToken();
    if (oauthToken) {
      try {
        const result = await callAnthropicDirect(body, oauthToken, true);
        console.log(`[llm-proxy] OK via ${getBackendMode()}`);
        return sendJson(200, result);
      } catch (err) {
        console.warn(`[llm-proxy] ${getBackendMode()} failed: ${err.message} — trying next`);
      }
    }

    // Route 2: AI Gateway (fallback)
    if (AI_GATEWAY_API_KEY) {
      try {
        const result = await callVercelGateway(body);
        console.log(`[llm-proxy] OK via ai-gateway`);
        return sendJson(200, result);
      } catch (err) {
        console.warn(`[llm-proxy] ai-gateway failed: ${err.message} — trying next`);
      }
    }

    // Route 3: Anthropic API key (fallback)
    let lastError = null;
    if (ANTHROPIC_API_KEY) {
      try {
        const result = await callAnthropicDirect(body, ANTHROPIC_API_KEY, false);
        console.log(`[llm-proxy] OK via anthropic-api-key`);
        return sendJson(200, result);
      } catch (err) {
        lastError = err;
        console.warn(`[llm-proxy] anthropic-api-key failed: ${err.message} — trying next`);
      }
    }

    // Route 4: local on-box model (last resort for unpinned requests)
    if (LOCAL_LLM_BASE_URL) {
      try {
        const result = await callLocal(body);
        console.log(`[llm-proxy] OK via local (fallback)`);
        return sendJson(200, result);
      } catch (err) {
        lastError = err;
        console.warn(`[llm-proxy] local failed: ${err.message}`);
      }
    }

    if (lastError) {
      console.error(`[llm-proxy] all backends failed. Last: ${lastError.message}`);
      return sendJson(502, { error: { message: `LLM unavailable: ${lastError.message}` } });
    }
    return sendJson(503, { error: { message: "No LLM backend configured" } });
  });

  req.on("error", (err) => sendJson(400, { error: { message: err.message } }));
});

server.listen(PORT, "0.0.0.0", () => {
  const mode = getBackendMode();
  console.log(`[llm-proxy] Listening on 0.0.0.0:${PORT}`);
  console.log(`[llm-proxy] Mode: ${mode}`);
});

server.on("error", (err) => { console.error(`[llm-proxy] fatal: ${err.message}`); process.exit(1); });
