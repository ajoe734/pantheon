export interface AskRequest {
  prompt: string;
  persona_ids?: string[];
  refs?: Array<Record<string, unknown>>;
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface AskSession {
  session_id: string;
  status: "active" | "completed";
  prompt?: string;
  transcript?: Array<Record<string, unknown>>;
  messages?: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

function resolvedBase(baseUrl?: string): string {
  if (baseUrl) return baseUrl.replace(/\/+$/,'');
  if (typeof window !== "undefined" && window.location && window.location.origin) {
    return window.location.origin.replace(/\/+$/,'');
  }
  return "";
}

/** POST /bff/agora/ask — create an ask session. Returns the parsed JSON body. */
export async function postAsk(body: AskRequest, baseUrl?: string): Promise<any> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/ask`;
  const res = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`POST ${url} failed ${res.status}: ${text}`);
  }
  return res.json();
}

/** GET /bff/agora/ask/sessions/{id} — fetch session detail/transcript */
export async function getAskSession(sessionId: string, baseUrl?: string): Promise<AskSession> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/ask/sessions/${encodeURIComponent(sessionId)}`;
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`GET ${url} failed ${res.status}: ${text}`);
  }
  const body = await res.json();
  return body.data ?? body;
}

/** Open an EventSource subscribed to the ask SSE channel. Returns the EventSource. */
export function openAskSse(onMessage: (ev: MessageEvent) => void, baseUrl?: string): EventSource {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/events/stream?channel=ask`;
  const es = new EventSource(url);
  es.addEventListener("message", onMessage);
  es.addEventListener("error", (e) => {
    // allow callers to observe errors via the returned EventSource
    // console.warn("Ask SSE error", e);
  });
  return es;
}
