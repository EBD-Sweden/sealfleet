// For calls to the MCP Runtime Router (http://localhost:8040 or ROUTER_URL env)
import { ApiError } from './api-client';

const ROUTER_URL = process.env.NEXT_PUBLIC_ROUTER_URL || 'http://localhost:8040';

async function routerRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${ROUTER_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new ApiError(res.status, text);
  }
  return res.json() as Promise<T>;
}

export const routerClient = {
  get: <T>(path: string) => routerRequest<T>(path),
  post: <T>(path: string, body: unknown) =>
    routerRequest<T>(path, { method: 'POST', body: JSON.stringify(body) }),
};
