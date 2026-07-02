import { BffError, liveStatus } from "@/lib/bff-v1";
import { readBffEnv } from "@/lib/bff-v1/runtimeEnv";

export interface BffRequest {
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  path: string;
  query?: Record<string, string | number | undefined>;
  body?: unknown;
}

function buildUrl(path: string, query?: Record<string, string | number | undefined>): string {
  const baseUrl = readBffEnv().VITE_BFF_BASE_URL?.replace(/\/+$/, "") ?? "";
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const url = baseUrl ? `${baseUrl}${normalizedPath}` : normalizedPath;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined) params.set(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

async function fallbackToMock<T>(mockFn: () => Promise<T>, error: unknown): Promise<T> {
  liveStatus.set({
    effective: "mock",
    lastError: errorMessage(error),
    fellBackAt: Date.now(),
  });
  return mockFn();
}

export function strictDataFrom(body: unknown): unknown | undefined {
  if (!body || typeof body !== "object" || Array.isArray(body)) return undefined;
  const record = body as Record<string, unknown>;
  return record.data ?? undefined;
}

export function strictNotFoundAsUndefined<T>(error: unknown): T | undefined {
  if (error instanceof BffError && error.status === 404) return undefined;
  throw error;
}

export async function withStrictLiveOrMock<T, _R = unknown>(
  req: BffRequest,
  mockFn: () => Promise<T>,
  liveAdapter?: (data: unknown) => T | undefined,
  onError?: (error: unknown) => T | undefined,
): Promise<T> {
  const env = readBffEnv();
  if (env.VITE_BFF_MODE !== "live") {
    return mockFn();
  }
  const strict = env.VITE_BFF_FALLBACK === "strict";
  try {
    const url = buildUrl(req.path, req.query);
    const res = await fetch(url, { method: req.method });
    if (!res.ok) {
      const err = new BffError(
        { code: "BFF_HTTP_ERROR", message: `BFF ${req.method} ${req.path}: HTTP ${res.status}` },
        res.status,
      );
      if (res.status === 404 && onError) return onError(err) as T;
      if (res.status >= 500 && !strict) {
        return fallbackToMock(mockFn, err);
      }
      if (onError) return onError(err) as T;
      throw err;
    }
    const json: unknown = await res.json();
    if (liveAdapter) {
      const adapted = liveAdapter(json);
      if (adapted !== undefined) {
        liveStatus.set({ mode: "live", effective: "live", baseUrl: env.VITE_BFF_BASE_URL });
        return adapted;
      }
    }
    liveStatus.set({ mode: "live", effective: "live", baseUrl: env.VITE_BFF_BASE_URL });
    return json as T;
  } catch (err) {
    if (err instanceof BffError && onError) return onError(err) as T;
    if (err instanceof BffError) throw err;
    if (strict) {
      throw new BffError({ code: "BFF_TRANSPORT_ERROR", message: errorMessage(err) }, 0);
    }
    return fallbackToMock(mockFn, err);
  }
}
