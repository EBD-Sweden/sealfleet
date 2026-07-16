FROM python:3.12-slim

# Install docker CLI + git (needed for deploy operations)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && curl -fsSL https://get.docker.com | sh \
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
