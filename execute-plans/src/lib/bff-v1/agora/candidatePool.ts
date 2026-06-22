/**
 * BFF client for the Candidate Pool surface (v1.4, agora.research.v1).
 * All data reads go through these functions; pages must not call fetch() directly.
 * No order routing, no capital binding — read/review/score only.
 */

// ── Types (snake_case matches BFF JSON response) ──────────────────────────────

export interface CandidateScoreComponent {
  component_id: string;
  label: string;
  category:
    | "alpha"
    | "confidence"
    | "liquidity"
    | "risk"
    | "execution"
    | "data_quality"
    | "custom";
  raw_value: number | null;
  normalized_value: number | null;
  transform: string;
  direction: "higher_better" | "lower_better";
  weight: number;
  contribution: number;
  missing_policy: string;
  evidence_refs: string[];
  explanation: string;
}

export interface CandidateScoreResult {
  candidate_id: string;
  pool_id: string;
  recipe_id: string;
  recipe_version: number;
  raw_score: number;
  penalty_score: number;
  evidence_confidence: number;
  effective_score: number;
  rank: number | null;
  band: "priority_review" | "discuss" | "needs_research" | "park" | "suppressed";
  components: CandidateScoreComponent[];
  blockers: string[];
  data_cutoff: string;
  scored_at: string;
  override_reason: string | null;
}

export interface CandidatePoolMember {
  artifact_id: string;
  strategy_ref: string;
  title?: string;
  lifecycle_state: "candidate" | "review" | "approved" | "rejected";
  producing_persona_id?: string;
  sharpe_summary?: number;
  run_ref?: string;
  created_at: string;
}

/** v1.4 contract enum — must match _REVIEW_DECISION_TO_LIFECYCLE on the BFF */
export type CandidateReviewDecision =
  | "approve_for_monitoring"
  | "send_to_shadow"
  | "needs_more_research"
  | "park"
  | "reject";

export interface CandidateReviewBody {
  decision: CandidateReviewDecision;
  rationale?: string;
  reviewed_by: string;
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

function extractItems<T>(value: unknown): T[] {
  const root = recordFrom(value);
  const items = root.items;
  return Array.isArray(items) ? (items as T[]) : [];
}

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Get ranked A2 score results for all candidates in a pool.
 * Returns empty array when no scores are computed yet (pool returns status:"queued").
 */
export async function getCandidatePoolScore(
  poolId: string,
  baseUrl?: string,
): Promise<CandidateScoreResult[]> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/candidate-pools/${encodeURIComponent(poolId)}/score`;
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    const body = await parseJson(res);
    const message =
      recordFrom(recordFrom(body).error).message ??
      `GET ${url} failed ${res.status}`;
    throw new Error(String(message));
  }
  const body = await parseJson(res);
  const root = recordFrom(body);
  // When no scores computed yet, BFF returns { status: "queued", data: {...} }
  if (root.status === "queued") return [];
  return extractItems<CandidateScoreResult>(body);
}

/**
 * Trigger (or re-trigger) A2 recipe scoring for a pool.
 * Non-blocking: returns immediately; fetch scores again after a delay.
 */
export async function triggerCandidatePoolScore(
  poolId: string,
  etag?: string,
  idempotencyKey?: string,
  baseUrl?: string,
): Promise<void> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/candidate-pools/${encodeURIComponent(poolId)}/score`;
  const headers: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": "application/json",
  };
  if (etag) headers["If-Match"] = etag;
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
  const res = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers,
    body: JSON.stringify({}),
  });
  if (!res.ok) {
    const body = await parseJson(res);
    const message =
      recordFrom(recordFrom(body).error).message ??
      `POST ${url} failed ${res.status}`;
    throw new Error(String(message));
  }
}

/** List candidate pool members (metadata, lifecycle state). */
export async function listCandidatePoolMembers(
  poolId: string,
  baseUrl?: string,
): Promise<CandidatePoolMember[]> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/candidate-pools/${encodeURIComponent(poolId)}/members`;
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    const body = await parseJson(res);
    const message =
      recordFrom(recordFrom(body).error).message ??
      `GET ${url} failed ${res.status}`;
    throw new Error(String(message));
  }
  const body = await parseJson(res);
  return extractItems<CandidatePoolMember>(body);
}

/**
 * Record a candidate review decision.
 * Decisions: approve_for_monitoring, send_to_shadow, needs_more_research, park, reject.
 * park and reject require a non-empty rationale.
 * Rejected candidates are retained as negative/preference examples; they are not deleted.
 */
export async function reviewCandidateMember(
  poolId: string,
  artifactId: string,
  body: CandidateReviewBody,
  etag?: string,
  idempotencyKey?: string,
  baseUrl?: string,
): Promise<Record<string, unknown>> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/candidate-pools/${encodeURIComponent(poolId)}/members/${encodeURIComponent(artifactId)}/review`;
  const headers: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": "application/json",
  };
  if (etag) headers["If-Match"] = etag;
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
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
