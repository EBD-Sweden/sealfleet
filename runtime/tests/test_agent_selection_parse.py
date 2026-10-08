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
