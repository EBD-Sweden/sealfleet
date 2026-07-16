FROM python:3.12-slim

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the registry package into /app/registry/
# so "from registry.storage import ..." resolves correctly
COPY __init__.py server.py storage.py registry/
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 8010

# Run as a non-root user (defense in depth).
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser /app
USER appuser

# Opt-in app-level TLS (SOC 2 CC6.7): HTTPS when TLS_CERT_FILE/TLS_KEY_FILE set,
# else plain HTTP (unchanged default).
ENV APP_MODULE=registry.server:app \
    APP_PORT=8010
CMD ["./entrypoint.sh"]
