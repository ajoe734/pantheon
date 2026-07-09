import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  BarChart3,
  BriefcaseBusiness,
  DollarSign,
  LineChart,
  Medal,
  RefreshCw,
  TrendingUp,
  Trophy,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { managementClient, type PersonaFleetAggregate, type PersonaFleetItem } from "@/lib/bff/client";
import * as managementApi from "@/lib/bff-v1/management";
import type {
  ManagementCostAttributionResponse,
  ManagementCostAttributionRow,
  ManagementPerformanceAttributionResponse,
  ManagementPerformanceAttributionRow,
  ManagementPersonaLeagueMoversResponse,
  ManagementPersonaLeagueResponse,
  ManagementPersonaLeagueRankingsResponse,
  ManagementPersonaLeagueRankingBlock,
  ManagementPersonaLeagueRankingItem,
  ManagementPortfolioBookExposureItem,
  ManagementPortfolioBookExposureResponse,
  ManagementPortfolioBookPosition,
  ManagementPortfolioBookPositionsResponse,
  ManagementQuarterlyRankingItem,
  ManagementQuarterlyRankingResponse,
  ManagementSurfaceRef,
  ManagementTradingPulseRankingsResponse,
  ManagementTradingPulseResponse,
  ManagementTradingPulseRuntimeRow,
  ManagementOperationsReadModelResponse,
} from "@/lib/bff-v1/management";
import { ManagementDenseTable } from "@/management/components/dense-table";
import {
  DATA_CONFIDENCE_EMPTY_COPY,
  DATA_CONFIDENCE_LABELS,
  DATA_CONFIDENCE_TONE,
  aggregateDataConfidence,
  asManagementRecord,
  buildPerformanceAttributionHref,
  dataConfidenceFromSurface,
  displayBps as safeDisplayBps,
  displayInteger as safeDisplayInteger,
  displayLabel as safeDisplayLabel,
  displayMoney as safeDisplayMoney,
  displayNumber as safeDisplayNumber,
  displayPercent as safeDisplayPercent,
  displayText as safeDisplayText,
  displayTime as safeDisplayTime,
  displayTitle as safeDisplayTitle,
  finiteNumber,
  firstSurface,
  firstSurfaceSource as firstSurfaceSourceLabel,
  managementField,
  surfaceIssueMessage,
  type ManagementDataConfidenceState,
  cn,
} from "@/lib/utils";

type LoadState = "loading" | "ready" | "error";

export const PERFORMANCE_REVIEW_LIMITS = {
  personaLeague: 8,
  personaMovers: 6,
  quarterlyRanking: 8,
  attribution: 8,
  costAttribution: 8,
  portfolioBook: 8,
  tradingRankings: 5,
} as const;

const REVIEW_PERIOD = "quarter";

interface ReviewIssue {
  source: string;
  message: string;
}

interface ManagementPerformanceReviewSnapshot {
  personaFleet?: PersonaFleetAggregate;
  personaLeague?: ManagementPersonaLeagueResponse;
  personaLeagueRankings?: ManagementPersonaLeagueRankingsResponse;
  personaMovers?: ManagementPersonaLeagueMoversResponse;
  quarterlyRanking?: ManagementQuarterlyRankingResponse;
  attributionByPersona?: ManagementPerformanceAttributionResponse;
  attributionByPool?: ManagementPerformanceAttributionResponse;
  costAttribution?: ManagementCostAttributionResponse;
  portfolioExposure?: ManagementPortfolioBookExposureResponse;
  portfolioPositions?: ManagementPortfolioBookPositionsResponse;
  tradingPulse?: ManagementTradingPulseResponse;
  tradingRankings?: ManagementTradingPulseRankingsResponse;
  operationsReadModel?: ManagementOperationsReadModelResponse;
  issues: ReviewIssue[];
}

type SnapshotKey = Exclude<keyof ManagementPerformanceReviewSnapshot, "issues">;

interface SnapshotTask<T> {
  key: SnapshotKey;
  source: string;
  run: () => Promise<T>;
}

function asRecord(value: unknown): Record<string, unknown> {
  return asManagementRecord(value);
}

function asNumber(value: unknown, fallback = 0): number {
  return finiteNumber(value) ?? fallback;
}

function nullableNumber(value: unknown): number | null {
  return finiteNumber(value);
}

function textFrom(value: unknown, fallback = "-"): string {
  return safeDisplayText(value, fallback);
}

function labelFrom(value: unknown, fallback = "unknown"): string {
  return safeDisplayLabel(value, fallback);
}

function titleFrom(value: unknown, fallback = "Unknown"): string {
  return safeDisplayTitle(value, fallback);
}

function formatInteger(value: unknown): string {
  return safeDisplayInteger(value);
}

function formatNumber(value: unknown, maximumFractionDigits = 2): string {
  return safeDisplayNumber(value, maximumFractionDigits);
}

function formatMoney(value: unknown): string {
  return safeDisplayMoney(value);
}

function formatPercent(value: unknown): string {
  return safeDisplayPercent(value);
}

function formatBps(value: unknown): string {
  return safeDisplayBps(value);
}

function formatTime(value: unknown): string {
  return safeDisplayTime(value);
}

function statusTone(status: unknown): string {
  const normalized = String(status ?? "").toLowerCase();
  if (["ok", "ready", "active", "healthy", "within_budget", "pass", "up"].includes(normalized)) {
    return "bg-status-success/15 text-status-success border-status-success/30";
  }
  if (["watch", "near_limit", "degraded", "pending", "flat", "new"].includes(normalized)) {
    return "bg-status-warning/15 text-status-warning border-status-warning/30";
  }
  if (["error", "failed", "breached", "over_budget", "critical", "down", "unavailable"].includes(normalized)) {
    return "bg-status-failed/15 text-status-failed border-status-failed/30";
  }
  return "bg-muted text-muted-foreground border-border";
}

function DataConfidenceBadge({
  state,
  source,
}: {
  state: ManagementDataConfidenceState;
  source?: unknown;
}) {
  const sourceLabel = labelFrom(source, "");
  return (
    <Badge
      variant="outline"
      className={cn("whitespace-nowrap", DATA_CONFIDENCE_TONE[state])}
      data-confidence-state={state}
      title={sourceLabel ? `${DATA_CONFIDENCE_LABELS[state]}: ${sourceLabel}` : DATA_CONFIDENCE_LABELS[state]}
    >
      {DATA_CONFIDENCE_LABELS[state]}
    </Badge>
  );
}

function safeErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function rowId(prefix: string, value: unknown, index: number): string {
  return `${prefix}-${textFrom(value, String(index)).replace(/[^A-Za-z0-9_-]+/g, "-")}`;
}

function surfaceIssues(source: string, response: { meta?: { surfaces?: Record<string, unknown> } } | undefined): ReviewIssue[] {
  const surfaces = asRecord(response?.meta?.surfaces) as Record<string, ManagementSurfaceRef | undefined>;
  return Object.entries(surfaces)
    .filter(([, surface]) => {
      const status = textFrom(surface?.status, "ok").toLowerCase();
      return status !== "ok" && status !== "ready" && status !== "healthy";
    })
    .map(([key, surface]) => ({
      source: `${source} / ${labelFrom(key)}`,
      message: surfaceIssueMessage(surface),
    }));
}

function allSurfaceIssues(snapshot: ManagementPerformanceReviewSnapshot): ReviewIssue[] {
  return [
    ...surfaceIssues("Persona Fleet", snapshot.personaFleet),
    ...surfaceIssues("Persona league", snapshot.personaLeague),
    ...surfaceIssues("Persona league rankings", snapshot.personaLeagueRankings),
    ...surfaceIssues("Persona movers", snapshot.personaMovers),
    ...surfaceIssues("Quarterly ranking", snapshot.quarterlyRanking),
    ...surfaceIssues("Persona attribution", snapshot.attributionByPersona),
    ...surfaceIssues("Pool attribution", snapshot.attributionByPool),
    ...surfaceIssues("Cost attribution", snapshot.costAttribution),
    ...surfaceIssues("Portfolio exposure", snapshot.portfolioExposure),
    ...surfaceIssues("Portfolio positions", snapshot.portfolioPositions),
    ...surfaceIssues("Trading pulse", snapshot.tradingPulse),
    ...surfaceIssues("Trading rankings", snapshot.tradingRankings),
  ];
}

function firstReviewSurfaceSource(snapshot: ManagementPerformanceReviewSnapshot): string {
  const responses = [
    snapshot.personaFleet,
    snapshot.quarterlyRanking,
    snapshot.personaLeague,
    snapshot.portfolioPositions,
    snapshot.tradingPulse,
  ];
  for (const response of responses) {
    const source = firstSurfaceSourceLabel(response?.meta?.surfaces, "");
    if (source) return source;
  }
  return "BFF";
}

function responseSnapshotAt(snapshot: ManagementPerformanceReviewSnapshot): string {
  return textFrom(
    snapshot.quarterlyRanking?.meta?.snapshot_at
      ?? snapshot.personaLeague?.meta?.snapshot_at
      ?? snapshot.portfolioPositions?.meta?.snapshot_at
      ?? snapshot.tradingPulse?.meta?.snapshot_at,
    "-",
  );
}

function quarterlyItems(snapshot: ManagementPerformanceReviewSnapshot): ManagementQuarterlyRankingItem[] {
  return (snapshot.quarterlyRanking?.data.items ?? []).slice(0, PERFORMANCE_REVIEW_LIMITS.quarterlyRanking);
}

function personaAttributionItems(snapshot: ManagementPerformanceReviewSnapshot): ManagementPerformanceAttributionRow[] {
  return (snapshot.attributionByPersona?.data.items ?? []).slice(0, PERFORMANCE_REVIEW_LIMITS.attribution);
}

function poolAttributionItems(snapshot: ManagementPerformanceReviewSnapshot): ManagementPerformanceAttributionRow[] {
  return (snapshot.attributionByPool?.data.items ?? []).slice(0, PERFORMANCE_REVIEW_LIMITS.attribution);
}

function personaFleetItems(snapshot: ManagementPerformanceReviewSnapshot): PersonaFleetItem[] {
  return (snapshot.personaFleet?.data.items ?? []).slice(0, PERFORMANCE_REVIEW_LIMITS.personaLeague);
}

function costItems(snapshot: ManagementPerformanceReviewSnapshot): ManagementCostAttributionRow[] {
  return (snapshot.costAttribution?.data.items ?? []).slice(0, PERFORMANCE_REVIEW_LIMITS.costAttribution);
}

function positionItems(snapshot: ManagementPerformanceReviewSnapshot): ManagementPortfolioBookPosition[] {
  const topLevelPositions = snapshot.portfolioPositions?.positions ?? [];
  const positions = topLevelPositions.length > 0
    ? topLevelPositions
    : snapshot.portfolioPositions?.data.positions ?? snapshot.portfolioPositions?.data.items ?? [];
  return positions.slice(0, PERFORMANCE_REVIEW_LIMITS.portfolioBook);
}

function exposureItems(snapshot: ManagementPerformanceReviewSnapshot): ManagementPortfolioBookExposureItem[] {
  const topLevelExposures = snapshot.portfolioExposure?.exposures ?? [];
  const exposures = topLevelExposures.length > 0
    ? topLevelExposures
    : snapshot.portfolioExposure?.data.exposures ?? snapshot.portfolioExposure?.data.items ?? [];
  return exposures.slice(0, PERFORMANCE_REVIEW_LIMITS.portfolioBook);
}

function tradingRuntimeRows(snapshot: ManagementPerformanceReviewSnapshot): ManagementTradingPulseRuntimeRow[] {
  return (snapshot.tradingPulse?.data.runtime_rows ?? []).slice(0, PERFORMANCE_REVIEW_LIMITS.portfolioBook);
}

function hasReviewData(snapshot: ManagementPerformanceReviewSnapshot): boolean {
  return [
    snapshot.personaFleet?.data.items,
    snapshot.personaLeague?.data.items,
    snapshot.personaMovers?.data.items,
    snapshot.quarterlyRanking?.data.items,
    snapshot.attributionByPersona?.data.items,
    snapshot.attributionByPool?.data.items,
    snapshot.costAttribution?.data.items,
    snapshot.portfolioExposure?.data.items,
    snapshot.portfolioPositions?.data.items,
    snapshot.tradingPulse?.data.runtime_rows,
    snapshot.tradingPulse?.data.rankings,
    snapshot.tradingRankings?.data.items,
  ].some((items) => Array.isArray(items) && items.length > 0);
}

function snapshotDataConfidence(snapshot: ManagementPerformanceReviewSnapshot | null, state: LoadState): ManagementDataConfidenceState {
  if (state === "error" || !snapshot) return "unavailable";
  const responses = [
    snapshot.personaFleet,
    snapshot.personaLeague,
    snapshot.personaMovers,
    snapshot.quarterlyRanking,
    snapshot.attributionByPersona,
    snapshot.attributionByPool,
    snapshot.costAttribution,
    snapshot.portfolioExposure,
    snapshot.portfolioPositions,
    snapshot.tradingPulse,
    snapshot.tradingRankings,
  ].filter(Boolean);
  if (responses.length === 0) return "unavailable";
  const aggregate = aggregateDataConfidence(
    responses.map((response) => dataConfidenceFromSurface(firstSurface(response?.meta?.surfaces))),
  );
  return snapshot.issues.length > 0 && aggregate === "formal" ? "partial" : aggregate;
}

function normalizeConfidenceState(value: unknown): ManagementDataConfidenceState | undefined {
  const normalized = textFrom(value, "").toLowerCase();
  return ["formal", "partial", "fallback", "degraded", "unavailable"].includes(normalized)
    ? normalized as ManagementDataConfidenceState
    : undefined;
}

export interface ManagementPerformanceReviewFocus {
  personaId?: string;
  runtimeId?: string;
  period: string;
  sourceHint?: string;
  sourceConfidence?: ManagementDataConfidenceState;
  diagnostic: boolean;
}

function performanceFocusFromLocation(): ManagementPerformanceReviewFocus {
  if (typeof window === "undefined") {
    return { period: REVIEW_PERIOD, diagnostic: false };
  }
  const params = new URLSearchParams(window.location.search);
  const sourceConfidence = normalizeConfidenceState(params.get("source_confidence"));
  const mode = textFrom(params.get("mode"), "");
  return {
    personaId: textFrom(params.get("persona_id") || params.get("persona"), "") || undefined,
    runtimeId: textFrom(params.get("runtime_id"), "") || undefined,
    period: textFrom(params.get("period"), REVIEW_PERIOD),
    sourceHint: textFrom(params.get("source_hint"), "") || undefined,
    sourceConfidence,
    diagnostic: mode === "fallback-diagnostic" || sourceConfidence === "fallback",
  };
}

async function settleTask<T>(
  task: SnapshotTask<T>,
): Promise<{ key: SnapshotKey; source: string; value?: T; issue?: ReviewIssue }> {
  try {
    return { key: task.key, source: task.source, value: await task.run() };
  } catch (error) {
    return {
      key: task.key,
      source: task.source,
      issue: { source: task.source, message: safeErrorMessage(error) },
    };
  }
}

export async function loadManagementPerformanceReviewSnapshot(
  focus: ManagementPerformanceReviewFocus = { period: REVIEW_PERIOD, diagnostic: false },
): Promise<ManagementPerformanceReviewSnapshot> {
  const period = textFrom(focus.period, REVIEW_PERIOD);
  const attributionQuery = {
    period,
    page_size: PERFORMANCE_REVIEW_LIMITS.attribution,
    persona_id: focus.personaId,
    runtime_id: focus.runtimeId,
    source_hint: focus.sourceHint,
    source_confidence: focus.sourceConfidence,
  };
  const tasks: SnapshotTask<unknown>[] = [
    {
      key: "personaFleet",
      source: "Persona Fleet",
      run: () => managementClient.personaFleet.list({ page_size: PERFORMANCE_REVIEW_LIMITS.personaLeague }),
    },
    {
      key: "personaLeague",
      source: "Persona league",
      run: () => managementApi.fetchManagementPersonaLeague({ page_size: PERFORMANCE_REVIEW_LIMITS.personaLeague }),
    },
    {
      key: "personaLeagueRankings",
      source: "Persona league rankings",
      run: () => managementApi.fetchManagementPersonaLeagueRankings({ limit: PERFORMANCE_REVIEW_LIMITS.personaLeague }),
    },
    {
      key: "personaMovers",
      source: "Persona movers",
      run: () => managementApi.fetchManagementPersonaLeagueMovers({ limit: PERFORMANCE_REVIEW_LIMITS.personaMovers }),
    },
    {
      key: "quarterlyRanking",
      source: "Quarterly ranking",
      run: () => managementApi.fetchManagementQuarterlyRanking({ page_size: PERFORMANCE_REVIEW_LIMITS.quarterlyRanking }),
    },
    {
      key: "attributionByPersona",
      source: "Persona attribution",
      run: () => managementApi.fetchManagementPerformanceAttributionByPersona(attributionQuery),
    },
    {
      key: "attributionByPool",
      source: "Pool attribution",
      run: () => managementApi.fetchManagementPerformanceAttributionByPool({
        period,
        page_size: PERFORMANCE_REVIEW_LIMITS.attribution,
      }),
    },
    {
      key: "costAttribution",
      source: "Cost attribution",
      run: () => managementApi.fetchManagementCostAttribution({
        period,
        page_size: PERFORMANCE_REVIEW_LIMITS.costAttribution,
      }),
    },
    {
      key: "portfolioExposure",
      source: "Portfolio exposure",
      run: () => managementApi.fetchManagementPortfolioBookExposure({ page_size: PERFORMANCE_REVIEW_LIMITS.portfolioBook }),
    },
    {
      key: "portfolioPositions",
      source: "Portfolio positions",
      run: () => managementApi.fetchManagementPortfolioBookPositions({ page_size: PERFORMANCE_REVIEW_LIMITS.portfolioBook }),
    },
    {
      key: "tradingPulse",
      source: "Trading pulse",
      run: () => managementClient.tradingPulse.list(),
    },
    {
      key: "tradingRankings",
      source: "Trading rankings",
      run: () => managementClient.tradingPulse.rankings({ limit: PERFORMANCE_REVIEW_LIMITS.tradingRankings }),
    },
  ];

  if (focus.personaId) {
    tasks.push({
      key: "operationsReadModel",
      source: "Operations Read Model",
      run: () => managementApi.fetchManagementOperationsReadModel(focus.personaId!, { period }),
    });
  }

  const results = await Promise.all(tasks.map(settleTask));
  const snapshot: ManagementPerformanceReviewSnapshot = { issues: [] };
  for (const result of results) {
    if (result.issue) {
      snapshot.issues.push(result.issue);
    } else {
      snapshot[result.key] = result.value as never;
    }
  }
  snapshot.issues.push(...allSurfaceIssues(snapshot));
  return snapshot;
}

export function ManagementPerformanceReviewPanel({ className }: { className?: string }) {
  const [state, setState] = useState<LoadState>("loading");
  const [snapshot, setSnapshot] = useState<ManagementPerformanceReviewSnapshot | null>(null);
  const [error, setError] = useState<string | undefined>();
  const [leagueCriteria, setLeagueCriteria] = useState<string>("overall");
  const focus = useMemo(() => performanceFocusFromLocation(), []);

  const load = useCallback(async () => {
    setState("loading");
    setError(undefined);
    try {
      const nextSnapshot = await loadManagementPerformanceReviewSnapshot(focus);
      setSnapshot(nextSnapshot);
      const everySourceFailed = nextSnapshot.issues.length >= 12
        && !nextSnapshot.personaFleet
        && !nextSnapshot.personaLeague
        && !nextSnapshot.personaLeagueRankings
        && !nextSnapshot.personaMovers
        && !nextSnapshot.quarterlyRanking
        && !nextSnapshot.attributionByPersona
        && !nextSnapshot.attributionByPool
        && !nextSnapshot.costAttribution
        && !nextSnapshot.portfolioExposure
        && !nextSnapshot.portfolioPositions
        && !nextSnapshot.tradingPulse
        && !nextSnapshot.tradingRankings;
      if (everySourceFailed) {
        setError("Every performance review source failed.");
        setState("error");
      } else {
        setState("ready");
      }
    } catch (err) {
      setSnapshot(null);
      setError(safeErrorMessage(err));
      setState("error");
    }
  }, [focus]);

  useEffect(() => {
    void load();
  }, [load]);

  const issueCount = snapshot?.issues.length ?? 0;
  const panelStatus = state === "error" ? "error" : issueCount > 0 ? "degraded" : "ok";
  const confidenceState = snapshotDataConfidence(snapshot, state);
  const quarter = textFrom(snapshot?.quarterlyRanking?.data.quarter_window?.label ?? snapshot?.quarterlyRanking?.data.summary.quarter, "Current quarter");
  const source = snapshot ? firstReviewSurfaceSource(snapshot) : "BFF";
  const snapshotAt = snapshot ? responseSnapshotAt(snapshot) : "-";
  const hasData = snapshot ? hasReviewData(snapshot) : false;

  return (
    <section className={cn("flex flex-col gap-4", className)} data-testid="management-performance-review-panel">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <Medal className="h-4 w-4 text-primary" />
            <h2 className="text-base font-semibold">Performance Review</h2>
            <Badge variant="outline" className={cn("capitalize", statusTone(panelStatus))}>
              {panelStatus}
            </Badge>
            <DataConfidenceBadge state={confidenceState} source={source} />
            {quarter ? <Badge variant="outline">{quarter}</Badge> : null}
            {focus.personaId ? <Badge variant="outline">Focus {focus.personaId}</Badge> : null}
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>Source: {source}</span>
            <span>Snapshot: {formatTime(snapshotAt)}</span>
            <span>Payload cap: {PERFORMANCE_REVIEW_LIMITS.quarterlyRanking} rows per list</span>
          </div>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          className="inline-flex h-8 items-center gap-2 rounded-md border border-border px-3 text-xs font-medium hover:bg-muted"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", state === "loading" ? "animate-spin" : "")} />
          Refresh
        </button>
      </header>

      {state === "loading" ? (
        <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground" role="status">
          <RefreshCw className="h-4 w-4 animate-spin" />
          Loading performance review
        </div>
      ) : null}

      {state === "error" ? (
        <EmptyState
          icon={<AlertTriangle className="h-8 w-8" />}
          title="Performance review unavailable"
          description={error}
          cta={{ label: "Retry", onClick: load }}
        />
      ) : null}

      {state === "ready" && snapshot && !hasData ? (
        <EmptyState
          icon={<BarChart3 className="h-8 w-8" />}
          title="No performance review data"
          description={DATA_CONFIDENCE_EMPTY_COPY[confidenceState]}
          cta={{ label: "Refresh", onClick: load }}
        />
      ) : null}

      {state === "ready" && snapshot && hasData ? (
        <>
          {issueCount > 0 ? <DegradedBanner issues={snapshot.issues} /> : null}
          <ReviewSummary snapshot={snapshot} />
          <PersonaFleetSection snapshot={snapshot} focus={focus} />
          <QuarterlyRankingSection snapshot={snapshot} />
          <PersonaLeagueSection snapshot={snapshot} criteria={leagueCriteria} setCriteria={setLeagueCriteria} />
          <AttributionSection snapshot={snapshot} focus={focus} />
          <PortfolioBookSection snapshot={snapshot} />
          <CostAttributionSection snapshot={snapshot} />
          <TradingPulseSection snapshot={snapshot} />
        </>
      ) : null}
    </section>
  );
}

function DegradedBanner({ issues }: { issues: ReviewIssue[] }) {
  return (
    <div
      className="rounded-md border border-status-warning/30 bg-status-warning/10 p-3 text-xs"
      data-testid="performance-review-degraded"
    >
      <div className="flex items-center gap-2 font-semibold text-status-warning">
        <AlertTriangle className="h-4 w-4" />
        Degraded sources: {issues.length}
      </div>
      <div className="mt-2 grid gap-1 text-muted-foreground sm:grid-cols-2">
        {issues.slice(0, 6).map((issue) => (
          <div key={`${issue.source}-${issue.message}`}>
            <span className="font-medium text-foreground">{issue.source}:</span> {issue.message}
          </div>
        ))}
      </div>
    </div>
  );
}

function ReviewSummary({ snapshot }: { snapshot: ManagementPerformanceReviewSnapshot }) {
  const quarterlySummary = snapshot.quarterlyRanking?.data.summary;
  const personaSummary = snapshot.personaLeague?.data.summary;
  const fleetSummary = snapshot.personaFleet?.data.summary;
  const exposureSummary = snapshot.portfolioExposure?.summary ?? snapshot.portfolioExposure?.data.summary;
  const positionSummary = snapshot.portfolioPositions?.summary ?? snapshot.portfolioPositions?.data.summary;
  const personaAttributionSummary = snapshot.attributionByPersona?.data.summary;
  const costSummary = snapshot.costAttribution?.data.summary;
  const tradingSummary = snapshot.tradingPulse?.data.summary;

  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4" data-testid="performance-review-summary">
      <SummaryMetric
        icon={<Medal className="h-4 w-4" />}
        label="Quarterly Ranking"
        primary={`${formatInteger(quarterlySummary?.ranked_count)} ranked`}
        secondary={`Formula ${textFrom(quarterlySummary?.formula_version)} / evidence ${formatInteger(quarterlySummary?.evidence_ref_count)}`}
      />
      <SummaryMetric
        icon={<BriefcaseBusiness className="h-4 w-4" />}
        label="Portfolio Book"
        primary={formatMoney(positionSummary?.total_market_value ?? exposureSummary?.current_exposure_total)}
        secondary={`${formatInteger(positionSummary?.active_position_count)} active positions / ${formatInteger(exposureSummary?.over_budget_count)} over budget`}
      />
      <SummaryMetric
        icon={<LineChart className="h-4 w-4" />}
        label="Performance Attribution"
        primary={formatMoney(personaAttributionSummary?.total_pnl)}
        secondary={`${formatInteger(personaAttributionSummary?.total_trades)} trades / drawdown ${formatPercent(personaAttributionSummary?.worst_drawdown)}`}
      />
      <SummaryMetric
        icon={<DollarSign className="h-4 w-4" />}
        label="Cost And Trading"
        primary={formatMoney(costSummary?.total_cost ?? costSummary?.totalCost)}
        secondary={`${formatInteger(tradingSummary?.runtime_count)} runtimes / baseline breaches ${formatInteger(tradingSummary?.baseline_breached_count)}`}
      />
      <SummaryMetric
        icon={<TrendingUp className="h-4 w-4" />}
        label="Persona Fleet"
        primary={`${formatInteger(fleetSummary?.total_personas ?? personaSummary?.persona_count)} personas`}
        secondary={`${formatInteger(fleetSummary?.runtime_bound_personas)} runtime bound / ${formatInteger(fleetSummary?.critical_personas)} critical`}
      />
      <SummaryMetric
        icon={<BarChart3 className="h-4 w-4" />}
        label="Exposure"
        primary={formatPercent(exposureSummary?.risk_budget_utilization ?? exposureSummary?.riskBudgetUtilization)}
        secondary={`${formatMoney(exposureSummary?.current_exposure_total ?? exposureSummary?.currentExposureTotal)} exposure / ${formatInteger(exposureSummary?.near_limit_count)} near limit`}
      />
      <SummaryMetric
        icon={<LineChart className="h-4 w-4" />}
        label="Trading Pulse"
        primary={formatMoney(tradingSummary?.total_pnl)}
        secondary={`${formatInteger(tradingSummary?.total_trades)} trades / fill ${formatPercent(tradingSummary?.average_fill_rate)}`}
      />
      <SummaryMetric
        icon={<DollarSign className="h-4 w-4" />}
        label="Cost Basis"
        primary={textFrom(costSummary?.basis, "unreported")}
        secondary={`Commission ${formatMoney(costSummary?.total_commission_cost ?? costSummary?.totalCommissionCost)} / slippage ${formatMoney(costSummary?.total_slippage_cost ?? costSummary?.totalSlippageCost)}`}
      />
    </div>
  );
}

function SummaryMetric({
  icon,
  label,
  primary,
  secondary,
}: {
  icon: React.ReactNode;
  label: string;
  primary: string;
  secondary: string;
}) {
  return (
    <div className="rounded-md border border-border bg-background p-3">
      <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
        {icon}
        {label}
      </div>
      <div className="mt-2 text-lg font-semibold">{primary}</div>
      <div className="mt-1 text-xs text-muted-foreground">{secondary}</div>
    </div>
  );
}

function personaRuntimeId(row: PersonaFleetItem): string {
  return textFrom(
    row.runtime_id
      ?? managementField(row.performance_summary, ["runtime_id", "runtimeId"])
      ?? managementField(row.binding_summary, ["runtime_id", "runtimeId"]),
    "",
  );
}

function personaFleetLinkConfidence(snapshot: ManagementPerformanceReviewSnapshot): ManagementDataConfidenceState {
  const formalAttributionRows = personaAttributionItems(snapshot).length > 0;
  if (!formalAttributionRows) return "fallback";
  return dataConfidenceFromSurface(firstSurface(snapshot.attributionByPersona?.meta?.surfaces));
}

function PersonaFleetSection({
  snapshot,
  focus,
}: {
  snapshot: ManagementPerformanceReviewSnapshot;
  focus: ManagementPerformanceReviewFocus;
}) {
  const rows = personaFleetItems(snapshot);
  if (rows.length === 0) return null;

  const fleetSurface = firstSurface(snapshot.personaFleet?.meta?.surfaces);
  const linkConfidence = personaFleetLinkConfidence(snapshot);
  const diagnostic = linkConfidence !== "formal";
  const sourceHint = fleetSurface?.source ?? focus.sourceHint ?? "persona_fleet";

  return (
    <PanelSection
      title="Persona Fleet"
      icon={<TrendingUp className="h-4 w-4" />}
      summary={diagnostic ? DATA_CONFIDENCE_EMPTY_COPY.fallback : "Performance links open formal attribution context."}
    >
      <ManagementDenseTable minWidth={920} testId="performance-review-table-persona-fleet">
        <table className="w-full min-w-[920px] text-left text-xs">
          <thead className="border-b border-border text-muted-foreground">
            <tr>
              <th className="px-2 py-2 font-medium">Persona</th>
              <th className="px-2 py-2 font-medium">Health</th>
              <th className="px-2 py-2 font-medium">Runtime</th>
              <th className="px-2 py-2 font-medium">Performance</th>
              <th className="px-2 py-2 font-medium">Attribution Link</th>
              <th className="px-2 py-2 font-medium">Confidence</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => {
              const personaId = textFrom(row.persona_id ?? row.id, "");
              const runtimeId = personaRuntimeId(row);
              const performance = asRecord(row.performance_summary);
              const href = buildPerformanceAttributionHref({
                personaId,
                runtimeId,
                period: focus.period,
                sourceHint,
                sourceConfidence: linkConfidence,
                diagnostic,
              });
              return (
                <tr key={row.id} className="border-b border-border/60" data-testid={rowId("persona-fleet-row", personaId || row.id, index)}>
                  <td className="px-2 py-2">
                    <div className="font-medium">{textFrom(row.name ?? personaId)}</div>
                    <div className="text-muted-foreground">{textFrom(row.owner, "unassigned")} / {textFrom(personaId)}</div>
                  </td>
                  <td className="px-2 py-2">
                    <Badge variant="outline" className={statusTone(row.health ?? row.status)}>
                      {labelFrom(row.health ?? row.status)}
                    </Badge>
                  </td>
                  <td className="px-2 py-2 font-mono">{textFrom(runtimeId, "-")}</td>
                  <td className="px-2 py-2">
                    <div>{formatMoney(managementField(performance, ["total_pnl", "totalPnl", "pnl"]))}</div>
                    <div className="text-muted-foreground">
                      violations {formatInteger(managementField(performance, ["violation_count", "violationCount"]))}
                    </div>
                  </td>
                  <td className="px-2 py-2">
                    <a
                      href={href}
                      className="font-medium text-primary hover:underline"
                      data-testid={`persona-fleet-performance-link-${personaId || row.id}`}
                    >
                      Performance Attribution
                    </a>
                  </td>
                  <td className="px-2 py-2">
                    <DataConfidenceBadge state={linkConfidence} source={sourceHint} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </ManagementDenseTable>
    </PanelSection>
  );
}

function governanceStateTone(state: string | undefined): string {
  switch (state) {
    case "applied receipt":
      return "bg-status-ok/10 border-status-ok/30 text-status-ok";
    case "approved review":
      return "bg-status-ok/10 border-status-ok/30 text-status-ok";
    case "submitted review":
      return "bg-status-warning/10 border-status-warning/30 text-status-warning";
    case "rejected":
      return "bg-status-error/10 border-status-error/30 text-status-error";
    case "blocked":
      return "bg-status-error/10 border-status-error/30 text-status-error animate-pulse";
    case "expired":
      return "bg-status-error/10 border-status-error/30 text-muted-foreground";
    default:
      return "bg-muted/10 border-border text-muted-foreground";
  }
}

function QuarterlyRankingSection({ snapshot }: { snapshot: ManagementPerformanceReviewSnapshot }) {
  const rows = quarterlyItems(snapshot);
  if (rows.length === 0) return null;
  const formula = snapshot.quarterlyRanking?.data.formula;
  return (
    <PanelSection
      title="Quarterly Ranking"
      icon={<Medal className="h-4 w-4" />}
      summary={`Score: ${textFrom(formula?.score_field)} / weight total ${formatPercent(snapshot.quarterlyRanking?.data.summary?.basis === "weighted_score" ? 100 : formulaWeightTotal(formula?.weights))}`}
    >
      <ManagementDenseTable minWidth={920} testId="performance-review-table-quarterly-ranking">
        <table className="w-full min-w-[920px] text-left text-xs">
          <thead className="border-b border-border text-muted-foreground">
            <tr>
              <th className="px-2 py-2 font-medium">Rank</th>
              <th className="px-2 py-2 font-medium">Persona / Links</th>
              <th className="px-2 py-2 font-medium">Tier & Risk</th>
              <th className="px-2 py-2 font-medium">Score & Criteria</th>
              <th className="px-2 py-2 font-medium">Eligibility</th>
              <th className="px-2 py-2 font-medium">Period & Coverage</th>
              <th className="px-2 py-2 font-medium">Gov State</th>
              <th className="px-2 py-2 font-medium">Confidence</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => {
              const attribHref = buildPerformanceAttributionHref({
                personaId: row.persona_id,
                period: "quarter",
                sourceHint: "quarterly_ranking",
                sourceConfidence: (row.source_confidence || "formal") as any,
              });
              const reviewHref = `/management/promotion-allocation?tab=promotion-review&review_id=pm12-${String(row.quarter || "current").toLowerCase()}-${row.persona_id}-promote_to_canary_candidate`;
              return (
                <tr key={row.id} className="border-b border-border/60" data-testid={rowId("quarterly-ranking-row", row.persona_id, index)}>
                  <td className="px-2 py-2 font-mono">{formatInteger(row.rank)}</td>
                  <td className="px-2 py-2">
                    <div className="font-medium">{textFrom(row.name ?? row.persona_id)}</div>
                    <div className="text-muted-foreground text-[10px]">{textFrom(row.owner, "unassigned")}</div>
                    <div className="flex gap-2 mt-1 text-[10px]">
                      <a href={`/management/persona-fleet`} className="text-primary hover:underline font-medium">Fleet</a>
                      <span className="text-border">|</span>
                      <a href={attribHref} className="text-primary hover:underline font-medium">Attribution</a>
                      <span className="text-border">|</span>
                      <a href={reviewHref} className="text-primary hover:underline font-medium">Human Review</a>
                    </div>
                  </td>
                  <td className="px-2 py-2">
                    <div><Badge variant="outline">{textFrom(row.tier_label ?? row.tier)}</Badge></div>
                    <div className="mt-1"><Badge variant="outline" className={statusTone(row.risk)}>{labelFrom(row.risk)}</Badge></div>
                  </td>
                  <td className="px-2 py-2">
                    <div className="font-medium">{formatNumber(row.score ?? row.overall_score)}</div>
                    <div className="text-muted-foreground capitalize text-[10px]">criteria: {textFrom(row.criteria || "overall")}</div>
                  </td>
                  <td className="px-2 py-2">
                    {row.eligible ? (
                      <Badge variant="outline" className="bg-status-ok/10 border-status-ok/30 text-status-ok">Eligible</Badge>
                    ) : (
                      <div>
                        <Badge variant="outline" className="bg-status-error/10 border-status-error/30 text-status-error">Excluded</Badge>
                        {row.exclusion_reason && <div className="text-[10px] text-muted-foreground mt-1 max-w-[150px] truncate" title={String(row.exclusion_reason)}>{String(row.exclusion_reason)}</div>}
                      </div>
                    )}
                  </td>
                  <td className="px-2 py-2">
                    <div className="capitalize">{textFrom(row.period || "quarter")}</div>
                    <div className="text-[10px] text-muted-foreground mt-0.5">coverage: {formatPercent(row.evidence_coverage ?? 1.0)}</div>
                  </td>
                  <td className="px-2 py-2">
                    <Badge variant="outline" className={governanceStateTone(row.governance_state as string)}>
                      {labelFrom(row.governance_state as string, "recommendation")}
                    </Badge>
                  </td>
                  <td className="px-2 py-2">
                    <DataConfidenceBadge state={row.source_confidence as any || "formal"} source="quarterly_ranking" />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </ManagementDenseTable>
    </PanelSection>
  );
}

function PersonaLeagueSection({
  snapshot,
  criteria,
  setCriteria,
}: {
  snapshot: ManagementPerformanceReviewSnapshot;
  criteria: string;
  setCriteria: (c: string) => void;
}) {
  const blocks = snapshot.personaLeagueRankings?.data.items ?? [];
  if (blocks.length === 0) return null;

  const currentBlock = blocks.find((b) => b.criteria === criteria) || blocks[0];
  const rows = currentBlock.items ?? [];

  return (
    <PanelSection
      title="Persona League Rankings"
      icon={<Trophy className="h-4 w-4 text-primary" />}
      summary={`Short-cycle operations ranking. Formula version: ${currentBlock.formula_version}`}
    >
      <div className="flex flex-wrap gap-2 mb-3 border-b border-border/40 pb-2">
        {blocks.map((block) => (
          <button
            key={block.criteria}
            onClick={() => setCriteria(block.criteria)}
            className={cn(
              "px-3 py-1 text-xs rounded-md border transition-all",
              criteria === block.criteria
                ? "bg-primary border-primary text-primary-foreground font-medium"
                : "border-border/60 text-muted-foreground hover:bg-muted/40 hover:text-foreground"
            )}
          >
            {block.label}
          </button>
        ))}
      </div>
      <ManagementDenseTable minWidth={920} testId="performance-review-table-persona-league">
        <table className="w-full min-w-[920px] text-left text-xs">
          <thead className="border-b border-border text-muted-foreground">
            <tr>
              <th className="px-2 py-2 font-medium">Rank</th>
              <th className="px-2 py-2 font-medium">Persona / Links</th>
              <th className="px-2 py-2 font-medium">Tier & Risk</th>
              <th className="px-2 py-2 font-medium">Score & Criteria</th>
              <th className="px-2 py-2 font-medium">Eligibility</th>
              <th className="px-2 py-2 font-medium">Period & Coverage</th>
              <th className="px-2 py-2 font-medium">Confidence</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => {
              const attribHref = buildPerformanceAttributionHref({
                personaId: row.persona_id,
                period: "quarter",
                sourceHint: "persona_league",
                sourceConfidence: (row.source_confidence || "formal") as any,
              });
              const reviewHref = `/management/promotion-allocation?tab=promotion-review&review_id=pm12-current-${row.persona_id}-promote_to_canary_candidate`;
              return (
                <tr key={row.id} className="border-b border-border/60" data-testid={rowId("persona-league-row", row.persona_id, index)}>
                  <td className="px-2 py-2 font-mono">{formatInteger(row.rank)}</td>
                  <td className="px-2 py-2">
                    <div className="font-medium">{textFrom(row.name ?? row.persona_id)}</div>
                    <div className="text-muted-foreground text-[10px]">{textFrom(row.owner, "unassigned")}</div>
                    <div className="flex gap-2 mt-1 text-[10px]">
                      <a href={`/management/persona-fleet`} className="text-primary hover:underline font-medium">Fleet</a>
                      <span className="text-border">|</span>
                      <a href={attribHref} className="text-primary hover:underline font-medium">Attribution</a>
                      <span className="text-border">|</span>
                      <a href={reviewHref} className="text-primary hover:underline font-medium">Human Review</a>
                    </div>
                  </td>
                  <td className="px-2 py-2">
                    <div><Badge variant="outline">{textFrom(row.tier_label ?? row.tier)}</Badge></div>
                    <div className="mt-1"><Badge variant="outline" className={statusTone(row.risk)}>{labelFrom(row.risk)}</Badge></div>
                  </td>
                  <td className="px-2 py-2">
                    <div className="font-medium">{formatNumber(row.score ?? row.overall_score)}</div>
                    <div className="text-muted-foreground text-[10px] capitalize">criteria: {textFrom(row.criteria || criteria)}</div>
                  </td>
                  <td className="px-2 py-2">
                    {row.eligible ? (
                      <Badge variant="outline" className="bg-status-ok/10 border-status-ok/30 text-status-ok">Eligible</Badge>
                    ) : (
                      <div>
                        <Badge variant="outline" className="bg-status-error/10 border-status-error/30 text-status-error">Excluded</Badge>
                        {row.exclusion_reason && <div className="text-[10px] text-muted-foreground mt-1 max-w-[150px] truncate" title={String(row.exclusion_reason)}>{String(row.exclusion_reason)}</div>}
                      </div>
                    )}
                  </td>
                  <td className="px-2 py-2">
                    <div className="capitalize">{textFrom(row.period || "short_cycle")}</div>
                    <div className="text-[10px] text-muted-foreground mt-0.5">coverage: {formatPercent(row.evidence_coverage ?? 1.0)}</div>
                  </td>
                  <td className="px-2 py-2">
                    <DataConfidenceBadge state={row.source_confidence as any || "formal"} source="persona_league" />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </ManagementDenseTable>
    </PanelSection>
  );
}

function formulaWeightTotal(weights: Record<string, number> | undefined): number | null {
  if (!weights) return null;
  return Object.values(weights).reduce((sum, value) => sum + asNumber(value), 0);
}

function componentSummary(components: Record<string, unknown> | undefined): string {
  const entries = Object.entries(asRecord(components))
    .map(([key, value]) => [key, nullableNumber(value)] as const)
    .filter((entry): entry is readonly [string, number] => entry[1] !== null)
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
    .slice(0, 3);
  if (entries.length === 0) return "-";
  return entries.map(([key, value]) => `${labelFrom(key)} ${formatNumber(value)}`).join(" / ");
}

function AttributionSection({
  snapshot,
  focus,
}: {
  snapshot: ManagementPerformanceReviewSnapshot;
  focus: ManagementPerformanceReviewFocus;
}) {
  const personaRows = personaAttributionItems(snapshot);
  const poolRows = poolAttributionItems(snapshot);

  if (personaRows.length === 0 && poolRows.length === 0 && !focus.personaId) return null;

  const summary = snapshot.attributionByPersona?.data.summary ?? snapshot.attributionByPool?.data.summary;
  const readModel = snapshot.operationsReadModel?.data;

  if (focus.personaId) {
    const matchedFormalRows = personaRows.filter(
      (row) => String(row.dimension_key || "").toLowerCase() === String(focus.personaId || "").toLowerCase()
    );

    return (
      <PanelSection
        title="Performance Attribution Drilldown"
        icon={<LineChart className="h-4 w-4" />}
        summary={focus.diagnostic ? "Fallback / degraded diagnostic context active." : "Formal attribution context active."}
      >
        {/* Confidence Banner */}
        <div
          className={cn(
            "p-4 rounded-lg border flex flex-col gap-1 mb-4",
            readModel?.data_confidence === "formal" ? "bg-status-success/15 border-status-success/30 text-status-success" :
            readModel?.data_confidence === "partial" ? "bg-status-warning/15 border-status-warning/30 text-status-warning" :
            readModel?.data_confidence === "fallback" ? "bg-status-warning/15 border-status-warning/30 text-status-warning" :
            "bg-status-failed/15 border-status-failed/30 text-status-failed"
          )}
          data-testid="attribution-confidence-banner"
          data-confidence={readModel?.data_confidence}
        >
          <div className="flex items-center gap-2 font-bold text-sm">
            <AlertTriangle className="h-4 w-4" />
            Confidence Level: {labelFrom(readModel?.data_confidence).toUpperCase()}
          </div>
          <div className="text-xs text-muted-foreground mt-1">
            {readModel?.data_confidence === "formal" && "Formal Performance Attribution: All matching data sources (attribution, holdings, capital pools, runtimes) are present and verified."}
            {readModel?.data_confidence === "partial" && "Partial Performance Attribution: Some matching data is present, but complete telemetry or holdings history is missing."}
            {readModel?.data_confidence === "fallback" && "Fallback Performance Summary: Synthesized from Persona Fleet summary because formal attribution and holdings are missing. Do not treat as formal evidence."}
            {readModel?.data_confidence === "degraded" && "Degraded Performance Diagnostics: One or more data sources are reporting errors or mismatched records."}
            {readModel?.data_confidence === "unavailable" && "No Performance Attribution Available: The selected persona has no matching runtime bindings or recorded activity."}
          </div>
        </div>

        {/* Focus Metadata Panel */}
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4 mb-4" data-testid="attribution-metadata-panel">
          <div className="bg-background p-3 rounded-lg border border-border">
            <div className="text-xs text-muted-foreground font-medium">Focus Persona</div>
            <div className="text-sm font-semibold mt-1 font-mono">{focus.personaId}</div>
            {readModel?.identity.persona_label && (
              <div className="text-xs text-muted-foreground mt-0.5">{readModel.identity.persona_label}</div>
            )}
          </div>
          <div className="bg-background p-3 rounded-lg border border-border">
            <div className="text-xs text-muted-foreground font-medium">Runtime IDs</div>
            <div className="text-sm font-semibold mt-1 font-mono truncate" title={readModel?.identity.runtime_ids.join(", ")}>
              {readModel?.identity.runtime_ids.join(", ") || "-"}
            </div>
          </div>
          <div className="bg-background p-3 rounded-lg border border-border">
            <div className="text-xs text-muted-foreground font-medium">Period / Source Timestamp</div>
            <div className="text-sm font-semibold mt-1">
              {readModel?.identity.period} / {readModel ? formatTime(readModel.identity.as_of) : "-"}
            </div>
          </div>
          <div className="bg-background p-3 rounded-lg border border-border">
            <div className="text-xs text-muted-foreground font-medium">Review Readiness</div>
            <div className="text-sm font-semibold mt-1">
              {readModel?.data_confidence === "formal" ? (
                <span className="text-status-success font-medium">Ready for Review</span>
              ) : (
                <span className="text-status-warning font-medium">Review Not Recommended</span>
              )}
            </div>
            <div className="text-[10px] text-muted-foreground mt-0.5">
              {readModel?.data_confidence === "formal"
                ? "Enough evidence for review (formal match)"
                : "Evidence degraded or missing. Verification required."}
            </div>
          </div>
        </div>

        {/* Split Table Sections */}
        {matchedFormalRows.length > 0 ? (
          <div className="mb-4">
            <h4 className="text-xs font-semibold mb-2">Formal Contribution Rows (Match Count: {matchedFormalRows.length})</h4>
            <ManagementDenseTable minWidth={740} testId="performance-review-table-persona-attribution-formal">
              <table className="w-full min-w-[740px] text-left text-xs">
                <thead className="border-b border-border text-muted-foreground">
                  <tr>
                    <th className="px-2 py-2 font-medium">Rank</th>
                    <th className="px-2 py-2 font-medium">Label</th>
                    <th className="px-2 py-2 font-medium">PnL</th>
                    <th className="px-2 py-2 font-medium">Contribution</th>
                    <th className="px-2 py-2 font-medium">Notional</th>
                    <th className="px-2 py-2 font-medium">Trades</th>
                    <th className="px-2 py-2 font-medium">Telemetry</th>
                  </tr>
                </thead>
                <tbody>
                  {matchedFormalRows.map((row, idx) => (
                    <tr key={row.id} className="border-b border-border/60" data-testid={rowId("formal-attribution-row", row.dimension_key, idx)}>
                      <td className="px-2 py-2 font-mono">{formatInteger(row.rank)}</td>
                      <td className="px-2 py-2">
                        <div className="font-medium">{textFrom(row.label)}</div>
                        <div className="text-muted-foreground">{labelFrom(row.dimension)} / {textFrom(row.dimension_key)}</div>
                      </td>
                      <td className="px-2 py-2 font-medium">{formatMoney(row.total_pnl ?? row.metrics.total_pnl)}</td>
                      <td className="px-2 py-2">{formatPercent(row.pnl_contribution_pct ?? row.metrics.pnl_contribution_pct)}</td>
                      <td className="px-2 py-2">{formatMoney(row.metrics.total_notional)}</td>
                      <td className="px-2 py-2">{formatInteger(row.metrics.total_trades)}</td>
                      <td className="px-2 py-2">{formatTime(row.metrics.latest_telemetry_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </ManagementDenseTable>
          </div>
        ) : (
          <div className="mb-4">
            <h4 className="text-xs font-semibold mb-2">Fallback Summary Rows (Match Count: 0 formal matches, using 1 fleet fallback)</h4>
            <ManagementDenseTable minWidth={740} testId="performance-review-table-persona-attribution-fallback">
              <table className="w-full min-w-[740px] text-left text-xs">
                <thead className="border-b border-border text-muted-foreground">
                  <tr>
                    <th className="px-2 py-2 font-medium">Source</th>
                    <th className="px-2 py-2 font-medium">Label</th>
                    <th className="px-2 py-2 font-medium">PnL</th>
                    <th className="px-2 py-2 font-medium">Sharpe</th>
                    <th className="px-2 py-2 font-medium">Max Drawdown</th>
                    <th className="px-2 py-2 font-medium">Performance Delta</th>
                    <th className="px-2 py-2 font-medium">Score</th>
                  </tr>
                </thead>
                <tbody>
                  <tr className="border-b border-border/60 bg-status-warning/5" data-testid="fallback-attribution-row">
                    <td className="px-2 py-2 font-semibold text-status-warning">Fleet Fallback</td>
                    <td className="px-2 py-2">
                      <div className="font-medium">{readModel?.identity.persona_label || "Persona label unavailable"}</div>
                      <div className="text-muted-foreground">persona_fleet_summary / {focus.personaId}</div>
                    </td>
                    <td className="px-2 py-2 font-medium text-status-warning">
                      {readModel?.performance.pnl !== undefined && readModel?.performance.pnl !== null
                        ? formatMoney(readModel.performance.pnl)
                        : "source returned null"}
                    </td>
                    <td className="px-2 py-2">
                      {readModel?.performance.sharpe !== undefined && readModel?.performance.sharpe !== null
                        ? formatNumber(readModel.performance.sharpe)
                        : "source returned null"}
                    </td>
                    <td className="px-2 py-2">
                      {readModel?.performance.drawdown_pct !== undefined && readModel?.performance.drawdown_pct !== null
                        ? formatPercent(readModel.performance.drawdown_pct)
                        : "source returned null"}
                    </td>
                    <td className="px-2 py-2">
                      {readModel?.performance.performance_delta !== undefined && readModel?.performance.performance_delta !== null
                        ? formatNumber(readModel.performance.performance_delta)
                        : "source returned null"}
                    </td>
                    <td className="px-2 py-2">
                      {readModel?.performance.score !== undefined && readModel?.performance.score !== null
                        ? formatNumber(readModel.performance.score)
                        : "source returned null"}
                    </td>
                  </tr>
                </tbody>
              </table>
            </ManagementDenseTable>
          </div>
        )}

        {/* Source Statuses & Coverage */}
        <div className="mb-4" data-testid="attribution-source-coverage-panel">
          <h4 className="text-xs font-semibold mb-2">Source Statuses & Coverage</h4>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {readModel?.sources.map((src) => (
              <div key={src.source_name} className="p-3 bg-muted/40 rounded-lg border border-border flex flex-col justify-between" data-testid={`source-status-${src.source_name}`}>
                <div className="text-xs font-medium text-muted-foreground truncate">{labelFrom(src.source_name)}</div>
                <div className="flex items-center justify-between mt-2">
                  <Badge variant="outline" className={statusTone(src.source_status)}>
                    {src.source_status}
                  </Badge>
                  <span className="text-xs font-mono">{src.source_row_count ?? 0} rows</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Diagnostics List */}
        {readModel?.diagnostics && readModel.diagnostics.length > 0 ? (
          <div className="mb-2" data-testid="attribution-diagnostics-panel">
            <h4 className="text-xs font-semibold mb-2 text-status-failed">Diagnostics & Data Quality States</h4>
            <div className="grid gap-2">
              {readModel.diagnostics.map((diag) => {
                const isHoldings = diag.code === "MISSING_HOLDINGS_MATCH";
                const isAttribution = diag.code === "MISSING_ATTRIBUTION_MATCH";
                return (
                  <div
                    key={diag.code}
                    className={cn(
                      "p-3 rounded-lg border text-xs",
                      isHoldings ? "bg-status-failed/10 border-status-failed/30 text-status-failed" : "bg-muted/60 border-border"
                    )}
                    data-testid={`diagnostic-card-${diag.code}`}
                  >
                    <div className="font-semibold flex items-center gap-1.5">
                      <AlertTriangle className="h-3.5 w-3.5" />
                      {diag.code}: {labelFrom(diag.source_name)}
                    </div>
                    <div className="mt-1 text-muted-foreground">{diag.message}</div>
                    {isHoldings && (
                      <div className="mt-2 text-[11px] font-medium text-status-failed bg-status-failed/10 p-1.5 rounded" data-testid="actionable-missing-holdings">
                        <strong>Action Required:</strong> Missing portfolio holdings for this persona. Please check if the active runtime binding has registered holdings to the ledger, or verify if the capital pool binding is active.
                      </div>
                    )}
                    {isAttribution && (
                      <div className="mt-2 text-[11px] text-muted-foreground bg-muted p-1.5 rounded">
                        <strong>Recommendation:</strong> Verify if the paper runtime has completed its daily run and written back telemetry to the BFF database.
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        ) : (
          <div className="text-xs text-muted-foreground p-3 bg-muted/20 border border-border rounded-lg" data-testid="attribution-diagnostics-none">
            No data-quality diagnostics triggered.
          </div>
        )}
      </PanelSection>
    );
  }

  return (
    <PanelSection
      title="Performance Attribution"
      icon={<LineChart className="h-4 w-4" />}
      summary={`${formatMoney(summary?.total_pnl)} PnL / ${formatMoney(summary?.total_exposure)} exposure / ${formatInteger(summary?.total_trades)} trades`}
    >
      <div className="grid gap-3 xl:grid-cols-2">
        {personaRows.length > 0 ? (
          <AttributionTable
            title="By Persona"
            rows={personaRows}
            testId="performance-review-table-persona-attribution"
          />
        ) : null}
        {poolRows.length > 0 ? (
          <AttributionTable
            title="By Pool"
            rows={poolRows}
            testId="performance-review-table-pool-attribution"
          />
        ) : null}
      </div>
    </PanelSection>
  );
}

function AttributionTable({
  title,
  rows,
  testId,
}: {
  title: string;
  rows: ManagementPerformanceAttributionRow[];
  testId: string;
}) {
  return (
    <div>
      <h4 className="mb-2 text-xs font-semibold">{title}</h4>
      <ManagementDenseTable minWidth={740} testId={testId}>
        <table className="w-full min-w-[740px] text-left text-xs">
          <thead className="border-b border-border text-muted-foreground">
            <tr>
              <th className="px-2 py-2 font-medium">Rank</th>
              <th className="px-2 py-2 font-medium">Label</th>
              <th className="px-2 py-2 font-medium">PnL</th>
              <th className="px-2 py-2 font-medium">Contribution</th>
              <th className="px-2 py-2 font-medium">Notional</th>
              <th className="px-2 py-2 font-medium">Trades</th>
              <th className="px-2 py-2 font-medium">Telemetry</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => {
              const pnl = row.total_pnl ?? row.metrics.total_pnl;
              const contribution = row.pnl_contribution_pct ?? row.metrics.pnl_contribution_pct;
              const notional = row.metrics.total_notional;
              const trades = row.metrics.total_trades;

              const isPnlNan = pnl === null || isNaN(pnl as number);
              const isNotionalNan = notional === null || isNaN(notional as number);
              const isTradesNan = trades === null || isNaN(trades as number);

              return (
                <tr key={row.id} className="border-b border-border/60" data-testid={rowId("attribution-row", row.dimension_key, index)}>
                  <td className="px-2 py-2 font-mono">{formatInteger(row.rank)}</td>
                  <td className="px-2 py-2">
                    <div className="font-medium">{textFrom(row.label)}</div>
                    <div className="text-muted-foreground">{labelFrom(row.dimension)} / {textFrom(row.dimension_key)}</div>
                  </td>
                  <td className="px-2 py-2 font-medium">
                    {isPnlNan ? <span className="text-status-failed" title="PnL returned NaN or null">source returned null</span> : formatMoney(pnl)}
                  </td>
                  <td className="px-2 py-2">
                    {contribution === null || isNaN(contribution as number) ? "-" : formatPercent(contribution)}
                  </td>
                  <td className="px-2 py-2">
                    {isNotionalNan ? <span className="text-muted-foreground">source returned null</span> : formatMoney(notional)}
                  </td>
                  <td className="px-2 py-2">
                    {isTradesNan ? <span className="text-muted-foreground">source returned null</span> : formatInteger(trades)}
                  </td>
                  <td className="px-2 py-2">{formatTime(row.metrics.latest_telemetry_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </ManagementDenseTable>
    </div>
  );
}

function PortfolioBookSection({ snapshot }: { snapshot: ManagementPerformanceReviewSnapshot }) {
  const positions = positionItems(snapshot);
  const exposures = exposureItems(snapshot);
  if (positions.length === 0 && exposures.length === 0) return null;
  const positionSummary = snapshot.portfolioPositions?.summary ?? snapshot.portfolioPositions?.data.summary;
  const exposureSummary = snapshot.portfolioExposure?.summary ?? snapshot.portfolioExposure?.data.summary;
  return (
    <PanelSection
      title="Portfolio Book"
      icon={<BriefcaseBusiness className="h-4 w-4" />}
      summary={`${formatMoney(positionSummary?.total_market_value)} market value / ${formatPercent(exposureSummary?.risk_budget_utilization)} budget used`}
    >
      <div className="grid gap-3 xl:grid-cols-2">
        {positions.length > 0 ? <PortfolioPositionsTable rows={positions} /> : null}
        {exposures.length > 0 ? <PortfolioExposureTable rows={exposures} /> : null}
      </div>
    </PanelSection>
  );
}

function PortfolioPositionsTable({ rows }: { rows: ManagementPortfolioBookPosition[] }) {
  return (
    <div>
      <h4 className="mb-2 text-xs font-semibold">Positions</h4>
      <ManagementDenseTable minWidth={820} testId="performance-review-table-portfolio-positions">
        <table className="w-full min-w-[820px] text-left text-xs">
          <thead className="border-b border-border text-muted-foreground">
            <tr>
              <th className="px-2 py-2 font-medium">Symbol</th>
              <th className="px-2 py-2 font-medium">Side</th>
              <th className="px-2 py-2 font-medium">Stage</th>
              <th className="px-2 py-2 font-medium">Persona</th>
              <th className="px-2 py-2 font-medium">Quantity</th>
              <th className="px-2 py-2 font-medium">Notional</th>
              <th className="px-2 py-2 font-medium">PnL</th>
              <th className="px-2 py-2 font-medium">Mark</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={row.position_id ?? row.id} className="border-b border-border/60" data-testid={rowId("portfolio-position-row", row.position_id ?? row.id, index)}>
                <td className="px-2 py-2 font-medium">{textFrom(row.symbol ?? row.instrument?.symbol)}</td>
                <td className="px-2 py-2">{labelFrom(row.side)}</td>
                <td className="px-2 py-2">
                  <Badge variant="outline" className={statusTone(row.deployment_stage ?? row.deploymentStage)}>
                    {labelFrom(row.deployment_stage ?? row.deploymentStage, "stage")}
                  </Badge>
                </td>
                <td className="px-2 py-2">{textFrom(row.persona_id ?? row.personaId)}</td>
                <td className="px-2 py-2">{formatNumber(row.quantity, 4)}</td>
                <td className="px-2 py-2">{formatMoney(row.notional ?? row.market_value ?? row.marketValue)}</td>
                <td className="px-2 py-2 font-medium">{formatMoney(row.total_pnl ?? row.unrealized_pnl)}</td>
                <td className="px-2 py-2">{formatTime(row.last_mark_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </ManagementDenseTable>
    </div>
  );
}

function PortfolioExposureTable({ rows }: { rows: ManagementPortfolioBookExposureItem[] }) {
  return (
    <div>
      <h4 className="mb-2 text-xs font-semibold">Exposure</h4>
      <ManagementDenseTable minWidth={820} testId="performance-review-table-portfolio-exposure">
        <table className="w-full min-w-[820px] text-left text-xs">
          <thead className="border-b border-border text-muted-foreground">
            <tr>
              <th className="px-2 py-2 font-medium">Pool</th>
              <th className="px-2 py-2 font-medium">Risk State</th>
              <th className="px-2 py-2 font-medium">Utilization</th>
              <th className="px-2 py-2 font-medium">Exposure</th>
              <th className="px-2 py-2 font-medium">Budget</th>
              <th className="px-2 py-2 font-medium">Available</th>
              <th className="px-2 py-2 font-medium">Runtimes</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={row.id} className="border-b border-border/60" data-testid={rowId("portfolio-exposure-row", row.id, index)}>
                <td className="px-2 py-2">
                  <div className="font-medium">{textFrom(row.name ?? row.capital_pool_id ?? row.capitalPoolId ?? row.pool_id)}</div>
                  <div className="text-muted-foreground">{textFrom(row.risk_policy_ref ?? row.riskPolicyRef, "policy unreported")}</div>
                </td>
                <td className="px-2 py-2">
                  <Badge variant="outline" className={statusTone(row.risk_state ?? row.riskState)}>
                    {labelFrom(row.risk_state ?? row.riskState)}
                  </Badge>
                </td>
                <td className="px-2 py-2">{formatPercent(row.risk_budget_utilization ?? row.riskBudgetUtilization)}</td>
                <td className="px-2 py-2">{formatMoney(row.current_exposure ?? row.currentExposure ?? row.exposure_amount ?? row.exposureAmount)}</td>
                <td className="px-2 py-2">{formatMoney(row.risk_budget ?? row.riskBudget)}</td>
                <td className="px-2 py-2">{formatMoney(row.available_budget ?? row.availableBudget)}</td>
                <td className="px-2 py-2">{formatInteger(row.runtime_count ?? row.active_runtime_count)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </ManagementDenseTable>
    </div>
  );
}

function CostAttributionSection({ snapshot }: { snapshot: ManagementPerformanceReviewSnapshot }) {
  const rows = costItems(snapshot);
  if (rows.length === 0) return null;
  const summary = snapshot.costAttribution?.data.summary;
  return (
    <PanelSection
      title="Cost Attribution"
      icon={<DollarSign className="h-4 w-4" />}
      summary={`${formatMoney(summary?.total_cost ?? summary?.totalCost)} total / ${formatMoney(summary?.total_slippage_cost ?? summary?.totalSlippageCost)} slippage`}
    >
      <ManagementDenseTable minWidth={920} testId="performance-review-table-cost-attribution">
        <table className="w-full min-w-[920px] text-left text-xs">
          <thead className="border-b border-border text-muted-foreground">
            <tr>
              <th className="px-2 py-2 font-medium">Capital Pool</th>
              <th className="px-2 py-2 font-medium">Persona</th>
              <th className="px-2 py-2 font-medium">Strategy</th>
              <th className="px-2 py-2 font-medium">Total Cost</th>
              <th className="px-2 py-2 font-medium">Commission</th>
              <th className="px-2 py-2 font-medium">Slippage</th>
              <th className="px-2 py-2 font-medium">Infrastructure</th>
              <th className="px-2 py-2 font-medium">Trades</th>
              <th className="px-2 py-2 font-medium">Basis</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={row.cost_id ?? row.costId ?? row.id} className="border-b border-border/60" data-testid={rowId("cost-attribution-row", row.cost_id ?? row.id, index)}>
                <td className="px-2 py-2">{textFrom(row.capital_pool_name ?? row.capitalPoolName ?? row.capital_pool_id ?? row.capitalPoolId)}</td>
                <td className="px-2 py-2">{textFrom(row.persona_label ?? row.personaLabel ?? row.persona_id ?? row.personaId)}</td>
                <td className="px-2 py-2">{textFrom(row.strategy_label ?? row.strategyLabel ?? row.strategy_id ?? row.strategyId)}</td>
                <td className="px-2 py-2 font-medium">{formatMoney(row.total_cost ?? row.totalCost)}</td>
                <td className="px-2 py-2">{formatMoney(row.commission_cost ?? row.commissionCost)}</td>
                <td className="px-2 py-2">{formatMoney(row.slippage_cost ?? row.slippageCost)}</td>
                <td className="px-2 py-2">{formatMoney(row.infrastructure_cost ?? row.infrastructureCost)}</td>
                <td className="px-2 py-2">{formatInteger(row.total_trades ?? row.totalTrades)}</td>
                <td className="px-2 py-2">{textFrom(row.cost_basis ?? row.costBasis)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </ManagementDenseTable>
    </PanelSection>
  );
}

function TradingPulseSection({ snapshot }: { snapshot: ManagementPerformanceReviewSnapshot }) {
  const runtimeRows = tradingRuntimeRows(snapshot);
  const rankingBlocks = snapshot.tradingRankings?.data.items ?? [];
  if (runtimeRows.length === 0 && rankingBlocks.length === 0) return null;
  const summary = snapshot.tradingPulse?.data.summary;
  return (
    <PanelSection
      title="Trading Pulse"
      icon={<TrendingUp className="h-4 w-4" />}
      summary={`${formatMoney(summary?.total_pnl)} PnL / ${formatPercent(summary?.average_fill_rate)} fill / ${formatBps(summary?.worst_slippage_bps)} worst slippage`}
    >
      {runtimeRows.length > 0 ? (
        <ManagementDenseTable minWidth={880} testId="performance-review-table-trading-pulse">
          <table className="w-full min-w-[880px] text-left text-xs">
            <thead className="border-b border-border text-muted-foreground">
              <tr>
                <th className="px-2 py-2 font-medium">Runtime</th>
                <th className="px-2 py-2 font-medium">Stage</th>
                <th className="px-2 py-2 font-medium">Status</th>
                <th className="px-2 py-2 font-medium">PnL</th>
                <th className="px-2 py-2 font-medium">Fill</th>
                <th className="px-2 py-2 font-medium">Slippage</th>
                <th className="px-2 py-2 font-medium">Trades</th>
                <th className="px-2 py-2 font-medium">Baseline</th>
                <th className="px-2 py-2 font-medium">Updated</th>
              </tr>
            </thead>
            <tbody>
              {runtimeRows.map((row, index) => {
                const telemetry = asRecord(row.telemetry_summary);
                const baseline = row.baseline_comparison;
                return (
                  <tr key={row.runtime_id ?? row.runtime_binding_id ?? index} className="border-b border-border/60" data-testid={rowId("trading-pulse-row", row.runtime_id ?? row.runtime_binding_id, index)}>
                    <td className="px-2 py-2 font-medium">{textFrom(row.runtime_id ?? row.runtime_binding_id)}</td>
                    <td className="px-2 py-2">{labelFrom(row.deployment_stage)}</td>
                    <td className="px-2 py-2">
                      <Badge variant="outline" className={statusTone(row.status)}>{labelFrom(row.status)}</Badge>
                    </td>
                    <td className="px-2 py-2">{formatMoney(telemetry.pnl ?? telemetry.total_pnl)}</td>
                    <td className="px-2 py-2">{formatPercent(telemetry.fill_rate ?? telemetry.average_fill_rate)}</td>
                    <td className="px-2 py-2">{formatBps(telemetry.avg_slippage_bps ?? telemetry.average_slippage_bps)}</td>
                    <td className="px-2 py-2">{formatInteger(telemetry.total_trades)}</td>
                    <td className="px-2 py-2">
                      <Badge variant="outline" className={statusTone(baseline?.status)}>{labelFrom(baseline?.status)}</Badge>
                    </td>
                    <td className="px-2 py-2">{formatTime(row.last_updated_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </ManagementDenseTable>
      ) : null}

      {rankingBlocks.length > 0 ? (
        <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-3" data-testid="performance-review-trading-rankings">
          {rankingBlocks.slice(0, PERFORMANCE_REVIEW_LIMITS.tradingRankings).map((block) => (
            <div key={block.block_id} className="rounded-md border border-border p-3">
              <div className="text-xs font-semibold">{block.label}</div>
              <div className="mt-1 text-xs text-muted-foreground">
                {labelFrom(block.metric)} / {labelFrom(block.sort_order)}
              </div>
              <div className="mt-2 grid gap-1">
                {block.items.slice(0, 3).map((item) => (
                  <div key={`${block.block_id}-${item.rank}-${item.runtime_id ?? item.runtime_binding_id}`} className="flex items-center justify-between gap-2 text-xs">
                    <span className="font-mono">#{item.rank} {textFrom(item.runtime_id ?? item.runtime_binding_id)}</span>
                    <span>{metricValue(item.ranking_metric ?? block.metric, item.ranking_metric_value ?? item.pnl ?? item.drawdown ?? item.fill_rate)}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </PanelSection>
  );
}

function metricValue(metric: unknown, value: unknown): string {
  const normalized = String(metric ?? "").toLowerCase();
  if (normalized.includes("pnl") || normalized.includes("cost") || normalized.includes("notional")) {
    return formatMoney(value);
  }
  if (normalized.includes("rate") || normalized.includes("drawdown") || normalized.includes("pct")) {
    return formatPercent(value);
  }
  if (normalized.includes("slippage")) {
    return formatBps(value);
  }
  return formatNumber(value);
}

function PanelSection({
  title,
  icon,
  summary,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  summary: string;
  children: React.ReactNode;
}) {
  return (
    <section className="grid gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-primary">{icon}</span>
          <h3 className="text-sm font-semibold">{title}</h3>
        </div>
        <div className="text-xs text-muted-foreground">{summary}</div>
      </div>
      {children}
    </section>
  );
}
