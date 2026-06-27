export interface Strategy { id: string; [k: string]: unknown }
export interface Persona { id: string; [k: string]: unknown }
export interface CapitalPool { id: string; [k: string]: unknown }
export interface RankingFormula { id: string; [k: string]: unknown }
export interface Rebalance { id: string; [k: string]: unknown }
export interface Deployment { id: string; [k: string]: unknown }
export interface EvolutionProgram { id: string; [k: string]: unknown }
export interface ResearchExperiment { id: string; [k: string]: unknown }
export interface Artifact { id: string; [k: string]: unknown }
export interface Tool { id: string; [k: string]: unknown }
export interface McpServer { id: string; [k: string]: unknown }
export interface McpTool { id: string; [k: string]: unknown }
export interface Skill { id: string; [k: string]: unknown }
export interface Channel { id: string; [k: string]: unknown }
export interface Job { id: string; [k: string]: unknown }
export interface Runtime { id: string; [k: string]: unknown }
export interface Alert { id: string; [k: string]: unknown }
export interface Incident { id: string; [k: string]: unknown }
export interface ApprovalRequest { id: string; [k: string]: unknown }
export interface AuditEvent { id: string; [k: string]: unknown }

export interface LoopTruthLabel {
  truth_level: string;
  truth_bucket: string;
  source_type: string;
  rank: number;
  label: string;
  description?: string;
  accepted_as_live: boolean;
}

export interface LoopTruthSource extends LoopTruthLabel {
  status: string;
  source: string;
  refs: string[];
  note?: string | null;
  is_live_truth_level?: boolean;
  operator_note?: string;
  operator_visibility: "live_proof" | "not_live_proof" | string;
}

export interface LoopOperatorTruth {
  truth_level: string;
  truth_bucket?: string;
  source_type?: string;
  source?: string;
  rank?: number;
  status?: string;
  label: string;
  description?: string;
  accepted_as_live: boolean;
  is_live_truth: boolean;
  degraded: boolean;
  degraded_reason?: string | null;
  highest_available_truth_level?: string;
  highest_available_source?: string;
  highest_available_label?: string;
  truth_labels?: Record<string, LoopTruthLabel>;
}

export interface LoopEvidencePacket {
  id: string;
  packet_id: string;
  loop_id: string;
  source: string;
  registry_ref: string;
  current_maturity?: string;
  target_maturity?: string;
  highest_truth_level: string;
  highest_truth_rank: number;
  accepted_live_liveness: boolean;
  can_claim_reconciled: boolean;
  can_claim_proven_live: boolean;
  captured_at?: string | null;
  refs: string[];
  truth_sources: LoopTruthSource[];
  operator_truth: LoopOperatorTruth;
}

export interface LoopHealthEntry {
  id: string;
  loop_id: string;
  name?: string;
  current_maturity?: string;
  target_maturity?: string;
  controller_health?: Record<string, unknown>;
  last_success?: Record<string, unknown> | null;
  last_failure?: Record<string, unknown> | null;
  downstream_actual_state?: Record<string, unknown>;
  evidence_packet: LoopEvidencePacket;
  truth_source?: Record<string, unknown>;
  live_status?: Record<string, unknown>;
  [k: string]: unknown;
}
