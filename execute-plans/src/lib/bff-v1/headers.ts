/**
 * Shared BFF request header builder (AG-DYNUI-LIVE-AUTH-003).
 *
 * AG-DYNUI-LIVE-AUTH-002 forwarded the `pantheon_session` cookie into the
 * Trading Room BFF routes, but the live post-deploy probe still shows
 * `/bff/agora/trading-room` and `/bff/agora/trading-room/decision-events`
 * returning 401 while `/bff/me` returns 200 for the same authenticated
 * browser session. `bff-v1` clients other than this one never send an
 * `Authorization` header at all, so any session that authenticates via a
 * bearer token rather than the cookie has no way to reach Trading Room.
 * This module is the single place that builds that header so every caller
 * shares the same auth transport instead of re-deriving it per client.
 */

export interface BffAuthSnapshot {
  bearerToken?: string;
  tenantId?: string;
}

export type BffAuthProvider = () => BffAuthSnapshot;

const BEARER_TOKEN_STORAGE_KEYS = ["pantheon.bff.bearerToken", "pantheon_operator_token"];
const TENANT_ID_STORAGE_KEYS = ["pantheon.bff.tenantId", "pantheon_tenant_id"];

let authProvider: BffAuthProvider | undefined;

/** Test-only seam: inject a fixed auth snapshot instead of reading browser storage/env. */
export function setAuthProvider(provider: BffAuthProvider | undefined): void {
  authProvider = provider;
}

function readStorageValue(keys: string[]): string | undefined {
  if (typeof window === "undefined" || !window.localStorage) return undefined;
  for (const key of keys) {
    const value = window.localStorage.getItem(key);
    if (value) return value;
  }
  return undefined;
}

function viteEnvValue(name: string): string {
  if (typeof import.meta === "undefined" || !(import.meta as Record<string, unknown>).env) {
    return "";
  }
  return (
    (import.meta as Record<string, Record<string, string | undefined>>).env[name] ?? ""
  ).trim();
}

function defaultAuthSnapshot(): BffAuthSnapshot {
  const bearerToken =
    readStorageValue(BEARER_TOKEN_STORAGE_KEYS) || viteEnvValue("VITE_BFF_DEV_BEARER_TOKEN") || undefined;
  const tenantId = readStorageValue(TENANT_ID_STORAGE_KEYS);
  return { bearerToken, tenantId };
}

export interface BuildHeadersOptions {
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  /** Caller-supplied headers (e.g. Content-Type, If-Match, Idempotency-Key). Wins on conflicts. */
  extra?: Record<string, string>;
}

/**
 * Build the shared BFF request headers: `Accept`, `Authorization` (when a
 * bearer token is available), `X-Tenant-Id` (when a tenant is scoped), plus
 * any caller-supplied extras. Callers still pass `credentials: "include"` on
 * the fetch call so cookie-backed sessions keep working alongside the
 * bearer path — this only adds the header this app was missing.
 */
export function buildHeaders(options: BuildHeadersOptions): Record<string, string> {
  const snapshot = (authProvider ?? defaultAuthSnapshot)();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (snapshot.bearerToken) headers.Authorization = `Bearer ${snapshot.bearerToken}`;
  if (snapshot.tenantId) headers["X-Tenant-Id"] = snapshot.tenantId;
  return { ...headers, ...options.extra };
}
