export interface BffRequest {
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  path: string;
  query?: Record<string, string | number | undefined>;
  body?: unknown;
  headers?: Record<string, string | undefined>;
}

export class BffError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "BffError";
    this.status = status;
  }
}

function viteEnvValue(name: string): string {
  if (typeof import.meta === "undefined" || !import.meta.env) return "";
  const value = (import.meta.env as Record<string, unknown>)[name];
  return typeof value === "string" ? value.trim() : "";
}

function resolvedBase(): string {
  const configured = viteEnvValue("VITE_BFF_BASE_URL").replace(/\/+$/, "");
  if (configured) return configured;
  return "";
}

function buildUrl(path: string, query?: Record<string, string | number | undefined>): string {
  const basePath = /^https?:\/\//.test(path) ? path : `${resolvedBase()}${path}`;
  if (!query) return basePath;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined) params.set(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${basePath}?${qs}` : basePath;
}

function requestInit(req: BffRequest): RequestInit {
  const headers = new Headers();
  for (const [key, value] of Object.entries(req.headers ?? {})) {
    if (value !== undefined && value !== "") headers.set(key, value);
  }
  const init: RequestInit = {
    method: req.method,
    credentials: "include",
  };
  if (req.body !== undefined) {
    if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    init.body = typeof req.body === "string" ? req.body : JSON.stringify(req.body);
  }
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  init.headers = headers;
  return init;
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
  const bffMode = typeof import.meta !== "undefined" && import.meta.env?.VITE_BFF_MODE;
  if (!bffMode || bffMode !== "live") {
    return mockFn();
  }
  try {
    const url = buildUrl(req.path, req.query);
    const res = await fetch(url, requestInit(req));
    if (!res.ok) {
      const err = new BffError(`BFF ${req.method} ${req.path}: HTTP ${res.status}`, res.status);
      if (onError) return onError(err) as T;
      throw err;
    }
    const json: unknown = await res.json();
    if (liveAdapter) {
      const adapted = liveAdapter(json);
      if (adapted !== undefined) return adapted;
    }
    return json as T;
  } catch (err) {
    if (onError) return onError(err) as T;
    if (err instanceof BffError) throw err;
    return mockFn();
  }
}
