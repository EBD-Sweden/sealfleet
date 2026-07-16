"""Tests for TypeGraph in isolation (pure Python, no HTTP, no DB)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from router import TypeGraph


def _types_registry():
    """Minimal types registry for testing."""
    return {
        "String": {"primitive": True},
        "Float": {"primitive": True},
        "Integer": {"primitive": True},
        "WeatherData": {
            "description": "Weather data for a location",
            "fields": {
                "temperature": {"type": "float"},
                "conditions": {"type": "string"},
            },
        },
        "OutfitRecommendation": {
            "description": "What to wear",
            "fields": {
                "outfit": {"type": "string"},
            },
        },
    }


def _weather_manifest():
    """Manifest for a weather MCP with typed tools."""
    return {
        "name": "weather-mcp",
        "tools": [
            {
                "name": "get_weather",
                "inputs": {
                    "location": {"type": "String", "required": True, "description": "City name"},
                },
                "outputs": {
                    "weather": {"type": "WeatherData"},
                },
            },
        ],
    }


def _clothes_manifest():
    """Manifest for a clothes MCP that consumes WeatherData."""
    return {
        "name": "clothes-mcp",
        "tools": [
            {
                "name": "recommend_outfit",
                "inputs": {
                    "weather": {"type": "WeatherData", "required": True},
                },
                "outputs": {
                    "outfit": {"type": "OutfitRecommendation"},
                },
            },
        ],
    }


def test_type_graph_register_and_lookup():
    """Register a tool that outputs 'WeatherData', verify producers list."""
    tg = TypeGraph()
    tg.register_manifest(_weather_manifest())

    assert "WeatherData" in tg.producers
    producers = tg.producers["WeatherData"]
    assert ("weather-mcp", "get_weather") in producers


def test_type_graph_chain_resolution():
    """Register weather + clothes tools, resolve chain for OutfitRecommendation."""
    tg = TypeGraph()
    types_reg = _types_registry()

    tg.register_manifest(_weather_manifest())
    tg.register_manifest(_clothes_manifest())

    chain = tg.resolve("OutfitRecommendation", {"location": "Stockholm"}, types_reg)
    assert len(chain) == 2
    assert chain[0]["mcp"] == "weather-mcp"
    assert chain[0]["tool"] == "get_weather"
    assert chain[1]["mcp"] == "clothes-mcp"
    assert chain[1]["tool"] == "recommend_outfit"


def test_type_graph_no_chain_found():
    """resolve_chain for unknown output type → raises ValueError."""
    tg = TypeGraph()
    types_reg = _types_registry()

    with pytest.raises(ValueError, match="No tool produces type"):
        tg.resolve("UnknownType", {}, types_reg)


def test_type_graph_circular_dependency():
    """Register tools where A needs B output and B needs A output.
    Should not infinite loop — raises ValueError."""
    tg = TypeGraph()

    # TypeA needs TypeB, TypeB needs TypeA
    types_reg = {
        "TypeA": {"description": "Type A"},
        "TypeB": {"description": "Type B"},
    }

    tg.register_manifest({
        "name": "mcp-a",
        "tools": [{
            "name": "make_a",
            "inputs": {"b": {"type": "TypeB", "required": True}},
            "outputs": {"a": {"type": "TypeA"}},
        }],
    })
    tg.register_manifest({
        "name": "mcp-b",
        "tools": [{
            "name": "make_b",
            "inputs": {"a": {"type": "TypeA", "required": True}},
            "outputs": {"b": {"type": "TypeB"}},
        }],
    })

    with pytest.raises(ValueError, match="Circular dependency"):
        tg.resolve("TypeA", {}, types_reg)


def test_type_graph_single_step_primitive_inputs():
    """Resolve a single-step chain with only primitive inputs."""
    tg = TypeGraph()
    types_reg = _types_registry()
    tg.register_manifest(_weather_manifest())

    chain = tg.resolve("WeatherData", {"location": "Berlin"}, types_reg)
    assert len(chain) == 1
    assert chain[0]["mcp"] == "weather-mcp"
    assert chain[0]["tool"] == "get_weather"
    assert chain[0]["inputs"]["location"] == "Berlin"


def test_type_graph_missing_required_input():
    """Resolving a chain with missing required primitive input raises ValueError."""
    tg = TypeGraph()
    types_reg = _types_registry()
    tg.register_manifest(_weather_manifest())

    with pytest.raises(ValueError, match="Required input 'location'"):
        tg.resolve("WeatherData", {}, types_reg, strict=True)


def test_type_graph_validate_manifests():
    """validate_all_manifests reports unknown types."""
    tg = TypeGraph()
    types_reg = {"String": {"primitive": True}}

    tg.register_manifest({
        "name": "test-mcp",
        "tools": [{
            "name": "test_tool",
            "inputs": {"x": {"type": "UnknownInput"}},
            "outputs": {"y": {"type": "UnknownOutput"}},
        }],
    })

    issues = tg.validate_all_manifests({}, types_reg)
    assert any("UnknownOutput" in i for i in issues)
    assert any("UnknownInput" in i for i in issues)
