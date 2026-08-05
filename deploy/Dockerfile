FROM python:3.12-slim

# git + the docker CLI (needed for deploy operations).
#
# Only the `docker` client binary is installed, not the engine. server.py shells
# out to `docker build`/`docker push` as a client against an external daemon, so
# dockerd/containerd/runc are never executed here — and shipping them pulled in
# their embedded Go dependencies (containerd CVE-2026-53488/53489, grpc
# GHSA-hrxh-6v49-42gf) with no runtime benefit. This also replaces piping
# get.docker.com straight into a shell with a pinned, checksum-verified download.
ARG DOCKER_CLI_VERSION=29.6.2
ARG DOCKER_CLI_SHA256=d6204aea92238e2453d5445c885b9d2e5eb8f82915568ec50edf9dbe12a3ac74
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && curl -fsSL -o /tmp/docker.tgz \
       "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" \
    && echo "${DOCKER_CLI_SHA256}  /tmp/docker.tgz" | sha256sum -c - \
    && tar -xzf /tmp/docker.tgz -C /tmp docker/docker \
    && install -m 0755 /tmp/docker/docker /usr/local/bin/docker \
    && rm -rf /tmp/docker.tgz /tmp/docker \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 8030

# Run as a non-root user so the pod satisfies runAsNonRoot. git + kubectl work
# fine unprivileged; the optional in-cluster image-build path (docker) requires
# additional privileges the operator must grant explicitly.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser /app
USER appuser

# Opt-in app-level TLS (SOC 2 CC6.7): HTTPS when TLS_CERT_FILE/TLS_KEY_FILE set,
# else plain HTTP (unchanged default).
ENV APP_MODULE=server:app \
    APP_PORT=8030
CMD ["./entrypoint.sh"]
