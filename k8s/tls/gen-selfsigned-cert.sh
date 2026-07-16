#!/usr/bin/env bash
# gen-selfsigned-cert.sh — generate a self-signed TLS cert + k8s Secret for Sealfleet edge TLS (dev).
#
# SOC 2 CC6.7 (encryption in transit): this produces the TLS material that the
# Traefik Ingress (k8s/tls/ingress.yaml) terminates with. For PRODUCTION use
# cert-manager + ACME/Let's Encrypt instead (see k8s/tls/README.md) — this
# self-signed path is for dev/internal clusters only.
#
# Usage:
#   ./gen-selfsigned-cert.sh                 # writes certs to ./_certs and prints the Secret
#   APPLY=1 ./gen-selfsigned-cert.sh         # also `kubectl apply` the Secret to the cluster
#   NAMESPACE=default DOMAIN=sealfleet.local ./gen-selfsigned-cert.sh
#
# The Secret name (mcpfinder-tls) is referenced by k8s/tls/ingress.yaml.
set -euo pipefail

NAMESPACE="${NAMESPACE:-default}"
DOMAIN="${DOMAIN:-sealfleet.local}"
SECRET_NAME="${SECRET_NAME:-mcpfinder-tls}"
OUTDIR="${OUTDIR:-$(cd "$(dirname "$0")" && pwd)/_certs}"
DAYS="${DAYS:-825}"
CONTEXT="${CONTEXT:-k3d-mcpfinder}"

mkdir -p "$OUTDIR"
KEY="$OUTDIR/tls.key"
CRT="$OUTDIR/tls.crt"

# SANs cover the wildcard apex + the per-service hostnames used by the Ingress.
cat > "$OUTDIR/san.cnf" <<EOF
[req]
distinguished_name = dn
x509_extensions = v3_req
prompt = no
[dn]
CN = ${DOMAIN}
O = Sealfleet (dev self-signed)
[v3_req]
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names
[alt_names]
DNS.1 = ${DOMAIN}
DNS.2 = *.${DOMAIN}
DNS.3 = router.${DOMAIN}
DNS.4 = deploy.${DOMAIN}
DNS.5 = registry.${DOMAIN}
DNS.6 = portal.${DOMAIN}
DNS.7 = localhost
IP.1 = 127.0.0.1
EOF

echo ">> Generating self-signed cert for ${DOMAIN} (valid ${DAYS} days)"
openssl req -x509 -nodes -newkey rsa:2048 \
  -keyout "$KEY" -out "$CRT" \
  -days "$DAYS" -config "$OUTDIR/san.cnf" 2>/dev/null

echo ">> Cert written:"
echo "   key: $KEY"
echo "   crt: $CRT"

# Render the Secret as YAML (so it can be committed/inspected) without applying.
SECRET_YAML="$OUTDIR/${SECRET_NAME}.secret.yaml"
kubectl create secret tls "$SECRET_NAME" \
  --cert="$CRT" --key="$KEY" \
  --namespace "$NAMESPACE" \
  --dry-run=client -o yaml > "$SECRET_YAML"
echo ">> Secret manifest written: $SECRET_YAML"

if [ "${APPLY:-0}" = "1" ]; then
  echo ">> Applying Secret to context=${CONTEXT} ns=${NAMESPACE}"
  kubectl --context "$CONTEXT" apply -f "$SECRET_YAML"
else
  echo ">> Dry-run only. To create the Secret in-cluster run:"
  echo "   kubectl --context ${CONTEXT} apply -f ${SECRET_YAML}"
fi
