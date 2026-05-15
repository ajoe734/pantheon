/**
 * BFF-CONSOL-013: Cookie-session write gate driven by /bff/me.
 *
 * liveWriteGated() replaces the old sessionStorage-only bearer check.
 * It fetches /bff/me and inspects session_kind so that cookie-based
 * sessions are recognised as authenticated, not rejected as unauthenticated.
 */

export type SessionKind = "cookie" | "bearer" | "stub";

export interface BffMeSession {
  authenticated: boolean;
  session_kind: SessionKind;
  auth_mode?: string;
  fresh?: boolean;
}

export interface BffMeData {
  session: BffMeSession;
}

export interface BffMeResponse {
  data: BffMeData;
}

let cachedDevBearer:
  | {
      token: string;
      expiresAtMs: number;
    }
  | undefined;

function viteEnvValue(name: string): string {
  if (typeof import.meta === "undefined" || !(import.meta as Record<string, unknown>).env) {
    return "";
  }
  return (
    (import.meta as Record<string, Record<string, string | undefined>>).env[name] ??
    ""
  ).trim();
}

async function acquireDevBearerToken(baseUrl: string): Promise<string | undefined> {
  if (cachedDevBearer && cachedDevBearer.expiresAtMs - Date.now() > 60_000) {
    return cachedDevBearer.token;
  }
  const clientId = viteEnvValue("VITE_BFF_OIDC_CLIENT_ID");
  const clientSecret = viteEnvValue("VITE_BFF_OIDC_CLIENT_SECRET");
  if (!clientId || !clientSecret) return undefined;

  const loginPath = viteEnvValue("VITE_BFF_DEV_LOGIN_PATH") || "/bff/auth/dev-login";
  const res = await fetch(`${baseUrl}${loginPath}`, {
    method: "POST",
    credentials: "omit",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      grant_type: "client_credentials",
      client_id: clientId,
      client_secret: clientSecret,
    }),
  });
  if (!res.ok) return undefined;
  const body = (await res.json()) as {
    access_token?: string;
    expires_in?: number;
  };
  if (!body.access_token) return undefined;
  const ttlSeconds = Math.max(300, Math.min(Number(body.expires_in || 900), 3600));
  cachedDevBearer = {
    token: body.access_token,
    expiresAtMs: Date.now() + ttlSeconds * 1000,
  };
  return cachedDevBearer.token;
}

/** Fetch the /bff/me endpoint. Includes credentials so cookies are sent. */
async function fetchBffMe(baseUrl: string): Promise<BffMeResponse> {
  const url = `${baseUrl}/bff/me`;
  const headers: Record<string, string> = { Accept: "application/json" };
  const bearer = await acquireDevBearerToken(baseUrl);
  if (bearer) headers.Authorization = `Bearer ${bearer}`;
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers,
  });
  if (!res.ok) {
    throw new Error(`/bff/me returned ${res.status}`);
  }
  return res.json() as Promise<BffMeResponse>;
}

/**
 * Returns true when the current session allows live write operations.
 *
 * - cookie session  → allowed (browser session cookie counts as authenticated)
 * - bearer session  → allowed
 * - stub session    → allowed only outside production (dev / paper stubs)
 * - no session / error → denied
 *
 * @param baseUrl  BFF base URL, e.g. "http://localhost:8000". Defaults to
 *                 the VITE_BFF_BASE_URL env var or window.location.origin.
 */
export async function liveWriteGated(baseUrl?: string): Promise<boolean> {
  const configuredBase = baseUrl ?? viteEnvValue("VITE_BFF_BASE_URL");
  const resolvedBase =
    configuredBase || (typeof window !== "undefined" ? window.location.origin : "");

  try {
    const me = await fetchBffMe(resolvedBase);
    const session = me?.data?.session;
    if (!session) return false;
    if (!session.authenticated) return false;
    const kind: SessionKind = session.session_kind;
    if (kind === "cookie" || kind === "bearer") return true;
    if (kind === "stub") {
      // Allow stubs outside strict production; deny in live/production mode.
      const isProduction =
        viteEnvValue("VITE_BFF_MODE") === "live" ||
        viteEnvValue("VITE_BFF_FALLBACK") === "strict";
      return !isProduction;
    }
    return false;
  } catch {
    return false;
  }
}

/** Synchronous helper for contexts that already have the /bff/me payload. */
export function sessionKindAllowsWrite(
  sessionKind: SessionKind | undefined,
  productionMode = false,
): boolean {
  if (!sessionKind) return false;
  if (sessionKind === "cookie" || sessionKind === "bearer") return true;
  if (sessionKind === "stub") return !productionMode;
  return false;
}
