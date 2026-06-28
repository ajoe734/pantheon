import type { StrategyWorkshop, StrategyCompleteness } from "./types";

export type { StrategyWorkshop, StrategyCompleteness };

export type WorkshopStreamEventType =
  | "workshop.snapshot"
  | "workshop.message.accepted"
  | "workshop.servant.response.started"
  | "workshop.servant.response.delta"
  | "workshop.servant.response.completed"
  | "workshop.completeness.updated"
  | "workshop.next_question.updated"
  | "workshop.patch.proposed"
  | "workshop.patch.validated"
  | "workshop.version.created"
  | "workshop.version.selected"
  | "workshop.readiness.updated"
  | "research.plan.created"
  | "research.plan.approved"
  | "research.plan.cancelled"
  | "research.run.queued"
  | "research.run.progress"
  | "research.run.completed"
  | "research.run.failed"
  | "consultation.started"
  | "consultation.completed"
  | "workshop.concluded"
  | "workshop.archived"
  | "stream.heartbeat"
  | "stream.error";

export interface WorkshopStreamEvent {
  spec_version: "1.0";
  event_id: string;
  event_type: WorkshopStreamEventType;
  aggregate_type: "strategy_workshop";
  aggregate_id: string;
  sequence_no: number;
  causal_parent_id?: string | null;
  event_time: string;
  emitted_at: string;
  trace_id: string;
  request_id?: string;
  idempotency_key: string;
  data_cutoff?: string;
  visibility?: "owner_private" | "owner_and_redacted_management";
  payload_schema?: string;
  payload: Record<string, unknown>;
}

export interface ReadinessGate {
  gate: "preliminary_research" | "full_validation" | "trading_room";
  state: "not_assessed" | "blocked" | "conditional" | "ready" | "stale";
  requirements: unknown[];
  blocking_requirement_ids?: string[];
  conditional_assumptions?: string[];
  evaluated_at?: string;
}

export interface StrategyReadinessAssessment {
  spec_version: string;
  assessment_id: string;
  workshop_id: string;
  strategy_id: string;
  workshop_version_id: string;
  strategy_spec_registry_id: string;
  assessment_version: number;
  gates: ReadinessGate[];
  highest_ready_gate?: "preliminary_research" | "full_validation" | "trading_room" | null;
  staleness_reasons?: string[];
  assessed_at: string;
  valid_until?: string;
  evidence_refs?: unknown[];
}

export interface WorkshopCard {
  spec_version: string;
  card_id: string;
  card_type: string;
  workshop_id: string;
  sequence_no: number;
  status: string;
  title: string;
  payload: Record<string, unknown>;
  created_at: string;
  source_event_ids?: string[];
  workshop_version_id?: string;
  strategy_spec_registry_id?: string;
  summary?: string;
  evidence_refs?: unknown[];
  allowed_actions?: Record<string, boolean>;
  updated_at?: string;
}

export interface WorkshopMessageRequest {
  content: string;
  attachment_refs?: string[];
}

function resolvedBase(baseUrl?: string): string {
  if (baseUrl) return baseUrl.replace(/\/+$/, "");
  if (typeof window !== "undefined" && window.location?.origin) {
    return window.location.origin.replace(/\/+$/, "");
  }
  return "";
}

function idempotencyKey(): string {
  const cryptoObj = typeof crypto !== "undefined" ? crypto : undefined;
  if (cryptoObj?.randomUUID) return cryptoObj.randomUUID();
  return `workshop-msg-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function recordFrom(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

async function parseJson(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { error: { message: text } };
  }
}

function workshopFrom(value: unknown): StrategyWorkshop {
  const root = recordFrom(value);
  const data = recordFrom(root.data ?? root);
  return data as unknown as StrategyWorkshop;
}

function workshopsFrom(value: unknown): StrategyWorkshop[] {
  const root = recordFrom(value);
  const items = root.data ?? root;
  if (Array.isArray(items)) return items as StrategyWorkshop[];
  const data = recordFrom(items);
  const list = data.workshops ?? data.items ?? data.results;
  return Array.isArray(list) ? (list as StrategyWorkshop[]) : [];
}

function completenessFrom(value: unknown): StrategyCompleteness {
  const root = recordFrom(value);
  const data = recordFrom(root.data ?? root);
  return data as unknown as StrategyCompleteness;
}

function readinessFrom(value: unknown): StrategyReadinessAssessment {
  const root = recordFrom(value);
  const data = recordFrom(root.data ?? root);
  return data as unknown as StrategyReadinessAssessment;
}

function cardsFrom(value: unknown): WorkshopCard[] {
  const root = recordFrom(value);
  const items = root.data ?? root;
  if (Array.isArray(items)) return items as WorkshopCard[];
  const data = recordFrom(items);
  const list = data.items ?? data.cards ?? data.results;
  return Array.isArray(list) ? (list as WorkshopCard[]) : [];
}

export async function listWorkshops(baseUrl?: string): Promise<StrategyWorkshop[]> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/workshops`;
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    const body = await parseJson(res);
    const message = recordFrom(recordFrom(body).error).message ?? `GET ${url} failed ${res.status}`;
    throw new Error(String(message));
  }
  const body = await parseJson(res);
  return workshopsFrom(body);
}

export async function getWorkshop(workshopId: string, baseUrl?: string): Promise<StrategyWorkshop | null> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/workshops/${encodeURIComponent(workshopId)}`;
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (res.status === 404) return null;
  if (!res.ok) {
    const body = await parseJson(res);
    const message = recordFrom(recordFrom(body).error).message ?? `GET ${url} failed ${res.status}`;
    throw new Error(String(message));
  }
  const body = await parseJson(res);
  return workshopFrom(body);
}

export async function postWorkshopMessage(
  workshopId: string,
  request: WorkshopMessageRequest,
  baseUrl?: string,
): Promise<Record<string, unknown>> {
  const base = resolvedBase(baseUrl);
  const getUrl = `${base}/bff/agora/workshops/${encodeURIComponent(workshopId)}`;
  const current = await fetch(getUrl, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (current.status === 404) throw new Error("Workshop not found");
  if (!current.ok) {
    const body = await parseJson(current);
    const message = recordFrom(recordFrom(body).error).message ?? `GET ${getUrl} failed ${current.status}`;
    throw new Error(String(message));
  }
  const etag = current.headers.get("ETag") ?? current.headers.get("etag");
  if (!etag) {
    throw new Error("Workshop ETag is required before posting a message");
  }

  const postUrl = `${base}/bff/agora/workshops/${encodeURIComponent(workshopId)}/messages`;
  const res = await fetch(postUrl, {
    method: "POST",
    credentials: "include",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "If-Match": etag,
      "Idempotency-Key": idempotencyKey(),
    },
    body: JSON.stringify({
      content: request.content,
      attachment_refs: request.attachment_refs ?? [],
    }),
  });
  if (!res.ok) {
    const body = await parseJson(res);
    const message = recordFrom(recordFrom(body).error).message ?? `POST ${postUrl} failed ${res.status}`;
    throw new Error(String(message));
  }
  const body = await parseJson(res);
  return recordFrom(recordFrom(body).data ?? body);
}

export async function getWorkshopCompleteness(workshopId: string, baseUrl?: string): Promise<StrategyCompleteness | null> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/workshops/${encodeURIComponent(workshopId)}/completeness`;
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (res.status === 404) return null;
  if (!res.ok) {
    const body = await parseJson(res);
    const message = recordFrom(recordFrom(body).error).message ?? `GET ${url} failed ${res.status}`;
    throw new Error(String(message));
  }
  const body = await parseJson(res);
  return completenessFrom(body);
}

export async function getWorkshopReadiness(workshopId: string, baseUrl?: string): Promise<StrategyReadinessAssessment | null> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/workshops/${encodeURIComponent(workshopId)}/readiness`;
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (res.status === 404) return null;
  if (!res.ok) {
    const body = await parseJson(res);
    const message = recordFrom(recordFrom(body).error).message ?? `GET ${url} failed ${res.status}`;
    throw new Error(String(message));
  }
  const body = await parseJson(res);
  return readinessFrom(body);
}

export async function listWorkshopCards(workshopId: string, baseUrl?: string): Promise<WorkshopCard[]> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/workshops/${encodeURIComponent(workshopId)}/cards`;
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    const body = await parseJson(res);
    const message = recordFrom(recordFrom(body).error).message ?? `GET ${url} failed ${res.status}`;
    throw new Error(String(message));
  }
  const body = await parseJson(res);
  return cardsFrom(body);
}

/**
 * Opens the SSE stream for a workshop and returns a teardown function.
 * Per design-closure-round2/03: sequence_no-ordered, at-least-once, Last-Event-ID replay.
 */
export function openWorkshopStream(
  workshopId: string,
  onEvent: (event: WorkshopStreamEvent) => void,
  baseUrl?: string,
): () => void {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/workshops/${encodeURIComponent(workshopId)}/stream`;
  const es = new EventSource(url, { withCredentials: true });
  es.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data) as WorkshopStreamEvent;
      onEvent(data);
    } catch {
      /* ignore malformed frames */
    }
  };
  return () => es.close();
}
