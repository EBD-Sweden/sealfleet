#!/usr/bin/env bash
# entrypoint.sh — start a Sealfleet FastAPI service with OPT-IN app-level TLS.
#
# SOC 2 CC6.7 (encryption in transit), defense-in-depth layer: lets a service
# terminate HTTPS itself (in addition to edge Ingress TLS) when cert paths are
# provided. If no cert is provided it starts plain HTTP exactly as before, so the
# default path is unchanged and nothing breaks.
#
# Env:
#   APP_MODULE        uvicorn target, e.g. "router:app"   (required)
#   APP_PORT          listen port, e.g. "8040"            (required)
#   APP_HOST          bind host (default 0.0.0.0)
#   TLS_CERT_FILE     path to PEM cert  -> enables HTTPS when BOTH cert+key set
#   TLS_KEY_FILE      path to PEM key
#   TLS_CA_CERTS      optional CA bundle (for client-cert verification / mTLS)
#   TLS_VERIFY_CLIENT optional: "required"|"optional" to request client certs
#   UVICORN_EXTRA     optional extra args appended verbatim
#
# Usage in Dockerfile:  CMD ["./entrypoint.sh"]   (with APP_MODULE/APP_PORT set)
set -euo pipefail

HOST="${APP_HOST:-0.0.0.0}"
PORT="${PORT:-${APP_PORT:?APP_PORT or PORT must be set}}"
MODULE="${APP_MODULE:?APP_MODULE must be set}"

args=(uvicorn "$MODULE" --host "$HOST" --port "$PORT")

if [ -n "${TLS_CERT_FILE:-}" ] && [ -n "${TLS_KEY_FILE:-}" ]; then
  echo "[entrypoint] TLS enabled: serving HTTPS on ${HOST}:${PORT}"
  args+=(--ssl-certfile "$TLS_CERT_FILE" --ssl-keyfile "$TLS_KEY_FILE")
  if [ -n "${TLS_CA_CERTS:-}" ]; then
    args+=(--ssl-ca-certs "$TLS_CA_CERTS")
  fi
  # Map a friendly verify setting to uvicorn/ssl cert_reqs values:
  #   ssl.CERT_OPTIONAL=1, ssl.CERT_REQUIRED=2
  case "${TLS_VERIFY_CLIENT:-}" in
    required) args+=(--ssl-cert-reqs 2) ;;
    optional) args+=(--ssl-cert-reqs 1) ;;
  esac
else
  echo "[entrypoint] TLS not configured (no TLS_CERT_FILE/TLS_KEY_FILE); serving plain HTTP on ${HOST}:${PORT}"
fi

if [ -n "${UVICORN_EXTRA:-}" ]; then
  # shellcheck disable=SC2206
  args+=(${UVICORN_EXTRA})
fi

exec "${args[@]}"
