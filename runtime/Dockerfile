FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# router_internal.py is an optional private overlay (absent in the platform kit)
COPY router*.py ./
COPY policy_hooks.py .
COPY credentials.py .
COPY licensing.py .
COPY channels*.yaml ./
COPY types.yaml .
COPY manifests/ manifests/
COPY pipelines/ pipelines/
COPY policies/ policies/
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 8040

# Run as a non-root user (defense in depth; the app only writes under /app).
# Runtime-authored pipelines are written here (see PIPELINES_DATA_DIR in
# router.py). Created and chowned in the image so a fresh named volume mounted
# on it inherits appuser ownership and stays writable as non-root.
RUN mkdir -p /data/pipelines
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser /app /data/pipelines
USER appuser
ENV PIPELINES_DATA_DIR=/data/pipelines

# Opt-in app-level TLS (SOC 2 CC6.7): entrypoint serves HTTPS when
# TLS_CERT_FILE/TLS_KEY_FILE are set, otherwise plain HTTP (unchanged default).
ENV APP_MODULE=router:app \
    APP_PORT=8040
CMD ["./entrypoint.sh"]
