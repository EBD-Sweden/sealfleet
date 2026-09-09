# llm-proxy — the platform's single LLM entry point. Node built-ins only.
FROM node:22-alpine
WORKDIR /app
COPY index.js .
RUN addgroup -S app && adduser -S -G app app
USER app
ENV LLM_PROXY_PORT=3456
EXPOSE 3456
HEALTHCHECK --interval=30s --timeout=5s CMD wget -qO- http://127.0.0.1:3456/health >/dev/null || exit 1
CMD ["node", "index.js"]
