# Named Pipeline Skill Contract

Named pipelines are YAML-defined, multi-stage MCP tool chains exposed as black-box callable tools. The runtime router loads, validates, and executes them.

## What a Named Pipeline Is

A named pipeline chains multiple MCP tools together through typed channels. Each stage calls one tool on one MCP, reads from an input channel, and writes to an output channel. The router orchestrates execution in stage order, passing outputs from one stage as inputs to the next.

Named pipelines are:
- **Declarative** — defined in YAML, not code
- **Validated** — the router checks all MCPs and tools exist on startup
- **Observable** — every run produces a trace with per-stage timing
- **Callable as MCP tools** — exposed via `/pipelines/tools` in MCP tools/list format

## `named_pipeline.yaml` Required Fields

```yaml
name: string              # Unique identifier (e.g. "my_pipeline")
description: string       # Human-readable purpose
inputs:                   # Parameters the pipeline accepts
  param_name:
    type: String          # Type (String, Integer, Float, etc.)
    description: string   # What this input represents
stages:                   # Ordered list of stages
  - name: string          # Stage identifier (unique within pipeline)
    mcp: string           # Registered MCP name (must exist in manifests)
    tool: string          # Tool name on that MCP
    input_channel: string | null   # Channel to read from (null for first stage)
    output_channel: string | null  # Channel to publish to
    input_type: string | null      # Expected input type
    output_type: string | null     # Produced output type
output_stage: string      # Name of the stage whose result is the final output
tags: [string]            # Optional categorization tags
```

## How Stages Chain

Stages execute **in order**. Data flows through channels:

1. **First stage**: Receives the pipeline's raw `inputs` (e.g. `{"location": "Stockholm"}`)
2. **Subsequent stages**: If `input_channel` is set, the stage receives the **previous stage's output** merged with the raw inputs. If `input_channel` is null, it receives only the raw inputs.
3. **Final output**: The result of `output_stage` is returned as the pipeline result.

```
User Input ──> Stage 1 (no input_channel) ──> Stage 2 (input_channel: weather.current) ──> Output
                 │                                │
                 └─ publishes to                   └─ reads from
                    weather.current                   weather.current
```

Channels are declarative metadata — the router uses them to determine data flow, not as separate pub/sub queues during pipeline execution.

## Input/Output Type Contracts

Types define the schema contract between stages:

- `input_type`: What the stage expects to receive (e.g. `String`, `WeatherData`)
- `output_type`: What the stage produces (e.g. `WeatherData`, `OutfitRecommendation`)
- Types must match: if Stage 2 has `input_type: WeatherData`, Stage 1 must have `output_type: WeatherData`
- Types are defined in `runtime/types.yaml` and validated by the type graph

## Registering a New Pipeline

### Option 1: YAML file (persistent, auto-loaded)

Create `runtime/pipelines/<pipeline-name>/named_pipeline.yaml`. The router loads all `named_pipeline.yaml` files on startup.

### Option 2: API (runtime, in-memory)

```bash
curl -X POST http://localhost:8040/pipelines/register \
  -H "Content-Type: application/json" \
  -d '{
    "pipeline": {
      "name": "my_pipeline",
      "description": "...",
      "inputs": {"city": {"type": "String", "description": "City name"}},
      "stages": [...],
      "output_stage": "final_stage"
    }
  }'
```

## Calling a Pipeline

### As a named pipeline:
```bash
curl -X POST http://localhost:8040/pipelines/my_pipeline/run \
  -H "Content-Type: application/json" \
  -d '{"inputs": {"location": "Stockholm"}}'
```

### As an MCP tool:
```bash
# List available pipeline tools
curl http://localhost:8040/pipelines/tools

# Call a pipeline tool
curl -X POST http://localhost:8040/pipelines/tools/call \
  -H "Content-Type: application/json" \
  -d '{"name": "my_pipeline", "arguments": {"location": "Stockholm"}}'
```

## Existing Pipelines

No v1 named pipelines ship in the public repository — register your own with
`POST /pipelines/register`. For the v2 engine, `weather_trip_planner`
(`runtime/pipelines/v2/weather_trip_planner.yaml`) is the reference example:
`weather-trip-mcp.fetch_cities_weather` -> `weather-trip-mcp.rank_cities`,
visualized by the portal's Weather Example page.

## Validation and Failure Modes

The router validates on registration/load:

| Check | When | Error |
|-------|------|-------|
| MCP exists in manifests | Register / startup | `Stage 'X' references unknown MCP 'Y'` |
| Tool exists in MCP manifest | Register / startup | `Stage 'X' references unknown tool 'Y' in MCP 'Z'` |
| `output_stage` matches a stage name | Register / startup | `output_stage 'X' not found in stages` |
| Required inputs present | Run | `Missing required input: 'X'` |
| MCP endpoint reachable | Run | HTTP error in step result |

Runtime failures (MCP down, tool errors) are captured per-stage in the trace — the pipeline does not abort, allowing partial results and graceful degradation.
