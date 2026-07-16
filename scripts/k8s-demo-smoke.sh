#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-demo-sandbox}"
SELECTOR="${SELECTOR:-app.kubernetes.io/part-of=mcpfinder-demo-sandbox}"
KUBECTL="${KUBECTL:-kubectl}"
DRY_RUN="${DRY_RUN:-1}"
ALLOW_NON_DEMO_NAMESPACE="${ALLOW_NON_DEMO_NAMESPACE:-0}"
CLEANUP=0

for arg in "$@"; do
  case "$arg" in
    --cleanup) CLEANUP=1 ;;
    -h|--help)
      cat <<'USAGE'
Usage: DRY_RUN=1 NAMESPACE=demo-sandbox SELECTOR=app.kubernetes.io/part-of=mcpfinder-demo-sandbox scripts/k8s-demo-smoke.sh [--cleanup]

Checks the demo sandbox Kubernetes namespace for stale failed pods/jobs and fails
if unhealthy pod states remain. Cleanup is dry-run by default and is allowed only
for the demo namespace plus demo label selector unless ALLOW_NON_DEMO_NAMESPACE=1
is set by an operator.
USAGE
      exit 0
      ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[dry-run] %q ' "$@"
    printf '\n'
  else
    "$@"
  fi
}

require_demo_cleanup_scope() {
  if [[ "$NAMESPACE" != "demo-sandbox" && "$ALLOW_NON_DEMO_NAMESPACE" != "1" ]]; then
    echo "Refusing cleanup outside demo namespace: NAMESPACE=$NAMESPACE" >&2
    exit 2
  fi
  if [[ -z "$SELECTOR" || "$SELECTOR" != *"mcpfinder-demo-sandbox"* ]]; then
    echo "Refusing cleanup without demo selector: SELECTOR=$SELECTOR" >&2
    exit 2
  fi
}

if ! command -v "$KUBECTL" >/dev/null 2>&1; then
  echo "kubectl not found; set KUBECTL=/path/to/kubectl" >&2
  exit 127
fi

if [[ "$CLEANUP" == "1" ]]; then
  require_demo_cleanup_scope
  echo "Cleaning stale failed demo pods/jobs in namespace $NAMESPACE selector $SELECTOR (DRY_RUN=$DRY_RUN)"
  mapfile -t pods < <("$KUBECTL" get pods -n "$NAMESPACE" -l "$SELECTOR" \
    --field-selector=status.phase=Failed \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null || true)
  for pod in "${pods[@]}"; do
    [[ -n "$pod" ]] && run "$KUBECTL" delete pod "$pod" -n "$NAMESPACE" -l "$SELECTOR" --ignore-not-found=true
  done

  mapfile -t jobs < <("$KUBECTL" get jobs -n "$NAMESPACE" -l "$SELECTOR" \
    -o jsonpath='{range .items[?(@.status.failed>0)]}{.metadata.name}{"\n"}{end}' 2>/dev/null || true)
  for job in "${jobs[@]}"; do
    [[ -n "$job" ]] && run "$KUBECTL" delete job "$job" -n "$NAMESPACE" -l "$SELECTOR" --ignore-not-found=true
  done
fi

status_output="$($KUBECTL get pods -n "$NAMESPACE" -l "$SELECTOR" -o wide 2>/dev/null || true)"
echo "$status_output"

unhealthy_pattern='ImagePullBackOff|CrashLoopBackOff|ContainerStatusUnknown|Evicted| Error |CreateContainerConfigError|RunContainerError'
if echo "$status_output" | grep -E "$unhealthy_pattern" >/dev/null; then
  echo "Unhealthy demo pod status detected. Run DRY_RUN=0 $0 --cleanup, then redeploy failing demo workloads." >&2
  exit 1
fi

if "$KUBECTL" get deploy -n "$NAMESPACE" -l "$SELECTOR" mcpfinder-portal >/dev/null 2>&1; then
  "$KUBECTL" rollout status deploy/mcpfinder-portal -n "$NAMESPACE" --timeout=60s
fi

if "$KUBECTL" get deploy -n "$NAMESPACE" -l "$SELECTOR" mcp-router >/dev/null 2>&1; then
  "$KUBECTL" rollout status deploy/mcp-router -n "$NAMESPACE" --timeout=60s
fi

echo "k8s demo smoke passed for namespace $NAMESPACE selector $SELECTOR"
