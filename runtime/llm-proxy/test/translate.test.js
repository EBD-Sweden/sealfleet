// node --test runtime/llm-proxy/test — pure translation, no network.
const test = require("node:test");
const assert = require("node:assert/strict");
const { toAnthropicBody, toOpenAiResponse } = require("../index.js");

test("tools are translated to Anthropic input_schema and tool_choice", () => {
  const body = toAnthropicBody({
    model: "anthropic/claude-sonnet-4-6",
    messages: [{ role: "system", content: "sys" }, { role: "user", content: "list mcps" }],
    tools: [{ type: "function", function: { name: "mcp_list", description: "List", parameters: { type: "object", properties: {} } } }],
    tool_choice: "auto",
    temperature: 0,
  });
  assert.equal(body.system, "sys");
  assert.deepEqual(body.tools, [{ name: "mcp_list", description: "List", input_schema: { type: "object", properties: {} } }]);
  assert.equal(body.tool_choice, undefined);         // auto is Anthropic's default
  assert.equal(body.temperature, 0);
  assert.deepEqual(body.messages, [{ role: "user", content: "list mcps" }]);
});

test("tool_choice required/none/specific map correctly", () => {
  const base = { messages: [{ role: "user", content: "x" }], tools: [{ type: "function", function: { name: "a", parameters: {} } }] };
  assert.deepEqual(toAnthropicBody({ ...base, tool_choice: "required" }).tool_choice, { type: "any" });
  assert.deepEqual(toAnthropicBody({ ...base, tool_choice: { type: "function", function: { name: "a" } } }).tool_choice, { type: "tool", name: "a" });
  assert.equal(toAnthropicBody({ ...base, tool_choice: "none" }).tools, undefined);
});

test("assistant tool_calls and role:tool results become tool_use / tool_result blocks", () => {
  const body = toAnthropicBody({
    messages: [
      { role: "user", content: "run it" },
      { role: "assistant", content: "", tool_calls: [
        { id: "c1", type: "function", function: { name: "job_create", arguments: "{\"pipeline\":\"p\"}" } },
        { id: "c2", type: "function", function: { name: "job_get", arguments: "{\"job_id\":\"1\"}" } },
      ] },
      { role: "tool", tool_call_id: "c1", content: "{\"job_id\":\"1\"}" },
      { role: "tool", tool_call_id: "c2", content: "{\"status\":\"completed\"}" },
    ],
  });
  assert.equal(body.messages.length, 3);
  assert.deepEqual(body.messages[1], { role: "assistant", content: [
    { type: "tool_use", id: "c1", name: "job_create", input: { pipeline: "p" } },
    { type: "tool_use", id: "c2", name: "job_get", input: { job_id: "1" } },
  ] });
  // Both results folded into ONE user turn, as Anthropic requires.
  assert.equal(body.messages[2].role, "user");
  assert.deepEqual(body.messages[2].content.map((b) => b.tool_use_id), ["c1", "c2"]);
  assert.equal(body.messages[2].content[0].type, "tool_result");
});

test("Anthropic tool_use response becomes OpenAI tool_calls with finish_reason tool_calls", () => {
  const r = toOpenAiResponse({
    id: "msg_1", stop_reason: "tool_use",
    content: [{ type: "text", text: "Sure." }, { type: "tool_use", id: "tu_1", name: "mcp_list", input: {} }],
    usage: { input_tokens: 10, output_tokens: 5 },
  }, "anthropic/claude-sonnet-4-6");
  const m = r.choices[0].message;
  assert.equal(r.choices[0].finish_reason, "tool_calls");
  assert.equal(m.content, "Sure.");
  assert.deepEqual(m.tool_calls, [{ id: "tu_1", type: "function", function: { name: "mcp_list", arguments: "{}" } }]);
  assert.equal(r.usage.total_tokens, 15);
});

test("plain text response is unchanged in shape; max_tokens maps to length", () => {
  const r = toOpenAiResponse({ id: "m", stop_reason: "max_tokens", content: [{ type: "text", text: "hi" }] });
  assert.equal(r.choices[0].message.content, "hi");
  assert.equal(r.choices[0].message.tool_calls, undefined);
  assert.equal(r.choices[0].finish_reason, "length");
});
