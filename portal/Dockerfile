FROM node:22-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3004 \
    HOSTNAME=0.0.0.0
# The runtime entrypoint is `node server.js`; npm is never invoked here. Its
# bundled dependencies (tar, sigstore, brace-expansion, picomatch) are the only
# remaining source of CVEs in this image, so drop npm from the runner stage.
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx

COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
# bcryptjs is not auto-detected by standalone tracer; copy it explicitly
COPY --from=builder /app/node_modules/bcryptjs ./node_modules/bcryptjs
EXPOSE 3004

# Run as the image's built-in non-root `node` user (defense in depth).
RUN chown -R node:node /app
USER node

# Next.js standalone reads PORT/HOSTNAME from env; set here so the image listens
# on 3004 (matching EXPOSE) without relying on the deployment to inject PORT.
CMD ["node", "server.js"]
