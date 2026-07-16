#!/usr/bin/env bash
#
# mcpfinder CLI end-to-end harness.
#
# Exercises the full agent-facing CLI surface (read, execute, control-plane,
# negative/fail-honestly paths) against a LIVE local mcpfinder instance, with
# per-check pass/fail accounting. Intended for public-preview validation and CI.
#
# Requirements:
#   - A running local cluster (router :8040, deploy :8030). See start-local.sh.
#   - MCPFINDER_API_KEY set to a key with the agent-operator action set
#     (pipeline.invoke, registry.export, ...). The seeded local-dev key from
#     scripts/001_create_api_keys.sql qualifies.
#
# Usage:
#   MCPFINDER_API_KEY=... ./scripts/cli_e2e.sh
#
# Env overrides:
#   PY                     python interpreter (default: runtime/.venv/bin/python)
#   MCPFINDER_RUNTIME_URL  default http://localhost:8040
#   MCPFINDER_DEPLOY_URL   default http://localhost:8030
#
set -uo pipefail

ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

PY="${PY:-runtime/.venv/bin/python}"
[ -x "$PY" ] || PY="python3"
CLI=("$PY" -m runtime.cli)

export MCPFINDER_RUNTIME_URL="${MCPFINDER_RUNTIME_URL:-http://localhost:8040}"
export MCPFINDER_DEPLOY_URL="${MCPFINDER_DEPLOY_URL:-http://localhost:8030}"

PASS=0
FAIL=0
FAILED_NAMES=()

# run a CLI invocation and assert its exit code.
#   check "<name>" <expected_rc> -- <cli args...>
check() {
  local name="$1" expected_rc="$2"
  shift 2
  [ "$1" = "--" ] && shift
  local out rc
  out="$("${CLI[@]}" "$@" 2>&1)"
  rc=$?
  if [ "$rc" -eq "$expected_rc" ]; then
    printf '  \033[32mPASS\033[0m  %-46s (rc=%s)\n' "$name" "$rc"
    PASS=$((PASS + 1))
  else
    printf '  \033[31mFAIL\033[0m  %-46s (rc=%s, expected %s)\n' "$name" "$rc" "$expected_rc"
    printf '        %s\n' "$(printf '%s' "$out" | tail -3 | tr '\n' '|')"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
  fi
}

section() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

if [ -z "${MCPFINDER_API_KEY:-}" ]; then
  echo "ERROR: MCPFINDER_API_KEY is not set. Export an agent-operator key first." >&2
  exit 3
fi

CFG="$(mktemp -t mcpfinder-cli-config.XXXXXX.json)"
printf '{"schema":"mcpfinder.cli.config/v1","runtime_url":"%s","deploy_url":"%s","cluster_mode":"k3d","kube_context":"k3d-mcpfinder"}\n' \
  "$MCPFINDER_RUNTIME_URL" "$MCPFINDER_DEPLOY_URL" > "$CFG"
trap 'rm -f "$CFG"' EXIT

section "Offline contract"
check "contract"                 0 -- contract
check "validate (config file)"   0 -- validate --config "$CFG"

section "Read paths"
check "status"                   0 -- status
check "cluster status (k3d)"     0 -- cluster status --mode k3d --kube-context k3d-mcpfinder
check "manifest list"            0 -- manifest list
check "manifest get weather-mcp" 0 -- manifest get weather-mcp
check "pipeline list"            0 -- pipeline list
check "pipeline get + type-check" 0 -- pipeline get weather_trip_planner --engine v2

section "Execute paths"
# --timeout 60 tolerates scale-from-zero cold starts on idle MCPs.
check "invoke weather-mcp.get_weather" 0 -- \
  invoke --mcp weather-mcp --tool get_weather --payload '{"location":"Stockholm"}' --timeout 60
check "pipeline run v2 (sync)"   0 -- \
  pipeline run --name sector_research --engine v2 --inputs '{"sni_code":"62010","max_companies":3}' --timeout 60

section "Workflow facade (v1 named + async job)"
JOB_OUT="$("${CLI[@]}" --json workflow run --name weather_trip_planner --engine v2 --inputs '{"cities":["Stockholm"]}' 2>&1)"
JOB_RC=$?
JOB_ID="$(printf '%s' "$JOB_OUT" | "$PY" -c 'import sys,json
try:
    d=json.load(sys.stdin)
    print(((d.get("response") or {}).get("body") or {}).get("job_id",""))
except Exception: print("")' 2>/dev/null)"
if [ "$JOB_RC" -eq 0 ] && [ -n "$JOB_ID" ]; then
  printf '  \033[32mPASS\033[0m  %-46s (job_id=%s)\n' "workflow run -> job submitted" "$JOB_ID"
  PASS=$((PASS + 1))
else
  printf '  \033[31mFAIL\033[0m  %-46s (rc=%s)\n' "workflow run -> job submitted" "$JOB_RC"
  printf '        %s\n' "$(printf '%s' "$JOB_OUT" | tail -3 | tr '\n' '|')"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("workflow run")
fi
if [ -n "$JOB_ID" ]; then
  check "workflow status (by id)" 0 -- workflow status --job-id "$JOB_ID"
  check "workflow status (list)"  0 -- workflow status --list
  check "workflow cancel"         0 -- workflow cancel --job-id "$JOB_ID"
fi

section "Control-plane"
check "registry export"          0 -- registry export

section "Smoke"
check "smoke zero-to-hero"       0 -- smoke zero-to-hero

section "Negative / fail-honestly (expect rc=2)"
check "invoke unknown tool -> rc2"    2 -- \
  invoke --mcp weather-mcp --tool no_such_tool --payload '{}'
check "invoke unknown mcp -> rc2"     2 -- \
  invoke --mcp no_such_mcp --tool get_weather --payload '{}'

# Missing auth: clear the env key AND pass no --api-key so the CLI must fail
# closed with auth_missing (rc=2) rather than silently succeeding.
auth_out="$(env -u MCPFINDER_API_KEY "${CLI[@]}" registry export 2>&1)"; auth_rc=$?
if [ "$auth_rc" -eq 2 ]; then
  printf '  \033[32mPASS\033[0m  %-46s (rc=2)\n' "missing auth -> rc2"; PASS=$((PASS + 1))
else
  printf '  \033[31mFAIL\033[0m  %-46s (rc=%s, expected 2)\n' "missing auth -> rc2" "$auth_rc"
  printf '        %s\n' "$(printf '%s' "$auth_out" | tail -2 | tr '\n' '|')"
  FAIL=$((FAIL + 1)); FAILED_NAMES+=("missing auth")
fi

section "Summary"
printf 'PASS=%s  FAIL=%s\n' "$PASS" "$FAIL"
if [ "$FAIL" -ne 0 ]; then
  printf 'Failed: %s\n' "${FAILED_NAMES[*]}"
  exit 1
fi
echo "All CLI e2e checks passed."
