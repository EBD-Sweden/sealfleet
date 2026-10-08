# Example MCP servers (weather-trip, demo-sandbox) for Kubernetes. Compose
# bind-mounts ./mcps into the runtime image instead; k8s needs them baked in.
# Build from the repo root:
#   docker build -f mcps/Dockerfile -t sealfleet-example-mcps:<tag> .
FROM python:3.12-slim
WORKDIR /workspace
COPY runtime/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mcps/ mcps/
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser
USER appuser
EXPOSE 8080
