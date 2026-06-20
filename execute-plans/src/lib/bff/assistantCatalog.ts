// Shared assistant catalog utilities.
// This module has NO management-specific BFF path dependencies — safe to import
// from both Agora and Management without pulling in management route strings.

export interface AssistantCatalogRoute {
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  path: string;
}

function resolvedBase(baseUrl?: string): string {
  if (baseUrl) return baseUrl.replace(/\/+$/, "");
  if (typeof window !== "undefined" && window.location && window.location.origin) {
    return window.location.origin.replace(/\/+$/, "");
  }
  return "";
}

function idempotencyKey(prefix: string): string {
  const random =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${random}`;
}

async function parseJsonResponse<T>(res: Response, url: string, method: string): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${method} ${url} failed ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

async function getJson<T>(path: string, baseUrl?: string): Promise<T> {
  const base = resolvedBase(baseUrl);
  const url = `${base}${path}`;
  const res = await fetch(url, {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  return parseJsonResponse<T>(res, url, "GET");
}

async function requestJson<T>(
  method: AssistantCatalogRoute["method"],
  path: string,
  body?: Record<string, unknown>,
  baseUrl?: string,
  idempotencyPrefix?: string,
): Promise<T> {
  const base = resolvedBase(baseUrl);
  const url = `${base}${path}`;
  const headers: Record<string, string> = {
    Accept: "application/json",
  };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (idempotencyPrefix) {
    headers["Idempotency-Key"] = idempotencyKey(idempotencyPrefix);
  }
  const res = await fetch(url, {
    method,
    credentials: "include",
    headers,
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  return parseJsonResponse<T>(res, url, method);
}

function catalogPathParamAliases(name: string): string[] {
  if (name.includes("_")) {
    return [
      name,
      name.replace(/_([a-z])/g, (_, char: string) => char.toUpperCase()),
    ];
  }
  return [
    name,
    name.replace(/[A-Z]/g, (char) => `_${char.toLowerCase()}`),
  ];
}

function resolveCatalogPath(path: string, body: Record<string, unknown>): string {
  return path.replace(/\{([A-Za-z0-9_]+)\}/g, (_match, name: string) => {
    for (const alias of catalogPathParamAliases(name)) {
      const value = body[alias];
      if (value === undefined || value === null) continue;
      const text = String(value).trim();
      if (text) return encodeURIComponent(text);
    }
    throw new Error(`Missing assistant catalog route path parameter: ${name}`);
  });
}

export function assistantCatalogRouteFromHandlerRef(
  handlerRef?: string | null,
): AssistantCatalogRoute | null {
  const value = String(handlerRef ?? "").trim();
  const match = /^bff\.route:(GET|POST|PUT|PATCH|DELETE)\s+(\S+)$/i.exec(value);
  if (!match) return null;
  const method = match[1].toUpperCase() as AssistantCatalogRoute["method"];
  const path = match[2];
  if (!path.startsWith("/bff/")) return null;
  return { method, path };
}

export async function invokeAssistantCatalogRoute(
  handlerRef: string,
  body: Record<string, unknown>,
  baseUrl?: string,
): Promise<Record<string, unknown>> {
  const route = assistantCatalogRouteFromHandlerRef(handlerRef);
  if (!route) {
    throw new Error("Assistant skill handler is not a frontend-routable BFF route.");
  }
  const path = resolveCatalogPath(route.path, body);
  return requestJson(
    route.method,
    path,
    route.method === "GET" ? undefined : body,
    baseUrl,
    "assistant-catalog",
  );
}

export function getAssistantOrchestratorStatus(
  baseUrl?: string,
): Promise<Record<string, unknown>> {
  return getJson<Record<string, unknown>>("/bff/assistant/orchestrator/status", baseUrl);
}
