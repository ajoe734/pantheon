/**
 * BFF client for the Trading Room surface (v1.3, live strict).
 * All data reads go through these functions; pages must not call fetch() directly.
 * No order routing, no capital binding — read/observe/intent-request only.
 *
 * Every read and write sends the shared BFF auth headers built by
 * `buildHeaders()` (see ../headers.ts) in addition to `credentials: "include"`,
 * so this surface authenticates the same way as the rest of the app whether
 * the browser session is cookie- or bearer-backed (AG-DYNUI-LIVE-AUTH-003).
 *
 * Mutating method (decideOnEvent) requires:
 *   If-Match        — ETag from the preceding GET response
 *   Idempotency-Key — client-generated UUID per submission
 *   X-Request-Id    — client-generated UUID per request
 * AG-BE-TR-002 rejects writes that omit these headers.
 */

import { buildHeaders } from "../headers";
import { readBffEnv } from "../runtimeEnv";

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

export type TradingRoomHttpMethod = "GET" | "POST";

export interface TradingRoomBffDiagnostic {
  method: TradingRoomHttpMethod;
  url: string;
  status: number;
  statusText?: string;
  code: string;
  message: string;
  requestId: string | null;
  correlationId: string | null;
  retryable: boolean;
}

export class TradingRoomBffError extends Error {
  readonly diagnostic: TradingRoomBffDiagnostic;

  constructor(diagnostic: TradingRoomBffDiagnostic) {
    super(diagnostic.message);
    this.name = "TradingRoomBffError";
    this.diagnostic = diagnostic;
  }
}

export function isTradingRoomBffError(error: unknown): error is TradingRoomBffError {
  return error instanceof TradingRoomBffError;
}

// ── Internal helpers ──────────────────────────────────────────────────────────

function resolvedBase(baseUrl?: string): string {
  if (baseUrl) return baseUrl.replace(/\/+$/, "");
  const configuredBase = readBffEnv().VITE_BFF_BASE_URL;
  if (configuredBase) return configuredBase.replace(/\/+$/, "");
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

function stringFrom(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
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

function diagnosticFromHttpError(
  method: TradingRoomHttpMethod,
  url: string,
  res: Response,
  body: unknown,
): TradingRoomBffDiagnostic {
  const root = recordFrom(body);
  const error = recordFrom(root.error);
  const meta = recordFrom(error.meta ?? root.meta);
  const message =
    stringFrom(error.message) ??
    stringFrom(root.message) ??
    `${method} ${url} failed ${res.status}`;
  const code =
    stringFrom(error.code) ??
    stringFrom(root.code) ??
    `BFF_HTTP_${res.status}`;

  return {
    method,
    url,
    status: res.status,
    statusText: res.statusText,
    code,
    message,
    requestId:
      stringFrom(error.request_id) ??
      stringFrom(error.requestId) ??
      stringFrom(meta.request_id) ??
      stringFrom(meta.requestId) ??
      stringFrom(res.headers.get("X-Request-Id")),
    correlationId:
      stringFrom(error.correlation_id) ??
      stringFrom(error.correlationId) ??
      stringFrom(meta.correlation_id) ??
      stringFrom(meta.correlationId) ??
      stringFrom(res.headers.get("X-Correlation-Id")),
    retryable: res.status === 408 || res.status === 409 || res.status === 412 || res.status >= 500,
  };
}

function diagnosticFromTransportError(
  method: TradingRoomHttpMethod,
  url: string,
  error: unknown,
): TradingRoomBffDiagnostic {
  return {
    method,
    url,
    status: 0,
    code: "BFF_NETWORK_ERROR",
    message: error instanceof Error ? error.message : "Network request failed",
    requestId: null,
    correlationId: null,
    retryable: true,
  };
}

async function fetchTradingRoom(
  url: string,
  init: RequestInit,
  method: TradingRoomHttpMethod,
): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch (error) {
    throw new TradingRoomBffError(diagnosticFromTransportError(method, url, error));
  }
}

async function assertOk(
  res: Response,
  method: TradingRoomHttpMethod,
  url: string,
): Promise<void> {
  if (res.ok) return;
  const body = await parseJson(res);
  throw new TradingRoomBffError(diagnosticFromHttpError(method, url, res, body));
}

// ── Public API ────────────────────────────────────────────────────────────────

/** Get the user-scoped Trading Room aggregate (all strategies, queue summary, risk). */
export async function getTradingRoom(baseUrl?: string): Promise<TradingRoomAggregate> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/trading-room`;
  const res = await fetchTradingRoom(url, {
    method: "GET",
    credentials: "include",
    headers: buildHeaders({ method: "GET" }),
  }, "GET");
  await assertOk(res, "GET", url);
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
  const res = await fetchTradingRoom(url, {
    method: "GET",
    credentials: "include",
    headers: buildHeaders({ method: "GET" }),
  }, "GET");
  if (res.status === 404) return null;
  await assertOk(res, "GET", url);
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
  const res = await fetchTradingRoom(url, {
    method: "GET",
    credentials: "include",
    headers: buildHeaders({ method: "GET" }),
  }, "GET");
  await assertOk(res, "GET", url);
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
  const res = await fetchTradingRoom(url, {
    method: "GET",
    credentials: "include",
    headers: buildHeaders({ method: "GET" }),
  }, "GET");
  if (res.status === 404) return null;
  await assertOk(res, "GET", url);
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
  const extra: Record<string, string> = { "Content-Type": "application/json" };
  if (options?.ifMatch) extra["If-Match"] = options.ifMatch;
  if (options?.idempotencyKey) extra["Idempotency-Key"] = options.idempotencyKey;
  if (options?.requestId) extra["X-Request-Id"] = options.requestId;
  const res = await fetchTradingRoom(url, {
    method: "POST",
    credentials: "include",
    headers: buildHeaders({ method: "POST", extra }),
    body: JSON.stringify(body),
  }, "POST");
  await assertOk(res, "POST", url);
  const responseBody = await parseJson(res);
  const root = recordFrom(responseBody);
  return recordFrom(root.data ?? root);
}
