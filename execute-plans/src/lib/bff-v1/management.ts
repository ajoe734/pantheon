import { paths } from "./paths";

export type ManagementSurfaceStatus = "ok" | "degraded" | "unavailable" | string;

export interface ManagementSurfaceRef {
  status: ManagementSurfaceStatus;
  source?: string;
  message?: string;
  note?: string;
  staleness?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ManagementCockpitMeta {
  snapshot_at?: string;
  staleness?: Record<string, unknown>;
  surfaces: {
    management_cockpit: ManagementSurfaceRef;
    operator_home?: ManagementSurfaceRef;
    runtime_health?: ManagementSurfaceRef;
    alerts?: ManagementSurfaceRef;
    human_inbox?: ManagementSurfaceRef;
    trading_pulse?: ManagementSurfaceRef;
    anomalies?: ManagementSurfaceRef;
    [key: string]: ManagementSurfaceRef | undefined;
  };
}

export interface ManagementCockpitSection<T = Record<string, unknown>> {
  items?: T[];
  summary?: Record<string, unknown>;
  cards?: Record<string, unknown>[];
  rankings?: Record<string, unknown>[];
  meta?: {
    snapshot_at?: string;
    surfaces?: Record<string, ManagementSurfaceRef>;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export interface ManagementCockpitData {
  id: "management-cockpit" | string;
  snapshotAt?: string;
  snapshot_at?: string;
  operatorHome: Record<string, unknown>;
  operator_home: Record<string, unknown>;
  runtimeHealth: Record<string, unknown>;
  runtime_health: Record<string, unknown>;
  alerts: ManagementCockpitSection;
  humanInbox: ManagementCockpitSection;
  human_inbox: ManagementCockpitSection;
  tradingPulse: ManagementCockpitSection;
  trading_pulse: ManagementCockpitSection;
  anomalies: ManagementCockpitSection;
  links?: Record<string, string>;
}

export interface ManagementCockpitResponse {
  data: ManagementCockpitData;
  operator_home: Record<string, unknown>;
  runtime_health: Record<string, unknown>;
  alerts: ManagementCockpitSection;
  human_inbox: ManagementCockpitSection;
  trading_pulse: ManagementCockpitSection;
  anomalies: ManagementCockpitSection;
  meta: ManagementCockpitMeta;
}

export interface ManagementBoardPackQuery {
  period?: string;
  state?: string;
  archetype?: string;
  q?: string;
  section_limit?: number;
}

export interface ManagementBoardPackSection {
  id: string;
  section_id: string;
  label: string;
  href: string;
  status: ManagementSurfaceStatus;
  source?: string;
  item_count?: number;
  itemCount?: number;
  returned_item_count?: number;
  returnedItemCount?: number;
  summary?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ManagementBoardPackSummary {
  section_count: number;
  sectionCount: number;
  section_ids: string[];
  sectionIds: string[];
  by_status: Record<string, number>;
  byStatus: Record<string, number>;
  ok_section_count: number;
  okSectionCount: number;
  degraded_section_count: number;
  degradedSectionCount: number;
  unavailable_section_count: number;
  unavailableSectionCount: number;
  period: string;
  section_limit: number;
  sectionLimit: number;
  policy: string;
  basis: string;
  [key: string]: unknown;
}

export interface ManagementBoardPackResponse {
  data: {
    id: "management-board-pack" | string;
    snapshotAt?: string;
    snapshot_at?: string;
    period: string;
    sectionLimit: number;
    section_limit: number;
    sections: ManagementBoardPackSection[];
    summary: ManagementBoardPackSummary;
    portfolioBook?: Record<string, unknown>;
    portfolio_book?: Record<string, unknown>;
    portfolioBookExposure?: ManagementPortfolioBookExposureResponse;
    portfolio_book_exposure?: ManagementPortfolioBookExposureResponse;
    portfolioBookPositions?: ManagementPortfolioBookPositionsResponse;
    portfolio_book_positions?: ManagementPortfolioBookPositionsResponse;
    strategyAllocation?: ManagementStrategyAllocationResponse;
    strategy_allocation?: ManagementStrategyAllocationResponse;
    personaLeague?: {
      league?: ManagementPersonaLeagueResponse;
      movers?: ManagementPersonaLeagueMoversResponse;
      [key: string]: unknown;
    };
    persona_league?: {
      league?: ManagementPersonaLeagueResponse;
      movers?: ManagementPersonaLeagueMoversResponse;
      [key: string]: unknown;
    };
    performanceAttribution?: {
      byPersona?: ManagementPerformanceAttributionResponse;
      by_persona?: ManagementPerformanceAttributionResponse;
      byPool?: ManagementPerformanceAttributionResponse;
      by_pool?: ManagementPerformanceAttributionResponse;
      [key: string]: unknown;
    };
    performance_attribution?: {
      byPersona?: ManagementPerformanceAttributionResponse;
      by_persona?: ManagementPerformanceAttributionResponse;
      byPool?: ManagementPerformanceAttributionResponse;
      by_pool?: ManagementPerformanceAttributionResponse;
      [key: string]: unknown;
    };
    policy?: string;
    [key: string]: unknown;
  };
  items: ManagementBoardPackSection[];
  sections: ManagementBoardPackSection[];
  summary: ManagementBoardPackSummary;
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    surfaces?: Record<string, ManagementSurfaceRef>;
    composition_sources?: string[];
    period?: string;
    section_limit?: number;
    policy?: string;
    [key: string]: unknown;
  };
}

export interface ManagementGovernanceLedgerQuery {
  source_type?: "approval" | "intervention" | "override" | string;
  status?: string;
  q?: string;
  page_token?: string;
  page_size?: number;
}

export type ManagementGovernanceLedgerSourceType =
  | "approval"
  | "intervention"
  | "override"
  | string;

export interface ManagementGovernanceLedgerItem {
  id: string;
  entry_id: string;
  ledgerId: string;
  ledger_id: string;
  sourceType: ManagementGovernanceLedgerSourceType;
  source_type: ManagementGovernanceLedgerSourceType;
  sourceDataset: string;
  source_dataset: string;
  eventType: string;
  event_type: string;
  status?: string | null;
  outcome?: string | null;
  actor?: string | null;
  targetType?: string | null;
  target_type?: string | null;
  targetId?: string | null;
  target_id?: string | null;
  riskLevel?: string | null;
  risk_level?: string | null;
  occurredAt?: string | null;
  occurred_at?: string | null;
  createdAt?: string | null;
  created_at?: string | null;
  title: string;
  summary?: string | null;
  href?: string | null;
  links?: Record<string, string | null | undefined>;
  evidenceRefs?: Record<string, unknown>[];
  evidence_refs?: Record<string, unknown>[];
  auditContext?: Record<string, unknown>;
  audit_context?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ManagementGovernanceLedgerSummary {
  ledgerCount: number;
  ledger_count: number;
  returnedLedgerCount: number;
  returned_ledger_count: number;
  approvalCount: number;
  approval_count: number;
  interventionCount: number;
  intervention_count: number;
  overrideCount: number;
  override_count: number;
  bySourceType: Record<string, number>;
  by_source_type: Record<string, number>;
  byStatus: Record<string, number>;
  by_status: Record<string, number>;
  byEventType: Record<string, number>;
  by_event_type: Record<string, number>;
  latestAt?: string | null;
  latest_at?: string | null;
  policy: string;
  basis: string;
  [key: string]: unknown;
}

export interface ManagementGovernanceLedgerResponse {
  data: {
    id: "management-governance-ledger" | string;
    items: ManagementGovernanceLedgerItem[];
    summary: ManagementGovernanceLedgerSummary;
    policy?: string;
    [key: string]: unknown;
  };
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    staleness?: Record<string, unknown>;
    surfaces?: Record<string, ManagementSurfaceRef>;
    composition_sources?: string[];
    policy?: string;
    filters?: Record<string, unknown>;
    [key: string]: unknown;
  };
}

export interface ManagementHiqBacklogQuery {
  source_type?: "intervention" | "sentinel_finding" | string;
  status?: string;
  kind?: string;
  priority?: "critical" | "high" | "medium" | "low" | "unknown" | string;
  q?: string;
  page_token?: string;
  page_size?: number;
}

export type ManagementHiqBacklogSourceType =
  | "intervention"
  | "sentinel_finding"
  | string;

export interface ManagementHiqBacklogItem {
  id: string;
  backlogId: string;
  backlog_id: string;
  sourceType: ManagementHiqBacklogSourceType;
  source_type: ManagementHiqBacklogSourceType;
  sourceId: string;
  source_id: string;
  humanInboxId?: string | null;
  human_inbox_id?: string | null;
  kind: string;
  status: string;
  actionState: string;
  action_state: string;
  priority: string;
  riskLevel?: string | null;
  risk_level?: string | null;
  severity?: string | null;
  title: string;
  summary?: string | null;
  createdAt?: string | null;
  created_at?: string | null;
  updatedAt?: string | null;
  updated_at?: string | null;
  target?: Record<string, unknown>;
  triggeredBy?: string | null;
  triggered_by?: string | null;
  correlationId?: string | null;
  correlation_id?: string | null;
  sourceRefs?: Record<string, unknown>;
  source_refs?: Record<string, unknown>;
  links?: Record<string, string | null | undefined>;
  allowedActions?: Record<string, unknown>;
  allowed_actions?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ManagementHiqBacklogSummary {
  backlogCount: number;
  backlog_count: number;
  returnedBacklogCount: number;
  returned_backlog_count: number;
  interventionCount: number;
  intervention_count: number;
  sentinelFindingCount: number;
  sentinel_finding_count: number;
  pendingCount: number;
  pending_count: number;
  criticalCount: number;
  critical_count: number;
  highCount: number;
  high_count: number;
  bySourceType: Record<string, number>;
  by_source_type: Record<string, number>;
  byStatus: Record<string, number>;
  by_status: Record<string, number>;
  byKind: Record<string, number>;
  by_kind: Record<string, number>;
  byPriority: Record<string, number>;
  by_priority: Record<string, number>;
  latestAt?: string | null;
  latest_at?: string | null;
  policy: string;
  basis: string;
  [key: string]: unknown;
}

export interface ManagementHiqBacklogResponse {
  data: {
    id: "management-hiq-backlog" | string;
    items: ManagementHiqBacklogItem[];
    summary: ManagementHiqBacklogSummary;
    policy?: string;
    [key: string]: unknown;
  };
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    surfaces?: Record<string, ManagementSurfaceRef>;
    composition_sources?: string[];
    policy?: string;
    filters?: Record<string, unknown>;
    [key: string]: unknown;
  };
}

export interface ManagementInterventionStreamQuery {
  persona_id?: string;
  personaId?: string;
  status?: string;
  kind?: string;
  q?: string;
  window_hours?: number;
  windowHours?: number;
  page_token?: string;
  page_size?: number;
}

export interface ManagementInterventionStreamItem {
  id: string;
  eventId: string;
  event_id: string;
  eventType: string;
  event_type: string;
  eventSource: string;
  event_source: string;
  sourceType: "intervention" | string;
  source_type: "intervention" | string;
  sourceDataset: string;
  source_dataset: string;
  interventionId: string;
  intervention_id: string;
  personaId?: string | null;
  persona_id?: string | null;
  runtimeId?: string | null;
  runtime_id?: string | null;
  strategyId?: string | null;
  strategy_id?: string | null;
  kind: string;
  status: string;
  priority?: string | null;
  riskLevel?: string | null;
  risk_level?: string | null;
  severity?: string | null;
  occurredAt?: string | null;
  occurred_at?: string | null;
  createdAt?: string | null;
  created_at?: string | null;
  updatedAt?: string | null;
  updated_at?: string | null;
  streamSequence?: number;
  stream_sequence?: number;
  actor?: string | null;
  title: string;
  summary?: string | null;
  target?: Record<string, unknown>;
  sourceRefs?: Record<string, unknown>;
  source_refs?: Record<string, unknown>;
  links?: Record<string, string | null | undefined>;
  [key: string]: unknown;
}

export interface ManagementInterventionStreamSummary {
  eventCount: number;
  event_count: number;
  returnedEventCount: number;
  returned_event_count: number;
  interventionCount: number;
  intervention_count: number;
  personaCount: number;
  persona_count: number;
  windowHours: number;
  window_hours: number;
  windowStartAt: string;
  window_start_at: string;
  windowEndAt: string;
  window_end_at: string;
  latestAt?: string | null;
  latest_at?: string | null;
  byPersona: Record<string, number>;
  by_persona: Record<string, number>;
  byStatus: Record<string, number>;
  by_status: Record<string, number>;
  byKind: Record<string, number>;
  by_kind: Record<string, number>;
  byEventSource: Record<string, number>;
  by_event_source: Record<string, number>;
  policy: string;
  basis: string;
  [key: string]: unknown;
}

export interface ManagementInterventionStreamResponse {
  data: {
    id: "management-intervention-stream" | string;
    items: ManagementInterventionStreamItem[];
    summary: ManagementInterventionStreamSummary;
    policy?: string;
    [key: string]: unknown;
  };
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    surfaces?: Record<string, ManagementSurfaceRef>;
    composition_sources?: string[];
    policy?: string;
    filters?: Record<string, unknown>;
    [key: string]: unknown;
  };
}

export interface ManagementTradingPulseSummary {
  runtimeCount: number;
  runtime_count: number;
  telemetryCoverageCount: number;
  telemetry_coverage_count: number;
  byStatus: Record<string, number>;
  by_status: Record<string, number>;
  byStage: Record<string, number>;
  by_stage: Record<string, number>;
  totalPnl?: number | null;
  total_pnl?: number | null;
  worstDrawdown?: number | null;
  worst_drawdown?: number | null;
  averageFillRate?: number | null;
  average_fill_rate?: number | null;
  worstSlippageBps?: number | null;
  worst_slippage_bps?: number | null;
  totalTrades: number;
  total_trades: number;
  baselineComparisonCount?: number;
  baseline_comparison_count?: number;
  baselineBreachedCount?: number;
  baseline_breached_count?: number;
  baselineWatchCount?: number;
  baseline_watch_count?: number;
  byBaselineStatus?: Record<string, number>;
  by_baseline_status?: Record<string, number>;
  [key: string]: unknown;
}

export interface ManagementTradingPulseCard {
  cardId: string;
  card_id: string;
  label: string;
  value?: number | string | boolean | null;
  details?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ManagementTradingPulseRuntimeRow {
  runtime_id?: string;
  runtime_binding_id?: string;
  deployment_stage?: string;
  status?: string;
  telemetry_summary?: Record<string, unknown> | null;
  rollback_summary?: Record<string, unknown> | null;
  baselineComparison?: ManagementTradingPulseBaselineComparison | null;
  baseline_comparison?: ManagementTradingPulseBaselineComparison | null;
  last_updated_at?: string | null;
  [key: string]: unknown;
}

export interface ManagementTradingPulseBaselineComparison {
  runtimeId?: string | null;
  runtime_id?: string | null;
  runtimeBindingId?: string | null;
  runtime_binding_id?: string | null;
  deploymentStage?: string | null;
  deployment_stage?: string | null;
  status: "ok" | "watch" | "breached" | "unavailable" | "unknown" | string;
  paperLiveDrift?: Record<string, unknown>;
  paper_live_drift?: Record<string, unknown>;
  paperBaseline?: Record<string, unknown> | null;
  paper_baseline?: Record<string, unknown> | null;
  observedState?: Record<string, unknown> | null;
  observed_state?: Record<string, unknown> | null;
  driftGroups?: Record<string, unknown>[];
  drift_groups?: Record<string, unknown>[];
  thresholdEvaluation?: Record<string, unknown>;
  threshold_evaluation?: Record<string, unknown>;
  metricCount?: number;
  metric_count?: number;
  breachedMetricCount?: number;
  breached_metric_count?: number;
  watchMetricCount?: number;
  watch_metric_count?: number;
  [key: string]: unknown;
}

export interface ManagementTradingPulseRankingItem {
  runtimeId?: string | null;
  runtime_id?: string | null;
  runtimeBindingId?: string | null;
  runtime_binding_id?: string | null;
  deploymentStage?: string | null;
  deployment_stage?: string | null;
  status?: string | null;
  rank: number;
  pnl?: number | null;
  drawdown?: number | null;
  sharpeRatio?: number | null;
  sharpe_ratio?: number | null;
  fillRate?: number | null;
  fill_rate?: number | null;
  avgSlippageBps?: number | null;
  avg_slippage_bps?: number | null;
  totalTrades?: number | null;
  total_trades?: number | null;
  lastUpdatedAt?: string | null;
  last_updated_at?: string | null;
  baselineComparisonStatus?: string | null;
  baseline_comparison_status?: string | null;
  breachedMetricCount?: number | null;
  breached_metric_count?: number | null;
  rankingBlockId?: string;
  ranking_block_id?: string;
  rankingMetric?: string;
  ranking_metric?: string;
  rankingMetricValue?: number | null;
  ranking_metric_value?: number | null;
  [key: string]: unknown;
}

export interface ManagementTradingPulseData {
  id: "management-trading-pulse" | string;
  summary: ManagementTradingPulseSummary;
  cards: ManagementTradingPulseCard[];
  rankings: ManagementTradingPulseRankingItem[];
  runtimeRows: ManagementTradingPulseRuntimeRow[];
  runtime_rows: ManagementTradingPulseRuntimeRow[];
  baselineComparisons: ManagementTradingPulseBaselineComparison[];
  baseline_comparisons: ManagementTradingPulseBaselineComparison[];
  [key: string]: unknown;
}

export interface ManagementTradingPulseResponse {
  data: ManagementTradingPulseData;
  items: ManagementTradingPulseCard[];
  cards: ManagementTradingPulseCard[];
  rankings: ManagementTradingPulseRankingItem[];
  runtimeRows: ManagementTradingPulseRuntimeRow[];
  runtime_rows: ManagementTradingPulseRuntimeRow[];
  baselineComparisons: ManagementTradingPulseBaselineComparison[];
  baseline_comparisons: ManagementTradingPulseBaselineComparison[];
  summary: ManagementTradingPulseSummary;
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    surfaces: {
      management_trading_pulse: ManagementSurfaceRef;
      runtime_roster?: ManagementSurfaceRef;
      telemetry_summary?: ManagementSurfaceRef;
      paper_live_drift?: ManagementSurfaceRef;
      baseline_comparison?: ManagementSurfaceRef;
      [key: string]: ManagementSurfaceRef | undefined;
    };
    [key: string]: unknown;
  };
}

export interface ManagementTradingPulseRankingsQuery {
  limit?: number;
}

export interface ManagementTradingPulseRankingBlock {
  blockId: string;
  block_id: string;
  label: string;
  metric: string;
  secondaryMetric?: string;
  secondary_metric?: string;
  sortOrder: "asc" | "desc" | string;
  sort_order: "asc" | "desc" | string;
  items: ManagementTradingPulseRankingItem[];
  [key: string]: unknown;
}

export interface ManagementTradingPulseRankingsResponse {
  data: ManagementTradingPulseRankingBlock[];
  items: ManagementTradingPulseRankingBlock[];
  rankings: ManagementTradingPulseRankingBlock[];
  rankingBlocks: ManagementTradingPulseRankingBlock[];
  ranking_blocks: ManagementTradingPulseRankingBlock[];
  summary: {
    runtimeCount: number;
    runtime_count: number;
    rankingBlockCount: number;
    ranking_block_count: number;
    rankedItemCount: number;
    ranked_item_count: number;
    criteria: string[];
    limit: number;
    topRuntimeId?: string | null;
    top_runtime_id?: string | null;
    [key: string]: unknown;
  };
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    surfaces: {
      management_trading_pulse_rankings: ManagementSurfaceRef;
      management_trading_pulse?: ManagementSurfaceRef;
      runtime_roster?: ManagementSurfaceRef;
      telemetry_summary?: ManagementSurfaceRef;
      paper_live_drift?: ManagementSurfaceRef;
      baseline_comparison?: ManagementSurfaceRef;
      [key: string]: ManagementSurfaceRef | undefined;
    };
    composition_sources?: string[];
    [key: string]: unknown;
  };
}

export interface ManagementSentinelPulseQuery {
  kind?: string;
  status?: string;
  severity?: string;
  q?: string;
  page_token?: string;
  page_size?: number;
}

export interface ManagementSentinelPulseSourceRefs {
  findingId?: string | null;
  finding_id?: string | null;
  incidentId?: string | null;
  incident_id?: string | null;
  loopRunId?: string | null;
  loop_run_id?: string | null;
  runtimeId?: string | null;
  runtime_id?: string | null;
  interventionId?: string | null;
  intervention_id?: string | null;
  [key: string]: unknown;
}

export interface ManagementSentinelPulseFinding {
  id: string;
  findingId: string;
  finding_id: string;
  kind: string;
  severity: string;
  riskLevel: string;
  risk_level: string;
  status: string;
  title: string;
  summary?: string;
  triggeredAt?: string | null;
  triggered_at?: string | null;
  createdAt?: string | null;
  created_at?: string | null;
  updatedAt?: string | null;
  updated_at?: string | null;
  target?: Record<string, unknown>;
  sourceRefs: ManagementSentinelPulseSourceRefs;
  source_refs: ManagementSentinelPulseSourceRefs;
  links?: Record<string, string | null | undefined>;
  sourceRecord?: Record<string, unknown>;
  source_record?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ManagementSentinelPulseIntervention {
  id: string;
  interventionId: string;
  intervention_id: string;
  findingId?: string | null;
  finding_id?: string | null;
  kind: string;
  severity: string;
  riskLevel: string;
  risk_level: string;
  status: string;
  title: string;
  summary?: string;
  triggeredAt?: string | null;
  triggered_at?: string | null;
  sourceRefs: ManagementSentinelPulseSourceRefs;
  source_refs: ManagementSentinelPulseSourceRefs;
  links?: Record<string, string | null | undefined>;
  sourceRecord?: Record<string, unknown>;
  source_record?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ManagementSentinelPulseCard {
  cardId: string;
  card_id: string;
  label: string;
  value?: number | string | boolean | null;
  details?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ManagementSentinelPulseSummary {
  findingCount: number;
  finding_count: number;
  returnedFindingCount: number;
  returned_finding_count: number;
  activeFindingCount: number;
  active_finding_count: number;
  criticalFindingCount: number;
  critical_finding_count: number;
  interventionCount: number;
  intervention_count: number;
  pendingInterventionCount: number;
  pending_intervention_count: number;
  highestSeverity?: string | null;
  highest_severity?: string | null;
  byStatus: Record<string, number>;
  by_status: Record<string, number>;
  bySeverity: Record<string, number>;
  by_severity: Record<string, number>;
  byKind: Record<string, number>;
  by_kind: Record<string, number>;
  policy: string;
  basis: string;
  [key: string]: unknown;
}

export interface ManagementSentinelPulseResponse {
  data: {
    id: "management-sentinel-pulse" | string;
    snapshotAt?: string;
    snapshot_at?: string;
    items: ManagementSentinelPulseFinding[];
    findings: ManagementSentinelPulseFinding[];
    interventions: ManagementSentinelPulseIntervention[];
    cards: ManagementSentinelPulseCard[];
    summary: ManagementSentinelPulseSummary;
    policy?: string;
    [key: string]: unknown;
  };
  items: ManagementSentinelPulseFinding[];
  findings: ManagementSentinelPulseFinding[];
  interventions: ManagementSentinelPulseIntervention[];
  cards: ManagementSentinelPulseCard[];
  summary: ManagementSentinelPulseSummary;
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    surfaces: {
      management_sentinel_pulse: ManagementSurfaceRef;
      sentinel_pulse?: ManagementSurfaceRef;
      sentinel_findings?: ManagementSurfaceRef;
      v5_interventions?: ManagementSurfaceRef;
      [key: string]: ManagementSurfaceRef | undefined;
    };
    composition_sources?: string[];
    filters?: ManagementSentinelPulseQuery;
    policy?: string;
    [key: string]: unknown;
  };
}

export interface ManagementEvidenceQuery {
  ref_id?: string;
  linked_entity_type?: string;
  linked_entity_ref?: string;
  link_type?: string;
  credibility_tier?: string;
  verified?: boolean;
  page_token?: string;
  page_size?: number;
}

export type ManagementReadinessId =
  | "ep5"
  | "broker-live"
  | "capital-binding-live"
  | "bff-ha"
  | "strict-publish"
  | string;

export type ManagementReadinessStatus = "ready" | "blocked" | "degraded" | "unknown" | string;

export interface ManagementReadinessCheck {
  id: string;
  label: string;
  status: "pass" | "fail" | "blocked" | "warn" | "unknown" | string;
  blocking: boolean;
  message: string;
  evidence_refs?: string[];
  details?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ManagementReadinessEvidenceRef {
  id: string;
  label: string;
  path: string;
  href?: string;
  exists?: boolean;
  [key: string]: unknown;
}

export interface ManagementReadinessSummary {
  readinessStatus: ManagementReadinessStatus;
  readiness_status: ManagementReadinessStatus;
  canProceed: boolean;
  can_proceed: boolean;
  checkCount: number;
  check_count: number;
  passedCheckCount: number;
  passed_check_count: number;
  blockingReasonCount: number;
  blocking_reason_count: number;
  blockingReasons: string[];
  blocking_reasons: string[];
  byStatus: Record<string, number>;
  by_status: Record<string, number>;
  [key: string]: unknown;
}

export interface ManagementReadinessData {
  id: ManagementReadinessId;
  readinessId: ManagementReadinessId;
  readiness_id: ManagementReadinessId;
  title: string;
  readinessStatus: ManagementReadinessStatus;
  readiness_status: ManagementReadinessStatus;
  canProceed: boolean;
  can_proceed: boolean;
  blockingReasons: string[];
  blocking_reasons: string[];
  checks: ManagementReadinessCheck[];
  evidenceRefs: ManagementReadinessEvidenceRef[];
  evidence_refs: ManagementReadinessEvidenceRef[];
  links?: Record<string, string>;
  details?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ManagementReadinessResponse {
  data: ManagementReadinessData;
  summary: ManagementReadinessSummary;
  checks: ManagementReadinessCheck[];
  items?: ManagementReadinessCheck[];
  evidence_refs: ManagementReadinessEvidenceRef[];
  meta: {
    snapshot_at?: string;
    staleness?: Record<string, unknown>;
    surfaces: Record<string, ManagementSurfaceRef>;
    [key: string]: unknown;
  };
}

export interface ManagementPortfolioBookHolding {
  id: string;
  holding_id: string;
  runtime_id?: string;
  runtime_binding_id?: string;
  deployment_plan_id?: string;
  capital_pool_id?: string;
  capitalPoolId?: string;
  persona_id?: string;
  personaId?: string;
  strategy_id?: string;
  strategyId?: string;
  artifact_id?: string;
  artifact_version?: string;
  deployment_stage?: string;
  deploymentStage?: string;
  status?: string;
  symbol?: string;
  side?: string;
  quantity?: number | null;
  average_price?: number | null;
  avgPrice?: number | null;
  mark_price?: number | null;
  markPrice?: number | null;
  market_value?: number | null;
  marketValue?: number | null;
  notional?: number | null;
  exposure?: number | null;
  weight?: number | null;
  total_pnl?: number | null;
  unrealized_pnl?: number | null;
  realized_pnl?: number | null;
  last_mark_at?: string | null;
  instrument?: Record<string, unknown>;
  capital_pool?: Record<string, unknown>;
  pnl?: Record<string, unknown>;
  telemetry?: Record<string, unknown>;
  links?: Record<string, string | null | undefined>;
  [key: string]: unknown;
}

export interface ManagementPortfolioBookHoldingsSummary {
  holding_count: number;
  returned_holding_count: number;
  active_holding_count: number;
  paper_holding_count: number;
  live_holding_count: number;
  runtime_count: number;
  telemetry_runtime_count: number;
  total_notional?: number | null;
  total_market_value?: number | null;
  total_unrealized_pnl?: number | null;
  total_realized_pnl?: number | null;
  total_pnl?: number | null;
  latest_mark_at?: string | null;
  [key: string]: unknown;
}

export interface ManagementPortfolioBookHoldingsResponse {
  data: {
    summary: ManagementPortfolioBookHoldingsSummary;
    items: ManagementPortfolioBookHolding[];
    holdings: ManagementPortfolioBookHolding[];
    [key: string]: unknown;
  };
  items: ManagementPortfolioBookHolding[];
  summary: ManagementPortfolioBookHoldingsSummary;
  page_info: {
    next_page_token: string | null;
    total: number;
  };
  meta: {
    snapshot_at?: string;
    surfaces?: Record<string, ManagementSurfaceRef>;
    composition_sources?: string[];
    [key: string]: unknown;
  };
}

export interface ManagementPortfolioBookHoldingsQuery {
  capital_pool_id?: string;
  persona_id?: string;
  runtime_id?: string;
  deployment_stage?: string;
  status?: string;
  q?: string;
  page_token?: string;
  page_size?: number;
}

export interface ManagementPortfolioBookPosition extends ManagementPortfolioBookHolding {
  position_id: string;
  positionId?: string;
}

export interface ManagementPortfolioBookPositionsSummary {
  position_count: number;
  returned_position_count: number;
  active_position_count: number;
  paper_position_count: number;
  live_position_count: number;
  runtime_count: number;
  telemetry_runtime_count: number;
  total_notional?: number | null;
  total_market_value?: number | null;
  total_unrealized_pnl?: number | null;
  total_realized_pnl?: number | null;
  total_pnl?: number | null;
  latest_mark_at?: string | null;
  [key: string]: unknown;
}

export interface ManagementPortfolioBookPositionsResponse {
  data: {
    summary: ManagementPortfolioBookPositionsSummary;
    items: ManagementPortfolioBookPosition[];
    positions: ManagementPortfolioBookPosition[];
    [key: string]: unknown;
  };
  items: ManagementPortfolioBookPosition[];
  positions: ManagementPortfolioBookPosition[];
  summary: ManagementPortfolioBookPositionsSummary;
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size?: number;
  };
  meta: {
    snapshot_at?: string;
    surfaces?: Record<string, ManagementSurfaceRef>;
    composition_sources?: string[];
    [key: string]: unknown;
  };
}

export type ManagementPortfolioBookPositionsQuery = ManagementPortfolioBookHoldingsQuery;

export interface ManagementPersonaLeagueQuery {
  state?: string;
  archetype?: string;
  q?: string;
  page_token?: string;
  page_size?: number;
}

export interface ManagementPersonaLeagueRow {
  id: string;
  personaId?: string;
  persona_id?: string;
  name?: string;
  owner?: string;
  updatedAt?: string;
  updated_at?: string;
  state?: string;
  risk?: string;
  archetype?: string;
  routedStrategies?: number;
  routed_strategies?: number;
  successRate?: number;
  success_rate?: number;
  mandate?: string;
  strategyFamily?: string;
  strategy_family?: string;
  routePolicy?: Record<string, unknown>;
  route_policy?: Record<string, unknown>;
  capabilities?: Record<string, unknown>;
  bindings?: Record<string, unknown>;
  sessions?: Record<string, unknown>;
  evaluations?: Record<string, unknown>;
  memory?: Record<string, unknown>;
  health?: Record<string, unknown>;
  allowedActions?: Record<string, unknown>;
  allowed_actions?: Record<string, unknown>;
  links?: Record<string, string | null | undefined>;
  [key: string]: unknown;
}

export interface ManagementPersonaLeagueSummary {
  personaCount: number;
  persona_count: number;
  returnedCount: number;
  returned_count: number;
  [key: string]: unknown;
}

export interface ManagementPersonaLeagueResponse {
  data: {
    id: "management-persona-league" | string;
    items: ManagementPersonaLeagueRow[];
    summary: ManagementPersonaLeagueSummary;
    [key: string]: unknown;
  };
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size?: number;
  };
  meta: {
    snapshot_at?: string;
    surfaces?: Record<string, ManagementSurfaceRef>;
    composition_sources?: string[];
    [key: string]: unknown;
  };
}

export interface ManagementPersonaLeagueRankingsQuery {
  state?: string;
  archetype?: string;
  q?: string;
  criteria?: string;
  limit?: number;
}

export interface ManagementPersonaLeagueRankingItem {
  id: string;
  personaId: string;
  persona_id: string;
  name?: string;
  owner?: string;
  state?: string;
  risk?: string;
  archetype?: string;
  tier: string;
  tierId: string;
  tier_id: string;
  tierLabel: string;
  tier_label: string;
  rank: number;
  score: number;
  overallScore: number;
  overall_score: number;
  scoreField?: string;
  score_field?: string;
  metrics: Record<string, unknown>;
  components: Record<string, number>;
  links?: Record<string, string | null | undefined>;
  [key: string]: unknown;
}

export interface ManagementPersonaLeagueRankingBlock {
  id: string;
  rankingId: string;
  ranking_id: string;
  criteria: string;
  label: string;
  formulaVersion: string;
  formula_version: string;
  weights: Record<string, number>;
  items: ManagementPersonaLeagueRankingItem[];
  rankedCount: number;
  ranked_count: number;
  [key: string]: unknown;
}

export interface ManagementPersonaLeagueRankingsResponse {
  data: {
    id: "management-persona-league-rankings" | string;
    items: ManagementPersonaLeagueRankingBlock[];
    summary: {
      personaCount: number;
      persona_count: number;
      criteria: string[];
      topPersonaId?: string | null;
      top_persona_id?: string | null;
      [key: string]: unknown;
    };
    [key: string]: unknown;
  };
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    surfaces?: Record<string, ManagementSurfaceRef>;
    composition_sources?: string[];
    [key: string]: unknown;
  };
}

export interface ManagementPersonaLeagueMoversQuery {
  state?: string;
  archetype?: string;
  q?: string;
  direction?: "all" | "up" | "down" | "flat" | "new" | string;
  limit?: number;
}

export interface ManagementPersonaLeagueMover extends ManagementPersonaLeagueRankingItem {
  moverId: string;
  mover_id: string;
  currentRank: number;
  current_rank: number;
  previousRank?: number | null;
  previous_rank?: number | null;
  rankDelta?: number | null;
  rank_delta?: number | null;
  direction: "up" | "down" | "flat" | "new" | string;
  currentScore: number;
  current_score: number;
  previousScore?: number | null;
  previous_score?: number | null;
  scoreDelta?: number | null;
  score_delta?: number | null;
  scoreDeltaDisplay?: string | null;
  score_delta_display?: string | null;
  baselineStatus: "ok" | "unavailable" | string;
  baseline_status: "ok" | "unavailable" | string;
  formulaVersion: string;
  formula_version: string;
  movement: {
    direction: string;
    rank_delta?: number | null;
    score_delta?: number | null;
    baseline_status?: string;
    basis?: string;
    [key: string]: unknown;
  };
  basis?: string;
  [key: string]: unknown;
}

export interface ManagementPersonaLeagueMoversSummary {
  personaCount: number;
  persona_count: number;
  moverCount: number;
  mover_count: number;
  returnedCount: number;
  returned_count: number;
  direction: string;
  formulaVersion: string;
  formula_version: string;
  baselineStatus: string;
  baseline_status: string;
  baselineUnavailableCount: number;
  baseline_unavailable_count: number;
  upCount: number;
  up_count: number;
  downCount: number;
  down_count: number;
  flatCount: number;
  flat_count: number;
  newCount: number;
  new_count: number;
  topMoverPersonaId?: string | null;
  top_mover_persona_id?: string | null;
  basis?: string;
  [key: string]: unknown;
}

export interface ManagementPersonaLeagueMoversResponse {
  data: {
    id: "management-persona-league-movers" | string;
    items: ManagementPersonaLeagueMover[];
    summary: ManagementPersonaLeagueMoversSummary;
    policy?: string;
    [key: string]: unknown;
  };
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    surfaces?: Record<string, ManagementSurfaceRef>;
    composition_sources?: string[];
    policy?: string;
    baseline_status?: string;
    [key: string]: unknown;
  };
}

export interface ManagementPersonaLeagueTiersQuery {
  state?: string;
  archetype?: string;
  q?: string;
}

export interface ManagementPersonaLeagueTierAssignment {
  personaId: string;
  persona_id: string;
  name?: string;
  tier: string;
  tierId: string;
  tier_id: string;
  tierLabel: string;
  tier_label: string;
  overallScore: number;
  overall_score: number;
  metrics: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ManagementPersonaLeagueTier {
  id: string;
  tierId: string;
  tier_id: string;
  label: string;
  minScore: number;
  min_score: number;
  maxScore: number;
  max_score: number;
  governancePosture: string;
  governance_posture: string;
  personaCount: number;
  persona_count: number;
  personaIds: string[];
  persona_ids: string[];
  assignments: ManagementPersonaLeagueTierAssignment[];
  [key: string]: unknown;
}

export interface ManagementPersonaLeagueTiersResponse {
  data: {
    id: "management-persona-league-tiers" | string;
    items: ManagementPersonaLeagueTier[];
    related: {
      assignments: ManagementPersonaLeagueTierAssignment[];
      [key: string]: unknown;
    };
    summary: {
      seasonId: string;
      season_id: string;
      formulaVersion: string;
      formula_version: string;
      personaCount: number;
      persona_count: number;
      tierCount: number;
      tier_count: number;
      byTier: Record<string, number>;
      by_tier: Record<string, number>;
      [key: string]: unknown;
    };
    policy?: string;
    [key: string]: unknown;
  };
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    surfaces?: Record<string, ManagementSurfaceRef>;
    composition_sources?: string[];
    policy?: string;
    [key: string]: unknown;
  };
}

export interface ManagementPersonaLeagueHeatmapQuery {
  state?: string;
  archetype?: string;
  q?: string;
  bucket?: "hour" | "day" | "week" | string;
  bucket_count?: number;
  limit?: number;
}

export interface ManagementPersonaLeagueHeatmapBucket {
  id: string;
  bucketId: string;
  bucket_id: string;
  index: number;
  label: string;
  startAt: string;
  start_at: string;
  endAt: string;
  end_at: string;
  endExclusiveAt: string;
  end_exclusive_at: string;
  [key: string]: unknown;
}

export interface ManagementPersonaLeagueHeatmapCell {
  id: string;
  personaId: string;
  persona_id: string;
  bucketId: string;
  bucket_id: string;
  bucketIndex: number;
  bucket_index: number;
  score: number;
  compositeScore: number;
  composite_score: number;
  overallScore: number;
  overall_score: number;
  components: Record<string, number>;
  metrics: Record<string, unknown>;
  formulaVersion: string;
  formula_version: string;
  source: string;
  observedTelemetryCount: number;
  observed_telemetry_count: number;
  latestTelemetryAt?: string | null;
  latest_telemetry_at?: string | null;
  [key: string]: unknown;
}

export interface ManagementPersonaLeagueHeatmapRow {
  id: string;
  personaId: string;
  persona_id: string;
  name?: string;
  owner?: string;
  state?: string;
  risk?: string;
  archetype?: string;
  tier?: string;
  tierId?: string;
  tier_id?: string;
  tierLabel?: string;
  tier_label?: string;
  latestScore?: number;
  latest_score?: number;
  runtimeIds: string[];
  runtime_ids: string[];
  cells: ManagementPersonaLeagueHeatmapCell[];
  links?: Record<string, string | null | undefined>;
  [key: string]: unknown;
}

export interface ManagementPersonaLeagueHeatmapResponse {
  data: {
    id: string;
    heatmapId: string;
    heatmap_id: string;
    bucket: string;
    items: ManagementPersonaLeagueHeatmapRow[];
    buckets: ManagementPersonaLeagueHeatmapBucket[];
    summary: Record<string, unknown>;
    formulaVersion: string;
    formula_version: string;
    basis: string;
    [key: string]: unknown;
  };
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    surfaces?: Record<string, ManagementSurfaceRef>;
    composition_sources?: string[];
    policy?: string;
    [key: string]: unknown;
  };
}

export interface ManagementQuarterlyRankingQuery {
  quarter?: string;
  state?: string;
  archetype?: string;
  q?: string;
  page_token?: string;
  page_size?: number;
}

export interface ManagementQuarterlyRankingWindow {
  quarter: string;
  year: number;
  quarterNumber: number;
  quarter_number: number;
  label: string;
  startAt: string;
  start_at: string;
  endExclusiveAt: string;
  end_exclusive_at: string;
  timezone: "UTC" | string;
  [key: string]: unknown;
}

export interface ManagementQuarterlyRankingFormulaVersion {
  id: string;
  version: string;
  formulaVersion: string;
  formula_version: string;
  effectiveAt: string;
  effective_at: string;
  changeType: string;
  change_type: string;
  governanceEvidenceRefs: string[];
  governance_evidence_refs: string[];
  description?: string;
  [key: string]: unknown;
}

export interface ManagementQuarterlyRankingFormulaChangeControl {
  versionPolicy: string;
  version_policy: string;
  requiresGovernanceEvidence: boolean;
  requires_governance_evidence: boolean;
  governanceEvidenceRefs: string[];
  governance_evidence_refs: string[];
  authority: string;
  [key: string]: unknown;
}

export interface ManagementQuarterlyRankingFormula {
  id: string;
  formulaId: string;
  formula_id: string;
  version: string;
  formulaVersion: string;
  formula_version: string;
  weights: Record<string, number>;
  scoreField: string;
  score_field: string;
  components: Array<Record<string, unknown>>;
  basis: string;
  policy: string;
  governanceEvidenceRefs: string[];
  governance_evidence_refs: string[];
  versionHistory: ManagementQuarterlyRankingFormulaVersion[];
  version_history: ManagementQuarterlyRankingFormulaVersion[];
  changeControl: ManagementQuarterlyRankingFormulaChangeControl;
  change_control: ManagementQuarterlyRankingFormulaChangeControl;
  [key: string]: unknown;
}

export interface ManagementQuarterlyRankingFormulaSummary {
  formulaId: string;
  formula_id: string;
  formulaVersion: string;
  formula_version: string;
  componentCount: number;
  component_count: number;
  weightTotal: number;
  weight_total: number;
  evidenceRefCount: number;
  evidence_ref_count: number;
  basis: string;
  policy: string;
  [key: string]: unknown;
}

export interface ManagementQuarterlyRankingFormulaResponse {
  data: ManagementQuarterlyRankingFormula;
  formula: ManagementQuarterlyRankingFormula;
  versionHistory: ManagementQuarterlyRankingFormulaVersion[];
  version_history: ManagementQuarterlyRankingFormulaVersion[];
  evidenceRefs: ManagementEvidenceItem[];
  evidence_refs: ManagementEvidenceItem[];
  summary: ManagementQuarterlyRankingFormulaSummary;
  meta: {
    snapshot_at?: string;
    surfaces?: Record<string, ManagementSurfaceRef>;
    composition_sources?: string[];
    policy?: string;
    version_policy?: string;
    [key: string]: unknown;
  };
}

export interface ManagementQuarterlyRankingItem extends ManagementPersonaLeagueRankingItem {
  quarter: string;
  quarterWindow: ManagementQuarterlyRankingWindow;
  quarter_window: ManagementQuarterlyRankingWindow;
  formulaVersion: string;
  formula_version: string;
  basis: string;
}

export interface ManagementQuarterlyRankingSummary {
  quarter: string;
  formulaVersion: string;
  formula_version: string;
  personaCount: number;
  persona_count: number;
  rankedCount: number;
  ranked_count: number;
  returnedCount: number;
  returned_count: number;
  topPersonaId?: string | null;
  top_persona_id?: string | null;
  evidenceRefCount: number;
  evidence_ref_count: number;
  redactedEvidenceCount: number;
  redacted_evidence_count: number;
  basis: string;
  [key: string]: unknown;
}

export interface ManagementQuarterlyRankingData {
  id: string;
  quarter: string;
  quarterWindow: ManagementQuarterlyRankingWindow;
  quarter_window: ManagementQuarterlyRankingWindow;
  formula: ManagementQuarterlyRankingFormula;
  items: ManagementQuarterlyRankingItem[];
  evidenceRefs: ManagementEvidenceItem[];
  evidence_refs: ManagementEvidenceItem[];
  summary: ManagementQuarterlyRankingSummary;
  [key: string]: unknown;
}

export interface ManagementQuarterlyRankingResponse {
  data: ManagementQuarterlyRankingData;
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    surfaces?: Record<string, ManagementSurfaceRef>;
    composition_sources?: string[];
    policy?: string;
    redacted_evidence_count?: number;
    [key: string]: unknown;
  };
}

export interface ManagementQuarterlyRankingDrilldownQuery {
  personaId: string;
  quarter?: string;
  state?: string;
  archetype?: string;
  q?: string;
}

export interface ManagementQuarterlyRankingContribution {
  id: string;
  key: string;
  label: string;
  scoreField: string;
  score_field: string;
  score: number;
  weight: number;
  weightedContribution: number;
  weighted_contribution: number;
  contributionShare: number;
  contribution_share: number;
  basis: string;
  [key: string]: unknown;
}

export interface ManagementQuarterlyRankingDrilldownSummary {
  quarter: string;
  personaId: string;
  persona_id: string;
  rank?: number | null;
  rankedCount: number;
  ranked_count: number;
  score: number;
  overallScore?: number | null;
  overall_score?: number | null;
  formulaVersion: string;
  formula_version: string;
  componentCount: number;
  component_count: number;
  totalWeightedContribution: number;
  total_weighted_contribution: number;
  evidenceRefCount: number;
  evidence_ref_count: number;
  redactedEvidenceCount?: number;
  redacted_evidence_count?: number;
  basis: string;
  [key: string]: unknown;
}

export interface ManagementQuarterlyRankingDrilldownData {
  id: string;
  quarter: string;
  quarterWindow: ManagementQuarterlyRankingWindow;
  quarter_window: ManagementQuarterlyRankingWindow;
  personaId: string;
  persona_id: string;
  rank?: number | null;
  score: number;
  rankingItem: ManagementQuarterlyRankingItem;
  ranking_item: ManagementQuarterlyRankingItem;
  formula: ManagementQuarterlyRankingFormula;
  contributions: ManagementQuarterlyRankingContribution[];
  contributionBreakdown: ManagementQuarterlyRankingContribution[];
  contribution_breakdown: ManagementQuarterlyRankingContribution[];
  sourceBreakdown: Record<string, unknown>;
  source_breakdown: Record<string, unknown>;
  evidenceRefs: ManagementEvidenceItem[];
  evidence_refs: ManagementEvidenceItem[];
  summary: ManagementQuarterlyRankingDrilldownSummary;
  links?: Record<string, string | null | undefined>;
  [key: string]: unknown;
}

export interface ManagementQuarterlyRankingDrilldownResponse {
  data: ManagementQuarterlyRankingDrilldownData;
  item: ManagementQuarterlyRankingItem;
  rankingItem: ManagementQuarterlyRankingItem;
  ranking_item: ManagementQuarterlyRankingItem;
  contributions: ManagementQuarterlyRankingContribution[];
  contributionBreakdown: ManagementQuarterlyRankingContribution[];
  contribution_breakdown: ManagementQuarterlyRankingContribution[];
  sourceBreakdown: Record<string, unknown>;
  source_breakdown: Record<string, unknown>;
  formula: ManagementQuarterlyRankingFormula;
  quarterWindow: ManagementQuarterlyRankingWindow;
  quarter_window: ManagementQuarterlyRankingWindow;
  evidenceRefs: ManagementEvidenceItem[];
  evidence_refs: ManagementEvidenceItem[];
  summary: ManagementQuarterlyRankingDrilldownSummary;
  meta: {
    snapshot_at?: string;
    correlationId?: string;
    correlation_id?: string;
    surfaces?: Record<string, ManagementSurfaceRef>;
    composition_sources?: string[];
    policy?: string;
    redacted_evidence_count?: number;
    [key: string]: unknown;
  };
}

export interface ManagementQuarterlyRankingRecommendationsQuery {
  quarter?: string;
  state?: string;
  archetype?: string;
  q?: string;
  page_token?: string;
  page_size?: number;
}

export type ManagementQuarterlyRankingRecommendationAction =
  | "promote_to_canary_candidate"
  | "increase_research_budget"
  | "grant_tool_access"
  | "reduce_capital_access"
  | "require_retraining"
  | "freeze_persona"
  | "suspend_persona"
  | "retire_persona"
  | string;

export interface ManagementQuarterlyRankingRecommendationGovernance {
  requiresHumanGateDecision: boolean;
  requires_human_gate_decision: boolean;
  destinations: string[];
  humanInboxRoute?: string;
  human_inbox_route?: string;
  governanceQueueRoute?: string;
  governance_queue_route?: string;
  decisionType?: string;
  decision_type?: string;
  liveCapitalMutation: boolean;
  live_capital_mutation: boolean;
  [key: string]: unknown;
}

export interface ManagementQuarterlyRankingRecommendationItem {
  id: string;
  recommendationId: string;
  recommendation_id: string;
  quarter: string;
  quarterWindow: ManagementQuarterlyRankingWindow;
  quarter_window: ManagementQuarterlyRankingWindow;
  personaId: string;
  persona_id: string;
  name?: string;
  owner?: string;
  state?: string;
  risk?: string;
  rank?: number;
  score: number;
  tier?: string;
  tierId?: string;
  tier_id?: string;
  tierLabel?: string;
  tier_label?: string;
  formulaVersion: string;
  formula_version: string;
  actionId: ManagementQuarterlyRankingRecommendationAction;
  action_id: ManagementQuarterlyRankingRecommendationAction;
  actionLabel: string;
  action_label: string;
  recommendationType: "governance_advisory" | string;
  recommendation_type: "governance_advisory" | string;
  status: "recommended" | string;
  priority: string;
  riskLevel: string;
  risk_level: string;
  target: {
    type: string;
    id: string;
    [key: string]: unknown;
  };
  rationale: string;
  rationaleCodes: string[];
  rationale_codes: string[];
  metrics: Record<string, unknown>;
  components: Record<string, unknown>;
  evidenceRefs: ManagementEvidenceItem[];
  evidence_refs: ManagementEvidenceItem[];
  evidenceRefIds: string[];
  evidence_ref_ids: string[];
  governance: ManagementQuarterlyRankingRecommendationGovernance;
  requiresHumanGateDecision: boolean;
  requires_human_gate_decision: boolean;
  liveCapitalMutation: boolean;
  live_capital_mutation: boolean;
  policy: string;
  links?: Record<string, string | null | undefined>;
  [key: string]: unknown;
}

export interface ManagementQuarterlyRankingRecommendationsSummary {
  quarter: string;
  formulaVersion: string;
  formula_version: string;
  personaCount: number;
  persona_count: number;
  rankedCount: number;
  ranked_count: number;
  recommendationCount: number;
  recommendation_count: number;
  returnedCount: number;
  returned_count: number;
  topPersonaId?: string | null;
  top_persona_id?: string | null;
  humanGateDecisionCount: number;
  human_gate_decision_count: number;
  liveCapitalMutationCount: number;
  live_capital_mutation_count: number;
  evidenceRefCount: number;
  evidence_ref_count: number;
  redactedEvidenceCount: number;
  redacted_evidence_count: number;
  byAction: Record<string, number>;
  by_action: Record<string, number>;
  allowedActions: ManagementQuarterlyRankingRecommendationAction[];
  allowed_actions: ManagementQuarterlyRankingRecommendationAction[];
  basis: string;
  policy: string;
  [key: string]: unknown;
}

export interface ManagementQuarterlyRankingRecommendationsData {
  id: string;
  quarter: string;
  quarterWindow: ManagementQuarterlyRankingWindow;
  quarter_window: ManagementQuarterlyRankingWindow;
  formula: ManagementQuarterlyRankingFormula;
  items: ManagementQuarterlyRankingRecommendationItem[];
  evidenceRefs: ManagementEvidenceItem[];
  evidence_refs: ManagementEvidenceItem[];
  summary: ManagementQuarterlyRankingRecommendationsSummary;
  policy: string;
  governanceDestinations: string[];
  governance_destinations: string[];
  allowedActions: ManagementQuarterlyRankingRecommendationAction[];
  allowed_actions: ManagementQuarterlyRankingRecommendationAction[];
  [key: string]: unknown;
}

export interface ManagementQuarterlyRankingRecommendationsResponse {
  data: ManagementQuarterlyRankingRecommendationsData;
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    surfaces?: Record<string, ManagementSurfaceRef>;
    composition_sources?: string[];
    policy?: string;
    governance_destinations?: string[];
    redacted_evidence_count?: number;
    live_capital_mutation?: boolean;
    [key: string]: unknown;
  };
}

export type ManagementPerformanceAttributionDimension =
  | "persona"
  | "strategy"
  | "pool"
  | "asset"
  | "broker"
  | "runtime"
  | "regime"
  | "all"
  | string;

export interface ManagementPerformanceAttributionQuery {
  dimension?: ManagementPerformanceAttributionDimension;
  period?: string;
  page_token?: string;
  page_size?: number;
}

export type ManagementPerformanceAttributionByStrategyQuery = Omit<
  ManagementPerformanceAttributionQuery,
  "dimension"
>;

export type ManagementPerformanceAttributionByPersonaQuery = Omit<
  ManagementPerformanceAttributionQuery,
  "dimension"
>;

export type ManagementPerformanceAttributionByPoolQuery = Omit<
  ManagementPerformanceAttributionQuery,
  "dimension"
>;

export interface ManagementPerformanceAttributionMetrics {
  runtimeCount: number;
  runtime_count: number;
  telemetryRuntimeCount: number;
  telemetry_runtime_count: number;
  holdingCount: number;
  holding_count: number;
  totalPnl?: number | null;
  total_pnl?: number | null;
  unrealizedPnl?: number | null;
  unrealized_pnl?: number | null;
  realizedPnl?: number | null;
  realized_pnl?: number | null;
  totalNotional?: number | null;
  total_notional?: number | null;
  totalMarketValue?: number | null;
  total_market_value?: number | null;
  totalExposure?: number | null;
  total_exposure?: number | null;
  worstDrawdown?: number | null;
  worst_drawdown?: number | null;
  averageFillRate?: number | null;
  average_fill_rate?: number | null;
  averageSlippageBps?: number | null;
  average_slippage_bps?: number | null;
  totalTrades: number;
  total_trades: number;
  latestTelemetryAt?: string | null;
  latest_telemetry_at?: string | null;
  pnlContributionPct?: number | null;
  pnl_contribution_pct?: number | null;
  notionalWeight?: number | null;
  notional_weight?: number | null;
  [key: string]: unknown;
}

export interface ManagementPerformanceAttributionSourceRefs {
  runtimeIds?: string[];
  runtime_ids?: string[];
  capitalPoolIds?: string[];
  capital_pool_ids?: string[];
  personaIds?: string[];
  persona_ids?: string[];
  strategyIds?: string[];
  strategy_ids?: string[];
  [key: string]: unknown;
}

export interface ManagementPerformanceAttributionRow {
  id: string;
  dimension: ManagementPerformanceAttributionDimension;
  dimensionKey: string;
  dimension_key: string;
  label: string;
  period: string;
  rank: number;
  metrics: ManagementPerformanceAttributionMetrics;
  totalPnl?: number | null;
  total_pnl?: number | null;
  pnlContributionPct?: number | null;
  pnl_contribution_pct?: number | null;
  notionalWeight?: number | null;
  notional_weight?: number | null;
  runtimeCount: number;
  runtime_count: number;
  holdingCount: number;
  holding_count: number;
  sourceRefs?: ManagementPerformanceAttributionSourceRefs;
  source_refs?: ManagementPerformanceAttributionSourceRefs;
  links?: Record<string, string | null | undefined>;
  [key: string]: unknown;
}

export interface ManagementPerformanceAttributionSummary {
  period: string;
  dimensions: string[];
  supportedDimensions: string[];
  supported_dimensions: string[];
  rowCount: number;
  row_count: number;
  returnedRowCount: number;
  returned_row_count: number;
  runtimeCount: number;
  runtime_count: number;
  telemetryRuntimeCount: number;
  telemetry_runtime_count: number;
  holdingCount: number;
  holding_count: number;
  totalPnl?: number | null;
  total_pnl?: number | null;
  totalNotional?: number | null;
  total_notional?: number | null;
  totalExposure?: number | null;
  total_exposure?: number | null;
  worstDrawdown?: number | null;
  worst_drawdown?: number | null;
  averageFillRate?: number | null;
  average_fill_rate?: number | null;
  averageSlippageBps?: number | null;
  average_slippage_bps?: number | null;
  totalTrades: number;
  total_trades: number;
  latestTelemetryAt?: string | null;
  latest_telemetry_at?: string | null;
  basis: string;
  [key: string]: unknown;
}

export interface ManagementPerformanceAttributionData {
  id: string;
  period: string;
  dimensions: string[];
  items: ManagementPerformanceAttributionRow[];
  rows: ManagementPerformanceAttributionRow[];
  summary: ManagementPerformanceAttributionSummary;
  [key: string]: unknown;
}

export interface ManagementPerformanceAttributionResponse {
  data: ManagementPerformanceAttributionData;
  items: ManagementPerformanceAttributionRow[];
  rows: ManagementPerformanceAttributionRow[];
  summary: ManagementPerformanceAttributionSummary;
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    surfaces?: Record<string, ManagementSurfaceRef>;
    composition_sources?: string[];
    period?: string;
    dimensions?: string[];
    policy?: string;
    [key: string]: unknown;
  };
}

export interface ManagementEvidenceItem {
  id: string;
  refId: string;
  ref_id: string;
  title?: string;
  displayLabel?: string;
  display_label?: string;
  sourceType?: string;
  source_type?: string;
  sourceRef?: string;
  source_ref?: string;
  capturedAt?: string;
  captured_at?: string;
  linkType?: string;
  link_type?: string;
  credibility?: Record<string, unknown>;
  linkedObjectSummary?: Record<string, unknown>;
  linked_object_summary?: Record<string, unknown>;
  resolvedLink?: Record<string, unknown>;
  resolved_link?: Record<string, unknown>;
  routeHref?: string;
  route_href?: string;
  managementHref?: string;
  management_href?: string;
  kind?: string;
  requiredCapability?: string;
  required_capability?: string;
  reason?: string;
  redacted?: boolean;
  [key: string]: unknown;
}

export interface ManagementEvidenceSummary {
  totalEvidence: number;
  total_evidence: number;
  returnedEvidence: number;
  returned_evidence: number;
  visibleEvidence: number;
  visible_evidence: number;
  redactedEvidence: number;
  redacted_evidence: number;
  verifiedEvidence: number;
  verified_evidence: number;
  bySourceType: Record<string, number>;
  by_source_type: Record<string, number>;
  byLinkType: Record<string, number>;
  by_link_type: Record<string, number>;
  byCredibilityTier: Record<string, number>;
  by_credibility_tier: Record<string, number>;
  [key: string]: unknown;
}

export interface ManagementEvidenceResponse {
  data: {
    id: string;
    items: ManagementEvidenceItem[];
    summary: ManagementEvidenceSummary;
    facets: {
      sourceTypes?: Record<string, number>;
      source_types?: Record<string, number>;
      linkTypes?: Record<string, number>;
      link_types?: Record<string, number>;
      credibilityTiers?: Record<string, number>;
      credibility_tiers?: Record<string, number>;
      [key: string]: unknown;
    };
    [key: string]: unknown;
  };
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    staleness?: Record<string, unknown>;
    surfaces: {
      management_evidence: ManagementSurfaceRef;
      evidence_refs?: ManagementSurfaceRef;
      knowledge_evidence?: ManagementSurfaceRef;
      [key: string]: ManagementSurfaceRef | undefined;
    };
    redacted_evidence_count?: number;
    [key: string]: unknown;
  };
}

export interface ManagementEvolutionJournalQuery {
  source_type?: string;
  status?: string;
  action_type?: string;
  risk_level?: string;
  page_token?: string;
  page_size?: number;
}

export type ManagementEvolutionJournalEntryType =
  | "evolution_decision"
  | "mutation_review"
  | "postmortem"
  | "rollback"
  | "freeze_order"
  | string;

export interface ManagementEvolutionJournalItem {
  id: string;
  journal_id: string;
  entryType: ManagementEvolutionJournalEntryType;
  entry_type: ManagementEvolutionJournalEntryType;
  source_id: string;
  title: string;
  summary: string;
  status: string;
  risk_level?: string | null;
  action_type?: string | null;
  target?: {
    type?: string | null;
    id?: string | null;
    version?: string | null;
    [key: string]: unknown;
  };
  created_at?: string | null;
  updated_at?: string | null;
  occurred_at?: string | null;
  route?: string | null;
  bff_detail_path?: string | null;
  decision?: Record<string, unknown>;
  mutationReview?: Record<string, unknown>;
  mutation_review?: Record<string, unknown>;
  postmortem?: Record<string, unknown>;
  rollback?: Record<string, unknown>;
  freezeOrder?: Record<string, unknown>;
  freeze_order?: Record<string, unknown>;
  record?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ManagementEvolutionJournalSummary {
  total_items: number;
  returned_items: number;
  decision_count: number;
  mutation_review_count: number;
  postmortem_count: number;
  rollback_count: number;
  freeze_order_count: number;
  pending_review_count: number;
  active_freeze_count: number;
  completed_rollback_count: number;
  latest_at?: string | null;
  byType: Record<string, number>;
  by_type: Record<string, number>;
  byStatus: Record<string, number>;
  by_status: Record<string, number>;
  byRiskLevel: Record<string, number>;
  by_risk_level: Record<string, number>;
  [key: string]: unknown;
}

export interface ManagementEvolutionJournalResponse {
  data: {
    id: string;
    items: ManagementEvolutionJournalItem[];
    summary: ManagementEvolutionJournalSummary;
    [key: string]: unknown;
  };
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    staleness?: Record<string, unknown>;
    surfaces: {
      management_evolution_journal: ManagementSurfaceRef;
      mutation_review?: ManagementSurfaceRef;
      evolution_decisions?: ManagementSurfaceRef;
      postmortems?: ManagementSurfaceRef;
      freeze_orders?: ManagementSurfaceRef;
      rollbacks?: ManagementSurfaceRef;
      approval_decisions?: ManagementSurfaceRef;
      [key: string]: ManagementSurfaceRef | undefined;
    };
    composition_sources?: string[];
    [key: string]: unknown;
  };
}

export interface ManagementPersonaIntentQuery {
  source_type?: string;
  persona_id?: string;
  status?: string;
  intent?: string;
  page_token?: string;
  page_size?: number;
}

export type ManagementPersonaIntentSourceType =
  | "persona_trace"
  | "trainer_session"
  | "agora_session"
  | string;

export interface ManagementPersonaIntentItem {
  id: string;
  intent_id: string;
  sourceType: ManagementPersonaIntentSourceType;
  source_type: ManagementPersonaIntentSourceType;
  source_id: string;
  personaId?: string | null;
  persona_id?: string | null;
  persona_ids?: string[];
  persona_label?: string | null;
  intent: string;
  title: string;
  summary: string;
  status: string;
  created_at?: string | null;
  updated_at?: string | null;
  occurred_at?: string | null;
  trace?: Record<string, unknown>;
  trainer?: Record<string, unknown>;
  agora?: Record<string, unknown>;
  redacted: boolean;
  redaction: {
    is_redacted?: boolean;
    redacted?: boolean;
    policy?: string;
    redacted_fields?: string[];
    [key: string]: unknown;
  };
  route?: string | null;
  bff_detail_path?: string | null;
  [key: string]: unknown;
}

export interface ManagementPersonaIntentSummary {
  total_items: number;
  returned_items: number;
  persona_trace_count: number;
  trainer_session_count: number;
  agora_session_count: number;
  redacted_item_count: number;
  persona_count: number;
  persona_ids: string[];
  latest_at?: string | null;
  bySourceType: Record<string, number>;
  by_source_type: Record<string, number>;
  byStatus: Record<string, number>;
  by_status: Record<string, number>;
  byIntent: Record<string, number>;
  by_intent: Record<string, number>;
  [key: string]: unknown;
}

export interface ManagementPersonaIntentResponse {
  data: {
    id: string;
    items: ManagementPersonaIntentItem[];
    summary: ManagementPersonaIntentSummary;
    [key: string]: unknown;
  };
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    staleness?: Record<string, unknown>;
    surfaces: {
      management_persona_intent: ManagementSurfaceRef;
      persona_traces?: ManagementSurfaceRef;
      personas?: ManagementSurfaceRef;
      persona_sessions?: ManagementSurfaceRef;
      capability_snapshots?: ManagementSurfaceRef;
      teaching_sessions?: ManagementSurfaceRef;
      agora_sessions?: ManagementSurfaceRef;
      [key: string]: ManagementSurfaceRef | undefined;
    };
    composition_sources?: string[];
    redacted_item_count?: number;
    [key: string]: unknown;
  };
}

export interface ManagementPortfolioBookPoolQuery {
  status?: string;
  risk_policy_ref?: string;
  page_token?: string;
  page_size?: number;
}

export interface ManagementPortfolioBookPoolItem {
  id: string;
  pool_id: string;
  name?: string;
  status?: string;
  risk_policy_ref?: string;
  owner?: Record<string, unknown>;
  currency?: string;
  risk_budget?: number | null;
  riskBudget?: number | null;
  current_exposure?: number | null;
  currentExposure?: number | null;
  risk_budget_utilization?: number | null;
  riskBudgetUtilization?: number | null;
  exposure?: Record<string, unknown>;
  risk?: Record<string, unknown>;
  pnl?: number | null;
  total_pnl?: number | null;
  pnl_summary?: Record<string, unknown>;
  telemetry?: Record<string, unknown>;
  binding_count?: number;
  active_binding_count?: number;
  deployment_count?: number;
  approved_deployment_count?: number;
  runtime_count?: number;
  active_runtime_count?: number;
  paper_runtime_count?: number;
  live_runtime_count?: number;
  deployment_stages?: string[];
  binding_ids?: string[];
  deployment_ids?: string[];
  runtime_ids?: string[];
  [key: string]: unknown;
}

export interface ManagementPortfolioBookPoolsResponse {
  data: ManagementPortfolioBookPoolItem[];
  items: ManagementPortfolioBookPoolItem[];
  pools: ManagementPortfolioBookPoolItem[];
  summary: {
    total_pools: number;
    returned_pools: number;
    active_pool_count: number;
    risk_budget_total?: number | null;
    current_exposure_total?: number | null;
    risk_budget_utilization?: number | null;
    telemetry_runtime_count?: number;
    total_pnl?: number | null;
    max_drawdown?: number | null;
    average_fill_rate?: number | null;
    total_trades?: number;
    latest_telemetry_at?: string | null;
    [key: string]: unknown;
  };
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    staleness?: Record<string, unknown>;
    surfaces: {
      portfolio_book_pools: ManagementSurfaceRef;
      capital_pools?: ManagementSurfaceRef;
      persona_bindings?: ManagementSurfaceRef;
      deployment_plans?: ManagementSurfaceRef;
      runtime_bindings?: ManagementSurfaceRef;
      telemetry_summaries?: ManagementSurfaceRef;
      [key: string]: ManagementSurfaceRef | undefined;
    };
    composition_sources?: string[];
    [key: string]: unknown;
  };
}

export interface ManagementStrategyAllocationQuery {
  strategy_id?: string;
  capital_pool_id?: string;
  deployment_stage?: string;
  drift_status?: string;
  page_token?: string;
  page_size?: number;
}

export interface ManagementStrategyAllocationRow {
  id: string;
  rank: number;
  strategyId: string;
  strategy_id: string;
  strategyLabel?: string;
  strategy_label?: string;
  capitalPoolId: string;
  capital_pool_id: string;
  capitalPoolName?: string;
  capital_pool_name?: string;
  status: string;
  deploymentStages: string[];
  deployment_stages: string[];
  runtimeIds: string[];
  runtime_ids: string[];
  runtimeBindingIds?: string[];
  runtime_binding_ids?: string[];
  deploymentPlanIds?: string[];
  deployment_plan_ids?: string[];
  personaIds?: string[];
  persona_ids?: string[];
  allocation: {
    amount?: number | null;
    riskBudget?: number | null;
    risk_budget?: number | null;
    utilization?: number | null;
    source?: string;
    sources?: string[];
    [key: string]: unknown;
  };
  allocationAmount?: number | null;
  allocation_amount?: number | null;
  riskBudget?: number | null;
  risk_budget?: number | null;
  allocationUtilization?: number | null;
  allocation_utilization?: number | null;
  metrics: Record<string, unknown>;
  drift: Record<string, unknown>;
  paperLiveDrift?: Record<string, unknown>;
  paper_live_drift?: Record<string, unknown>;
  sourceRefs?: Record<string, unknown>;
  source_refs?: Record<string, unknown>;
  links?: Record<string, string | null | undefined>;
  [key: string]: unknown;
}

export interface ManagementStrategyAllocationSummary {
  allocationCount: number;
  allocation_count: number;
  returnedAllocationCount: number;
  returned_allocation_count: number;
  strategyCount: number;
  strategy_count: number;
  capitalPoolCount: number;
  capital_pool_count: number;
  activeRuntimeCount: number;
  active_runtime_count: number;
  totalAllocatedCapital?: number | null;
  total_allocated_capital?: number | null;
  totalPnl?: number | null;
  total_pnl?: number | null;
  driftBreachedCount: number;
  drift_breached_count: number;
  driftWatchCount: number;
  drift_watch_count: number;
  driftUnavailableCount: number;
  drift_unavailable_count: number;
  byDriftStatus: Record<string, number>;
  by_drift_status: Record<string, number>;
  basis: string;
  [key: string]: unknown;
}

export interface ManagementStrategyAllocationResponse {
  data: {
    id: "management-strategy-allocation" | string;
    items: ManagementStrategyAllocationRow[];
    rows: ManagementStrategyAllocationRow[];
    summary: ManagementStrategyAllocationSummary;
    [key: string]: unknown;
  };
  items: ManagementStrategyAllocationRow[];
  rows: ManagementStrategyAllocationRow[];
  summary: ManagementStrategyAllocationSummary;
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    surfaces: {
      strategy_allocation: ManagementSurfaceRef;
      runtime_bindings?: ManagementSurfaceRef;
      deployment_plans?: ManagementSurfaceRef;
      persona_bindings?: ManagementSurfaceRef;
      capital_pools?: ManagementSurfaceRef;
      strategies?: ManagementSurfaceRef;
      telemetry_summaries?: ManagementSurfaceRef;
      paper_live_drift?: ManagementSurfaceRef;
      [key: string]: ManagementSurfaceRef | undefined;
    };
    composition_sources?: string[];
    policy?: string;
    [key: string]: unknown;
  };
}

export type ManagementCapitalFlowDirection = "inflow" | "outflow" | "flat" | "unknown" | string;

export interface ManagementCapitalFlowQuery {
  capital_pool_id?: string;
  persona_id?: string;
  strategy_id?: string;
  deployment_stage?: string;
  direction?: ManagementCapitalFlowDirection;
  page_token?: string;
  page_size?: number;
}

export interface ManagementCapitalFlowRow {
  id: string;
  rank: number;
  flowId?: string;
  flow_id?: string;
  direction: ManagementCapitalFlowDirection;
  status: string;
  capitalPoolId: string;
  capital_pool_id: string;
  capitalPoolName?: string;
  capital_pool_name?: string;
  personaId: string;
  persona_id: string;
  personaLabel?: string;
  persona_label?: string;
  strategyId: string;
  strategy_id: string;
  strategyLabel?: string;
  strategy_label?: string;
  deploymentStage: string;
  deployment_stage: string;
  runtimeIds: string[];
  runtime_ids: string[];
  runtimeBindingIds?: string[];
  runtime_binding_ids?: string[];
  deploymentPlanIds?: string[];
  deployment_plan_ids?: string[];
  personaCapitalBindingIds?: string[];
  persona_capital_binding_ids?: string[];
  runtimeStatuses?: string[];
  runtime_statuses?: string[];
  amount?: number | null;
  netCapitalFlow?: number | null;
  net_capital_flow?: number | null;
  inflowAmount?: number | null;
  inflow_amount?: number | null;
  outflowAmount?: number | null;
  outflow_amount?: number | null;
  allocatedCapital?: number | null;
  allocated_capital?: number | null;
  currentExposure?: number | null;
  current_exposure?: number | null;
  riskBudget?: number | null;
  risk_budget?: number | null;
  availableCapital?: number | null;
  available_capital?: number | null;
  riskBudgetUtilization?: number | null;
  risk_budget_utilization?: number | null;
  latestFlowAt?: string | null;
  latest_flow_at?: string | null;
  metrics: Record<string, unknown>;
  sourceRefs?: Record<string, unknown>;
  source_refs?: Record<string, unknown>;
  links?: Record<string, string | null | undefined>;
  [key: string]: unknown;
}

export interface ManagementCapitalFlowSummary {
  flowCount: number;
  flow_count: number;
  returnedFlowCount: number;
  returned_flow_count: number;
  capitalPoolCount: number;
  capital_pool_count: number;
  personaCount: number;
  persona_count: number;
  strategyCount: number;
  strategy_count: number;
  runtimeCount: number;
  runtime_count: number;
  telemetryRuntimeCount: number;
  telemetry_runtime_count: number;
  netCapitalFlow?: number | null;
  net_capital_flow?: number | null;
  totalInflow?: number | null;
  total_inflow?: number | null;
  totalOutflow?: number | null;
  total_outflow?: number | null;
  allocatedCapitalTotal?: number | null;
  allocated_capital_total?: number | null;
  byDirection: Record<string, number>;
  by_direction: Record<string, number>;
  latestFlowAt?: string | null;
  latest_flow_at?: string | null;
  basis: string;
  [key: string]: unknown;
}

export interface ManagementCapitalFlowResponse {
  data: {
    id: "management-capital-flow" | string;
    items: ManagementCapitalFlowRow[];
    rows: ManagementCapitalFlowRow[];
    flows: ManagementCapitalFlowRow[];
    summary: ManagementCapitalFlowSummary;
    [key: string]: unknown;
  };
  items: ManagementCapitalFlowRow[];
  rows: ManagementCapitalFlowRow[];
  flows: ManagementCapitalFlowRow[];
  summary: ManagementCapitalFlowSummary;
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    surfaces: {
      capital_flow: ManagementSurfaceRef;
      runtime_bindings?: ManagementSurfaceRef;
      deployment_plans?: ManagementSurfaceRef;
      persona_bindings?: ManagementSurfaceRef;
      capital_pools?: ManagementSurfaceRef;
      strategies?: ManagementSurfaceRef;
      telemetry_summaries?: ManagementSurfaceRef;
      [key: string]: ManagementSurfaceRef | undefined;
    };
    composition_sources?: string[];
    policy?: string;
    [key: string]: unknown;
  };
}

export interface ManagementRiskRadarQuery {
  persona_id?: string;
  strategy_id?: string;
  capital_pool_id?: string;
  risk_state?: string;
  page_token?: string;
  page_size?: number;
}

export type ManagementRiskRadarState = "ok" | "watch" | "critical" | "unknown" | string;

export interface ManagementRiskRadarMetricIndicator {
  id: "drawdown" | "exposure" | "value-at-risk" | string;
  metric: string;
  label?: string;
  value?: number | null;
  risk_budget?: number | null;
  utilization?: number | null;
  status: ManagementRiskRadarState;
  source?: string;
  watch_threshold?: number;
  critical_threshold?: number;
  basis?: string;
  [key: string]: unknown;
}

export interface ManagementRiskRadarItem {
  id: string;
  rank: number;
  personaId: string;
  persona_id: string;
  personaLabel?: string;
  persona_label?: string;
  strategyId: string;
  strategy_id: string;
  strategyLabel?: string;
  strategy_label?: string;
  capitalPoolId: string;
  capital_pool_id: string;
  capitalPoolName?: string;
  capital_pool_name?: string;
  riskState: ManagementRiskRadarState;
  risk_state: ManagementRiskRadarState;
  riskScore?: number;
  risk_score?: number;
  deploymentStages?: string[];
  deployment_stages?: string[];
  runtimeStatuses?: string[];
  runtime_statuses?: string[];
  indicators: ManagementRiskRadarMetricIndicator[];
  metrics: Record<string, unknown>;
  drawdown?: number | null;
  worstDrawdown?: number | null;
  worst_drawdown?: number | null;
  exposure?: number | null;
  totalExposure?: number | null;
  total_exposure?: number | null;
  valueAtRisk?: number | null;
  value_at_risk?: number | null;
  riskBudget?: number | null;
  risk_budget?: number | null;
  exposureUtilization?: number | null;
  exposure_utilization?: number | null;
  valueAtRiskUtilization?: number | null;
  value_at_risk_utilization?: number | null;
  sourceRefs?: Record<string, unknown>;
  source_refs?: Record<string, unknown>;
  links?: Record<string, string | null | undefined>;
  [key: string]: unknown;
}

export interface ManagementRiskRadarSummary {
  indicatorCount: number;
  indicator_count: number;
  returnedIndicatorCount: number;
  returned_indicator_count: number;
  personaCount: number;
  persona_count: number;
  strategyCount: number;
  strategy_count: number;
  capitalPoolCount: number;
  capital_pool_count: number;
  criticalCount: number;
  critical_count: number;
  watchCount: number;
  watch_count: number;
  unknownCount: number;
  unknown_count: number;
  okCount: number;
  ok_count: number;
  byRiskState: Record<string, number>;
  by_risk_state: Record<string, number>;
  totalExposure?: number | null;
  total_exposure?: number | null;
  worstDrawdown?: number | null;
  worst_drawdown?: number | null;
  valueAtRiskTotal?: number | null;
  value_at_risk_total?: number | null;
  basis?: string;
  [key: string]: unknown;
}

export interface ManagementRiskRadarResponse {
  data: {
    id: "management-risk-radar" | string;
    items: ManagementRiskRadarItem[];
    rows: ManagementRiskRadarItem[];
    indicators: ManagementRiskRadarItem[];
    summary: ManagementRiskRadarSummary;
    [key: string]: unknown;
  };
  items: ManagementRiskRadarItem[];
  rows: ManagementRiskRadarItem[];
  indicators: ManagementRiskRadarItem[];
  summary: ManagementRiskRadarSummary;
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    surfaces: {
      risk_radar: ManagementSurfaceRef;
      runtime_bindings?: ManagementSurfaceRef;
      deployment_plans?: ManagementSurfaceRef;
      persona_bindings?: ManagementSurfaceRef;
      capital_pools?: ManagementSurfaceRef;
      strategies?: ManagementSurfaceRef;
      telemetry_summaries?: ManagementSurfaceRef;
      [key: string]: ManagementSurfaceRef | undefined;
    };
    composition_sources?: string[];
    policy?: string;
    [key: string]: unknown;
  };
}

export interface ManagementIncidentTimelineQuery {
  status?: string;
  severity?: string;
  capital_pool_id?: string;
  affected_pool_id?: string;
  runtime_id?: string;
  sort_order?: "asc" | "desc" | string;
  page_token?: string;
  page_size?: number;
}

export type ManagementIncidentSeverityBucket = "high" | "medium" | "low" | string;

export interface ManagementIncidentTimelineItem {
  id: string;
  incidentId?: string;
  incident_id: string;
  timelineId?: string | null;
  timeline_id?: string | null;
  sequence: number;
  timelineSequence?: number;
  timeline_sequence?: number;
  title?: string;
  status: string;
  severity: string;
  severityBucket: ManagementIncidentSeverityBucket;
  severity_bucket: ManagementIncidentSeverityBucket;
  occurredAt?: string | null;
  occurred_at?: string | null;
  updatedAt?: string | null;
  updated_at?: string | null;
  runtimeId?: string | null;
  runtime_id?: string | null;
  deploymentPlanId?: string | null;
  deployment_plan_id?: string | null;
  capitalPoolId?: string | null;
  capital_pool_id?: string | null;
  artifactId?: string | null;
  artifact_id?: string | null;
  lineageRef?: string | null;
  lineage_ref?: string | null;
  evidenceSummary?: string | null;
  evidence_summary?: string | null;
  telemetryEventIds?: string[];
  telemetry_event_ids?: string[];
  sourceRefs?: Record<string, unknown>;
  source_refs?: Record<string, unknown>;
  links?: Record<string, string | null | undefined>;
  [key: string]: unknown;
}

export interface ManagementIncidentTimelineSummary {
  incidentCount: number;
  incident_count: number;
  returnedIncidentCount: number;
  returned_incident_count: number;
  activeIncidentCount: number;
  active_incident_count: number;
  resolvedIncidentCount: number;
  resolved_incident_count: number;
  highSeverityCount: number;
  high_severity_count: number;
  mediumSeverityCount: number;
  medium_severity_count: number;
  lowSeverityCount: number;
  low_severity_count: number;
  severityBuckets: Record<string, number>;
  severity_buckets: Record<string, number>;
  byStatus: Record<string, number>;
  by_status: Record<string, number>;
  firstIncidentAt?: string | null;
  first_incident_at?: string | null;
  latestIncidentAt?: string | null;
  latest_incident_at?: string | null;
  sortOrder?: string;
  sort_order?: string;
  basis?: string;
  [key: string]: unknown;
}

export interface ManagementIncidentTimelineResponse {
  data: {
    id: "management-incident-timeline" | string;
    items: ManagementIncidentTimelineItem[];
    rows: ManagementIncidentTimelineItem[];
    incidents: ManagementIncidentTimelineItem[];
    events: ManagementIncidentTimelineItem[];
    summary: ManagementIncidentTimelineSummary;
    severityBuckets: Record<string, number>;
    severity_buckets: Record<string, number>;
    [key: string]: unknown;
  };
  items: ManagementIncidentTimelineItem[];
  rows: ManagementIncidentTimelineItem[];
  incidents: ManagementIncidentTimelineItem[];
  events: ManagementIncidentTimelineItem[];
  summary: ManagementIncidentTimelineSummary;
  severityBuckets: Record<string, number>;
  severity_buckets: Record<string, number>;
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    surfaces: {
      incident_timeline: ManagementSurfaceRef;
      incidents?: ManagementSurfaceRef;
      [key: string]: ManagementSurfaceRef | undefined;
    };
    composition_sources?: string[];
    policy?: string;
    [key: string]: unknown;
  };
}

export interface ManagementCostAttributionQuery {
  persona_id?: string;
  strategy_id?: string;
  capital_pool_id?: string;
  period?: string;
  page_token?: string;
  page_size?: number;
}

export interface ManagementLoopThroughputQuery {
  status?: string;
  runtime_id?: string;
  page_token?: string;
  page_size?: number;
}

export interface ManagementCostAttributionRow {
  id: string;
  costId: string;
  cost_id: string;
  capitalPoolId?: string | null;
  capital_pool_id?: string | null;
  capitalPoolName?: string | null;
  capital_pool_name?: string | null;
  personaId?: string | null;
  persona_id?: string | null;
  personaLabel?: string | null;
  persona_label?: string | null;
  strategyId?: string | null;
  strategy_id?: string | null;
  strategyLabel?: string | null;
  strategy_label?: string | null;
  runtimeIds?: string[];
  runtime_ids?: string[];
  totalCost?: number | null;
  total_cost?: number | null;
  commissionCost?: number | null;
  commission_cost?: number | null;
  slippageCost?: number | null;
  slippage_cost?: number | null;
  infrastructureCost?: number | null;
  infrastructure_cost?: number | null;
  allocatedCapital?: number | null;
  allocated_capital?: number | null;
  riskBudget?: number | null;
  risk_budget?: number | null;
  totalTrades?: number;
  total_trades?: number;
  totalNotional?: number | null;
  total_notional?: number | null;
  avgSlippageBps?: number | null;
  avg_slippage_bps?: number | null;
  latestAt?: string | null;
  latest_at?: string | null;
  costBasis?: string;
  cost_basis?: string;
  sourceRefs?: Record<string, unknown>;
  links?: Record<string, string | null | undefined>;
  [key: string]: unknown;
}

export interface ManagementLoopThroughputItem {
  id: string;
  loopRunId: string;
  loop_run_id: string;
  sequence: number;
  rank: number;
  status: string;
  runtimeId?: string | null;
  runtime_id?: string | null;
  bindingId?: string | null;
  binding_id?: string | null;
  incidentId?: string | null;
  incident_id?: string | null;
  queuedAt?: string | null;
  queued_at?: string | null;
  startedAt?: string | null;
  started_at?: string | null;
  completedAt?: string | null;
  completed_at?: string | null;
  eventAt?: string | null;
  event_at?: string | null;
  queueLagSeconds?: number | null;
  queue_lag_seconds?: number | null;
  durationSeconds?: number | null;
  duration_seconds?: number | null;
  sourceRefs?: Record<string, unknown>;
  source_refs?: Record<string, unknown>;
  links?: Record<string, string | null | undefined>;
  [key: string]: unknown;
}

export interface ManagementCostAttributionSummary {
  rowCount: number;
  row_count: number;
  returnedRowCount: number;
  returned_row_count: number;
  capitalPoolCount: number;
  capital_pool_count: number;
  personaCount: number;
  persona_count: number;
  strategyCount: number;
  strategy_count: number;
  totalCost?: number | null;
  total_cost?: number | null;
  totalCommissionCost?: number | null;
  total_commission_cost?: number | null;
  totalSlippageCost?: number | null;
  total_slippage_cost?: number | null;
  totalInfrastructureCost?: number | null;
  total_infrastructure_cost?: number | null;
  period: string;
  policy: string;
  basis: string;
  [key: string]: unknown;
}

export interface ManagementCostAttributionResponse {
  data: {
    id: "management-cost-attribution" | string;
    items: ManagementCostAttributionRow[];
    summary: ManagementCostAttributionSummary;
    policy?: string;
    [key: string]: unknown;
  };
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    surfaces: {
      cost_attribution: ManagementSurfaceRef;
      runtime_bindings?: ManagementSurfaceRef;
      deployment_plans?: ManagementSurfaceRef;
      persona_bindings?: ManagementSurfaceRef;
      capital_pools?: ManagementSurfaceRef;
      strategies?: ManagementSurfaceRef;
      telemetry_summaries?: ManagementSurfaceRef;
      [key: string]: ManagementSurfaceRef | undefined;
    };
    composition_sources?: string[];
    policy?: string;
    filters?: Record<string, unknown>;
    [key: string]: unknown;
  };
}

export interface ManagementLoopThroughputSummary {
  loopCount: number;
  loop_count: number;
  returnedLoopCount: number;
  returned_loop_count: number;
  runtimeCount: number;
  runtime_count: number;
  queueDepth: number;
  queue_depth: number;
  activeLoopCount: number;
  active_loop_count: number;
  completedLoopCount: number;
  completed_loop_count: number;
  failedLoopCount: number;
  failed_loop_count: number;
  runsPerMinute: number;
  runs_per_minute: number;
  completedRunsPerMinute: number;
  completed_runs_per_minute: number;
  observedWindowMinutes?: number | null;
  observed_window_minutes?: number | null;
  averageQueueLagSeconds?: number | null;
  average_queue_lag_seconds?: number | null;
  maxQueueLagSeconds?: number | null;
  max_queue_lag_seconds?: number | null;
  queueLagSampleCount: number;
  queue_lag_sample_count: number;
  byStatus: Record<string, number>;
  by_status: Record<string, number>;
  latestLoopAt?: string | null;
  latest_loop_at?: string | null;
  basis?: string;
  [key: string]: unknown;
}

export interface ManagementLoopThroughputResponse {
  data: {
    id: "management-loop-throughput" | string;
    items: ManagementLoopThroughputItem[];
    rows: ManagementLoopThroughputItem[];
    loops: ManagementLoopThroughputItem[];
    summary: ManagementLoopThroughputSummary;
    metrics: ManagementLoopThroughputSummary;
    [key: string]: unknown;
  };
  items: ManagementLoopThroughputItem[];
  rows: ManagementLoopThroughputItem[];
  loops: ManagementLoopThroughputItem[];
  summary: ManagementLoopThroughputSummary;
  metrics: ManagementLoopThroughputSummary;
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    surfaces: {
      loop_throughput: ManagementSurfaceRef;
      loop_runs?: ManagementSurfaceRef;
      incidents?: ManagementSurfaceRef;
      [key: string]: ManagementSurfaceRef | undefined;
    };
    composition_sources?: string[];
    policy?: string;
    filters?: Record<string, unknown>;
    [key: string]: unknown;
  };
}

export interface ManagementPortfolioBookExposureQuery {
  status?: string;
  risk_policy_ref?: string;
  capital_pool_id?: string;
  page_token?: string;
  page_size?: number;
}

export type ManagementPortfolioBookExposureRiskState =
  | "within_budget"
  | "near_limit"
  | "over_budget"
  | "unknown"
  | string;

export interface ManagementPortfolioBookExposureItem {
  id: string;
  pool_id: string;
  capital_pool_id?: string;
  capitalPoolId?: string;
  name?: string;
  status?: string;
  risk_policy_ref?: string;
  riskPolicyRef?: string;
  currency?: string;
  risk_budget?: number | null;
  riskBudget?: number | null;
  current_exposure?: number | null;
  currentExposure?: number | null;
  exposure_amount?: number | null;
  exposureAmount?: number | null;
  available_budget?: number | null;
  availableBudget?: number | null;
  risk_budget_utilization?: number | null;
  riskBudgetUtilization?: number | null;
  risk_state?: ManagementPortfolioBookExposureRiskState;
  riskState?: ManagementPortfolioBookExposureRiskState;
  exposure_source?: string | null;
  exposureSource?: string | null;
  exposure?: Record<string, unknown>;
  risk?: Record<string, unknown>;
  pnl?: number | null;
  total_pnl?: number | null;
  pnl_summary?: Record<string, unknown>;
  telemetry?: Record<string, unknown>;
  binding_count?: number;
  active_binding_count?: number;
  deployment_count?: number;
  approved_deployment_count?: number;
  runtime_count?: number;
  active_runtime_count?: number;
  paper_runtime_count?: number;
  live_runtime_count?: number;
  deployment_stages?: string[];
  sourceRefs?: Record<string, unknown>;
  source_refs?: Record<string, unknown>;
  links?: Record<string, string | null | undefined>;
  [key: string]: unknown;
}

export interface ManagementPortfolioBookExposureSummary {
  exposure_count: number;
  exposureCount?: number;
  returned_exposure_count: number;
  returnedExposureCount?: number;
  total_pools: number;
  totalPools?: number;
  active_pool_count: number;
  activePoolCount?: number;
  risk_budget_total?: number | null;
  riskBudgetTotal?: number | null;
  current_exposure_total?: number | null;
  currentExposureTotal?: number | null;
  available_budget_total?: number | null;
  availableBudgetTotal?: number | null;
  risk_budget_utilization?: number | null;
  riskBudgetUtilization?: number | null;
  over_budget_count: number;
  overBudgetCount?: number;
  near_limit_count: number;
  nearLimitCount?: number;
  unknown_exposure_count: number;
  unknownExposureCount?: number;
  telemetry_runtime_count?: number;
  telemetryRuntimeCount?: number;
  total_pnl?: number | null;
  totalPnl?: number | null;
  max_drawdown?: number | null;
  maxDrawdown?: number | null;
  average_fill_rate?: number | null;
  averageFillRate?: number | null;
  total_trades?: number;
  totalTrades?: number;
  latest_telemetry_at?: string | null;
  latestTelemetryAt?: string | null;
  basis?: string;
  [key: string]: unknown;
}

export interface ManagementPortfolioBookExposureResponse {
  data: {
    id: string;
    summary: ManagementPortfolioBookExposureSummary;
    items: ManagementPortfolioBookExposureItem[];
    exposures: ManagementPortfolioBookExposureItem[];
    [key: string]: unknown;
  };
  items: ManagementPortfolioBookExposureItem[];
  exposures: ManagementPortfolioBookExposureItem[];
  summary: ManagementPortfolioBookExposureSummary;
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  meta: {
    snapshot_at?: string;
    staleness?: Record<string, unknown>;
    surfaces?: Record<string, ManagementSurfaceRef>;
    composition_sources?: string[];
    policy?: string;
    [key: string]: unknown;
  };
}

type ManagementQueryValue = string | number | boolean | undefined | null;

function withQuery(path: string, query?: object): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query as Record<string, ManagementQueryValue>)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const suffix = params.toString();
  return suffix ? `${path}?${suffix}` : path;
}

export function managementCockpitPath(): string {
  return paths.managementCockpit();
}

export function managementBoardPackPath(query?: ManagementBoardPackQuery): string {
  return withQuery(paths.managementBoardPack(), query);
}

export function managementGovernanceLedgerPath(
  query?: ManagementGovernanceLedgerQuery,
): string {
  return withQuery(paths.managementGovernanceLedger(), query);
}

export function managementHiqBacklogPath(query?: ManagementHiqBacklogQuery): string {
  return withQuery(paths.managementHiqBacklog(), query);
}

export function managementInterventionStreamPath(
  query?: ManagementInterventionStreamQuery,
): string {
  return withQuery(paths.managementInterventionStream(), query);
}

export function managementTradingPulsePath(): string {
  return paths.managementTradingPulse();
}

export function managementTradingPulseRankingsPath(
  query?: ManagementTradingPulseRankingsQuery,
): string {
  return withQuery(paths.managementTradingPulseRankings(), query);
}

export function managementSentinelPulsePath(query?: ManagementSentinelPulseQuery): string {
  return withQuery(paths.managementSentinelPulse(), query);
}

export function managementEvidencePath(query?: ManagementEvidenceQuery): string {
  return withQuery(paths.managementEvidence(), query);
}

export function managementEvolutionJournalPath(query?: ManagementEvolutionJournalQuery): string {
  return withQuery(paths.managementEvolutionJournal(), query);
}

export function managementPersonaIntentPath(query?: ManagementPersonaIntentQuery): string {
  return withQuery(paths.managementPersonaIntent(), query);
}

export function managementReadinessEp5Path(): string {
  return paths.managementReadinessEp5();
}

export function managementReadinessBrokerLivePath(): string {
  return paths.managementReadinessBrokerLive();
}

export function managementReadinessCapitalBindingLivePath(): string {
  return paths.managementReadinessCapitalBindingLive();
}

export function managementReadinessBffHaPath(): string {
  return paths.managementReadinessBffHa();
}

export function managementReadinessStrictPublishPath(): string {
  return paths.managementReadinessStrictPublish();
}

export function managementStrategyAllocationPath(
  query?: ManagementStrategyAllocationQuery,
): string {
  return withQuery(paths.managementStrategyAllocation(), query);
}

export function managementCapitalFlowPath(query?: ManagementCapitalFlowQuery): string {
  return withQuery(paths.managementCapitalFlow(), query);
}

export function managementRiskRadarPath(query?: ManagementRiskRadarQuery): string {
  return withQuery(paths.managementRiskRadar(), query);
}

export function managementIncidentTimelinePath(
  query?: ManagementIncidentTimelineQuery,
): string {
  return withQuery(paths.managementIncidentTimeline(), query);
}

export function managementCostAttributionPath(
  query?: ManagementCostAttributionQuery,
): string {
  return withQuery(paths.managementCostAttribution(), query);
}

export function managementLoopThroughputPath(
  query?: ManagementLoopThroughputQuery,
): string {
  return withQuery(paths.managementLoopThroughput(), query);
}

export function managementPortfolioBookPath(): string {
  return paths.managementPortfolioBook();
}

export function managementPortfolioBookPoolsPath(query?: ManagementPortfolioBookPoolQuery): string {
  return withQuery(paths.managementPortfolioBookPools(), query);
}

export function managementPortfolioBookExposurePath(
  query?: ManagementPortfolioBookExposureQuery,
): string {
  return withQuery(paths.managementPortfolioBookExposure(), query);
}

export function managementPortfolioBookHoldingsPath(
  query?: ManagementPortfolioBookHoldingsQuery,
): string {
  return withQuery(paths.managementPortfolioBookHoldings(), query);
}

export function managementPortfolioBookPositionsPath(
  query?: ManagementPortfolioBookPositionsQuery,
): string {
  return withQuery(paths.managementPortfolioBookPositions(), query);
}

export function managementPersonaLeaguePath(query?: ManagementPersonaLeagueQuery): string {
  return withQuery(paths.managementPersonaLeague(), query);
}

export function managementPersonaLeagueMoversPath(
  query?: ManagementPersonaLeagueMoversQuery,
): string {
  return withQuery(paths.managementPersonaLeagueMovers(), query);
}

export function managementPersonaLeagueRankingsPath(
  query?: ManagementPersonaLeagueRankingsQuery,
): string {
  return withQuery(paths.managementPersonaLeagueRankings(), query);
}

export function managementPersonaLeagueTiersPath(
  query?: ManagementPersonaLeagueTiersQuery,
): string {
  return withQuery(paths.managementPersonaLeagueTiers(), query);
}

export function managementPersonaLeagueHeatmapPath(
  query?: ManagementPersonaLeagueHeatmapQuery,
): string {
  return withQuery(paths.managementPersonaLeagueHeatmap(), query);
}

export function managementQuarterlyRankingPath(
  query?: ManagementQuarterlyRankingQuery,
): string {
  return withQuery(paths.managementQuarterlyRanking(), query);
}

export function managementQuarterlyRankingDrilldownPath(
  query: ManagementQuarterlyRankingDrilldownQuery,
): string {
  return withQuery(paths.managementQuarterlyRankingDrilldown(), query);
}

export function managementQuarterlyRankingFormulaPath(): string {
  return paths.managementQuarterlyRankingFormula();
}

export function managementQuarterlyRankingRecommendationsPath(
  query?: ManagementQuarterlyRankingRecommendationsQuery,
): string {
  return withQuery(paths.managementQuarterlyRankingRecommendations(), query);
}

export function managementPerformanceAttributionPath(
  query?: ManagementPerformanceAttributionQuery,
): string {
  return withQuery(paths.managementPerformanceAttribution(), query);
}

export function managementPerformanceAttributionByStrategyPath(
  query?: ManagementPerformanceAttributionByStrategyQuery,
): string {
  return withQuery(paths.managementPerformanceAttributionByStrategy(), query);
}

export function managementPerformanceAttributionByPersonaPath(
  query?: ManagementPerformanceAttributionByPersonaQuery,
): string {
  return withQuery(paths.managementPerformanceAttributionByPersona(), query);
}

export function managementPerformanceAttributionByPoolPath(
  query?: ManagementPerformanceAttributionByPoolQuery,
): string {
  return withQuery(paths.managementPerformanceAttributionByPool(), query);
}

export async function fetchManagementCockpit(
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementCockpitResponse> {
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${managementCockpitPath()}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${managementCockpitPath()} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementCockpitResponse>;
}

export async function fetchManagementBoardPack(
  query?: ManagementBoardPackQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementBoardPackResponse> {
  const path = managementBoardPackPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementBoardPackResponse>;
}

export async function fetchManagementGovernanceLedger(
  query?: ManagementGovernanceLedgerQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementGovernanceLedgerResponse> {
  const path = managementGovernanceLedgerPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementGovernanceLedgerResponse>;
}

export async function fetchManagementHiqBacklog(
  query?: ManagementHiqBacklogQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementHiqBacklogResponse> {
  const path = managementHiqBacklogPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementHiqBacklogResponse>;
}

export async function fetchManagementInterventionStream(
  query?: ManagementInterventionStreamQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementInterventionStreamResponse> {
  const path = managementInterventionStreamPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementInterventionStreamResponse>;
}

export async function fetchManagementTradingPulse(
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementTradingPulseResponse> {
  const path = managementTradingPulsePath();
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementTradingPulseResponse>;
}

export async function fetchManagementTradingPulseRankings(
  query?: ManagementTradingPulseRankingsQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementTradingPulseRankingsResponse> {
  const path = managementTradingPulseRankingsPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementTradingPulseRankingsResponse>;
}

export async function fetchManagementSentinelPulse(
  query?: ManagementSentinelPulseQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementSentinelPulseResponse> {
  const path = managementSentinelPulsePath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementSentinelPulseResponse>;
}

export async function fetchManagementEvidence(
  query?: ManagementEvidenceQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementEvidenceResponse> {
  const path = managementEvidencePath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementEvidenceResponse>;
}

export async function fetchManagementEvolutionJournal(
  query?: ManagementEvolutionJournalQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementEvolutionJournalResponse> {
  const path = managementEvolutionJournalPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementEvolutionJournalResponse>;
}

export async function fetchManagementPersonaIntent(
  query?: ManagementPersonaIntentQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementPersonaIntentResponse> {
  const path = managementPersonaIntentPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementPersonaIntentResponse>;
}

export async function fetchManagementReadiness(
  path: string,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementReadinessResponse> {
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementReadinessResponse>;
}

export async function fetchManagementReadinessEp5(
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementReadinessResponse> {
  return fetchManagementReadiness(managementReadinessEp5Path(), init, baseUrl);
}

export async function fetchManagementReadinessBrokerLive(
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementReadinessResponse> {
  return fetchManagementReadiness(managementReadinessBrokerLivePath(), init, baseUrl);
}

export async function fetchManagementReadinessCapitalBindingLive(
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementReadinessResponse> {
  return fetchManagementReadiness(managementReadinessCapitalBindingLivePath(), init, baseUrl);
}

export async function fetchManagementReadinessBffHa(
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementReadinessResponse> {
  return fetchManagementReadiness(managementReadinessBffHaPath(), init, baseUrl);
}

export async function fetchManagementReadinessStrictPublish(
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementReadinessResponse> {
  return fetchManagementReadiness(managementReadinessStrictPublishPath(), init, baseUrl);
}

export async function fetchManagementStrategyAllocation(
  query?: ManagementStrategyAllocationQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementStrategyAllocationResponse> {
  const path = managementStrategyAllocationPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementStrategyAllocationResponse>;
}

export async function fetchManagementCapitalFlow(
  query?: ManagementCapitalFlowQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementCapitalFlowResponse> {
  const path = managementCapitalFlowPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementCapitalFlowResponse>;
}

export async function fetchManagementRiskRadar(
  query?: ManagementRiskRadarQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementRiskRadarResponse> {
  const path = managementRiskRadarPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementRiskRadarResponse>;
}

export async function fetchManagementIncidentTimeline(
  query?: ManagementIncidentTimelineQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementIncidentTimelineResponse> {
  const path = managementIncidentTimelinePath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementIncidentTimelineResponse>;
}

export async function fetchManagementLoopThroughput(
  query?: ManagementLoopThroughputQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementLoopThroughputResponse> {
  const path = managementLoopThroughputPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementLoopThroughputResponse>;
}

export async function fetchManagementPortfolioBookPools(
  query?: ManagementPortfolioBookPoolQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementPortfolioBookPoolsResponse> {
  const path = managementPortfolioBookPoolsPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementPortfolioBookPoolsResponse>;
}

export async function fetchManagementPortfolioBookExposure(
  query?: ManagementPortfolioBookExposureQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementPortfolioBookExposureResponse> {
  const path = managementPortfolioBookExposurePath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementPortfolioBookExposureResponse>;
}

export async function fetchManagementPortfolioBookHoldings(
  query?: ManagementPortfolioBookHoldingsQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementPortfolioBookHoldingsResponse> {
  const path = managementPortfolioBookHoldingsPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementPortfolioBookHoldingsResponse>;
}

export async function fetchManagementPortfolioBookPositions(
  query?: ManagementPortfolioBookPositionsQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementPortfolioBookPositionsResponse> {
  const path = managementPortfolioBookPositionsPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementPortfolioBookPositionsResponse>;
}

export async function fetchManagementPersonaLeague(
  query?: ManagementPersonaLeagueQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementPersonaLeagueResponse> {
  const path = managementPersonaLeaguePath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementPersonaLeagueResponse>;
}

export async function fetchManagementPersonaLeagueMovers(
  query?: ManagementPersonaLeagueMoversQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementPersonaLeagueMoversResponse> {
  const path = managementPersonaLeagueMoversPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementPersonaLeagueMoversResponse>;
}

export async function fetchManagementPersonaLeagueRankings(
  query?: ManagementPersonaLeagueRankingsQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementPersonaLeagueRankingsResponse> {
  const path = managementPersonaLeagueRankingsPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementPersonaLeagueRankingsResponse>;
}

export async function fetchManagementPersonaLeagueTiers(
  query?: ManagementPersonaLeagueTiersQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementPersonaLeagueTiersResponse> {
  const path = managementPersonaLeagueTiersPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementPersonaLeagueTiersResponse>;
}

export async function fetchManagementPersonaLeagueHeatmap(
  query?: ManagementPersonaLeagueHeatmapQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementPersonaLeagueHeatmapResponse> {
  const path = managementPersonaLeagueHeatmapPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementPersonaLeagueHeatmapResponse>;
}

export async function fetchManagementQuarterlyRanking(
  query?: ManagementQuarterlyRankingQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementQuarterlyRankingResponse> {
  const path = managementQuarterlyRankingPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementQuarterlyRankingResponse>;
}

export async function fetchManagementQuarterlyRankingDrilldown(
  query: ManagementQuarterlyRankingDrilldownQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementQuarterlyRankingDrilldownResponse> {
  const path = managementQuarterlyRankingDrilldownPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementQuarterlyRankingDrilldownResponse>;
}

export async function fetchManagementQuarterlyRankingFormula(
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementQuarterlyRankingFormulaResponse> {
  const path = managementQuarterlyRankingFormulaPath();
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementQuarterlyRankingFormulaResponse>;
}

export async function fetchManagementQuarterlyRankingRecommendations(
  query?: ManagementQuarterlyRankingRecommendationsQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementQuarterlyRankingRecommendationsResponse> {
  const path = managementQuarterlyRankingRecommendationsPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementQuarterlyRankingRecommendationsResponse>;
}

export async function fetchManagementPerformanceAttribution(
  query?: ManagementPerformanceAttributionQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementPerformanceAttributionResponse> {
  const path = managementPerformanceAttributionPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementPerformanceAttributionResponse>;
}

export async function fetchManagementPerformanceAttributionByStrategy(
  query?: ManagementPerformanceAttributionByStrategyQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementPerformanceAttributionResponse> {
  const path = managementPerformanceAttributionByStrategyPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementPerformanceAttributionResponse>;
}

export async function fetchManagementPerformanceAttributionByPersona(
  query?: ManagementPerformanceAttributionByPersonaQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementPerformanceAttributionResponse> {
  const path = managementPerformanceAttributionByPersonaPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementPerformanceAttributionResponse>;
}

export async function fetchManagementPerformanceAttributionByPool(
  query?: ManagementPerformanceAttributionByPoolQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementPerformanceAttributionResponse> {
  const path = managementPerformanceAttributionByPoolPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementPerformanceAttributionResponse>;
}

export async function fetchManagementCostAttribution(
  query?: ManagementCostAttributionQuery,
  init?: RequestInit,
  baseUrl = "",
): Promise<ManagementCostAttributionResponse> {
  const path = managementCostAttributionPath(query);
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    method: "GET",
    headers,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<ManagementCostAttributionResponse>;
}
