#!/usr/bin/env bash
set -euo pipefail

# Fast guard for pipeline/MCP routing regressions.
# Fails if in-cluster MCP endpoints use localhost, 127.0.0.1, host.k3d.internal,
# or if a runtime manifest has a Kubernetes Service but is missing scale-to-zero coverage.

cd "$(dirname "$0")/.."
python3 -m pytest runtime/tests/test_cluster_service_routing.py -q
