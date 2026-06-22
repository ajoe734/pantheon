/**
 * BFF client for Agora research plan and run endpoints.
 * Source: services/control-plane/openapi/agora_v1_3.openapi.yaml §Research Plans / §Research Runs
 * Live strict: pages must not call these BFF endpoints directly — import from here instead.
 */

export type ResearchPlanStatus = "draft" | "approved" | "running" | "completed" | "cancelled";
export type ResearchRunExecutionStatus =
  | "queued"
  | "dispatching"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "timed_out";
export type ResearchRunOutcome = "pending" | "pass" | "fail" | "inconclusive";
export type ResearchBackendMode = "real" | "fixture" | "stub";

export interface ResearchPlanExecution {
  spec_version: "1.0";
  plan_id: string;
  workshop_id: string;
  strategy_id: string;
  strategy_spec_registry_id: string;
  status: ResearchPlanStatus;
  approval?: {
    state: "pending" | "approved" | "rejected" | "not_required";
    decided_by?: string;
    decided_at?: string;
    reason?: string;
  };
  stages: Array<{
    stage_id: string;
    stage_type: string;
    status: string;
    dependencies: string[];
    required_capability: string;
    routing: {
      preferred_backend: string;
      effective_backend?: string;
      backend_mode?: ResearchBackendMode;
      fallback_policy: "fail_closed" | "explicit_fixture_only";
      routing_reason?: string;
    };
    input_refs?: string[];
    output_refs?: string[];
    parameters?: Record<string, unknown>;
    blocking_reasons?: string[];
  }>;
  budget?: {
    compute_tier?: "light" | "standard" | "heavy";
    max_runtime_seconds?: number;
    max_parallel_stages?: number;
    external_data_spend_allowed?: boolean;
  };
  run_ids?: string[];
  no_order_route_proof: "research_plan_no_order_route";
  created_at: string;
  updated_at?: string;
}

export interface ResearchRunProjection {
  spec_version: "1.0";
  run_id: string;
  plan_id: string;
  workshop_id: string;
  strategy_id: string;
  strategy_spec_registry_id: string;
  stage_id: string;
  stage_type: string;
  execution_status: ResearchRunExecutionStatus;
  outcome: ResearchRunOutcome;
  progress: {
    phase: string;
    percent: number;
    completed_units?: number;
    total_units?: number;
    message?: string;
    updated_at: string;
  };
  backend: {
    requested: string;
    effective: string;
    mode: ResearchBackendMode;
    version?: string;
    activation_state?: string;
  };
  metrics?: Array<{
    category: "performance" | "risk" | "cost" | "capacity" | "robustness" | "calibration" | "data_quality";
    name: string;
    value: number;
    unit?: string;
    direction?: "higher_better" | "lower_better" | "target_range";
    threshold?: number;
    gate_result: "pass" | "fail" | "not_applicable" | "not_evaluated";
    baseline?: number;
    delta?: number;
    ci_lower?: number;
    ci_upper?: number;
    source_ref?: string;
  }>;
  findings?: Array<{
    finding_id: string;
    severity: "info" | "watch" | "warning" | "high" | "critical";
    summary: string;
    detail?: string;
    evidence_refs?: Array<{ ref_type: string; ref_id: string; summary?: string; data_cutoff?: string }>;
  }>;
  warnings?: string[];
  blocking_reasons?: string[];
  artifact_refs?: string[];
  evidence_refs?: Array<{ ref_type: string; ref_id: string; summary?: string; data_cutoff?: string }>;
  lineage_refs?: string[];
  failure?: {
    code?: string;
    message?: string;
    retryable?: boolean;
    failed_at?: string;
  };
  data_cutoff?: string;
  no_order_route_proof: "research_only_not_direct_action";
  created_at: string;
  started_at?: string;
  completed_at?: string;
  updated_at?: string;
}

export interface CommandResponse {
  status: "accepted" | "queued" | "completed";
  data: unknown;
  meta: Record<string, unknown>;
}

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

function planFrom(value: unknown): ResearchPlanExecution {
  const root = recordFrom(value);
  const data = recordFrom(root.data ?? root);
  return data as unknown as ResearchPlanExecution;
}

function plansFrom(value: unknown): ResearchPlanExecution[] {
  const root = recordFrom(value);
  const items = root.data ?? root;
  if (Array.isArray(items)) return items as ResearchPlanExecution[];
  const data = recordFrom(items);
  const list = data.items ?? data.plans ?? data.results;
  return Array.isArray(list) ? (list as ResearchPlanExecution[]) : [];
}

function runFrom(value: unknown): ResearchRunProjection {
  const root = recordFrom(value);
  const data = recordFrom(root.data ?? root);
  return data as unknown as ResearchRunProjection;
}

function runsFrom(value: unknown): ResearchRunProjection[] {
  const root = recordFrom(value);
  const items = root.data ?? root;
  if (Array.isArray(items)) return items as ResearchRunProjection[];
  const data = recordFrom(items);
  const list = data.items ?? data.runs ?? data.results;
  return Array.isArray(list) ? (list as ResearchRunProjection[]) : [];
}

function commandResponseFrom(value: unknown): CommandResponse {
  const root = recordFrom(value);
  return {
    status: (root.status as CommandResponse["status"]) ?? "accepted",
    data: root.data ?? null,
    meta: (root.meta as Record<string, unknown>) ?? {},
  };
}

function artifactRefsFrom(value: unknown): string[] {
  const root = recordFrom(value);
  const items = root.data ?? root;
  if (Array.isArray(items)) return items as string[];
  const data = recordFrom(items);
  const list = data.items ?? data.artifact_refs ?? data.results;
  return Array.isArray(list) ? (list as string[]) : [];
}

async function throwOnError(res: Response, url: string): Promise<void> {
  if (!res.ok) {
    const body = await parseJson(res);
    const message =
      recordFrom(recordFrom(body).error).message ?? `${res.status} ${res.statusText} at ${url}`;
    throw new Error(String(message));
  }
}

/**
 * GET /bff/agora/workshops/{workshop_id}/research-plans
 * List research plans attached to a workshop.
 */
export async function listWorkshopResearchPlans(
  workshopId: string,
  baseUrl?: string,
): Promise<ResearchPlanExecution[]> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/workshops/${encodeURIComponent(workshopId)}/research-plans`;
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  await throwOnError(res, url);
  return plansFrom(await parseJson(res));
}

/**
 * POST /bff/agora/workshops/{workshop_id}/research-plans
 * Create a draft research plan attached to the workshop.
 */
export async function createWorkshopResearchPlan(
  workshopId: string,
  plan: Partial<ResearchPlanExecution>,
  options?: { idempotencyKey?: string; ifMatch?: string; requestId?: string },
  baseUrl?: string,
): Promise<ResearchPlanExecution> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/workshops/${encodeURIComponent(workshopId)}/research-plans`;
  const headers: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": "application/json",
  };
  if (options?.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;
  if (options?.ifMatch) headers["If-Match"] = options.ifMatch;
  if (options?.requestId) headers["X-Request-Id"] = options.requestId;
  const res = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers,
    body: JSON.stringify(plan),
  });
  await throwOnError(res, url);
  return planFrom(await parseJson(res));
}

/**
 * GET /bff/agora/research-plans/{plan_id}
 * Get research plan detail.
 */
export async function getResearchPlan(
  planId: string,
  baseUrl?: string,
): Promise<ResearchPlanExecution | null> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/research-plans/${encodeURIComponent(planId)}`;
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (res.status === 404) return null;
  await throwOnError(res, url);
  return planFrom(await parseJson(res));
}

/**
 * POST /bff/agora/research-plans/{plan_id}/approve
 * Approve a draft research plan for dispatch.
 */
export async function approveResearchPlan(
  planId: string,
  options?: { idempotencyKey?: string; ifMatch?: string; requestId?: string },
  baseUrl?: string,
): Promise<CommandResponse> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/research-plans/${encodeURIComponent(planId)}/approve`;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options?.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;
  if (options?.ifMatch) headers["If-Match"] = options.ifMatch;
  if (options?.requestId) headers["X-Request-Id"] = options.requestId;
  const res = await fetch(url, { method: "POST", credentials: "include", headers });
  await throwOnError(res, url);
  return commandResponseFrom(await parseJson(res));
}

/**
 * POST /bff/agora/research-plans/{plan_id}/cancel
 * Cancel an approved or running research plan.
 */
export async function cancelResearchPlan(
  planId: string,
  options?: { idempotencyKey?: string; ifMatch?: string; requestId?: string },
  baseUrl?: string,
): Promise<CommandResponse> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/research-plans/${encodeURIComponent(planId)}/cancel`;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options?.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;
  if (options?.ifMatch) headers["If-Match"] = options.ifMatch;
  if (options?.requestId) headers["X-Request-Id"] = options.requestId;
  const res = await fetch(url, { method: "POST", credentials: "include", headers });
  await throwOnError(res, url);
  return commandResponseFrom(await parseJson(res));
}

/**
 * GET /bff/agora/research-plans/{plan_id}/runs
 * List research runs for a plan.
 */
export async function listResearchPlanRuns(
  planId: string,
  baseUrl?: string,
): Promise<ResearchRunProjection[]> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/research-plans/${encodeURIComponent(planId)}/runs`;
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  await throwOnError(res, url);
  return runsFrom(await parseJson(res));
}

/**
 * POST /bff/agora/research-plans/{plan_id}/runs
 * Dispatch an approved research plan (creates a new run).
 */
export async function dispatchResearchPlan(
  planId: string,
  options?: { idempotencyKey?: string; ifMatch?: string; requestId?: string },
  baseUrl?: string,
): Promise<CommandResponse> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/research-plans/${encodeURIComponent(planId)}/runs`;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options?.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;
  if (options?.ifMatch) headers["If-Match"] = options.ifMatch;
  if (options?.requestId) headers["X-Request-Id"] = options.requestId;
  const res = await fetch(url, { method: "POST", credentials: "include", headers });
  await throwOnError(res, url);
  return commandResponseFrom(await parseJson(res));
}

/**
 * GET /bff/agora/research-runs/{run_id}
 * Get research run projection.
 */
export async function getResearchRun(
  runId: string,
  baseUrl?: string,
): Promise<ResearchRunProjection | null> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/research-runs/${encodeURIComponent(runId)}`;
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (res.status === 404) return null;
  await throwOnError(res, url);
  return runFrom(await parseJson(res));
}

/**
 * POST /bff/agora/research-runs/{run_id}/cancel
 * Request cancellation of an active research run.
 */
export async function cancelResearchRun(
  runId: string,
  options?: { idempotencyKey?: string; requestId?: string },
  baseUrl?: string,
): Promise<CommandResponse> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/research-runs/${encodeURIComponent(runId)}/cancel`;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options?.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;
  if (options?.requestId) headers["X-Request-Id"] = options.requestId;
  const res = await fetch(url, { method: "POST", credentials: "include", headers });
  await throwOnError(res, url);
  return commandResponseFrom(await parseJson(res));
}

/**
 * GET /bff/agora/research-runs/{run_id}/artifacts
 * List artifact and evidence refs produced by a research run.
 */
export async function listResearchRunArtifacts(
  runId: string,
  baseUrl?: string,
): Promise<string[]> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/research-runs/${encodeURIComponent(runId)}/artifacts`;
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  await throwOnError(res, url);
  return artifactRefsFrom(await parseJson(res));
}
