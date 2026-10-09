"""Pipeline selection survives a malformed arguments object.

Small local models (Qwen2.5-7B on spark-infer) echo the prompt's
{"<param>": ...} placeholder and return {"tool": "platform", "arguments": {"}}.
The tool choice is right; dropping it to the keyword fallback lost the turn.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent"))

import pytest  # noqa: E402

pytest.importorskip("openai")
import server  # noqa: E402  (agent/server.py)


def test_valid_json_is_parsed_as_is():
    assert server._parse_selection('{"tool": "weather", "arguments": {"cities": ["Oslo"]}}') == {
        "tool": "weather", "arguments": {"cities": ["Oslo"]},
    }


def test_malformed_arguments_keep_the_tool_choice():
    assert server._parse_selection('{"tool": "platform", "arguments": {"}}') == {
        "tool": "platform", "arguments": {},
    }


def test_malformed_null_tool_stays_null():
    assert server._parse_selection('{"tool": null, "arguments": {"}}')["tool"] is None


def test_no_tool_key_still_raises():
    with pytest.raises(json.JSONDecodeError):
        server._parse_selection('{"arguments": {"}}')


TOOLS = [{"name": "weather_trip_planner"}]


def test_sentinel_without_underscores_maps_back():
    # Markdown-trained models render __platform__ as bold and answer "platform".
    assert server._normalize_sentinel("platform", TOOLS) == server.PLATFORM_TOOL
    assert server._normalize_sentinel("create_pipeline", TOOLS) == server.CREATE_PIPELINE_TOOL
    assert server._normalize_sentinel(server.PLATFORM_TOOL, TOOLS) == server.PLATFORM_TOOL


def test_real_pipeline_of_the_same_name_wins():
    assert server._normalize_sentinel("platform", [{"name": "platform"}]) == "platform"
    assert server._normalize_sentinel("weather_trip_planner", TOOLS) == "weather_trip_planner"


def test_system_prefix_prepends_to_existing_system_turn():
    msgs = [{"role": "system", "content": "You operate"}, {"role": "user", "content": "hi"}]
    out = server._with_system_prefix(msgs, "/no_think")
    assert out[0]["content"] == "/no_think\nYou operate" and out[1] == msgs[1]


def test_system_prefix_inserts_system_turn_when_missing():
    out = server._with_system_prefix([{"role": "user", "content": "hi"}], "/no_think")
    assert out == [{"role": "system", "content": "/no_think"}, {"role": "user", "content": "hi"}]


SCHEMA_MCP_GET = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
SCHEMA_DEPLOY = {"type": "object", "properties": {"repo_url": {"type": "string"}, "name": {"type": "string"}},
                 "required": ["repo_url", "name"]}


def test_repair_renames_single_misnamed_required_arg():
    assert server._repair_tool_args({"mcp": "weather-trip-mcp"}, SCHEMA_MCP_GET) == {"name": "weather-trip-mcp"}
    assert server._repair_tool_args({"mcp": "fx-mcp", "repo_url": "https://x"}, SCHEMA_DEPLOY) == {
        "name": "fx-mcp", "repo_url": "https://x"}


def test_repair_leaves_ambiguous_or_valid_args_alone():
    assert server._repair_tool_args({"name": "a"}, SCHEMA_MCP_GET) == {"name": "a"}
    assert server._repair_tool_args({"x": 1, "y": 2}, SCHEMA_MCP_GET) == {"x": 1, "y": 2}
    assert server._repair_tool_args({}, SCHEMA_DEPLOY) == {}
    assert server._repair_tool_args({"mcp": "a"}, None) == {"mcp": "a"}


SCHEMAS = {"job_create": {}, "tool_invoke": {}, "job_get": {}}


def test_self_check_flags_claimed_action_without_a_write():
    msg = server._answer_needs_correction("Started job 3421888888888888 (uptime_report, queued).", set(), SCHEMAS)
    assert msg and "not called any tool" in msg


def test_self_check_allows_claims_backed_by_a_write():
    assert server._answer_needs_correction("Started job abc (queued).", {"job_create:{}"}, SCHEMAS) is None


def test_self_check_flags_leaked_tool_markup_naming_an_unknown_tool():
    msg = server._answer_needs_correction("<tool_call>\n<function=tool_call>\n</function>\n</tool_call>", set(), SCHEMAS)
    assert msg and "'tool_call' is not one of your tools" in msg and "tool_invoke" in msg


def test_self_check_passes_plain_answers_and_questions():
    assert server._answer_needs_correction("Which pipeline should I delete? I can see: a, b.", set(), SCHEMAS) is None
    assert server._answer_needs_correction("There are 3 MCP servers registered: a, b, c.", set(), SCHEMAS) is None
    assert server._answer_needs_correction(
        "Scaling x to zero stops its pods; nothing is deleted. I haven't done anything.", set(), SCHEMAS) is None


def test_self_check_ignores_explanations_with_past_participles():
    for text in ["Credentials are encrypted at rest; a key that is stored here is never shown to the model.",
                 "Deleting removes a runtime-authored pipeline's definition; shipped examples are protected.",
                 "Cancelling ends a queued or running job; finished steps aren't rolled back.",
                 "I haven't done anything — say \"scale x down\" if you want me to."]:
        assert server._answer_needs_correction(text, set(), SCHEMAS) is None, text


def test_self_check_catches_first_person_and_summary_claims():
    for text in ["I've deleted the pipeline nordic_weather.", "The MCP has been deployed.",
                 "Deleted the pipeline old.", "fx-mcp is now scaled down to zero."]:
        assert server._answer_needs_correction(text, set(), SCHEMAS), text
