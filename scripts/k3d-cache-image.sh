#!/usr/bin/env bash
set -euo pipefail

# Warm the exact image reference used by checked-in k3d manifests into every
# schedulable node's containerd cache. This avoids scale-from-zero pulls through
# Docker's sometimes-flaky embedded DNS for the k3d registry alias while keeping
# manifests portable.
#
# Usage:
#   scripts/k3d-cache-image.sh [image]
#
# Pass the image to cache as the first argument.

IMAGE="${1:?usage: k3d-cache-image.sh <image>}"
CLUSTER="${K3D_CLUSTER:-mcpfinder}"
HOST_REGISTRY="${HOST_REGISTRY:-localhost:5050}"
K3D_CACHE_MIN_ROOT_FREE_GB="${K3D_CACHE_MIN_ROOT_FREE_GB:-20}"
ARCHIVE="$(mktemp -t mcpfinder-k3d-image-cache.XXXXXX.tar)"
REMOTE_ARCHIVE="/tmp/mcpfinder-image-cache-${IMAGE//[^a-zA-Z0-9_.-]/-}.tar"

cleanup() {
  rm -f "${ARCHIVE}"
}
trap cleanup EXIT

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 127
  fi
}

need docker
need kubectl
need python3

root_free_gb="$(df -Pk / | awk 'NR == 2 { printf "%d", $4 / 1024 / 1024 }')"
if [ "${root_free_gb}" -lt "${K3D_CACHE_MIN_ROOT_FREE_GB}" ]; then
  cat >&2 <<EOF
root disk free space is ${root_free_gb}GiB, below K3D_CACHE_MIN_ROOT_FREE_GB=${K3D_CACHE_MIN_ROOT_FREE_GB}GiB.
Refusing to import ${IMAGE} into k3d because kubelet/containerd image growth can return the cluster to DiskPressure.
Free disk, prune stale build/image caches, or lower K3D_CACHE_MIN_ROOT_FREE_GB intentionally for this host.
EOF
  exit 1
fi

TEMP_KUBECONFIG=""
if ! kubectl config current-context >/dev/null 2>&1; then
  if command -v k3d >/dev/null 2>&1; then
    TEMP_KUBECONFIG="$(mktemp -t mcpfinder-k3d-kubeconfig.XXXXXX.yaml)"
    k3d kubeconfig get "${CLUSTER}" > "${TEMP_KUBECONFIG}"
    export KUBECONFIG="${TEMP_KUBECONFIG}"
  else
    echo "kubectl has no current context and k3d is unavailable to load cluster ${CLUSTER}" >&2
    exit 1
  fi
fi

cleanup_kubeconfig() {
  if [ -n "${TEMP_KUBECONFIG}" ]; then
    rm -f "${TEMP_KUBECONFIG}"
  fi
}
trap 'cleanup; cleanup_kubeconfig' EXIT

repo_tag="${IMAGE#*/}"
host_image="${HOST_REGISTRY}/${repo_tag}"

if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
  if ! docker image inspect "${host_image}" >/dev/null 2>&1; then
    echo "local Docker image not found; pulling ${host_image}" >&2
    docker pull "${host_image}"
  fi
  docker tag "${host_image}" "${IMAGE}"
fi

echo "saving ${IMAGE} to ${ARCHIVE}" >&2
docker image save "${IMAGE}" -o "${ARCHIVE}"

mapfile -t nodes < <(
  kubectl get nodes -o json | python3 -c '
import json, sys
payload = json.load(sys.stdin)
for item in payload.get("items", []):
    spec = item.get("spec") or {}
    if spec.get("unschedulable"):
        continue
    name = item.get("metadata", {}).get("name", "")
    if name:
        print(name)
'
)

if [ "${#nodes[@]}" -eq 0 ]; then
  echo "no schedulable Kubernetes nodes found" >&2
  exit 1
fi

for node_container in "${nodes[@]}"; do
  if ! docker inspect "${node_container}" >/dev/null 2>&1; then
    echo "schedulable node ${node_container} is not a local Docker container; set node names to k3d container names or run on the k3d host" >&2
    exit 1
  fi

  echo "warming ${IMAGE} on ${node_container}" >&2
  docker cp "${ARCHIVE}" "${node_container}:${REMOTE_ARCHIVE}"
  docker exec "${node_container}" ctr -n k8s.io images import --all-platforms "${REMOTE_ARCHIVE}"
  docker exec "${node_container}" rm -f "${REMOTE_ARCHIVE}"

  if docker exec "${node_container}" crictl image inspect "${IMAGE}" >/dev/null 2>&1; then
    echo "verified ${IMAGE} on ${node_container} via crictl image inspect" >&2
  elif docker exec "${node_container}" crictl images | grep -F "${IMAGE%:*}" >/dev/null; then
    echo "verified ${IMAGE} on ${node_container} via crictl images" >&2
  else
    echo "failed to verify ${IMAGE} in ${node_container} containerd cache" >&2
    exit 1
  fi
done

echo "cached ${IMAGE} on ${#nodes[@]} schedulable k3d node(s) for cluster ${CLUSTER}" >&2
