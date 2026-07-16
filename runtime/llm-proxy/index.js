/**
 * McpFinder LLM Proxy
 *
 * OpenAI-compatible server that routes LLM calls through the configured
 * AI gateway first, with Anthropic direct paths kept as fallbacks.
 *
 * All MCPs call http://host.k3d.internal:3456/v1 — this is the single
 * LLM entry point for the entire McpFinder platform.
 *
 * Routing priority:
 *   1. Claude CLI / Anthropic OAuth              → Anthropic API directly (preferred)
 *   2. AI_GATEWAY_API_KEY                        → AI Gateway (fallback)
 *   3. ANTHROPIC_API_KEY (sk-ant-api...)        → Anthropic API directly (fallback)
 *   4. 503
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

// --- HTTP server ---

const server = http.createServer((req, res) => {
  const sendJson = (code, obj) => {
    const body = JSON.stringify(obj);
    res.writeHead(code, { "content-type": "application/json", "content-length": Buffer.byteLength(body) });
    res.end(body);
  };

  // Health
  if (req.method === "GET" && (req.url === "/" || req.url === "/health")) {
    return sendJson(200, { status: "ok", service: "llm-proxy", mode: getBackendMode(), port: PORT });
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
    if (ANTHROPIC_API_KEY) {
      try {
        const result = await callAnthropicDirect(body, ANTHROPIC_API_KEY, false);
        console.log(`[llm-proxy] OK via anthropic-api-key`);
        return sendJson(200, result);
      } catch (err) {
        console.error(`[llm-proxy] all backends failed. Last: ${err.message}`);
        return sendJson(502, { error: { message: `LLM unavailable: ${err.message}` } });
      }
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
