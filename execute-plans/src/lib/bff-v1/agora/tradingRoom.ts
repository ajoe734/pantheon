/**
 * BFF client for the Trading Room surface (v1.3, live strict).
 * All data reads go through these functions; pages must not call fetch() directly.
 * No order routing, no capital binding — read/observe/intent-request only.
 *
 * Mutating method (decideOnEvent) requires:
 *   If-Match        — ETag from the preceding GET response
 *   Idempotency-Key — client-generated UUID per submission
 *   X-Request-Id    — client-generated UUID per request
 * AG-BE-TR-002 rejects writes that omit these headers.
 */

// ── Types derived from v4 schemas ──────────────────────────────────────────────

export interface TradingRoomStrategyEntry {
  strategy_id: string;
  strategy_spec_registry_id: string;
  title: string;
  readiness_state: "blocked" | "conditional" | "ready" | "stale";
  dashboard_recipe_id?: string;
  monitoring_state: "inactive" | "shadow" | "paper_requested" | "monitoring" | "paused";
  candidate_count?: number;
  position_count?: number;
  pending_event_counts: {
    entry?: number;
    add?: number;
    reduce?: number;
    exit?: number;
    review?: number;
  };
  shadow_status?: string;
  performance_summary?: Record<string, unknown>;
  staleness_reasons?: string[];
}

export interface TradingRoomQueueSummary {
  entry: number;
  add: number;
  reduce: number;
  exit: number;
  review: number;
}

export interface TradingRoomRiskSummary {
  state: "normal" | "watch" | "warning" | "critical";
  summary?: string;
  alerts?: string[];
}

export interface TradingRoomAggregate {
  spec_version: "1.0";
  user_scope_ref: string;
  strategies: TradingRoomStrategyEntry[];
  queue_summary: TradingRoomQueueSummary;
  top_decision_events?: TradingDecisionEvent[];
  position_summaries?: unknown[];
  risk_summary: TradingRoomRiskSummary;
  snapshot_at: string;
  data_cutoff: string;
}

export interface EvidenceRef {
  ref_type:
    | "evidence_bundle"
    | "evidence_item"
    | "source_record"
    | "citation"
    | "experiment_artifact"
    | "registry_entry"
    | "consult_memo"
    | "research_run"
    | "telemetry_snapshot"
    | "market_context";
  ref_id: string;
  summary?: string;
  data_cutoff?: string;
}

export interface TradingDecisionEvent {
  spec_version: "1.0";
  decision_event_id: string;
  dedupe_key?: string;
  event_kind: "entry" | "add" | "reduce" | "exit" | "review";
  origin: "strategy_signal" | "risk_rule" | "position_rule" | "servant_analysis" | "trader_request";
  strategy_id: string;
  strategy_spec_registry_id: string;
  candidate_ref?: string;
  position_ref?: string;
  subject: {
    symbol: string;
    asset_class?: string;
    venue?: string;
  };
  state:
    | "approaching"
    | "triggered"
    | "pending_review"
    | "decided"
    | "expired"
    | "invalidated"
    | "superseded";
  trigger?: {
    rule_id?: string;
    summary?: string;
    current_value?: unknown;
    threshold?: unknown;
    distance_to_trigger?: number;
  };
  triggered_at: string;
  expires_at?: string;
  confidence: {
    value: number;
    basis: "model" | "statistical" | "heuristic" | "mixed";
    calibration_state: "calibrated" | "partially_calibrated" | "uncalibrated";
    sample_size?: number;
    source_ref?: string;
  };
  probability: {
    target_outcome: string;
    horizon: string;
    value: number;
    ci_lower?: number;
    ci_upper?: number;
    model_ref?: string;
    as_of?: string;
  };
  expected_value: {
    horizon: string;
    unit: "pct_return" | "currency" | "risk_units";
    gross: number;
    cost: number;
    net: number;
    downside: number;
    expected_shortfall?: number;
  };
  rationale: Array<{
    claim: string;
    confidence: number;
    evidence_refs?: EvidenceRef[];
  }>;
  risk_notes: Array<{
    severity: "info" | "watch" | "warning" | "high" | "critical";
    domain: string;
    summary: string;
    mitigation?: string;
  }>;
  evidence_refs: EvidenceRef[];
  invalidation: {
    conditions: string[];
    current_state: "valid" | "watch" | "invalidated";
    last_checked_at?: string;
  };
  suggested_action: "enter" | "add" | "reduce" | "exit" | "review" | "no_action";
  suggested_size?: {
    size_hint?: "small" | "medium" | "large" | "full_position";
    portfolio_pct?: number;
    non_binding: true;
  };
  position_snapshot?: Record<string, unknown>;
  decision_state?:
    | "pending"
    | "approved_by_trader"
    | "rejected_by_trader"
    | "deferred"
    | "expired"
    | "handed_off"
    | "superseded";
  data_cutoff?: string;
  no_order_route_proof: "agora_decision_support_only";
}

export type DecisionChoice = "approve" | "reject" | "defer" | "modify";

export interface DecisionBody {
  decision: DecisionChoice;
  rationale?: string;
  modifications?: Record<string, unknown>;
}

// ── Internal helpers ──────────────────────────────────────────────────────────

function resolvedBase(baseUrl?: string): string {
  if (baseUrl) return baseUrl.replace(/\/+$/, "");
  if (typeof window !== "undefined" && window.location?.origin) {
    return window.location.origin.replace(/\/+$/, "");
  }
  return "";
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

function extractAggregate(value: unknown): TradingRoomAggregate {
  const root = recordFrom(value);
  const data = recordFrom(root.data ?? root);
  return data as unknown as TradingRoomAggregate;
}

function extractDecisionEvents(value: unknown): TradingDecisionEvent[] {
  const root = recordFrom(value);
  const items = root.data ?? root;
  if (Array.isArray(items)) return items as TradingDecisionEvent[];
  const data = recordFrom(items);
  const list = data.items ?? data.events ?? data.results;
  return Array.isArray(list) ? (list as TradingDecisionEvent[]) : [];
}

function extractDecisionEvent(value: unknown): TradingDecisionEvent {
  const root = recordFrom(value);
  const data = recordFrom(root.data ?? root);
  return data as unknown as TradingDecisionEvent;
}

// ── Public API ────────────────────────────────────────────────────────────────

/** Get the user-scoped Trading Room aggregate (all strategies, queue summary, risk). */
export async function getTradingRoom(baseUrl?: string): Promise<TradingRoomAggregate> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/trading-room`;
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
  return extractAggregate(body);
}

/** Get strategy-level Trading Room detail. Returns the raw payload (DetailEnvelope). */
export async function getTradingRoomStrategy(
  strategyId: string,
  baseUrl?: string,
): Promise<Record<string, unknown> | null> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/trading-room/strategies/${encodeURIComponent(strategyId)}`;
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
  const root = recordFrom(body);
  return recordFrom(root.data ?? root);
}

export interface ListDecisionEventsParams {
  event_kind?: "entry" | "add" | "reduce" | "exit" | "review";
  state?: string;
}

export interface DecisionEventsResult {
  items: TradingDecisionEvent[];
  /** ETag from the response — forward as If-Match in subsequent writes. */
  etag: string | null;
}

/** List Trading Room decision events. Filterable by kind and state. Returns items + ETag for If-Match. */
export async function listDecisionEvents(
  params?: ListDecisionEventsParams,
  baseUrl?: string,
): Promise<DecisionEventsResult> {
  const base = resolvedBase(baseUrl);
  const qs = new URLSearchParams();
  if (params?.event_kind) qs.set("event_kind", params.event_kind);
  if (params?.state) qs.set("state", params.state);
  const query = qs.toString();
  const url = `${base}/bff/agora/trading-room/decision-events${query ? `?${query}` : ""}`;
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
  return {
    items: extractDecisionEvents(body),
    etag: res.headers.get("ETag"),
  };
}

/** Get a single decision event by ID. */
export async function getDecisionEvent(
  decisionEventId: string,
  baseUrl?: string,
): Promise<TradingDecisionEvent | null> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/trading-room/decision-events/${encodeURIComponent(decisionEventId)}`;
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
  return extractDecisionEvent(body);
}

/**
 * Record a trader decision on a decision event.
 * "approve" may create a TradingIntent (request-only; no order route; no capital binding).
 *
 * options.ifMatch       — ETag from listDecisionEvents or getDecisionEvent; required by AG-BE-TR-002.
 * options.idempotencyKey — client-generated UUID per submission; required by AG-BE-TR-002.
 * options.requestId      — client-generated UUID per request; required by AG-BE-TR-002.
 */
export async function decideOnEvent(
  decisionEventId: string,
  body: DecisionBody,
  options?: { ifMatch?: string; idempotencyKey?: string; requestId?: string },
  baseUrl?: string,
): Promise<Record<string, unknown>> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/trading-room/decision-events/${encodeURIComponent(decisionEventId)}/decisions`;
  const headers: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": "application/json",
  };
  if (options?.ifMatch) headers["If-Match"] = options.ifMatch;
  if (options?.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;
  if (options?.requestId) headers["X-Request-Id"] = options.requestId;
  const res = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const responseBody = await parseJson(res);
    const message =
      recordFrom(recordFrom(responseBody).error).message ??
      `POST ${url} failed ${res.status}`;
    throw new Error(String(message));
  }
  const responseBody = await parseJson(res);
  const root = recordFrom(responseBody);
  return recordFrom(root.data ?? root);
}
