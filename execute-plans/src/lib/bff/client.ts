// BFF-LUV-FE-002 — Management Console live read adapter surface.
//
// This is the canonical place from which Management Console pages obtain
// list/detail data for every route family the Pantheon BFF currently
// exposes. It wraps the underlying `bff-v1` transport (`withLiveOrMock`,
// `paths`, `lists`) so:
//
//   * VITE_BFF_MODE=mock → all reads come from the in-process seed.
//   * VITE_BFF_MODE=live + VITE_BFF_FALLBACK=auto (default) → "hybrid" mode:
//       try live BFF, transparently fall back to mock on transport failure,
//       and report the fallback through `liveStatus` so the UI can surface
//       it in the live-mode banner. The fallback path is explicit, not
//       silent.
//   * VITE_BFF_MODE=live + VITE_BFF_FALLBACK=strict → "real" mode:
//       transport failure surfaces as a typed BffError; mock seeds are
//       NEVER substituted in this mode. 4xx responses always propagate.
//
// Detail (`get(id)`) calls bypass `bff-v1/lists.ts` because that layer only
// covers list endpoints. They use `withLiveOrMock` directly with the
// canonical `/bff/<resource>/{id}` paths defined in `bff-v1/paths.ts`.

import type { ListEnvelope } from "@/lib/bff-v1";
import {
  paths,
  lists as bffV1Lists,
  withLiveOrMock,
  liveStatus,
} from "@/lib/bff-v1";
import { normalizeLiveListResponse } from "@/lib/bff-v1/lists";
import { readBffEnv } from "@/lib/bff-v1/runtimeEnv";
import {
  strictDataFrom,
  strictNotFoundAsUndefined,
  withStrictLiveOrMock,
} from "@/lib/bff/liveRead";
import * as seed from "@/mocks/seed";
import type {
  OodaLoopPacket,
  OodaPacketDetail,
  OodaPacketMeta,
} from "@/lib/ooda/packets";
import type {
  ManagementEvidenceItem,
  ManagementEvidenceQuery,
  ManagementEvidenceResponse,
  ManagementEvolutionJournalItem,
  ManagementEvolutionJournalQuery,
  ManagementEvolutionJournalResponse,
  ManagementPersonaIntentItem,
  ManagementPersonaIntentQuery,
  ManagementPersonaIntentResponse,
  ManagementTradingPulseRankingsQuery,
  ManagementTradingPulseRankingsResponse,
  ManagementTradingPulseResponse,
  ManagementReadinessResponse,
} from "@/lib/bff-v1/management";
import type {
  Strategy,
  Persona,
  CapitalPool,
  RankingFormula,
  Rebalance,
  Deployment,
  EvolutionProgram,
  ResearchExperiment,
  Artifact,
  Tool,
  McpServer,
  McpTool,
  Skill,
  Channel,
  Job,
  Runtime,
  Alert,
  Incident,
  ApprovalRequest,
  AuditEvent,
} from "@/lib/bff/types";

export type PersonaFleetHealthStatus = "healthy" | "degraded" | "critical";
export type PersonaFleetCompetitionTrack =
  | "paper_challenger"
  | "canary_challenger"
  | "live_incumbent"
  | "watchlist_incumbent"
  | "risk_off_excluded"
  | (string & {});
export type PersonaFleetCapitalScope =
  | "none"
  | "paper"
  | "canary"
  | "live"
  | "risk_off"
  | (string & {});
export type PersonaFleetReviewStatus =
  | "none"
  | "promotion_pending"
  | "live_pending"
  | "quarterly_pending"
  | "incident_resume_pending"
  | "rejected"
  | (string & {});
export type PersonaFleetProductLifecycleState =
  | "paper_provisioning"
  | "paper_running"
  | "paper_warming_up"
  | "paper_ineligible"
  | "paper_eligible"
  | "promotion_review_pending"
  | "promotion_rejected"
  | "canary_running"
  | "live_review_pending"
  | "live_running"
  | "quarterly_review_pending"
  | "watchlist"
  | "auto_reduced"
  | "risk_off"
  | "frozen"
  | "retired"
  | "setup_failed"
  | "repair_required"
  | (string & {});
export type PersonaFleetHumanReviewRequirement =
  | "promotion_to_canary"
  | "canary_to_live"
  | "quarterly_rebalance"
  | "resume_after_incident"
  | "retire"
  | (string & {});
export type PersonaFleetRowActionId =
  | "view_persona"
  | "monitor_paper_runtime"
  | "monitor_canary_runtime"
  | "monitor_live_runtime"
  | "submit_live_review"
  | "open_human_review"
  | "open_incident_review"
  | "repair_paper_setup"
  | (string & {});

export interface PersonaFleetHealth {
  status: PersonaFleetHealthStatus | string;
  severity: string;
  score: number;
  reasons: string[];
  latest_telemetry_at?: string | null;
  active_incident_count: number;
  [key: string]: unknown;
}

export interface PersonaFleetPaperRuntimeProjection {
  runtime_binding_id?: string | null;
  runtimeBindingId?: string | null;
  runtime_id?: string | null;
  runtimeId?: string | null;
  status?: string | null;
  heartbeat_status?: string | null;
  heartbeatStatus?: string | null;
  [key: string]: unknown;
}

export interface PersonaFleetRepairProjection {
  retryable?: boolean;
  failed_step?: string | null;
  failedStep?: string | null;
  repair_url?: string | null;
  repairUrl?: string | null;
  [key: string]: unknown;
}

export interface PersonaFleetReadinessProjection {
  persona_id?: string;
  personaId?: string;
  product_lifecycle_state?: PersonaFleetProductLifecycleState;
  productLifecycleState?: PersonaFleetProductLifecycleState;
  setup_status?: string;
  setupStatus?: string;
  paper_runtime?: PersonaFleetPaperRuntimeProjection;
  paperRuntime?: PersonaFleetPaperRuntimeProjection;
  competition_track?: PersonaFleetCompetitionTrack;
  competitionTrack?: PersonaFleetCompetitionTrack;
  capital_scope?: PersonaFleetCapitalScope;
  capitalScope?: PersonaFleetCapitalScope;
  required_human_review?: PersonaFleetHumanReviewRequirement | null;
  requiredHumanReview?: PersonaFleetHumanReviewRequirement | null;
  repair?: PersonaFleetRepairProjection;
  trace_id?: string;
  traceId?: string;
  snapshot_at?: string;
  snapshotAt?: string;
  [key: string]: unknown;
}

export interface PersonaFleetCompetitionStanding {
  standing_id?: string;
  standingId?: string;
  persona_id?: string;
  personaId?: string;
  cohort_id?: string;
  cohortId?: string;
  competition_track?: PersonaFleetCompetitionTrack;
  competitionTrack?: PersonaFleetCompetitionTrack;
  cohort_rank?: number;
  cohortRank?: number;
  score_snapshot_id?: string;
  scoreSnapshotId?: string;
  promotion_score?: number;
  promotionScore?: number;
  product_lifecycle_state?: PersonaFleetProductLifecycleState;
  productLifecycleState?: PersonaFleetProductLifecycleState;
  capital_scope?: PersonaFleetCapitalScope;
  capitalScope?: PersonaFleetCapitalScope;
  human_review_state?: PersonaFleetReviewStatus;
  humanReviewState?: PersonaFleetReviewStatus;
  incumbent_persona_id?: string | null;
  incumbentPersonaId?: string | null;
  challenger_delta_score?: number | null;
  challengerDeltaScore?: number | null;
  replacement_risk?: string;
  replacementRisk?: string;
  snapshot_at?: string;
  snapshotAt?: string;
  [key: string]: unknown;
}

export interface PersonaFleetRowAction {
  action_id: PersonaFleetRowActionId;
  actionId: PersonaFleetRowActionId;
  label: string;
  href: string;
  requires_human_review: boolean;
  requiresHumanReview: boolean;
  startup_wizard_visible: false;
  startupWizardVisible: false;
  legacy_startup_wizard_hidden: true;
  legacyStartupWizardHidden: true;
  [key: string]: unknown;
}

export interface PersonaFleetItem {
  id: string;
  persona_id: string;
  personaId?: string;
  persona?: Record<string, unknown>;
  name?: string;
  persona_name?: string;
  personaName?: string;
  mode?: string;
  state?: string;
  status?: PersonaFleetHealthStatus | string;
  health: PersonaFleetHealth | PersonaFleetHealthStatus | string;
  score?: number;
  bindings?: Array<Record<string, unknown>>;
  capitalPools?: Array<Record<string, unknown>>;
  capital_pools?: Array<Record<string, unknown>>;
  runtimeBindings?: Array<Record<string, unknown>>;
  runtime_bindings?: Array<Record<string, unknown>>;
  telemetrySummary?: Record<string, unknown>;
  telemetry_summary?: Record<string, unknown>;
  training?: Record<string, unknown>;
  evolution?: Record<string, unknown>;
  allowedActions?: Record<string, unknown>;
  deployment_stage?: string;
  deploymentStage?: string;
  product_lifecycle_state?: PersonaFleetProductLifecycleState;
  productLifecycleState?: PersonaFleetProductLifecycleState;
  competition_track?: PersonaFleetCompetitionTrack;
  competitionTrack?: PersonaFleetCompetitionTrack;
  capital_scope?: PersonaFleetCapitalScope;
  capitalScope?: PersonaFleetCapitalScope;
  paper_runtime_status?: string;
  paperRuntimeStatus?: string;
  evaluation_status?: string;
  evaluationStatus?: string;
  review_status?: PersonaFleetReviewStatus;
  reviewStatus?: PersonaFleetReviewStatus;
  required_human_review?: PersonaFleetHumanReviewRequirement | null;
  requiredHumanReview?: PersonaFleetHumanReviewRequirement | null;
  live_status?: string;
  liveStatus?: string;
  setup_failed_step?: string | null;
  setupFailedStep?: string | null;
  repair_action?: PersonaFleetRowAction | null;
  repairAction?: PersonaFleetRowAction | null;
  readiness_projection?: PersonaFleetReadinessProjection;
  readinessProjection?: PersonaFleetReadinessProjection;
  competition_standing?: PersonaFleetCompetitionStanding;
  competitionStanding?: PersonaFleetCompetitionStanding;
  row_action?: PersonaFleetRowAction;
  rowAction?: PersonaFleetRowAction;
  [key: string]: unknown;
}

export interface PersonaFleetAggregate {
  data: PersonaFleetItem[];
  items: PersonaFleetItem[];
  summary: {
    total_personas: number;
    returned_personas: number;
    critical_personas: number;
    degraded_personas: number;
    healthy_personas: number;
    bound_personas: number;
    runtime_bound_personas: number;
    competition_tracks?: Record<string, number>;
    capital_scopes?: Record<string, number>;
    review_states?: Record<string, number>;
    competition_default?: string;
  };
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    surfaces?: Record<string, unknown>;
    [key: string]: unknown;
  };
}

export type PaperPersonaLaunchStatus =
  | "paper_provisioning"
  | "paper_running"
  | "paper_warming_up"
  | "setup_failed"
  | "repair_required"
  | (string & {});

export type PaperPersonaLaunchStep =
  | "persona_identity_created"
  | "policy_snapshots_created"
  | "paper_capital_pool_ready"
  | "paper_binding_active"
  | "paper_deployment_plan_created"
  | "paper_approval_recorded"
  | "paper_runtime_binding_created"
  | "paper_runtime_started"
  | "telemetry_heartbeat_verified"
  | (string & {});

export interface PaperCapitalPoolSelection {
  mode: "select_existing" | "create_from_template" | (string & {});
  capital_scope?: "paper";
  capitalScope?: "paper";
  capital_pool_id?: string | null;
  capitalPoolId?: string | null;
  template_id?: string | null;
  templateId?: string | null;
  [key: string]: unknown;
}

export interface PaperPersonaLaunchRequest {
  name: string;
  mandate: string;
  strategy_family?: string[];
  strategyFamily?: string[];
  market_scope?: string[];
  marketScope?: string[];
  source_scope?: string[];
  sourceScope?: string[];
  risk_profile_id?: string;
  riskProfileId?: string;
  paper_capital_pool?: PaperCapitalPoolSelection;
  paperCapitalPool?: PaperCapitalPoolSelection;
  paper_budget?: number;
  paperBudget?: number;
  artifact_id?: string;
  artifactId?: string;
  operator_note?: string | null;
  operatorNote?: string | null;
  persona_id?: string;
  personaId?: string;
  launch_id?: string;
  launchId?: string;
  [key: string]: unknown;
}

export interface PaperPersonaLaunch {
  launch_id: string;
  name: string;
  mandate: string;
  strategy_family: string[];
  market_scope: string[];
  source_scope: string[];
  risk_profile_id: string;
  paper_capital_pool: PaperCapitalPoolSelection;
  paper_budget: number;
  artifact_id: string;
  operator_note?: string | null;
  status: PaperPersonaLaunchStatus;
  persona_id: string;
  capital_pool_id?: string | null;
  binding_id?: string | null;
  deployment_plan_id?: string | null;
  approval_decision_id?: string | null;
  runtime_binding_id?: string | null;
  runtime_id?: string | null;
  completed_steps: PaperPersonaLaunchStep[];
  failed_step?: PaperPersonaLaunchStep | null;
  retryable: boolean;
  repair_url?: string | null;
  trace_id: string;
  created_at: string;
  updated_at?: string;
  [key: string]: unknown;
}

export interface PaperPersonaLaunchResponse {
  data: PaperPersonaLaunch;
  launch: PaperPersonaLaunch;
  meta: {
    snapshot_at?: string;
    idempotency?: {
      idempotencyKey?: string;
      replayed?: boolean;
    };
    trace_id?: string;
    surfaces?: Record<string, unknown>;
    audit_events?: Array<Record<string, unknown>>;
    [key: string]: unknown;
  };
}

export interface PersonaReadinessResponse {
  data: PersonaFleetReadinessProjection;
  readinessProjection: PersonaFleetReadinessProjection;
  latest_launch?: PaperPersonaLaunch | null;
  meta: {
    snapshot_at?: string;
    surfaces?: Record<string, unknown>;
    [key: string]: unknown;
  };
}

export interface PaperPersonaLaunchMutationOptions {
  idempotencyKey: string;
  traceId?: string;
  requestId?: string;
}

export type HumanInboxSourceType = "approval" | "intervention";
export type HumanInboxPriority = "critical" | "high" | "medium" | "low" | "unknown";

export interface HumanInboxItem {
  id: string;
  inbox_id: string;
  inboxType: HumanInboxSourceType;
  source_type: HumanInboxSourceType;
  source_id: string;
  title: string;
  summary: string;
  priority: HumanInboxPriority;
  risk_level: string;
  status: string;
  action_state: "pending" | "resolved" | string;
  created_at?: string | null;
  updated_at?: string | null;
  target: {
    type?: string | null;
    id?: string | null;
  };
  route: string;
  bff_detail_path: string;
  allowedActions: Record<string, unknown>;
  approval_decision_id?: string;
  intervention_id?: string;
  decision_context?: Record<string, unknown>;
  remediation_context?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface HumanInboxAggregate {
  data: HumanInboxItem[];
  items: HumanInboxItem[];
  summary: {
    total_items: number;
    returned_items: number;
    pending_items: number;
    approval_count: number;
    intervention_count: number;
    critical_count: number;
    high_count: number;
  };
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    surfaces?: Record<string, unknown>;
    [key: string]: unknown;
  };
}

// ---------- Mode helpers ----------

export type ManagementMode = "mock" | "hybrid" | "real";

const truthy = (v: unknown): boolean =>
  ["1", "true", "yes", "on"].includes(String(v ?? "").trim().toLowerCase());

/** Detect the management read mode from env.
 *  - `mock`  : configured mode is mock (default; also used by tests).
 *  - `real`  : configured mode is live AND VITE_BFF_FALLBACK=strict.
 *  - `hybrid`: configured mode is live with default `auto` fallback. */
export function detectManagementMode(): ManagementMode {
  const env = readBffEnv();
  if (env.MODE === "test" || env.NODE_ENV === "test") return "mock";
  if (env.VITE_BFF_MODE !== "live") return "mock";
  return env.VITE_BFF_FALLBACK === "strict" ? "real" : "hybrid";
}

/** True if the runtime is currently allowed to silently mock on transport failure.
 *  In `real` mode this returns false (the transport will throw instead). */
export function isHybridFallbackEnabled(): boolean {
  return detectManagementMode() === "hybrid";
}

/** True if the runtime configuration forbids silent mock fallback.
 *  Use this from UI banners to label the "live, no-fallback" mode. */
export function isStrictRealMode(): boolean {
  return detectManagementMode() === "real";
}

// ---------- Detail helper ----------

/** Build a `(id) => Promise<T | undefined>` reader that prefers live BFF and
 *  falls back to mock per the configured fallback mode. Mock returns
 *  `undefined` when the id is unknown; live BFF returns 404 → BffError
 *  (propagated to caller). */
function liveOrMockDetail<T>(
  pathFor: (id: string) => string,
  loader: (id: string) => Promise<T | undefined>,
): (id: string) => Promise<T | undefined> {
  return (id) => {
    const mockFn = async (): Promise<T | undefined> => loader(id);
    return withLiveOrMock<T | undefined>({ method: "GET", path: pathFor(id) }, mockFn);
  };
}

function asObject(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function emptyOodaPacketList(): ListEnvelope<OodaLoopPacket> {
  return {
    items: [],
    cursor: {},
    pageSize: 0,
    totalCountExact: true,
    estimatedTotal: 0,
    meta: {
      surfaces: {
        ooda_packets: {
          status: "unavailable",
          source: "mock",
          reason: "OODA packets are served only by the Pantheon OODA read surface.",
        },
      },
    },
  };
}

function emptyPersonaFleetAggregate(): PersonaFleetAggregate {
  const items = seed.personas.map((persona) => ({
    id: persona.id,
    persona_id: persona.id,
    persona,
    health: {
      status: "degraded",
      severity: "medium",
      score: 0,
      reasons: ["mock_persona_fleet_unavailable"],
      latest_telemetry_at: null,
      active_incident_count: 0,
    },
    bindings: [],
    capitalPools: [],
    capital_pools: [],
    runtimeBindings: [],
    runtime_bindings: [],
    telemetrySummary: {
      latest: null,
      runtime_count: 0,
      covered_runtime_count: 0,
      summaries: [],
    },
    telemetry_summary: {
      latest: null,
      runtime_count: 0,
      covered_runtime_count: 0,
      summaries: [],
    },
    training: {
      session_count: 0,
      active_session_count: 0,
      completed_session_count: 0,
      latest_session: null,
    },
    evolution: {
      decision_count: 0,
      pending_decision_count: 0,
      latest_decision: null,
      decisions: [],
    },
    allowedActions: {},
    product_lifecycle_state: "watchlist",
    productLifecycleState: "watchlist",
    competition_track: "watchlist_incumbent",
    competitionTrack: "watchlist_incumbent",
    capital_scope: "none",
    capitalScope: "none",
    review_status: "none",
    reviewStatus: "none",
    required_human_review: null,
    requiredHumanReview: null,
    row_action: {
      action_id: "view_persona",
      actionId: "view_persona",
      label: "查看 persona",
      href: `/personas/${persona.id}`,
      requires_human_review: false,
      requiresHumanReview: false,
      startup_wizard_visible: false,
      startupWizardVisible: false,
      legacy_startup_wizard_hidden: true,
      legacyStartupWizardHidden: true,
    },
    rowAction: {
      action_id: "view_persona",
      actionId: "view_persona",
      label: "查看 persona",
      href: `/personas/${persona.id}`,
      requires_human_review: false,
      requiresHumanReview: false,
      startup_wizard_visible: false,
      startupWizardVisible: false,
      legacy_startup_wizard_hidden: true,
      legacyStartupWizardHidden: true,
    },
  })) as PersonaFleetItem[];
  return {
    data: items,
    items,
    summary: {
      total_personas: items.length,
      returned_personas: items.length,
      critical_personas: 0,
      degraded_personas: items.length,
      healthy_personas: 0,
      bound_personas: 0,
      runtime_bound_personas: 0,
      competition_tracks: {
        paper_challenger: 0,
        canary_challenger: 0,
        live_incumbent: 0,
        watchlist_incumbent: items.length,
        risk_off_excluded: 0,
      },
      capital_scopes: {
        none: items.length,
        paper: 0,
        canary: 0,
        live: 0,
        risk_off: 0,
      },
      review_states: {
        none: items.length,
        promotion_pending: 0,
        live_pending: 0,
        quarterly_pending: 0,
        incident_resume_pending: 0,
        rejected: 0,
      },
      competition_default: "unified_paper_canary_live_cohort",
    },
    page_info: {
      next_page_token: null,
      total: items.length,
      page_size: items.length,
    },
    meta: {
      surfaces: {
        persona_fleet: {
          status: "unavailable",
          source: "mock",
          reason: "Persona Fleet aggregate is served by the Pantheon BFF management aggregate.",
        },
      },
    },
  };
}

function emptyHumanInboxAggregate(): HumanInboxAggregate {
  return {
    data: [],
    items: [],
    summary: {
      total_items: 0,
      returned_items: 0,
      pending_items: 0,
      approval_count: 0,
      intervention_count: 0,
      critical_count: 0,
      high_count: 0,
    },
    page_info: {
      next_page_token: null,
      total: 0,
      page_size: 0,
    },
    meta: {
      surfaces: {
        human_inbox: {
          status: "unavailable",
          source: "mock",
          reason: "Human Inbox aggregate is served only by the Pantheon BFF management aggregate.",
        },
      },
    },
  };
}

function emptyManagementTradingPulseAggregate(): ManagementTradingPulseResponse {
  const summary = {
    runtimeCount: 0,
    runtime_count: 0,
    telemetryCoverageCount: 0,
    telemetry_coverage_count: 0,
    byStatus: {},
    by_status: {},
    byStage: {},
    by_stage: {},
    totalPnl: null,
    total_pnl: null,
    worstDrawdown: null,
    worst_drawdown: null,
    averageFillRate: null,
    average_fill_rate: null,
    worstSlippageBps: null,
    worst_slippage_bps: null,
    totalTrades: 0,
    total_trades: 0,
    baselineComparisonCount: 0,
    baseline_comparison_count: 0,
    baselineBreachedCount: 0,
    baseline_breached_count: 0,
    baselineWatchCount: 0,
    baseline_watch_count: 0,
    byBaselineStatus: {},
    by_baseline_status: {},
  };
  const data = {
    id: "management-trading-pulse",
    summary,
    cards: [],
    rankings: [],
    runtimeRows: [],
    runtime_rows: [],
    baselineComparisons: [],
    baseline_comparisons: [],
  };
  return {
    data,
    items: [],
    cards: [],
    rankings: [],
    runtimeRows: [],
    runtime_rows: [],
    baselineComparisons: [],
    baseline_comparisons: [],
    summary,
    page_info: {
      next_page_token: null,
      total: 0,
      page_size: 0,
    },
    meta: {
      surfaces: {
        management_trading_pulse: {
          status: "unavailable",
          source: "mock",
          reason: "Trading Pulse aggregate is served by the Pantheon BFF management aggregate.",
        },
        baseline_comparison: {
          status: "unavailable",
          source: "mock",
          reason: "Trading Pulse baseline comparison is served by the Pantheon BFF management aggregate.",
        },
      },
    },
  };
}

function emptyManagementTradingPulseRankingsAggregate(
  limit = 20,
): ManagementTradingPulseRankingsResponse {
  return {
    data: [],
    items: [],
    rankings: [],
    rankingBlocks: [],
    ranking_blocks: [],
    summary: {
      runtimeCount: 0,
      runtime_count: 0,
      rankingBlockCount: 0,
      ranking_block_count: 0,
      rankedItemCount: 0,
      ranked_item_count: 0,
      criteria: [],
      limit,
      topRuntimeId: null,
      top_runtime_id: null,
    },
    page_info: {
      next_page_token: null,
      total: 0,
      page_size: 0,
    },
    meta: {
      surfaces: {
        management_trading_pulse_rankings: {
          status: "unavailable",
          source: "mock",
          reason: "Trading Pulse rankings are served by the Pantheon BFF management aggregate.",
        },
      },
      composition_sources: [],
    },
  };
}

function emptyManagementEvidenceAggregate(): ManagementEvidenceResponse {
  return {
    data: [],
    items: [],
    summary: {
      totalEvidence: 0,
      total_evidence: 0,
      returnedEvidence: 0,
      returned_evidence: 0,
      visibleEvidence: 0,
      visible_evidence: 0,
      redactedEvidence: 0,
      redacted_evidence: 0,
      verifiedEvidence: 0,
      verified_evidence: 0,
      bySourceType: {},
      by_source_type: {},
      byLinkType: {},
      by_link_type: {},
      byCredibilityTier: {},
      by_credibility_tier: {},
    },
    facets: {
      sourceTypes: {},
      source_types: {},
      linkTypes: {},
      link_types: {},
      credibilityTiers: {},
      credibility_tiers: {},
    },
    page_info: {
      next_page_token: null,
      total: 0,
      page_size: 0,
    },
    pagination: {
      next_page_token: null,
      has_more: false,
      page_size: 0,
    },
    meta: {
      surfaces: {
        management_evidence: {
          status: "unavailable",
          source: "mock",
          reason: "Evidence Explorer aggregate is served by the Pantheon BFF management aggregate.",
        },
      },
      redacted_evidence_count: 0,
    },
  };
}

function emptyManagementReadinessAggregate(id: string): ManagementReadinessResponse {
  const surfaceKey = `management_readiness_${id.replace(/-/g, "_")}`;
  return {
    data: {
      id,
      readinessId: id,
      readiness_id: id,
      title: id,
      readinessStatus: "unknown",
      readiness_status: "unknown",
      canProceed: false,
      can_proceed: false,
      blockingReasons: ["mock_unavailable"],
      blocking_reasons: ["mock_unavailable"],
      checks: [],
      evidenceRefs: [],
      evidence_refs: [],
      links: {},
      details: {},
    },
    summary: {
      readinessStatus: "unknown",
      readiness_status: "unknown",
      canProceed: false,
      can_proceed: false,
      checkCount: 0,
      check_count: 0,
      passedCheckCount: 0,
      passed_check_count: 0,
      blockingReasonCount: 1,
      blocking_reason_count: 1,
      blockingReasons: ["mock_unavailable"],
      blocking_reasons: ["mock_unavailable"],
      byStatus: {},
      by_status: {},
    },
    checks: [],
    items: [],
    evidence_refs: [],
    meta: {
      surfaces: {
        [surfaceKey]: {
          status: "unavailable",
          source: "mock",
          reason: "Management readiness aggregates are served only by the Pantheon BFF.",
        },
      },
    },
  };
}

function emptyManagementEvolutionJournalAggregate(): ManagementEvolutionJournalResponse {
  return {
    data: [],
    items: [],
    summary: {
      total_items: 0,
      returned_items: 0,
      decision_count: 0,
      mutation_review_count: 0,
      postmortem_count: 0,
      rollback_count: 0,
      freeze_order_count: 0,
      pending_review_count: 0,
      active_freeze_count: 0,
      completed_rollback_count: 0,
      latest_at: null,
      byType: {},
      by_type: {},
      byStatus: {},
      by_status: {},
      byRiskLevel: {},
      by_risk_level: {},
    },
    page_info: {
      next_page_token: null,
      total: 0,
      page_size: 0,
    },
    meta: {
      surfaces: {
        management_evolution_journal: {
          status: "unavailable",
          source: "mock",
          reason: "Evolution Journal aggregate is served by the Pantheon BFF management aggregate.",
        },
      },
      composition_sources: [
        "evolution_decisions",
        "postmortems",
        "mutation_review",
        "rollbacks",
        "freeze_orders",
      ],
    },
  };
}

function emptyManagementPersonaIntentAggregate(): ManagementPersonaIntentResponse {
  return {
    data: [],
    items: [],
    summary: {
      total_items: 0,
      returned_items: 0,
      persona_trace_count: 0,
      trainer_session_count: 0,
      agora_session_count: 0,
      redacted_item_count: 0,
      persona_count: 0,
      persona_ids: [],
      latest_at: null,
      bySourceType: {},
      by_source_type: {},
      byStatus: {},
      by_status: {},
      byIntent: {},
      by_intent: {},
    },
    page_info: {
      next_page_token: null,
      total: 0,
      page_size: 0,
    },
    meta: {
      surfaces: {
        management_persona_intent: {
          status: "unavailable",
          source: "mock",
          reason: "Persona Intent aggregate is served by the Pantheon BFF management aggregate.",
        },
      },
      composition_sources: [
        "persona_traces",
        "teaching_sessions",
        "agora_sessions",
      ],
      redacted_item_count: 0,
    },
  };
}

function adaptOodaPacketDetail(body: unknown): OodaPacketDetail | undefined {
  const envelope = asObject(body);
  const rawPacket = asObject(strictDataFrom(body) ?? body);
  const id = String(rawPacket.packet_id ?? rawPacket.id ?? "").trim();
  if (!id) return undefined;
  const packet = {
    ...rawPacket,
    packet_id: id,
  } as OodaLoopPacket;
  const meta = asObject(envelope.meta) as OodaPacketMeta;
  return {
    packet,
    meta: Object.keys(meta).length > 0 ? meta : undefined,
  };
}

function adaptPersonaFleetAggregate(body: unknown): PersonaFleetAggregate {
  const envelope = asObject(body);
  const rawItems = Array.isArray(envelope.items)
    ? envelope.items
    : Array.isArray(envelope.data)
      ? envelope.data
      : [];
  const items = rawItems.filter((item) => item && typeof item === "object") as PersonaFleetItem[];
  const summary = asObject(envelope.summary);
  const pageInfo = asObject(envelope.page_info);
  const meta = asObject(envelope.meta);
  return {
    data: items,
    items,
    summary: {
      total_personas: Number(summary.total_personas ?? items.length),
      returned_personas: Number(summary.returned_personas ?? items.length),
      critical_personas: Number(summary.critical_personas ?? 0),
      degraded_personas: Number(summary.degraded_personas ?? 0),
      healthy_personas: Number(summary.healthy_personas ?? 0),
      bound_personas: Number(summary.bound_personas ?? 0),
      runtime_bound_personas: Number(summary.runtime_bound_personas ?? 0),
    },
    page_info: {
      next_page_token: typeof pageInfo.next_page_token === "string" ? pageInfo.next_page_token : null,
      total: Number(pageInfo.total ?? items.length),
      page_size: Number(pageInfo.page_size ?? items.length),
    },
    meta,
  };
}

function adaptPaperPersonaLaunchResponse(body: unknown): PaperPersonaLaunchResponse | undefined {
  const envelope = asObject(body);
  const rawLaunch = asObject(envelope.data ?? envelope.launch);
  const launchId = String(rawLaunch.launch_id ?? "").trim();
  if (!launchId) return undefined;
  const launch = rawLaunch as unknown as PaperPersonaLaunch;
  return {
    data: launch,
    launch,
    meta: asObject(envelope.meta) as PaperPersonaLaunchResponse["meta"],
  };
}

function adaptPersonaReadinessResponse(body: unknown): PersonaReadinessResponse | undefined {
  const envelope = asObject(body);
  const rawReadiness = asObject(envelope.data ?? envelope.readinessProjection);
  const personaId = String(rawReadiness.persona_id ?? rawReadiness.personaId ?? "").trim();
  if (!personaId) return undefined;
  const readiness = rawReadiness as unknown as PersonaFleetReadinessProjection;
  const rawLaunch = envelope.latest_launch === null ? null : asObject(envelope.latest_launch);
  return {
    data: readiness,
    readinessProjection: readiness,
    latest_launch: rawLaunch && Object.keys(rawLaunch).length > 0
      ? rawLaunch as unknown as PaperPersonaLaunch
      : null,
    meta: asObject(envelope.meta) as PersonaReadinessResponse["meta"],
  };
}

function adaptHumanInboxAggregate(body: unknown): HumanInboxAggregate {
  const envelope = asObject(body);
  const rawItems = Array.isArray(envelope.items)
    ? envelope.items
    : Array.isArray(envelope.data)
      ? envelope.data
      : [];
  const items = rawItems.filter((item) => item && typeof item === "object") as HumanInboxItem[];
  const summary = asObject(envelope.summary);
  const pageInfo = asObject(envelope.page_info);
  const meta = asObject(envelope.meta);
  return {
    data: items,
    items,
    summary: {
      total_items: Number(summary.total_items ?? items.length),
      returned_items: Number(summary.returned_items ?? items.length),
      pending_items: Number(summary.pending_items ?? 0),
      approval_count: Number(summary.approval_count ?? 0),
      intervention_count: Number(summary.intervention_count ?? 0),
      critical_count: Number(summary.critical_count ?? 0),
      high_count: Number(summary.high_count ?? 0),
    },
    page_info: {
      next_page_token: typeof pageInfo.next_page_token === "string" ? pageInfo.next_page_token : null,
      total: Number(pageInfo.total ?? items.length),
      page_size: Number(pageInfo.page_size ?? items.length),
    },
    meta,
  };
}

function adaptManagementTradingPulseAggregate(body: unknown): ManagementTradingPulseResponse {
  const envelope = asObject(body);
  const dataEnvelope = asObject(envelope.data);
  const rawCards = Array.isArray(envelope.cards)
    ? envelope.cards
    : Array.isArray(envelope.items)
      ? envelope.items
      : Array.isArray(dataEnvelope.cards)
        ? dataEnvelope.cards
        : [];
  const rawRankings = Array.isArray(envelope.rankings)
    ? envelope.rankings
    : Array.isArray(dataEnvelope.rankings)
      ? dataEnvelope.rankings
      : [];
  const rawRuntimeRows = Array.isArray(envelope.runtimeRows)
    ? envelope.runtimeRows
    : Array.isArray(envelope.runtime_rows)
      ? envelope.runtime_rows
      : Array.isArray(dataEnvelope.runtimeRows)
        ? dataEnvelope.runtimeRows
        : Array.isArray(dataEnvelope.runtime_rows)
          ? dataEnvelope.runtime_rows
          : [];
  const rawBaselineComparisons = Array.isArray(envelope.baselineComparisons)
    ? envelope.baselineComparisons
    : Array.isArray(envelope.baseline_comparisons)
      ? envelope.baseline_comparisons
      : Array.isArray(dataEnvelope.baselineComparisons)
        ? dataEnvelope.baselineComparisons
        : Array.isArray(dataEnvelope.baseline_comparisons)
          ? dataEnvelope.baseline_comparisons
          : [];
  const cards = rawCards.filter((item) => item && typeof item === "object") as ManagementTradingPulseResponse["cards"];
  const rankings = rawRankings.filter((item) => item && typeof item === "object") as ManagementTradingPulseResponse["rankings"];
  const runtimeRows = rawRuntimeRows.filter((item) => item && typeof item === "object") as ManagementTradingPulseResponse["runtimeRows"];
  const baselineComparisons = rawBaselineComparisons.filter((item) => item && typeof item === "object") as ManagementTradingPulseResponse["baselineComparisons"];
  const summary = asObject(envelope.summary ?? dataEnvelope.summary) as ManagementTradingPulseResponse["summary"];
  const pageInfo = asObject(envelope.page_info);
  const meta = asObject(envelope.meta) as ManagementTradingPulseResponse["meta"];
  const data = {
    id: typeof dataEnvelope.id === "string" ? dataEnvelope.id : "management-trading-pulse",
    summary,
    cards,
    rankings,
    runtimeRows,
    runtime_rows: runtimeRows,
    baselineComparisons,
    baseline_comparisons: baselineComparisons,
  };
  return {
    data,
    items: cards,
    cards,
    rankings,
    runtimeRows,
    runtime_rows: runtimeRows,
    baselineComparisons,
    baseline_comparisons: baselineComparisons,
    summary,
    page_info: {
      next_page_token: typeof pageInfo.next_page_token === "string" ? pageInfo.next_page_token : null,
      total: Number(pageInfo.total ?? cards.length),
      page_size: Number(pageInfo.page_size ?? cards.length),
    },
    meta,
  };
}

function adaptManagementTradingPulseRankingsAggregate(
  body: unknown,
): ManagementTradingPulseRankingsResponse {
  const envelope = asObject(body);
  const rawBlocks = Array.isArray(envelope.rankingBlocks)
    ? envelope.rankingBlocks
    : Array.isArray(envelope.ranking_blocks)
      ? envelope.ranking_blocks
      : Array.isArray(envelope.rankings)
        ? envelope.rankings
        : Array.isArray(envelope.items)
          ? envelope.items
          : Array.isArray(envelope.data)
            ? envelope.data
            : [];
  const rankingBlocks = rawBlocks.filter((item) => item && typeof item === "object") as ManagementTradingPulseRankingsResponse["rankingBlocks"];
  const summary = asObject(envelope.summary);
  const pageInfo = asObject(envelope.page_info);
  const meta = asObject(envelope.meta) as ManagementTradingPulseRankingsResponse["meta"];
  const criteria = Array.isArray(summary.criteria)
    ? summary.criteria.map((item) => String(item))
    : rankingBlocks.map((block) => String(block.metric || "")).filter(Boolean);
  return {
    data: rankingBlocks,
    items: rankingBlocks,
    rankings: rankingBlocks,
    rankingBlocks,
    ranking_blocks: rankingBlocks,
    summary: {
      runtimeCount: Number(summary.runtimeCount ?? summary.runtime_count ?? 0),
      runtime_count: Number(summary.runtime_count ?? summary.runtimeCount ?? 0),
      rankingBlockCount: Number(summary.rankingBlockCount ?? summary.ranking_block_count ?? rankingBlocks.length),
      ranking_block_count: Number(summary.ranking_block_count ?? summary.rankingBlockCount ?? rankingBlocks.length),
      rankedItemCount: Number(summary.rankedItemCount ?? summary.ranked_item_count ?? 0),
      ranked_item_count: Number(summary.ranked_item_count ?? summary.rankedItemCount ?? 0),
      criteria,
      limit: Number(summary.limit ?? 20),
      topRuntimeId: typeof summary.topRuntimeId === "string" ? summary.topRuntimeId : null,
      top_runtime_id: typeof summary.top_runtime_id === "string" ? summary.top_runtime_id : null,
    },
    page_info: {
      next_page_token: typeof pageInfo.next_page_token === "string" ? pageInfo.next_page_token : null,
      total: Number(pageInfo.total ?? rankingBlocks.length),
      page_size: Number(pageInfo.page_size ?? rankingBlocks.length),
    },
    meta,
  };
}

function adaptManagementEvidenceAggregate(body: unknown): ManagementEvidenceResponse {
  const envelope = asObject(body);
  const rawItems = Array.isArray(envelope.items)
    ? envelope.items
    : Array.isArray(envelope.data)
      ? envelope.data
      : [];
  const items = rawItems.filter((item) => item && typeof item === "object") as ManagementEvidenceItem[];
  const summary = asObject(envelope.summary);
  const facets = asObject(envelope.facets);
  const pageInfo = asObject(envelope.page_info);
  const pagination = asObject(envelope.pagination);
  const meta = asObject(envelope.meta);
  return {
    data: items,
    items,
    summary: {
      totalEvidence: Number(summary.totalEvidence ?? summary.total_evidence ?? items.length),
      total_evidence: Number(summary.total_evidence ?? summary.totalEvidence ?? items.length),
      returnedEvidence: Number(summary.returnedEvidence ?? summary.returned_evidence ?? items.length),
      returned_evidence: Number(summary.returned_evidence ?? summary.returnedEvidence ?? items.length),
      visibleEvidence: Number(summary.visibleEvidence ?? summary.visible_evidence ?? items.length),
      visible_evidence: Number(summary.visible_evidence ?? summary.visibleEvidence ?? items.length),
      redactedEvidence: Number(summary.redactedEvidence ?? summary.redacted_evidence ?? 0),
      redacted_evidence: Number(summary.redacted_evidence ?? summary.redactedEvidence ?? 0),
      verifiedEvidence: Number(summary.verifiedEvidence ?? summary.verified_evidence ?? 0),
      verified_evidence: Number(summary.verified_evidence ?? summary.verifiedEvidence ?? 0),
      bySourceType: asObject(summary.bySourceType ?? summary.by_source_type) as Record<string, number>,
      by_source_type: asObject(summary.by_source_type ?? summary.bySourceType) as Record<string, number>,
      byLinkType: asObject(summary.byLinkType ?? summary.by_link_type) as Record<string, number>,
      by_link_type: asObject(summary.by_link_type ?? summary.byLinkType) as Record<string, number>,
      byCredibilityTier: asObject(summary.byCredibilityTier ?? summary.by_credibility_tier) as Record<string, number>,
      by_credibility_tier: asObject(summary.by_credibility_tier ?? summary.byCredibilityTier) as Record<string, number>,
    },
    facets: facets as ManagementEvidenceResponse["facets"],
    page_info: {
      next_page_token: typeof pageInfo.next_page_token === "string" ? pageInfo.next_page_token : null,
      total: Number(pageInfo.total ?? items.length),
      page_size: Number(pageInfo.page_size ?? items.length),
    },
    pagination: pagination as ManagementEvidenceResponse["pagination"],
    meta: meta as ManagementEvidenceResponse["meta"],
  };
}

function adaptManagementReadinessAggregate(body: unknown, fallbackId: string): ManagementReadinessResponse {
  const envelope = asObject(body);
  const rawData = asObject(envelope.data);
  const rawChecks = Array.isArray(envelope.checks)
    ? envelope.checks
    : Array.isArray(rawData.checks)
      ? rawData.checks
      : [];
  const checks = rawChecks.filter((item) => item && typeof item === "object") as ManagementReadinessResponse["checks"];
  const rawEvidenceRefs = Array.isArray(envelope.evidence_refs)
    ? envelope.evidence_refs
    : Array.isArray(rawData.evidence_refs)
      ? rawData.evidence_refs
      : Array.isArray(rawData.evidenceRefs)
        ? rawData.evidenceRefs
        : [];
  const evidenceRefs = rawEvidenceRefs.filter((item) => item && typeof item === "object") as ManagementReadinessResponse["evidence_refs"];
  const summary = asObject(envelope.summary);
  const readinessStatus = String(
    summary.readinessStatus ?? summary.readiness_status ?? rawData.readinessStatus ?? rawData.readiness_status ?? "unknown",
  );
  const canProceed = Boolean(summary.canProceed ?? summary.can_proceed ?? rawData.canProceed ?? rawData.can_proceed);
  const blockingReasons = Array.isArray(summary.blockingReasons)
    ? summary.blockingReasons.map(String)
    : Array.isArray(summary.blocking_reasons)
      ? summary.blocking_reasons.map(String)
      : [];
  const data = {
    ...rawData,
    id: String(rawData.id ?? fallbackId),
    readinessId: String(rawData.readinessId ?? rawData.readiness_id ?? rawData.id ?? fallbackId),
    readiness_id: String(rawData.readiness_id ?? rawData.readinessId ?? rawData.id ?? fallbackId),
    title: String(rawData.title ?? fallbackId),
    readinessStatus,
    readiness_status: readinessStatus,
    canProceed,
    can_proceed: canProceed,
    blockingReasons,
    blocking_reasons: blockingReasons,
    checks,
    evidenceRefs,
    evidence_refs: evidenceRefs,
  } as ManagementReadinessResponse["data"];
  return {
    data,
    summary: {
      readinessStatus,
      readiness_status: readinessStatus,
      canProceed,
      can_proceed: canProceed,
      checkCount: Number(summary.checkCount ?? summary.check_count ?? checks.length),
      check_count: Number(summary.check_count ?? summary.checkCount ?? checks.length),
      passedCheckCount: Number(summary.passedCheckCount ?? summary.passed_check_count ?? 0),
      passed_check_count: Number(summary.passed_check_count ?? summary.passedCheckCount ?? 0),
      blockingReasonCount: Number(summary.blockingReasonCount ?? summary.blocking_reason_count ?? blockingReasons.length),
      blocking_reason_count: Number(summary.blocking_reason_count ?? summary.blockingReasonCount ?? blockingReasons.length),
      blockingReasons,
      blocking_reasons: blockingReasons,
      byStatus: asObject(summary.byStatus ?? summary.by_status) as Record<string, number>,
      by_status: asObject(summary.by_status ?? summary.byStatus) as Record<string, number>,
    },
    checks,
    items: checks,
    evidence_refs: evidenceRefs,
    meta: asObject(envelope.meta) as ManagementReadinessResponse["meta"],
  };
}

function adaptManagementEvolutionJournalAggregate(body: unknown): ManagementEvolutionJournalResponse {
  const envelope = asObject(body);
  const rawItems = Array.isArray(envelope.items)
    ? envelope.items
    : Array.isArray(envelope.data)
      ? envelope.data
      : [];
  const items = rawItems.filter((item) => item && typeof item === "object") as ManagementEvolutionJournalItem[];
  const summary = asObject(envelope.summary);
  const pageInfo = asObject(envelope.page_info);
  const meta = asObject(envelope.meta);
  return {
    data: items,
    items,
    summary: {
      total_items: Number(summary.total_items ?? items.length),
      returned_items: Number(summary.returned_items ?? items.length),
      decision_count: Number(summary.decision_count ?? 0),
      mutation_review_count: Number(summary.mutation_review_count ?? 0),
      postmortem_count: Number(summary.postmortem_count ?? 0),
      rollback_count: Number(summary.rollback_count ?? 0),
      freeze_order_count: Number(summary.freeze_order_count ?? 0),
      pending_review_count: Number(summary.pending_review_count ?? 0),
      active_freeze_count: Number(summary.active_freeze_count ?? 0),
      completed_rollback_count: Number(summary.completed_rollback_count ?? 0),
      latest_at: typeof summary.latest_at === "string" ? summary.latest_at : null,
      byType: asObject(summary.byType ?? summary.by_type) as Record<string, number>,
      by_type: asObject(summary.by_type ?? summary.byType) as Record<string, number>,
      byStatus: asObject(summary.byStatus ?? summary.by_status) as Record<string, number>,
      by_status: asObject(summary.by_status ?? summary.byStatus) as Record<string, number>,
      byRiskLevel: asObject(summary.byRiskLevel ?? summary.by_risk_level) as Record<string, number>,
      by_risk_level: asObject(summary.by_risk_level ?? summary.byRiskLevel) as Record<string, number>,
    },
    page_info: {
      next_page_token: typeof pageInfo.next_page_token === "string" ? pageInfo.next_page_token : null,
      total: Number(pageInfo.total ?? items.length),
      page_size: Number(pageInfo.page_size ?? items.length),
    },
    meta: meta as ManagementEvolutionJournalResponse["meta"],
  };
}

function adaptManagementPersonaIntentAggregate(body: unknown): ManagementPersonaIntentResponse {
  const envelope = asObject(body);
  const rawItems = Array.isArray(envelope.items)
    ? envelope.items
    : Array.isArray(envelope.data)
      ? envelope.data
      : [];
  const items = rawItems.filter((item) => item && typeof item === "object") as ManagementPersonaIntentItem[];
  const summary = asObject(envelope.summary);
  const pageInfo = asObject(envelope.page_info);
  const meta = asObject(envelope.meta);
  return {
    data: items,
    items,
    summary: {
      total_items: Number(summary.total_items ?? items.length),
      returned_items: Number(summary.returned_items ?? items.length),
      persona_trace_count: Number(summary.persona_trace_count ?? 0),
      trainer_session_count: Number(summary.trainer_session_count ?? 0),
      agora_session_count: Number(summary.agora_session_count ?? 0),
      redacted_item_count: Number(summary.redacted_item_count ?? 0),
      persona_count: Number(summary.persona_count ?? 0),
      persona_ids: Array.isArray(summary.persona_ids) ? summary.persona_ids.map(String) : [],
      latest_at: typeof summary.latest_at === "string" ? summary.latest_at : null,
      bySourceType: asObject(summary.bySourceType ?? summary.by_source_type) as Record<string, number>,
      by_source_type: asObject(summary.by_source_type ?? summary.bySourceType) as Record<string, number>,
      byStatus: asObject(summary.byStatus ?? summary.by_status) as Record<string, number>,
      by_status: asObject(summary.by_status ?? summary.byStatus) as Record<string, number>,
      byIntent: asObject(summary.byIntent ?? summary.by_intent) as Record<string, number>,
      by_intent: asObject(summary.by_intent ?? summary.byIntent) as Record<string, number>,
    },
    page_info: {
      next_page_token: typeof pageInfo.next_page_token === "string" ? pageInfo.next_page_token : null,
      total: Number(pageInfo.total ?? items.length),
      page_size: Number(pageInfo.page_size ?? items.length),
    },
    meta: meta as ManagementPersonaIntentResponse["meta"],
  };
}

function managementEvidenceQueryParams(
  query?: ManagementEvidenceQuery,
): Record<string, string | number | undefined> | undefined {
  if (!query) return undefined;
  return {
    ...query,
    verified: typeof query.verified === "boolean" ? String(query.verified) : undefined,
  } as Record<string, string | number | undefined>;
}

export type OodaPacketListQuery = {
  status?: string;
  stage?: string;
  strategy_id?: string;
  runtime_id?: string;
  evolution_program_id?: string;
  page_token?: string;
  page_size?: number;
};

export type PersonaFleetQuery = {
  state?: string;
  health?: string;
  competition_track?: PersonaFleetCompetitionTrack | string;
  capital_scope?: PersonaFleetCapitalScope | string;
  review_status?: PersonaFleetReviewStatus | string;
  page_token?: string;
  page_size?: number;
};

export type HumanInboxQuery = {
  source_type?: HumanInboxSourceType | string;
  status?: string;
  priority?: HumanInboxPriority | string;
  page_token?: string;
  page_size?: number;
};

export type PersonaLeagueEntry = Record<string, unknown> & {
  id?: string;
  persona_id?: string;
  name?: string;
  market_scope?: string[];
  rank?: number;
  league_score?: number;
};

export type ManagementFleetProjection = Record<string, unknown> & {
  persona_fleet?: PersonaLeagueEntry[];
  persona_league?: PersonaLeagueEntry[];
  capital_pools?: Array<Record<string, unknown>>;
  runtime_bindings?: Array<Record<string, unknown>>;
};

export interface EvolutionReviewProjection {
  decision_id: string;
  target_type: string;
  target_id: string;
  target_version: string;
  action_type: string;
  decision_state: string;
  risk_level: string;
  created_at: string;
  approval_decision_id: string | null;
  proposed_changes: {
    summary: string;
    target_stage: string | null;
    downstream_plane: string | null;
    change_details: Array<Record<string, unknown>>;
  };
  risk_assessment: {
    risk_summary: string;
    severity: string | null;
    threshold_triggers: Array<Record<string, unknown>>;
  };
  required_approvals: Array<{
    role: string;
    approved_by: string | null;
    approved_at: string | null;
    status: string;
  }>;
  review_chain: Array<{
    action: string;
    actor_role: string;
    actor_id: string;
    acted_at: string;
    note: string | null;
  }>;
  linked_incident_id: string | null;
  linked_postmortem_id: string | null;
  evidence_refs: Array<Record<string, unknown>>;
  rollback_followthrough: Record<string, unknown> | null;
  allowedActions: {
    canApproveMutation?: boolean;
    canRejectMutation?: boolean;
  };
  meta: {
    snapshot_at?: string;
    surfaces?: {
      mutation_review?: string;
      [key: string]: unknown;
    };
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

function isEvolutionReviewProjection(value: unknown): value is EvolutionReviewProjection {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  return [
    "decision_id",
    "target_type",
    "target_id",
    "target_version",
    "action_type",
    "decision_state",
    "risk_level",
    "created_at",
    "proposed_changes",
    "risk_assessment",
    "required_approvals",
    "review_chain",
    "evidence_refs",
    "allowedActions",
    "meta",
  ].every((field) => record[field] !== undefined && record[field] !== null);
}

function oodaPacketList(
  path: string,
  query?: OodaPacketListQuery,
): Promise<ListEnvelope<OodaLoopPacket>> {
  const queryParams = query as Record<string, string | number | undefined> | undefined;
  return withStrictLiveOrMock<ListEnvelope<OodaLoopPacket>, unknown>(
    { method: "GET", path, query: queryParams },
    async () => emptyOodaPacketList(),
    (data) => normalizeLiveListResponse<OodaLoopPacket>(data, "loopRun"),
  );
}

function oodaPacketDetail(id: string): Promise<OodaPacketDetail | undefined> {
  return withStrictLiveOrMock<OodaPacketDetail | undefined, unknown>(
    { method: "GET", path: paths.oodaPacket(id) },
    async () => undefined,
    adaptOodaPacketDetail,
    strictNotFoundAsUndefined,
  );
}

function personaLeagueList(): Promise<ListEnvelope<PersonaLeagueEntry>> {
  return withStrictLiveOrMock<ListEnvelope<PersonaLeagueEntry>, unknown>(
    { method: "GET", path: paths.personaLeague() },
    async () => ({
      items: [],
      cursor: {},
      pageSize: 0,
      totalCountExact: true,
      estimatedTotal: 0,
      meta: {
        surfaces: {
          persona_league: {
            status: "unavailable",
            source: "mock",
            reason: "Persona League is served by the Pantheon management BFF.",
          },
        },
      },
    }),
    (data) => normalizeLiveListResponse<PersonaLeagueEntry>(data, "personaLeague"),
  );
}

function personaLeagueDetail(id: string): Promise<PersonaLeagueEntry | undefined> {
  return withStrictLiveOrMock<PersonaLeagueEntry | undefined, unknown>(
    { method: "GET", path: paths.personaLeagueEntry(id) },
    async () => undefined,
    (data) => strictDataFrom(data) as PersonaLeagueEntry | undefined,
    strictNotFoundAsUndefined,
  );
}

function managementFleetDetail(): Promise<ManagementFleetProjection | undefined> {
  return withStrictLiveOrMock<ManagementFleetProjection | undefined, unknown>(
    { method: "GET", path: paths.managementFleet() },
    async () => undefined,
    (data) => strictDataFrom(data) as ManagementFleetProjection | undefined,
  );
}

function adaptEvolutionReview(body: unknown): EvolutionReviewProjection | undefined {
  const raw = strictDataFrom(body) ?? body;
  return isEvolutionReviewProjection(raw) ? raw : undefined;
}

function evolutionReviewDetail(decisionId: string): Promise<EvolutionReviewProjection | undefined> {
  return withStrictLiveOrMock<EvolutionReviewProjection | undefined, unknown>(
    { method: "GET", path: paths.evolutionMutationReview(decisionId) },
    async () => undefined,
    adaptEvolutionReview,
    strictNotFoundAsUndefined,
  );
}

function adaptHumanInboxDetail(body: unknown): HumanInboxItem | undefined {
  const raw = strictDataFrom(body) ?? body;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return undefined;
  const item = raw as HumanInboxItem;
  return typeof item.id === "string" && item.id ? item : undefined;
}

function humanInboxDetail(itemId: string): Promise<HumanInboxItem | undefined> {
  return withStrictLiveOrMock<HumanInboxItem | undefined, unknown>(
    { method: "GET", path: paths.managementHumanInboxItem(itemId) },
    async () => undefined,
    adaptHumanInboxDetail,
    strictNotFoundAsUndefined,
  );
}

// ---------- Per-family adapters ----------

const strategies = {
  list:  bffV1Lists.strategies as () => Promise<ListEnvelope<Strategy>>,
  get:   liveOrMockDetail<Strategy>(paths.strategy, async (id) => seed.strategies.find((item) => item.id === id)),
};

const personas = {
  list:  bffV1Lists.personas as () => Promise<ListEnvelope<Persona>>,
  get:   liveOrMockDetail<Persona>(paths.persona, async (id) => seed.personas.find((item) => item.id === id)),
};

const capitalPools = {
  list:  bffV1Lists.capitalPools as () => Promise<ListEnvelope<CapitalPool>>,
  get:   liveOrMockDetail<CapitalPool>(paths.capitalPool, async (id) => seed.capitalPools.find((item) => item.id === id)),
};

const rankingFormulas = {
  list:  bffV1Lists.rankingFormulas as () => Promise<ListEnvelope<RankingFormula>>,
  get:   liveOrMockDetail<RankingFormula>(
    (id) => `${paths.rankingFormulas()}/${encodeURIComponent(id)}`,
    async (id) => seed.rankingFormulas.find((item) => item.id === id),
  ),
};

const rebalances = {
  list:  bffV1Lists.rebalances as () => Promise<ListEnvelope<Rebalance>>,
  get:   liveOrMockDetail<Rebalance>(paths.rebalance, async (id) => seed.rebalances.find((item) => item.id === id)),
};

const deployments = {
  list:  bffV1Lists.deployments as () => Promise<ListEnvelope<Deployment>>,
  get:   liveOrMockDetail<Deployment>(paths.deployment, async (id) => seed.deployments.find((item) => item.id === id)),
};

const evolution = {
  list:  bffV1Lists.evolution as () => Promise<ListEnvelope<EvolutionProgram>>,
  get:   liveOrMockDetail<EvolutionProgram>(paths.evolutionProgram, async (id) => seed.evolutionPrograms.find((item) => item.id === id)),
};

const research = {
  list:  bffV1Lists.research as () => Promise<ListEnvelope<ResearchExperiment>>,
  get:   liveOrMockDetail<ResearchExperiment>(
    (id) => `${paths.researchExperiments()}/${encodeURIComponent(id)}`,
    async (id) => seed.researchExperiments.find((item) => item.id === id),
  ),
};

const artifacts = {
  list:  bffV1Lists.artifacts as () => Promise<ListEnvelope<Artifact>>,
  get:   liveOrMockDetail<Artifact>(paths.artifact, async (id) => seed.artifacts.find((item) => item.id === id)),
};

const tools = {
  list:  bffV1Lists.tools as () => Promise<ListEnvelope<Tool>>,
  get:   liveOrMockDetail<Tool>(
    (id) => `${paths.tools()}/${encodeURIComponent(id)}`,
    async (id) => seed.tools.find((item) => item.id === id),
  ),
};

const mcpServers = {
  list:  bffV1Lists.mcpServers as () => Promise<ListEnvelope<McpServer>>,
  get:   liveOrMockDetail<McpServer>(
    (id) => `${paths.mcpServers()}/${encodeURIComponent(id)}`,
    async (id) => seed.mcpServers.find((item) => item.id === id),
  ),
};

const mcpTools = {
  list:  bffV1Lists.mcpTools as () => Promise<ListEnvelope<McpTool>>,
  get:   liveOrMockDetail<McpTool>(
    (id) => `${paths.mcpTools()}/${encodeURIComponent(id)}`,
    async (id) => seed.mcpTools.find((item) => item.id === id),
  ),
};

const skills = {
  list:  bffV1Lists.skills as () => Promise<ListEnvelope<Skill>>,
  get:   liveOrMockDetail<Skill>(
    (id) => `${paths.skills()}/${encodeURIComponent(id)}`,
    async (id) => seed.skills.find((item) => item.id === id),
  ),
};

const channels = {
  list:  bffV1Lists.channels as () => Promise<ListEnvelope<Channel>>,
  get:   liveOrMockDetail<Channel>(
    (id) => `${paths.channels()}/${encodeURIComponent(id)}`,
    async (id) => seed.channels.find((item) => item.id === id),
  ),
};

const jobs = {
  list:  bffV1Lists.jobs as () => Promise<ListEnvelope<Job>>,
  get:   liveOrMockDetail<Job>(paths.job, async () => undefined),
};

const runtimes = {
  list:  bffV1Lists.runtimes as () => Promise<ListEnvelope<Runtime>>,
  get:   liveOrMockDetail<Runtime>(
    (id) => `${paths.runtimes()}/${encodeURIComponent(id)}`,
    async (id) => seed.runtimes.find((item) => item.id === id),
  ),
};

const alerts = {
  list:  bffV1Lists.alerts as () => Promise<ListEnvelope<Alert>>,
  get:   liveOrMockDetail<Alert>(
    (id) => `${paths.alerts()}/${encodeURIComponent(id)}`,
    async (id) => seed.alerts.find((item) => item.id === id),
  ),
};

const incidents = {
  list:  bffV1Lists.incidents as () => Promise<ListEnvelope<Incident>>,
  get:   liveOrMockDetail<Incident>(paths.incident, async (id) => seed.incidents.find((item) => item.id === id)),
};

const approvals = {
  list:  bffV1Lists.approvals as () => Promise<ListEnvelope<ApprovalRequest>>,
  get:   liveOrMockDetail<ApprovalRequest>(paths.approval, async (id) => seed.approvals.find((item) => item.id === id)),
};

const audit = {
  list:  bffV1Lists.audit as () => Promise<ListEnvelope<AuditEvent>>,
};

const oodaPackets = {
  list: (query?: OodaPacketListQuery): Promise<ListEnvelope<OodaLoopPacket>> =>
    oodaPacketList(paths.oodaPackets(), query),
  get: oodaPacketDetail,
  forStrategy: (id: string, query?: Omit<OodaPacketListQuery, "strategy_id">): Promise<ListEnvelope<OodaLoopPacket>> =>
    oodaPacketList(paths.strategyOodaPackets(id), query),
  forRuntime: (id: string, query?: Omit<OodaPacketListQuery, "runtime_id">): Promise<ListEnvelope<OodaLoopPacket>> =>
    oodaPacketList(paths.runtimeOodaPackets(id), query),
  forEvolutionProgram: (id: string, query?: Omit<OodaPacketListQuery, "evolution_program_id">): Promise<ListEnvelope<OodaLoopPacket>> =>
    oodaPacketList(paths.evolutionProgramOodaPackets(id), query),
};

const personaLeague = {
  list: personaLeagueList,
  get: personaLeagueDetail,
};

const managementFleet = {
  get: managementFleetDetail,
};

const evolutionReviews = {
  get: evolutionReviewDetail,
};

function paperLaunchMutationHeaders(
  options: PaperPersonaLaunchMutationOptions,
): Record<string, string> {
  const idempotencyKey = String(options.idempotencyKey || "").trim();
  if (!idempotencyKey) {
    throw new Error("personaPaperLaunch mutations require idempotencyKey");
  }
  const headers: Record<string, string> = {
    "Idempotency-Key": idempotencyKey,
  };
  if (options.traceId) headers["X-Trace-Id"] = options.traceId;
  if (options.requestId) headers["X-Request-Id"] = options.requestId;
  return headers;
}

async function paperLaunchLiveOnly(): Promise<never> {
  throw new Error("Paper persona launch requires live BFF mode.");
}

const personaPaperLaunch = {
  create: (
    payload: PaperPersonaLaunchRequest,
    options: PaperPersonaLaunchMutationOptions,
  ): Promise<PaperPersonaLaunchResponse> =>
    withStrictLiveOrMock<PaperPersonaLaunchResponse, unknown>(
      {
        method: "POST",
        path: paths.managementPersonaPaperLaunch(),
        body: payload,
        headers: paperLaunchMutationHeaders(options),
      },
      paperLaunchLiveOnly,
      adaptPaperPersonaLaunchResponse,
      (error) => { throw error; },
    ),
  readiness: (personaId: string): Promise<PersonaReadinessResponse | undefined> =>
    withStrictLiveOrMock<PersonaReadinessResponse | undefined, unknown>(
      {
        method: "GET",
        path: paths.managementPersonaReadiness(personaId),
      },
      async () => undefined,
      adaptPersonaReadinessResponse,
      strictNotFoundAsUndefined,
    ),
  retry: (
    personaId: string,
    payload: Partial<PaperPersonaLaunchRequest> = {},
    options: PaperPersonaLaunchMutationOptions,
  ): Promise<PaperPersonaLaunchResponse> =>
    withStrictLiveOrMock<PaperPersonaLaunchResponse, unknown>(
      {
        method: "POST",
        path: paths.managementPersonaSetupRetry(personaId),
        body: payload,
        headers: paperLaunchMutationHeaders(options),
      },
      paperLaunchLiveOnly,
      adaptPaperPersonaLaunchResponse,
      (error) => { throw error; },
    ),
};

const personaFleet = {
  list: (query?: PersonaFleetQuery): Promise<PersonaFleetAggregate> =>
    withStrictLiveOrMock<PersonaFleetAggregate, unknown>(
      {
        method: "GET",
        path: paths.managementPersonaFleet(),
        query: query as Record<string, string | number | undefined> | undefined,
      },
      async () => emptyPersonaFleetAggregate(),
      adaptPersonaFleetAggregate,
    ),
};

const humanInbox = {
  list: (query?: HumanInboxQuery): Promise<HumanInboxAggregate> =>
    withStrictLiveOrMock<HumanInboxAggregate, unknown>(
      {
        method: "GET",
        path: paths.managementHumanInbox(),
        query: query as Record<string, string | number | undefined> | undefined,
      },
      async () => emptyHumanInboxAggregate(),
      adaptHumanInboxAggregate,
    ),
  get: humanInboxDetail,
};

const tradingPulse = {
  list: (): Promise<ManagementTradingPulseResponse> =>
    withStrictLiveOrMock<ManagementTradingPulseResponse, unknown>(
      {
        method: "GET",
        path: paths.managementTradingPulse(),
      },
      async () => emptyManagementTradingPulseAggregate(),
      adaptManagementTradingPulseAggregate,
    ),
  rankings: (query?: ManagementTradingPulseRankingsQuery): Promise<ManagementTradingPulseRankingsResponse> =>
    withStrictLiveOrMock<ManagementTradingPulseRankingsResponse, unknown>(
      {
        method: "GET",
        path: paths.managementTradingPulseRankings(),
        query: query as Record<string, string | number | undefined> | undefined,
      },
      async () => emptyManagementTradingPulseRankingsAggregate(query?.limit),
      adaptManagementTradingPulseRankingsAggregate,
    ),
};

const evidenceExplorer = {
  list: (query?: ManagementEvidenceQuery): Promise<ManagementEvidenceResponse> =>
    withStrictLiveOrMock<ManagementEvidenceResponse, unknown>(
      {
        method: "GET",
        path: paths.managementEvidence(),
        query: managementEvidenceQueryParams(query),
      },
      async () => emptyManagementEvidenceAggregate(),
      adaptManagementEvidenceAggregate,
    ),
};

const readiness = {
  ep5: (): Promise<ManagementReadinessResponse> =>
    withStrictLiveOrMock<ManagementReadinessResponse, unknown>(
      {
        method: "GET",
        path: paths.managementReadinessEp5(),
      },
      async () => emptyManagementReadinessAggregate("ep5"),
      (body) => adaptManagementReadinessAggregate(body, "ep5"),
    ),
  brokerLive: (): Promise<ManagementReadinessResponse> =>
    withStrictLiveOrMock<ManagementReadinessResponse, unknown>(
      {
        method: "GET",
        path: paths.managementReadinessBrokerLive(),
      },
      async () => emptyManagementReadinessAggregate("broker-live"),
      (body) => adaptManagementReadinessAggregate(body, "broker-live"),
    ),
  capitalBindingLive: (): Promise<ManagementReadinessResponse> =>
    withStrictLiveOrMock<ManagementReadinessResponse, unknown>(
      {
        method: "GET",
        path: paths.managementReadinessCapitalBindingLive(),
      },
      async () => emptyManagementReadinessAggregate("capital-binding-live"),
      (body) => adaptManagementReadinessAggregate(body, "capital-binding-live"),
    ),
  bffHa: (): Promise<ManagementReadinessResponse> =>
    withStrictLiveOrMock<ManagementReadinessResponse, unknown>(
      {
        method: "GET",
        path: paths.managementReadinessBffHa(),
      },
      async () => emptyManagementReadinessAggregate("bff-ha"),
      (body) => adaptManagementReadinessAggregate(body, "bff-ha"),
    ),
  strictPublish: (): Promise<ManagementReadinessResponse> =>
    withStrictLiveOrMock<ManagementReadinessResponse, unknown>(
      {
        method: "GET",
        path: paths.managementReadinessStrictPublish(),
      },
      async () => emptyManagementReadinessAggregate("strict-publish"),
      (body) => adaptManagementReadinessAggregate(body, "strict-publish"),
    ),
};

const evolutionJournal = {
  list: (query?: ManagementEvolutionJournalQuery): Promise<ManagementEvolutionJournalResponse> =>
    withStrictLiveOrMock<ManagementEvolutionJournalResponse, unknown>(
      {
        method: "GET",
        path: paths.managementEvolutionJournal(),
        query: query as Record<string, string | number | undefined> | undefined,
      },
      async () => emptyManagementEvolutionJournalAggregate(),
      adaptManagementEvolutionJournalAggregate,
    ),
};

const personaIntent = {
  list: (query?: ManagementPersonaIntentQuery): Promise<ManagementPersonaIntentResponse> =>
    withStrictLiveOrMock<ManagementPersonaIntentResponse, unknown>(
      {
        method: "GET",
        path: paths.managementPersonaIntent(),
        query: query as Record<string, string | number | undefined> | undefined,
      },
      async () => emptyManagementPersonaIntentAggregate(),
      adaptManagementPersonaIntentAggregate,
    ),
};

// ---------- Public surface ----------

/** Canonical Management Console read surface — list + detail per family,
 *  with hybrid/real/mock mode behaviour governed by environment. */
export const managementClient = {
  strategies,
  personas,
  capitalPools,
  rankingFormulas,
  rebalances,
  deployments,
  evolution,
  research,
  artifacts,
  tools,
  mcpServers,
  mcpTools,
  skills,
  channels,
  jobs,
  runtimes,
  alerts,
  incidents,
  approvals,
  audit,
  oodaPackets,
  personaLeague,
  managementFleet,
  evolutionReviews,
  personaPaperLaunch,
  personaFleet,
  humanInbox,
  tradingPulse,
  evidenceExplorer,
  readiness,
  evolutionJournal,
  personaIntent,
} as const;

export type ManagementFamily = keyof typeof managementClient;

/** All Management Console families that expose a `list()` reader. */
export const MANAGEMENT_FAMILIES: readonly ManagementFamily[] = [
  "strategies", "personas", "capitalPools", "rankingFormulas",
  "rebalances", "deployments", "evolution", "research", "artifacts",
  "tools", "mcpServers", "mcpTools", "skills", "channels",
  "jobs", "runtimes", "alerts", "incidents", "approvals", "audit",
  "oodaPackets", "personaLeague", "personaFleet", "humanInbox",
  "tradingPulse", "evidenceExplorer", "evolutionJournal", "personaIntent",
] as const;

/** Snapshot of the current live-status, useful for UI banners that show
 *  "live failed → using mock" or "live OK". Re-exported here so callers
 *  do not need to depend on `bff-v1` directly. */
export function getLiveStatusSnapshot(): {
  managementMode: ManagementMode;
  effective: "mock" | "live";
  lastError?: string;
  fellBackAt?: number;
} {
  const s = liveStatus.get();
  return {
    managementMode: detectManagementMode(),
    effective: s.effective,
    lastError: s.lastError,
    fellBackAt: s.fellBackAt,
  };
}

export type { ListEnvelope } from "@/lib/bff-v1";
export type {
  ManagementEvidenceItem,
  ManagementEvidenceQuery,
  ManagementEvidenceResponse,
} from "@/lib/bff-v1/management";
