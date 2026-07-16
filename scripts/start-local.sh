#!/bin/bash
# start-local.sh — Start all Sealfleet services for local development
#
# Usage:
#   ./scripts/start-local.sh          Start all services in background
#   ./scripts/start-local.sh --bg     Same as above (explicit background)
#   ./scripts/start-local.sh --stop   Stop all running services
#   ./scripts/start-local.sh --status Show status of all services
#
# Optional DB override:
#   DATABASE_URL=postgresql://admin:***@localhost:54323/mcpfinder ./scripts/start-local.sh
#
# Services started:
#   registry            :8010  (FastAPI)
#   deploy              :8030  (FastAPI)
#   router              :8040  (FastAPI)
#   portal              :3000  (Next.js)

set -euo pipefail

BASE="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$BASE/runtime/.venv/bin/python"
PIDFILE="$BASE/runtime/.local-pids"
LOGDIR="$BASE/runtime/logs"
DB_URL="${DATABASE_URL:-postgresql://admin:${PGPASSWORD:?set PGPASSWORD or DATABASE_URL for local Postgres}@localhost:54323/mcpfinder}"
REGISTRY_PORT="${REGISTRY_PORT:-8010}"
DEPLOY_PORT="${DEPLOY_PORT:-8030}"
ROUTER_PORT="${ROUTER_PORT:-8040}"
PORTAL_PORT="${PORTAL_PORT:-3000}"
AUTH_ALLOW_EPHEMERAL_KEYS="${AUTH_ALLOW_EPHEMERAL_KEYS:-1}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# ── Service definitions ────────────────────────────────────────────
# Format: name|dir|module|port|type
# type: python or node
SERVICES=(
  "registry|${BASE}/registry|server:app|${REGISTRY_PORT}|python"
  "deploy|${BASE}/deploy|server:app|${DEPLOY_PORT}|python"
  "router|${BASE}/runtime|router:app|${ROUTER_PORT}|python"
  "portal|${BASE}/portal|dev|${PORTAL_PORT}|node"
)

# ── Helpers ────────────────────────────────────────────────────────

port_in_use() {
  local port=$1
  ss -tlnp 2>/dev/null | grep -q ":${port} " && return 0
  return 1
}

health_check() {
  local name=$1
  local port=$2
  local type=$3
  local max_attempts=10
  local attempt=0

  while [ $attempt -lt $max_attempts ]; do
    attempt=$((attempt + 1))
    if [ "$type" = "node" ]; then
      code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${port}/" 2>/dev/null || true)
    else
      code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${port}/health" 2>/dev/null || true)
    fi
    if [[ "$code" =~ ^[0-9]{3}$ ]] && [ "$code" != "000" ] && [ "$code" != "502" ]; then
      echo -e "  ${GREEN}✓${NC} ${name} (${port}): HTTP ${code}"
      return 0
    fi
    sleep 1
  done
  echo -e "  ${RED}✗${NC} ${name} (${port}): not responding after ${max_attempts}s"
  return 1
}

check_postgres() {
  echo -n "Checking PostgreSQL on port 54323... "
  if port_in_use 54323; then
    echo -e "${GREEN}OK${NC}"
    return 0
  else
    echo -e "${RED}NOT RUNNING${NC}"
    echo -e "${YELLOW}Start PostgreSQL first (e.g. supabase start, or docker compose up db)${NC}"
    return 1
  fi
}

# ── Stop mode ──────────────────────────────────────────────────────

stop_services() {
  if [ ! -f "$PIDFILE" ]; then
    echo "No PID file found at $PIDFILE. Nothing to stop."
    exit 0
  fi

  echo -e "${YELLOW}Stopping Sealfleet services...${NC}"
  local failed=0
  while IFS=' ' read -r pid name; do
    if [ -z "$pid" ] || [ -z "$name" ]; then
      continue
    fi
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null
      # Wait up to 3s for graceful shutdown
      for i in 1 2 3; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
      done
      # Force kill if still alive
      if kill -0 "$pid" 2>/dev/null; then
        kill -9 "$pid" 2>/dev/null || true
      fi
      echo -e "  Stopped ${name} (PID ${pid})"
    else
      echo -e "  ${name} (PID ${pid}) already stopped"
    fi
  done < "$PIDFILE"

  rm -f "$PIDFILE"
  echo -e "${GREEN}All services stopped.${NC}"
}

# ── Status mode ────────────────────────────────────────────────────

show_status() {
  echo -e "${CYAN}Sealfleet service status:${NC}"
  echo ""
  for entry in "${SERVICES[@]}"; do
    IFS='|' read -r name dir module port type <<< "$entry"
    if port_in_use "$port"; then
      echo -e "  ${GREEN}●${NC} ${name} (port ${port}): running"
    else
      echo -e "  ${RED}●${NC} ${name} (port ${port}): stopped"
    fi
  done
  echo ""
  if [ -f "$PIDFILE" ]; then
    echo "PID file: $PIDFILE"
    cat "$PIDFILE"
  else
    echo "No PID file found."
  fi
}

# ── Start mode ─────────────────────────────────────────────────────

start_services() {
  # Preflight checks
  if [ ! -x "$VENV" ]; then
    echo -e "${RED}Python venv not found at ${VENV}${NC}"
    echo "Create it: python3 -m venv ${BASE}/runtime/.venv && pip install -r ${BASE}/runtime/requirements.txt"
    exit 1
  fi

  check_postgres || exit 1

  mkdir -p "$LOGDIR"
  > "$PIDFILE"

  echo -e "${GREEN}Starting Sealfleet services...${NC}"
  echo ""

  local all_ok=true

  for entry in "${SERVICES[@]}"; do
    IFS='|' read -r name dir module port type <<< "$entry"

    # Skip if already running
    if port_in_use "$port"; then
      echo -e "  ${YELLOW}⊘${NC} ${name} (${port}): already running, skipping"
      continue
    fi

    echo -n "  Starting ${name} on port ${port}... "

    if [ "$type" = "python" ]; then
      cd "$dir"
      DATABASE_URL="$DB_URL" \
      AUTH_ALLOW_EPHEMERAL_KEYS="$AUTH_ALLOW_EPHEMERAL_KEYS" \
      PYTHONPATH="$BASE:${PYTHONPATH:-}" \
      PYTHONUNBUFFERED=1 \
        "$VENV" -m uvicorn "$module" --host 0.0.0.0 --port "$port" \
        > "${LOGDIR}/${name}.log" 2>&1 &
      local pid=$!
      echo "$pid $name" >> "$PIDFILE"
      echo -e "${GREEN}PID ${pid}${NC}"

    elif [ "$type" = "node" ]; then
      cd "$dir"
      PORT="$port" npm run dev \
        > "${LOGDIR}/${name}.log" 2>&1 &
      local pid=$!
      echo "$pid $name" >> "$PIDFILE"
      echo -e "${GREEN}PID ${pid}${NC}"
    fi
  done

  echo ""
  echo -e "${CYAN}Running health checks...${NC}"
  echo ""

  for entry in "${SERVICES[@]}"; do
    IFS='|' read -r name dir module port type <<< "$entry"
    health_check "$name" "$port" "$type" || all_ok=false
  done

  echo ""
  echo -e "${GREEN}Startup complete.${NC}"
  echo -e "  PIDs:  ${PIDFILE}"
  echo -e "  Logs:  ${LOGDIR}/"
  echo -e "  Stop:  ${YELLOW}$0 --stop${NC}"
  echo ""

  if [ "$all_ok" = false ]; then
    echo -e "${YELLOW}Warning: Some services failed health checks. Check logs for details.${NC}"
    echo ""
  fi
}

# ── Main ───────────────────────────────────────────────────────────

case "${1:-}" in
  --stop)
    stop_services
    ;;
  --status)
    show_status
    ;;
  --bg|"")
    start_services
    ;;
  *)
    echo "Usage: $0 [--bg | --stop | --status]"
    exit 1
    ;;
esac
