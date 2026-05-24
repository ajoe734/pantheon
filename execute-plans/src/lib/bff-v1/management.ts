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

export interface ManagementPersonaLeagueResponse {
  data: ManagementPersonaLeagueRow[];
  items: ManagementPersonaLeagueRow[];
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
  data: ManagementPersonaLeagueRankingBlock[];
  items: ManagementPersonaLeagueRankingBlock[];
  rankings: ManagementPersonaLeagueRankingBlock[];
  rankingBlocks: ManagementPersonaLeagueRankingBlock[];
  ranking_blocks: ManagementPersonaLeagueRankingBlock[];
  summary: {
    personaCount: number;
    persona_count: number;
    criteria: string[];
    topPersonaId?: string | null;
    top_persona_id?: string | null;
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
  data: ManagementPersonaLeagueTier[];
  items: ManagementPersonaLeagueTier[];
  tiers: ManagementPersonaLeagueTier[];
  assignments: ManagementPersonaLeagueTierAssignment[];
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
    buckets: ManagementPersonaLeagueHeatmapBucket[];
    rows: ManagementPersonaLeagueHeatmapRow[];
    cells: ManagementPersonaLeagueHeatmapCell[];
    summary: Record<string, unknown>;
    formulaVersion: string;
    formula_version: string;
    basis: string;
    [key: string]: unknown;
  };
  items: ManagementPersonaLeagueHeatmapRow[];
  rows: ManagementPersonaLeagueHeatmapRow[];
  buckets: ManagementPersonaLeagueHeatmapBucket[];
  cells: ManagementPersonaLeagueHeatmapCell[];
  summary: Record<string, unknown>;
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
  rankings: ManagementQuarterlyRankingItem[];
  evidenceRefs: ManagementEvidenceItem[];
  evidence_refs: ManagementEvidenceItem[];
  summary: ManagementQuarterlyRankingSummary;
  [key: string]: unknown;
}

export interface ManagementQuarterlyRankingResponse {
  data: ManagementQuarterlyRankingData;
  items: ManagementQuarterlyRankingItem[];
  rankings: ManagementQuarterlyRankingItem[];
  formula: ManagementQuarterlyRankingFormula;
  quarterWindow: ManagementQuarterlyRankingWindow;
  quarter_window: ManagementQuarterlyRankingWindow;
  evidenceRefs: ManagementEvidenceItem[];
  evidence_refs: ManagementEvidenceItem[];
  summary: ManagementQuarterlyRankingSummary;
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
  recommendations: ManagementQuarterlyRankingRecommendationItem[];
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
  items: ManagementQuarterlyRankingRecommendationItem[];
  recommendations: ManagementQuarterlyRankingRecommendationItem[];
  formula: ManagementQuarterlyRankingFormula;
  quarterWindow: ManagementQuarterlyRankingWindow;
  quarter_window: ManagementQuarterlyRankingWindow;
  evidenceRefs: ManagementEvidenceItem[];
  evidence_refs: ManagementEvidenceItem[];
  summary: ManagementQuarterlyRankingRecommendationsSummary;
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

export type ManagementPerformanceAttributionByPersonaQuery = Omit<
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
  data: ManagementEvidenceItem[];
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
  page_info: {
    next_page_token: string | null;
    total: number;
    page_size: number;
  };
  pagination?: {
    next_page_token?: string | null;
    has_more?: boolean;
    page_size?: number;
    [key: string]: unknown;
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
  data: ManagementEvolutionJournalItem[];
  items: ManagementEvolutionJournalItem[];
  summary: ManagementEvolutionJournalSummary;
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
  data: ManagementPersonaIntentItem[];
  items: ManagementPersonaIntentItem[];
  summary: ManagementPersonaIntentSummary;
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

export function managementTradingPulsePath(): string {
  return paths.managementTradingPulse();
}

export function managementTradingPulseRankingsPath(
  query?: ManagementTradingPulseRankingsQuery,
): string {
  return withQuery(paths.managementTradingPulseRankings(), query);
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

export function managementPortfolioBookPath(): string {
  return paths.managementPortfolioBook();
}

export function managementPortfolioBookPoolsPath(query?: ManagementPortfolioBookPoolQuery): string {
  return withQuery(paths.managementPortfolioBookPools(), query);
}

export function managementPortfolioBookHoldingsPath(
  query?: ManagementPortfolioBookHoldingsQuery,
): string {
  return withQuery(paths.managementPortfolioBookHoldings(), query);
}

export function managementPersonaLeaguePath(query?: ManagementPersonaLeagueQuery): string {
  return withQuery(paths.managementPersonaLeague(), query);
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

export function managementPerformanceAttributionByPersonaPath(
  query?: ManagementPerformanceAttributionByPersonaQuery,
): string {
  return withQuery(paths.managementPerformanceAttributionByPersona(), query);
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
