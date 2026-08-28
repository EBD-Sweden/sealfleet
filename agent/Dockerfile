FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
EXPOSE 8050

# Run as a non-root user (defense in depth).
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser /app
USER appuser

# Cloud Run injects PORT; default stays 8050 for compose/k8s.
CMD ["sh", "-c", "exec uvicorn server:app --host 0.0.0.0 --port ${PORT:-8050}"]
