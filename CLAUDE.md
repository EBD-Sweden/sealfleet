# Sealfleet Project Guide for Claude Code

## Project Overview

**Sealfleet** is an open-source MCP (Model Context Protocol) Agent Platform that enables organizations to expose capabilities as agent-callable tools with secure execution, policy enforcement, and full observability.

**First wedge use case:** Crypto trading (exchanges have mature APIs, fast integration cycles, clear success metrics)

## Current Phase: Platform Hardening + Transport Flexibility

**Phase 0 MVP: COMPLETE**

**What's running:**
- Full k8s deployment (k3d, 8 services)
- Runtime Router with typed pipelines, named pipelines, channel messaging
- Scale-to-zero (ScaleManager in router.py)
- Registry with PostgreSQL-backed discovery
- Portal with docs, catalog, test console, deploy UI
- Core Agent (LLM-powered natural language → pipeline execution)
- Dual transport modes: HTTP (default) + Docker stdio (one-shot containers)

**Current focus:**
- Transport flexibility (HTTP + stdio per-MCP)
- Platform hardening and production readiness

**Tech stack:** Python (fast prototyping), with Go/Rust later for performance-critical parts

## Architecture Principles

1. **LLM is planner, not secret-holder** — Never expose credentials to the model
2. **Sealed inputs** — Sensitive data bypasses LLM via handles/encrypted blobs
3. **Least privilege** — Scope every tool call by identity/action/data/time
4. **Verifiable execution** — Every action produces trace IDs + audit events

## Working with This Codebase

### Code Organization Best Practices

**Maintain these index files:**

1. **`ARCHITECTURE.md`** — Component relationships, data flow, key decisions
2. **`API.md`** — Public interfaces, function signatures, contract boundaries
3. **`COMPONENTS.md`** — List of all modules with purpose, dependencies, status

When you add/change code:
- Update the relevant index file
- Keep function signatures documented
- Note cross-component dependencies

### Incremental Development

**Start small, expand:**
1. Build the minimal working core first
2. Add one feature at a time
3. Test/run after each addition
4. Commit working states frequently

**Where the core lives now (Phase 0 MVP complete):**
- `runtime/router.py` — the MCP server framework / runtime router (invoke, pipelines, jobs, manifests, auth gates)
- `runtime/cli.py` — agent/operator CLI (`python -m runtime.cli`)
- `broker/`, `policy/`, `observability/` — credential injection, policy enforcement, tracing/audit
- `deploy/` — git-to-Kubernetes MCP deployment; `registry/` — discovery; `portal/` — web UI; `mcps/` — example MCP servers

### Dependencies

**Python packages needed:**
- `mcp` or equivalent MCP SDK
- `fastapi` + `uvicorn` (for REST adapters)
- `grpcio` (for gRPC adapters)
- `pyyaml` (config parsing)
- `opentelemetry-api` (tracing hooks)
- `pydantic` (schema validation)

### Testing Strategy

**MVP phase:** Manual testing is fine
- Can you run `python main.py`?
- Can you call the example tool?
- Do stubs/hooks exist for auth/policy/tracing?

**Later:** Add pytest, contract tests, E2E tests

## Current Status

- **Phase 0 MVP:** COMPLETE
- **Full k8s deployment:** k3d cluster, 8 services running
- **Scale-to-zero:** Implemented (ScaleManager in router.py)
- **Dual transport modes:** HTTP + Docker stdio (per-MCP configuration)
- **Current phase:** Platform hardening + transport flexibility

## Key Files to Read

1. `README.md` — High-level overview
2. This file (`CLAUDE.md`) — Your working guide

## Tips for Success

- **Don't try to build everything at once** — Focus on one component/feature at a time
- **Keep it simple** — Stubs are fine for MVP, polish later
- **Make it runnable** — A working skeleton beats a perfect plan
- **Document as you go** — Update ARCHITECTURE.md and API.md when you add modules
- **Commit often** — Working states > perfect commits

The platform core is built. Start by reading `runtime/router.py` (the runtime router) and `runtime/cli.py` (the CLI), then the relevant component under `broker/`, `policy/`, `deploy/`, `registry/`, `portal/`, or `mcps/`.
