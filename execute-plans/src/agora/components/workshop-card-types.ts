/**
 * Typed payload interfaces for each WorkshopCard card_type.
 * Field-for-field aligned with services/control-plane/specs/agora/v4/workshop_card.schema.json.
 * Do not add fields not present in that schema.
 */

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

export interface PayloadUserStrategyDescription {
  owner_visible_content: string;
  redacted_summary?: string;
  attachment_refs?: string[];
  message_event_id: string;
  created_at: string;
}

export interface ServantInference {
  statement: string;
  confidence: number;
  needs_confirmation: boolean;
}

export interface CausalStep {
  step_id: string;
  premise: string;
  mechanism: string;
  expected_observation: string;
  confidence: number;
  evidence_refs?: EvidenceRef[];
}

export interface PayloadServantReconstruction {
  strategy_title: string;
  causal_chain: CausalStep[];
  explicit_definitions?: string[];
  servant_inferences?: ServantInference[];
  uncertainties?: string[];
  contradictions?: string[];
  proposed_next_actions?: string[];
  patch_proposal_ref?: string;
}

export interface DimensionUpdate {
  dimension: string;
  prior_grade: string;
  current_grade: string;
  gaps?: string[];
  required_actions?: string[];
}

export interface PayloadCompletenessUpdate {
  overall_grade: string;
  dimension_updates: DimensionUpdate[];
  blockers?: string[];
  research_ready: boolean;
  readiness_gates?: string[];
  change_since_previous?: string;
}

export interface PayloadMissingDefinition {
  gap_id: string;
  category: string;
  severity: "low" | "medium" | "high" | "critical";
  missing_definition: string;
  why_it_matters: string;
  downstream_blocked_capabilities?: string[];
  suggested_temporary_assumption?: string;
  answer_options?: string[];
  can_defer?: boolean;
  deferral_consequence?: string;
}

export interface ScoreComponents {
  information_gain?: number;
  downstream_blocking_weight?: number;
  risk_impact?: number;
  research_cost_reduction?: number;
  user_relevance?: number;
  penalties?: number;
}

export interface PayloadNextQuestion {
  question_id: string;
  question: string;
  why_now: string;
  score_total: number;
  score_components?: ScoreComponents;
  answer_options?: string[];
  freeform_allowed?: boolean;
  defer_allowed?: boolean;
  defer_consequence?: string;
  golden_case_ref?: string;
}

export interface ResearchStage {
  stage_id: string;
  stage_type: string;
  purpose: string;
  preferred_backend?: string;
  dependencies?: string[];
}

export interface ResearchBudget {
  compute_tier?: string;
  max_runtime_seconds?: number;
  max_parallel_stages?: number;
}

export interface PayloadResearchPlanProposal {
  plan_id: string;
  objectives: string[];
  data_requirements?: string[];
  stages: ResearchStage[];
  evaluation_criteria?: string;
  budget?: ResearchBudget;
  assumptions?: string[];
  warnings?: string[];
  approval_requirement?: string;
}

export type ResearchExecutionStatus =
  | "queued"
  | "dispatching"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "timed_out";

export interface PayloadResearchProgress {
  run_id: string;
  plan_id: string;
  stage_id: string;
  stage_type: string;
  execution_status: ResearchExecutionStatus;
  progress: number;
  backend: string;
  latest_progress_message?: string;
  warnings?: string[];
  blocking_reasons?: string[];
  started_at?: string;
  updated_at?: string;
}

export interface ResearchMetric {
  category:
    | "performance"
    | "risk"
    | "cost"
    | "capacity"
    | "robustness"
    | "calibration"
    | "data_quality";
  name: string;
  value: number;
  unit?: string;
  gate_result: "pass" | "fail" | "not_applicable" | "not_evaluated";
}

export interface ResearchFinding {
  finding_id: string;
  severity: "info" | "watch" | "warning" | "high" | "critical";
  summary: string;
  detail?: string;
}

export interface PayloadResearchResult {
  run_id: string;
  outcome: "pending" | "pass" | "fail" | "inconclusive";
  metrics?: ResearchMetric[];
  findings?: ResearchFinding[];
  warnings?: string[];
  blocking_reasons?: string[];
  artifact_refs?: string[];
  evidence_refs?: EvidenceRef[];
  gate_impacts?: string[];
  recommended_patch_proposal_refs?: string[];
  backend?: { effective: string; mode: "real" | "fixture" | "stub" };
  data_cutoff?: string;
}

export interface PayloadConsultResult {
  consultation_id: string;
  consultation_type: string;
  participant_persona_refs?: string[];
  status: "pending" | "in_progress" | "completed" | "inconclusive" | "cancelled";
  consensus_summary?: string;
  disagreements?: string[];
  risk_notes?: string[];
  conditions?: string[];
  evidence_refs?: EvidenceRef[];
  freshness?: "fresh" | "stale" | "expired";
}

export interface ChangeSummaryEntry {
  path: string;
  summary: string;
  reason?: string;
}

export interface PredictedEffect {
  metric: string;
  direction: "increase" | "decrease" | "unchanged" | "uncertain";
  estimated_delta?: number;
  confidence: number;
}

export interface PatchValidation {
  path_policy_valid?: boolean;
  base_hash_valid?: boolean;
  strategy_schema_valid?: boolean;
  policy_valid?: boolean;
  conflicts?: string[];
  warnings?: string[];
}

export interface PayloadVersionPatchProposal {
  proposal_id: string;
  base_version: string;
  change_summary?: ChangeSummaryEntry[];
  rationale: string;
  predicted_effects?: PredictedEffect[];
  validation?: PatchValidation;
  status:
    | "draft"
    | "validating"
    | "validated"
    | "invalid"
    | "accepted"
    | "rejected"
    | "superseded";
}

export interface VersionRef {
  workshop_version_id: string;
  strategy_spec_registry_id: string;
  label: string;
}

export interface FieldDiff {
  path: string;
  change_kind: "added" | "removed" | "changed" | "unchanged";
  candidate_version_id: string;
  materiality?: "low" | "medium" | "high" | "critical";
}

export interface MetricDiff {
  metric: string;
  candidate_version_id: string;
  base_value?: number | null;
  candidate_value?: number | null;
  absolute_delta?: number | null;
  evidence_class: "predicted" | "backtested_in_sample" | "backtested_oos" | "paper_observed";
}

export interface RiskDiff {
  candidate_version_id: string;
  risk_domain: string;
  change: "improved" | "worsened" | "unchanged" | "uncertain";
  summary?: string;
}

export interface ReadinessDiff {
  candidate_version_id: string;
  gate: "preliminary_research" | "full_validation" | "trading_room";
  base_state: string;
  candidate_state: string;
}

export interface PayloadVersionCompare {
  base_version: VersionRef;
  candidate_versions: VersionRef[];
  field_diffs: FieldDiff[];
  metric_diffs: MetricDiff[];
  risk_diffs?: RiskDiff[];
  readiness_diffs: ReadinessDiff[];
  recommendation?: {
    recommended_version_id?: string;
    rationale?: string;
    confidence?: number;
    limitations?: string[];
  };
}

export type GateState = "not_assessed" | "blocked" | "conditional" | "ready" | "stale";
export type GateName = "preliminary_research" | "full_validation" | "trading_room";
export type RequirementState = "missing" | "partial" | "satisfied" | "waived" | "stale";

export interface GateRequirement {
  requirement_id: string;
  title: string;
  hardness: "hard" | "soft";
  state: RequirementState;
  summary?: string;
}

export interface ReadinessGateEntry {
  gate: GateName;
  state: GateState;
  requirements: GateRequirement[];
  blocking_requirement_ids?: string[];
  conditional_assumptions?: string[];
}

export interface PayloadReadinessGate {
  gates: [ReadinessGateEntry, ReadinessGateEntry, ReadinessGateEntry];
  hard_blockers?: string[];
  temporary_assumptions?: string[];
  staleness_reasons?: string[];
  highest_ready_gate?: GateName | null;
  assessed_at: string;
  valid_until?: string;
}
