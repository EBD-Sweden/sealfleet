#!/usr/bin/env bash
# End-to-end check of the chat + local-LLM + MCP path against a running stack.
#
#   docker compose --profile local-llm up -d      # (or COMPOSE_PROFILES=local-llm in .env)
#   scripts/e2e-local-llm.sh
#
# Exercises the real services — no mocks. Covers service health, the LLM proxy's
# routing (including that a pinned local request never falls back to a cloud
# backend), the MCP tool catalog, running a pipeline, the chat answering on the
# on-box model, the chat AUTHORING a new pipeline and running it, bring-your-own
# provider credentials, and the delegation authorization matrix.
set -uo pipefail

ROUTER=${ROUTER:-http://localhost:8040}
AGENT=${AGENT:-http://localhost:8050}
PROXY=${PROXY:-http://localhost:3456}
PORTAL=${PORTAL:-http://localhost:3004}
NIM=${NIM:-http://127.0.0.1:8000}

pass=0; fail=0; skipped=0
ok()   { printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass+1)); }
no()   { printf '  \033[31mFAIL\033[0m %s\n    -> %s\n' "$1" "${2:-}"; fail=$((fail+1)); }
skip() { printf '  \033[33mSKIP\033[0m %s (%s)\n' "$1" "${2:-}"; skipped=$((skipped+1)); }
head_() { printf '\n\033[1m%s\033[0m\n' "$1"; }

API_KEY=$(docker compose exec -T core-agent cat /keys/runtime-api-key 2>/dev/null | tr -d '\r\n')
[ -n "$API_KEY" ] || { echo "could not read the runtime API key — is the stack up?"; exit 1; }

# A platform admin to delegate as; credential work is authorized as a real user.
read -r ADMIN_ID ADMIN_TENANT <<<"$(docker compose exec -T db psql -U admin -d mcpfinder -t -A -F' ' \
  -c "SELECT id, tenant_id FROM users WHERE is_admin AND is_active LIMIT 1;" 2>/dev/null | tr -d '\r')"
[ -n "${ADMIN_ID:-}" ] || { echo "no active platform admin found"; exit 1; }

AUTH=(-H "X-API-Key: $API_KEY")
AS_ADMIN=(-H "X-McpFinder-User-Id: $ADMIN_ID" -H "X-McpFinder-Tenant-Id: $ADMIN_TENANT")

jqp() { python3 -c "import json,sys;d=json.load(sys.stdin);print($1)" 2>/dev/null; }
code() { curl -s -o /dev/null -w '%{http_code}' -m "${2:-10}" "${@:3}" "$1"; }

# --------------------------------------------------------------------------
head_ "1. Service health"
for svc in "router $ROUTER/health" "agent $AGENT/health" "proxy $PROXY/health" "registry http://localhost:8010/health"; do
  set -- $svc
  c=$(code "$2")
  [ "$c" = 200 ] && ok "$1 healthy" || no "$1 healthy" "HTTP $c"
done
c=$(code "$PORTAL"); [ "$c" = 200 ] && ok "portal serving" || no "portal serving" "HTTP $c"

# --------------------------------------------------------------------------
head_ "2. LLM proxy configuration"
H=$(curl -s -m 10 "$PROXY/health")
[ "$(echo "$H" | jqp "d['local']['enabled']")" = "True" ] \
  && ok "local backend enabled" || no "local backend enabled" "$H"
[ -n "$(echo "$H" | jqp "','.join(d['byo']['providers'])")" ] \
  && ok "BYO providers advertised" || no "BYO providers advertised" "$H"

NIM_UP=$(code "$NIM/v1/models" 5)
[ "$NIM_UP" = 200 ] && ok "on-box model serving" || skip "on-box model serving" "HTTP $NIM_UP"

# --------------------------------------------------------------------------
head_ "3. Local model inference"
if [ "$NIM_UP" = 200 ]; then
  MODEL=$(curl -s -m 10 "$NIM/v1/models" | jqp "d['data'][0]['id']")
  R=$(curl -s -m 180 -X POST "$PROXY/v1/chat/completions" -H 'Content-Type: application/json' \
      -d "{\"model\":\"local/$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: ok\"}],\"max_tokens\":400,\"temperature\":0}")
  C=$(echo "$R" | jqp "d['choices'][0]['message']['content'] or ''")
  [ -n "$C" ] && ok "pinned local completion (content: $(echo "$C" | tr -d '\n' | cut -c1-30))" \
              || no "pinned local completion" "$(echo "$R" | cut -c1-200)"
  S=$(echo "$R" | jqp "d['model']")
  [ "$S" = "$MODEL" ] && ok "served by the on-box model ($S)" || no "served by on-box model" "got $S"
else
  skip "pinned local completion" "model not serving"; skip "served by on-box model" "model not serving"
fi

# The safety property: a pinned local request must never reach a cloud backend.
R=$(curl -s -m 30 -X POST "$PROXY/v1/chat/completions" -H 'Content-Type: application/json' \
    -d '{"model":"local/definitely-not-a-real-model","messages":[{"role":"user","content":"hi"}],"max_tokens":10}')
echo "$R" | grep -qi "local llm" \
  && ok "pinned local fails closed (no cloud fallback)" \
  || no "pinned local fails closed" "$(echo "$R" | cut -c1-200)"

# --------------------------------------------------------------------------
head_ "4. MCP layer"
M=$(curl -s -m 10 "${AUTH[@]}" "$ROUTER/manifests")
N=$(echo "$M" | jqp "len(d)")
[ "${N:-0}" -ge 2 ] && ok "manifests registered ($N)" || no "manifests registered" "got ${N:-none}"

T=$(curl -s -m 20 "${AUTH[@]}" "$ROUTER/mcp-tools")
TOOLS=$(echo "$T" | jqp "sum(len(m['tools']) for m in d['mcps'])")
[ "${TOOLS:-0}" -ge 4 ] && ok "MCP tool catalog with schemas ($TOOLS tools)" || no "MCP tool catalog" "$T"
echo "$T" | grep -q '"warning"' && no "all MCPs reachable" "at least one returned a warning" \
                                || ok "all MCPs reachable"

# Direct MCP tool invocation through the router.
R=$(curl -s -m 30 -X POST "${AUTH[@]}" -H 'Content-Type: application/json' "$ROUTER/call" \
    -d '{"mcp":"demo-sandbox-mcp","tool":"summarize_fake_invoice","inputs":{"invoice_id":"INV-E2E-1","amount_usd":100,"vendor_name":"E2E"}}')
echo "$R" | grep -qiE "error|not found" && no "direct MCP tool call" "$(echo "$R" | cut -c1-200)" \
                                        || ok "direct MCP tool call"

# --------------------------------------------------------------------------
head_ "5. Pipelines"
P=$(curl -s -m 10 "${AUTH[@]}" "$ROUTER/v2/pipelines")
PN=$(echo "$P" | jqp "len(d['pipelines'])")
[ "${PN:-0}" -ge 2 ] && ok "v2 pipelines loaded ($PN)" || no "v2 pipelines loaded" "$P"
echo "$P" | jqp "all(p['input_schema']['properties'] is not None for p in d['pipelines'])" | grep -q True \
  && ok "pipelines expose input schemas" || no "pipelines expose input schemas" "$P"

R=$(curl -s -m 90 -X POST "${AUTH[@]}" -H 'Content-Type: application/json' "$ROUTER/v2/pipelines/run" \
    -d '{"pipeline":"weather_trip_planner","inputs":{"cities":["Stockholm"]}}')
echo "$R" | jqp "'mcp' in list(d['results'].values())[0]" | grep -q True \
  && ok "pipeline run reports mcp/tool per step" || no "pipeline run step metadata" "$(echo "$R" | cut -c1-200)"

# --------------------------------------------------------------------------
head_ "6. Chat on the on-box model"
if [ "$NIM_UP" = 200 ]; then
  R=$(curl -s -m 300 -X POST "$AGENT/ask" -H 'Content-Type: application/json' "${AS_ADMIN[@]}" \
      -d "{\"question\":\"Compare the weather in Oslo and Bergen\",\"model\":\"local/$MODEL\"}")
  CH=$(echo "$R" | jqp "len(d['resolved_chain'])")
  AN=$(echo "$R" | jqp "d['answer']")
  [ "${CH:-0}" -ge 1 ] && ok "chat selected and ran a pipeline (chain: $(echo "$R" | jqp "','.join(d['resolved_chain'])"))" \
                       || no "chat selected and ran a pipeline" "$(echo "$R" | cut -c1-300)"
  # A pipeline that ran must not be summarised as "no data" — that means the
  # summariser was handed a truncated or mangled result.
  if [ -z "$AN" ] || echo "$AN" | grep -qE "don't have a pipeline|^\{"; then
    no "chat produced a prose answer" "$(echo "$AN" | cut -c1-160)"
  elif echo "$AN" | grep -qiE "no (weather )?(information|data)( is)? available|not available|unable to (find|retrieve)"; then
    no "chat answer reflects the pipeline data" "$(echo "$AN" | cut -c1-160)"
  elif echo "$AN" | grep -qE "Oslo|Bergen"; then
    ok "chat produced a prose answer naming the cities ($(echo "$AN" | cut -c1-60)...)"
  else
    no "chat answer mentions the requested cities" "$(echo "$AN" | cut -c1-160)"
  fi
else
  skip "chat on-box" "model not serving"; skip "chat prose answer" "model not serving"
fi

# --------------------------------------------------------------------------
head_ "7. Chat authors a NEW pipeline, then runs it"
if [ "$NIM_UP" = 200 ]; then
  NEW="e2e_authored_$$"
  R=$(curl -s -m 400 -X POST "$AGENT/ask" -H 'Content-Type: application/json' "${AS_ADMIN[@]}" \
      -d "{\"question\":\"Create a pipeline named $NEW that summarizes a fake invoice and scores the vendor using the demo sandbox tools\",\"model\":\"local/$MODEL\"}")
  OT=$(echo "$R" | jqp "d['output_type']")
  if [ "$OT" = "pipeline.create" ]; then
    ok "chat routed to pipeline authoring"
    CREATED=$(echo "$R" | jqp "d['raw_result'].get('pipeline',{}).get('name','')")
    if [ -n "$CREATED" ]; then
      ok "pipeline authored: $CREATED"
      # It must now be a first-class pipeline the router knows about.
      c=$(code "$ROUTER/v2/pipelines/$CREATED" 10 "${AUTH[@]}")
      [ "$c" = 200 ] && ok "authored pipeline is registered" || no "authored pipeline registered" "HTTP $c"
      RR=$(curl -s -m 120 -X POST "${AUTH[@]}" -H 'Content-Type: application/json' \
           "$ROUTER/v2/pipelines/run" -d "{\"pipeline\":\"$CREATED\",\"inputs\":{}}")
      echo "$RR" | grep -q '"results"' && ok "authored pipeline re-runs standalone" \
                                       || no "authored pipeline re-runs" "$(echo "$RR" | cut -c1-200)"
      # Authored pipelines persist on the router's data volume; delete the probe
      # through the API (as the admin — it needs pipeline.deploy) and confirm.
      dc=$(code "$ROUTER/v2/pipelines/$CREATED" 15 -X DELETE "${AUTH[@]}" "${AS_ADMIN[@]}")
      [ "$dc" = 204 ] && ok "authored pipeline deleted via API" || no "authored pipeline deleted via API" "HTTP $dc"
      c=$(code "$ROUTER/v2/pipelines/$CREATED" 10 "${AUTH[@]}")
      [ "$c" = 404 ] && ok "deleted pipeline is gone" || no "deleted pipeline is gone" "HTTP $c"
    else
      no "pipeline authored" "$(echo "$R" | jqp "d['answer']" | cut -c1-200)"
    fi
  else
    no "chat routed to pipeline authoring" "output_type=$OT :: $(echo "$R" | jqp "d['answer']" | cut -c1-160)"
  fi
else
  skip "pipeline authoring" "model not serving"
fi

c=$(code "$ROUTER/v2/pipelines/weather_trip_planner" 10 -X DELETE "${AUTH[@]}" "${AS_ADMIN[@]}")
[ "$c" = 409 ] && ok "shipped example pipeline cannot be deleted" || no "shipped example protected" "HTTP $c"

# --------------------------------------------------------------------------
head_ "8. Bring-your-own provider"
CRED_NAME="e2e-byo-$$"
CR=$(curl -s -m 15 -X POST "${AUTH[@]}" "${AS_ADMIN[@]}" -H 'Content-Type: application/json' "$ROUTER/credentials" \
     -d "{\"name\":\"$CRED_NAME\",\"description\":\"e2e\",\"secret_type\":\"api_key\",\"storage_mode\":\"platform\",\"value\":\"e2e-secret\",\"assigned_mcp\":\"llm:custom:${MODEL:-m}@http://local-llm:8000/v1\"}")
CID=$(echo "$CR" | jqp "d['id']")
[ -n "$CID" ] && ok "credential stored" || no "credential stored" "$(echo "$CR" | cut -c1-200)"

if [ -n "$CID" ]; then
  MD=$(curl -s -m 20 "${AS_ADMIN[@]}" "$AGENT/models")
  echo "$MD" | grep -q "$CID" && ok "BYO provider offered as a brain" || no "BYO brain discovery" "$MD"

  # The agent's own key is denied credential.* — no identity must reveal nothing.
  MD0=$(curl -s -m 20 "$AGENT/models")
  echo "$MD0" | grep -q "$CID" && no "BYO hidden without identity" "leaked" \
                               || ok "BYO hidden without identity"

  if [ "$NIM_UP" = 200 ]; then
    R=$(curl -s -m 300 -X POST "$AGENT/ask" -H 'Content-Type: application/json' "${AS_ADMIN[@]}" \
        -d "{\"question\":\"Compare the weather in Oslo and Bergen\",\"model\":\"byo/custom/$MODEL\",\"credential_id\":\"$CID\",\"base_url\":\"http://local-llm:8000/v1\"}")
    echo "$R" | jqp "len(d['resolved_chain'])" | grep -qvx 0 \
      && ok "chat answered via the tenant's own provider" \
      || no "chat via BYO provider" "$(echo "$R" | cut -c1-250)"
  else
    skip "chat via BYO provider" "model not serving"
  fi
  curl -s -o /dev/null -m 10 -X DELETE "${AUTH[@]}" "${AS_ADMIN[@]}" "$ROUTER/credentials/$CID"
fi

# --------------------------------------------------------------------------
head_ "9. Authorization matrix"
c=$(code "$ROUTER/credentials" 10 "${AUTH[@]}");                       [ "$c" = 403 ] && ok "bare API key denied credential.read" || no "bare key denied" "HTTP $c"
c=$(code "$ROUTER/credentials" 10 "${AUTH[@]}" "${AS_ADMIN[@]}");      [ "$c" = 200 ] && ok "delegated admin allowed"            || no "delegated admin allowed" "HTTP $c"
c=$(code "$ROUTER/credentials" 10 "${AUTH[@]}" -H "X-McpFinder-User-Id: 11111111-1111-1111-1111-111111111111" -H "X-McpFinder-Tenant-Id: $ADMIN_TENANT")
[ "$c" = 403 ] && ok "forged user id denied" || no "forged user id denied" "HTTP $c"
c=$(code "$ROUTER/v2/pipelines" 10);                                   [ "$c" = 401 ] && ok "unauthenticated router call rejected" || no "unauthenticated rejected" "HTTP $c"
c=$(code "$PORTAL/api/ask" 10);                                        [ "$c" = 401 ] && ok "portal chat behind auth"             || no "portal chat behind auth" "HTTP $c"

# --------------------------------------------------------------------------
printf '\n\033[1mResult: %d passed, %d failed, %d skipped\033[0m\n' "$pass" "$fail" "$skipped"
[ "$fail" -eq 0 ]
