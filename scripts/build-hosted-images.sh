#!/usr/bin/env bash
# Build the hosted Sealfleet images from COMMITTED SOURCE ONLY.
#
# WHY THIS EXISTS: this working tree carries an internal overlay whose files are
# untracked in the public repo. `docker build ./portal` uses the directory as its
# build context, so it silently includes those untracked files — internal pages
# and API routes were baked into a published portal image that way. .dockerignore
# does not help: it is a denylist, so anything new leaks by default.
#
# `git archive` inverts that: only tracked files are exported, so the build
# context is an allowlist of the public repo. Anything not committed cannot ship.
#
# Usage:
#   scripts/build-hosted-images.sh <tag> [service ...]     # default: portal
#   PUSH=1 scripts/build-hosted-images.sh 0.7.5 portal
#
# Verify the result before deploying:
#   docker run --rm --entrypoint sh <image> -c 'ls .next/server/app'
set -euo pipefail

TAG="${1:-}"
[ -n "$TAG" ] || { echo "usage: $0 <tag> [service ...]" >&2; exit 2; }
shift || true
# Directories containing a Dockerfile. The chat needs `agent` and
# `runtime/llm-proxy` in addition to the three original services.
SERVICES=("$@")
[ ${#SERVICES[@]} -gt 0 ] || SERVICES=(portal)

REGISTRY="${REGISTRY:-us-east1-docker.pkg.dev/sealfleet/images}"
REF="${REF:-HEAD}"
PUSH="${PUSH:-0}"

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"

# Refuse to build from a ref that isn't committed cleanly, so the tag always
# corresponds to something reproducible.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "WARNING: tracked files have uncommitted changes; they will NOT be in the image." >&2
  echo "         The build uses committed content at ${REF}." >&2
fi

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

for svc in "${SERVICES[@]}"; do
  echo "==> exporting committed source for '$svc' (ref ${REF})"
  git archive "$REF" "$svc" | tar -x -C "$workdir"
  [ -d "$workdir/$svc" ] || { echo "no tracked files under '$svc'" >&2; exit 1; }

  # Image name is the last path segment (runtime/llm-proxy → sealfleet-llm-proxy).
  image="${REGISTRY}/sealfleet-$(basename "$svc"):${TAG}"
  echo "==> building $image"
  docker build --platform linux/amd64 -t "$image" "$workdir/$svc"

  if [ "$PUSH" = "1" ]; then
    echo "==> pushing $image"
    docker push "$image"
  else
    echo "==> built (not pushed; set PUSH=1 to push)"
  fi
done
