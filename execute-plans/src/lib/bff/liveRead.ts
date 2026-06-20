export interface BffRequest {
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  path: string;
  query?: Record<string, string | number | undefined>;
  body?: unknown;
}

export class BffError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "BffError";
    this.status = status;
  }
}

function buildUrl(path: string, query?: Record<string, string | number | undefined>): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined) params.set(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
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
    const res = await fetch(url, { method: req.method });
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
