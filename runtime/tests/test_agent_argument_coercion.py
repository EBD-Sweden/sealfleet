"""The chat agent bends LLM-extracted arguments to the pipeline's schema.

Models hand back "Stockholm, Barcelona" for an array input or "27" for a
number; the MCP then 400s and the user is told the request was invalid.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent"))

import pytest  # noqa: E402

# agent/server.py builds an OpenAI client at import; the router test image may
# not ship that package. Skip cleanly rather than fail collection.
pytest.importorskip("openai")
import server  # noqa: E402  (agent/server.py)

SCHEMA = {"properties": {
    "cities": {"type": "array"}, "target_temp_c": {"type": "number"},
    "limit": {"type": "integer"}, "strict": {"type": "boolean"}, "label": {"type": "string"},
}}


def test_comma_and_conjunction_strings_become_lists():
    assert server.coerce_arguments({"cities": "Stockholm, Barcelona"}, SCHEMA)["cities"] == ["Stockholm", "Barcelona"]
    assert server.coerce_arguments({"cities": "Oslo and Bergen"}, SCHEMA)["cities"] == ["Oslo", "Bergen"]
    assert server.coerce_arguments({"cities": "Rome"}, SCHEMA)["cities"] == ["Rome"]


def test_lists_and_unknown_keys_pass_through():
    args = {"cities": ["Oslo"], "mystery": "x"}
    assert server.coerce_arguments(args, SCHEMA) == args


def test_numeric_strings_become_numbers():
    out = server.coerce_arguments({"target_temp_c": "27", "limit": "5"}, SCHEMA)
    assert out["target_temp_c"] == 27 and isinstance(out["target_temp_c"], int)
    assert out["limit"] == 5
    assert server.coerce_arguments({"target_temp_c": "21.5°C"}, SCHEMA)["target_temp_c"] == 21.5


def test_bad_values_are_left_for_the_pipeline_to_reject():
    assert server.coerce_arguments({"target_temp_c": "warm"}, SCHEMA)["target_temp_c"] == "warm"


def test_step_errors_are_surfaced_from_a_200_run():
    run = {"results": {
        "fetch": {"mcp": "weather-trip-mcp", "tool": "fetch_cities_weather",
                  "output": {"error": "HTTP 400", "detail": '{"detail":"\'cities\' must be a non-empty list"}'}},
        "rank": {"output": {"ranking": []}},
    }}
    errs = server.step_errors(run)
    assert errs == ["weather-trip-mcp.fetch_cities_weather: 'cities' must be a non-empty list"]
    assert server.step_errors({"results": {"ok": {"output": {"x": 1}}}}) == []


# --- MCP → OpenAI tool conversion for the platform loop -------------------

def test_mcp_tools_convert_to_openai_function_tools():
    mcp_tools = [{"name": "pipeline_run", "description": "Run it",
                  "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
                 {"name": "platform_status", "description": "Health"}]
    out = server._as_openai_tools(mcp_tools)
    assert [t["type"] for t in out] == ["function", "function"]
    assert out[0]["function"]["name"] == "pipeline_run"
    assert out[0]["function"]["parameters"]["required"] == ["name"]
    # A tool without a schema still gets a valid empty object schema.
    assert out[1]["function"]["parameters"] == {"type": "object", "properties": {}}


def test_platform_pseudo_tool_is_offered_in_selection_prompt():
    assert server.PLATFORM_TOOL == "__platform__"
    assert server.CREATE_PIPELINE_TOOL != server.PLATFORM_TOOL
