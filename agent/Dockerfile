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

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8050"]
