# Public-test Kubernetes secret contract

Public-test starts with an OSS clone-and-run local demo. Kubernetes is the optional operator/QA path for validating production-like manifests after the local fake-data quickstart is clean.

Kubernetes manifests must not carry plaintext connection strings, auth/session secrets, provider keys, or local-only runtime escape hatches. The top-level `k8s/*.yaml` manifests consume sensitive configuration only through Kubernetes `envFrom.secretRef` entries. Populate those Secret objects through your cluster secret manager or ExternalSecrets controller before applying the manifests.

Required Secret objects and keys:

| Secret object | Required keys | Consumers |
| --- | --- | --- |
| `mcpfinder-database` | `DATABASE_URL` | router, registry, deploy, portal, example cron jobs |
| `mcpfinder-runtime-auth` | `NEXTAUTH_SECRET`, `ENCRYPTION_KEY` | router |
| `mcpfinder-portal-auth` | `NEXTAUTH_SECRET` | portal |
| `mcpfinder-llm` | `LLM_API_KEY` | router planner integration |
| `mcpfinder-core-agent` | `OPENAI_API_KEY` | core-agent |
| `mcpfinder-anthropic` | `ANTHROPIC_API_KEY` | core agent / LLM-backed MCPs |
| `mcpfinder-example` | `EXAMPLE_URL` | an example MCP |

Public-test guardrails:

- `AUTH_ALLOW_EPHEMERAL_KEYS=false` in the public router manifest. Temporary API-key minting is a local-development convenience only.
- `DOCKER_STDIO_ENABLED=false` in the public router manifest. The public router manifest does not mount `/var/run/docker.sock`.
- Local Docker stdio testing is isolated in `k8s/dev-local/` as a kustomize overlay. Use it only on disposable local clusters with no production credentials or reachable customer data. Because the overlay intentionally reuses the checked-in public router manifest from its parent directory, build it with `kubectl kustomize --load-restrictor=LoadRestrictionsNone k8s/dev-local` (or the equivalent `kustomize build --load-restrictor=LoadRestrictionsNone k8s/dev-local`).
- Secret values should come from an external secret store or one-off operator-created Kubernetes Secrets. Do not commit Secret manifests with `data`/`stringData` values.

Example operator commands, using environment variables already present in the shell, are intentionally kept outside the repository so command history and local shell policy can handle sensitive values. Verify with:

```bash
python -m pytest runtime/tests/test_k8s_public_manifests.py -q
grep -RInE 'postgres://|postgresql://|password:|api[_-]?key|mcpfinder-secret-change-in-prod|not-needed' k8s/*.yaml
kubectl kustomize --load-restrictor=LoadRestrictionsNone k8s/dev-local >/tmp/mcpfinder-dev-local.yaml
```

The grep command should return no plaintext values in top-level public-test manifests. Secret key names are documented here for operators; key names are not secret values.
