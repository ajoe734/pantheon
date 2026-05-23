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

export interface PersonaFleetItem {
  id: string;
  persona_id: string;
  persona: Record<string, unknown>;
  health: {
    status: PersonaFleetHealthStatus;
    severity: string;
    score: number;
    reasons: string[];
    latest_telemetry_at?: string | null;
    active_incident_count: number;
  };
  bindings: Array<Record<string, unknown>>;
  capitalPools: Array<Record<string, unknown>>;
  capital_pools: Array<Record<string, unknown>>;
  runtimeBindings: Array<Record<string, unknown>>;
  runtime_bindings: Array<Record<string, unknown>>;
  telemetrySummary: Record<string, unknown>;
  telemetry_summary: Record<string, unknown>;
  training: Record<string, unknown>;
  evolution: Record<string, unknown>;
  allowedActions: Record<string, unknown>;
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

const evolutionReviews = {
  get: evolutionReviewDetail,
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
  evolutionReviews,
  personaFleet,
  humanInbox,
  evidenceExplorer,
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
  "oodaPackets", "humanInbox", "evidenceExplorer", "evolutionJournal", "personaIntent",
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
