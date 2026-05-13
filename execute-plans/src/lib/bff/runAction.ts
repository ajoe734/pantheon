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

/** Fetch the /bff/me endpoint. Includes credentials so cookies are sent. */
async function fetchBffMe(baseUrl: string): Promise<BffMeResponse> {
  const url = `${baseUrl}/bff/me`;
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
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
  const resolvedBase =
    baseUrl ??
    (typeof import.meta !== "undefined" && (import.meta as Record<string, unknown>).env
      ? ((import.meta as Record<string, Record<string, string>>).env["VITE_BFF_BASE_URL"] ?? "")
      : "") ||
    (typeof window !== "undefined" ? window.location.origin : "");

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
        typeof import.meta !== "undefined" && (import.meta as Record<string, unknown>).env
          ? ((import.meta as Record<string, Record<string, string>>).env[
              "VITE_BFF_MODE"
            ] === "live" ||
              (import.meta as Record<string, Record<string, string>>).env[
                "VITE_BFF_FALLBACK"
              ] === "strict")
          : false;
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
