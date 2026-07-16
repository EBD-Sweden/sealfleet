FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3004 \
    HOSTNAME=0.0.0.0
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
