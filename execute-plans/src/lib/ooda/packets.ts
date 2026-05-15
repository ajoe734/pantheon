export type OodaLoopStatus =
  | "open"
  | "observing"
  | "oriented"
  | "decided"
  | "acted"
  | "evolving"
  | "closed"
  | "failed"
  | string;

export type OodaLoopEnvironment =
  | "dev"
  | "paper"
  | "sandbox"
  | "canary"
  | "live"
  | string;

export type OodaStageKey = "observe" | "orient" | "decide" | "act" | "learn";

export interface OodaLoopPacket {
  packet_id: string;
  loop_type?: string;
  status?: OodaLoopStatus;
  stage?: OodaStageKey | string;
  current_stage?: OodaStageKey | string;
  environment?: OodaLoopEnvironment;
  capital_pool_id?: string | null;
  strategy_id?: string | null;
  strategy_ids?: string[];
  runtime_id?: string | null;
  evolution_program_id?: string | null;
  persona_ids?: string[];
  observe?: Record<string, unknown>;
  orient?: Record<string, unknown>;
  decide?: Record<string, unknown>;
  act?: Record<string, unknown>;
  learn?: Record<string, unknown>;
  audit_refs?: unknown[];
  created_at?: string;
  updated_at?: string;
  closed_at?: string | null;
  [key: string]: unknown;
}

export interface OodaPacketSurfaceState {
  status?: string;
  source?: string;
  reason?: string;
  message?: string;
}

export interface OodaPacketMeta {
  snapshot_at?: string;
  total?: number;
  related?: Record<string, unknown>;
  surfaces?: Record<string, OodaPacketSurfaceState>;
  [key: string]: unknown;
}

export interface OodaPacketDetail {
  packet: OodaLoopPacket;
  meta?: OodaPacketMeta;
}

export interface OodaEvidenceRef {
  stage: OodaStageKey | "audit";
  field: string;
  value: string;
}

export type OodaStageDisplayStatus = "complete" | "current" | "missing" | "pending";

export interface OodaStageRow {
  key: OodaStageKey;
  label: string;
  status: OodaStageDisplayStatus;
  evidenceRefs: OodaEvidenceRef[];
}

export const OODA_STAGE_ORDER: readonly OodaStageKey[] = [
  "observe",
  "orient",
  "decide",
  "act",
  "learn",
] as const;

const STAGE_LABELS: Record<OodaStageKey, string> = {
  observe: "Observe",
  orient: "Orient",
  decide: "Decide",
  act: "Act",
  learn: "Learn",
};

const REACHED_RANK_BY_STATUS: Record<string, number> = {
  open: 0,
  observing: 1,
  oriented: 2,
  decided: 3,
  acted: 4,
  evolving: 5,
  closed: 5,
  failed: 5,
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function addRef(refs: OodaEvidenceRef[], stage: OodaEvidenceRef["stage"], field: string, value: unknown): void {
  if (value === undefined || value === null || value === "") return;
  if (Array.isArray(value)) {
    value.forEach((item) => addRef(refs, stage, field, item));
    return;
  }
  if (typeof value === "object") {
    const record = asRecord(value);
    const id =
      record.id ??
      record.ref_id ??
      record.refId ??
      record.object_id ??
      record.objectId ??
      record.value;
    if (id !== undefined && id !== null && String(id).trim()) {
      refs.push({ stage, field, value: String(id).trim() });
    }
    return;
  }
  const text = String(value).trim();
  if (text) refs.push({ stage, field, value: text });
}

export function collectOodaStageEvidence(packet: OodaLoopPacket, stage: OodaStageKey): OodaEvidenceRef[] {
  const refs: OodaEvidenceRef[] = [];
  const bundle = asRecord(packet[stage]);

  if (stage === "observe") {
    [
      "source_refs",
      "telemetry_refs",
      "signal_refs",
      "market_refs",
      "incident_refs",
      "human_feedback_refs",
    ].forEach((field) => addRef(refs, stage, field, bundle[field]));
  }

  if (stage === "orient") {
    [
      "regime_state_ref",
      "universe_selection_ref",
      "signal_inference_refs",
      "allocation_proposal_refs",
      "risk_adjudication_ref",
      "persona_proposal_refs",
      "evidence_bundle_refs",
    ].forEach((field) => addRef(refs, stage, field, bundle[field]));
  }

  if (stage === "decide") {
    [
      "approval_decision_id",
      "deployment_plan_id",
      "evolution_decision_id",
      "sponsor_persona_id",
      "decision_rationale_ref",
      "policy_decision_refs",
    ].forEach((field) => addRef(refs, stage, field, bundle[field]));
  }

  if (stage === "act") {
    [
      "runtime_binding_id",
      "command_receipt_refs",
      "broker_evidence_refs",
      "rollback_refs",
      "safe_mode_refs",
    ].forEach((field) => addRef(refs, stage, field, bundle[field]));
  }

  if (stage === "learn") {
    [
      "telemetry_refs",
      "postmortem_refs",
      "evolution_followthrough_refs",
      "trainer_refs",
      "retrain_refs",
    ].forEach((field) => addRef(refs, stage, field, bundle[field]));

    const observationWindow = asRecord(bundle.observation_window);
    addRef(refs, stage, "observation_window.start_at", observationWindow.start_at);
    addRef(refs, stage, "observation_window.end_at", observationWindow.end_at);
  }

  return refs;
}

export function collectOodaAuditRefs(packet: OodaLoopPacket): OodaEvidenceRef[] {
  const refs: OodaEvidenceRef[] = [];
  addRef(refs, "audit", "audit_refs", packet.audit_refs);
  return refs;
}

export function collectOodaEvidence(packet: OodaLoopPacket): OodaEvidenceRef[] {
  return [
    ...OODA_STAGE_ORDER.flatMap((stage) => collectOodaStageEvidence(packet, stage)),
    ...collectOodaAuditRefs(packet),
  ];
}

function rankForPacket(packet: OodaLoopPacket): number {
  const explicitStage = String(packet.current_stage ?? packet.stage ?? "").trim().toLowerCase();
  const explicitRank = OODA_STAGE_ORDER.findIndex((stage) => stage === explicitStage);
  if (explicitRank >= 0) return explicitRank + 1;
  return REACHED_RANK_BY_STATUS[String(packet.status ?? "open").toLowerCase()] ?? 0;
}

export function deriveOodaStageRows(packet: OodaLoopPacket): OodaStageRow[] {
  const reachedRank = rankForPacket(packet);
  const status = String(packet.status ?? "open").toLowerCase();

  return OODA_STAGE_ORDER.map((stage, index) => {
    const evidenceRefs = collectOodaStageEvidence(packet, stage);
    const stageRank = index + 1;
    let displayStatus: OodaStageDisplayStatus = "pending";

    if (reachedRank >= stageRank) {
      displayStatus = evidenceRefs.length > 0 ? "complete" : "missing";
      if (status !== "closed" && status !== "failed" && reachedRank === stageRank && evidenceRefs.length > 0) {
        displayStatus = "current";
      }
    }

    return {
      key: stage,
      label: STAGE_LABELS[stage],
      status: displayStatus,
      evidenceRefs,
    };
  });
}

export type OodaCapitalSafetyState = "no_side_effects" | "live_asserted" | "non_live_unsafe";

export function oodaCapitalSafetyState(packet: OodaLoopPacket): OodaCapitalSafetyState {
  const environment = String(packet.environment ?? "").toLowerCase();
  const liveSideEffects = asRecord(packet.act).live_capital_side_effects === true;
  if (!liveSideEffects) return "no_side_effects";
  return environment === "live" ? "live_asserted" : "non_live_unsafe";
}

export function oodaPacketDisplayName(packet: OodaLoopPacket): string {
  const strategyId = String(packet.strategy_id ?? "").trim();
  const runtimeId = String(packet.runtime_id ?? asRecord(packet.act).runtime_binding_id ?? "").trim();
  if (strategyId) return strategyId;
  if (runtimeId) return runtimeId;
  return packet.packet_id;
}

export function oodaSourceState(meta: OodaPacketMeta | undefined): OodaPacketSurfaceState {
  const surfaces = meta?.surfaces ?? {};
  return (
    surfaces.ooda_packet_detail ??
    surfaces.ooda_packets ??
    { status: meta ? "ok" : "unknown", source: meta ? "service_store" : "unknown" }
  );
}
