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
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { managementClient } from "@/lib/bff/client";
import * as managementApi from "@/lib/bff-v1/management";
import type {
  ManagementCostAttributionResponse,
  ManagementCostAttributionRow,
  ManagementPerformanceAttributionResponse,
  ManagementPerformanceAttributionRow,
  ManagementPersonaLeagueMoversResponse,
  ManagementPersonaLeagueResponse,
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
} from "@/lib/bff-v1/management";
import { ManagementDenseTable } from "@/management/components/dense-table";
import { cn } from "@/lib/utils";

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
  personaLeague?: ManagementPersonaLeagueResponse;
  personaMovers?: ManagementPersonaLeagueMoversResponse;
  quarterlyRanking?: ManagementQuarterlyRankingResponse;
  attributionByPersona?: ManagementPerformanceAttributionResponse;
  attributionByPool?: ManagementPerformanceAttributionResponse;
  costAttribution?: ManagementCostAttributionResponse;
  portfolioExposure?: ManagementPortfolioBookExposureResponse;
  portfolioPositions?: ManagementPortfolioBookPositionsResponse;
  tradingPulse?: ManagementTradingPulseResponse;
  tradingRankings?: ManagementTradingPulseRankingsResponse;
  issues: ReviewIssue[];
}

type SnapshotKey = Exclude<keyof ManagementPerformanceReviewSnapshot, "issues">;

interface SnapshotTask<T> {
  key: SnapshotKey;
  source: string;
  run: () => Promise<T>;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function asNumber(value: unknown, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function nullableNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function textFrom(value: unknown, fallback = "-"): string {
  const text = String(value ?? "").trim();
  return text || fallback;
}

function labelFrom(value: unknown, fallback = "unknown"): string {
  return textFrom(value, fallback).replace(/_/g, " ");
}

function titleFrom(value: unknown, fallback = "Unknown"): string {
  return labelFrom(value, fallback)
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function numberFrom(record: Record<string, unknown>, keys: string[], fallback = 0): number {
  for (const key of keys) {
    const parsed = nullableNumber(record[key]);
    if (parsed !== null) return parsed;
  }
  return fallback;
}

function nullableFrom(record: Record<string, unknown>, keys: string[]): number | null {
  for (const key of keys) {
    const parsed = nullableNumber(record[key]);
    if (parsed !== null) return parsed;
  }
  return null;
}

function formatInteger(value: unknown): string {
  const parsed = nullableNumber(value);
  if (parsed === null) return "-";
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(parsed);
}

function formatNumber(value: unknown, maximumFractionDigits = 2): string {
  const parsed = nullableNumber(value);
  if (parsed === null) return "-";
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits,
  }).format(parsed);
}

function formatMoney(value: unknown): string {
  const parsed = nullableNumber(value);
  if (parsed === null) return "-";
  const sign = parsed < 0 ? "-" : "";
  const absolute = Math.abs(parsed);
  return `${sign}$${new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(absolute)}`;
}

function formatPercent(value: unknown): string {
  const parsed = nullableNumber(value);
  if (parsed === null) return "-";
  const normalized = Math.abs(parsed) <= 1 ? parsed * 100 : parsed;
  return `${new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(normalized)}%`;
}

function formatBps(value: unknown): string {
  const parsed = nullableNumber(value);
  if (parsed === null) return "-";
  return `${new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(parsed)} bps`;
}

function formatTime(value: unknown): string {
  const text = String(value ?? "").trim();
  if (!text) return "-";
  const parsed = new Date(text);
  if (Number.isNaN(parsed.getTime())) return text;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
  }).format(parsed);
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

function safeErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function rowId(prefix: string, value: unknown, index: number): string {
  return `${prefix}-${textFrom(value, String(index)).replace(/[^A-Za-z0-9_-]+/g, "-")}`;
}

function surfaceIssues(source: string, response: { meta?: { surfaces?: Record<string, ManagementSurfaceRef | undefined> } } | undefined): ReviewIssue[] {
  const surfaces = asRecord(response?.meta?.surfaces) as Record<string, ManagementSurfaceRef | undefined>;
  return Object.entries(surfaces)
    .filter(([, surface]) => {
      const status = String(surface?.status ?? "ok").toLowerCase();
      return status !== "ok" && status !== "ready" && status !== "healthy";
    })
    .map(([key, surface]) => ({
      source: `${source} / ${labelFrom(key)}`,
      message: textFrom(surface?.message ?? surface?.note ?? surface?.source ?? surface?.status, "degraded"),
    }));
}

function allSurfaceIssues(snapshot: ManagementPerformanceReviewSnapshot): ReviewIssue[] {
  return [
    ...surfaceIssues("Persona league", snapshot.personaLeague),
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

function firstSurfaceSource(snapshot: ManagementPerformanceReviewSnapshot): string {
  const responses = [
    snapshot.quarterlyRanking,
    snapshot.personaLeague,
    snapshot.portfolioPositions,
    snapshot.tradingPulse,
  ];
  for (const response of responses) {
    const surfaces = asRecord(response?.meta?.surfaces) as Record<string, ManagementSurfaceRef | undefined>;
    const surface = Object.values(surfaces).find(Boolean);
    if (surface?.source) return labelFrom(surface.source);
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

function summaryCount(value: unknown, fallback = 0): number {
  return asNumber(value, fallback);
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

export async function loadManagementPerformanceReviewSnapshot(): Promise<ManagementPerformanceReviewSnapshot> {
  const tasks: SnapshotTask<unknown>[] = [
    {
      key: "personaLeague",
      source: "Persona league",
      run: () => managementApi.fetchManagementPersonaLeague({ page_size: PERFORMANCE_REVIEW_LIMITS.personaLeague }),
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
      run: () => managementApi.fetchManagementPerformanceAttributionByPersona({
        period: REVIEW_PERIOD,
        page_size: PERFORMANCE_REVIEW_LIMITS.attribution,
      }),
    },
    {
      key: "attributionByPool",
      source: "Pool attribution",
      run: () => managementApi.fetchManagementPerformanceAttributionByPool({
        period: REVIEW_PERIOD,
        page_size: PERFORMANCE_REVIEW_LIMITS.attribution,
      }),
    },
    {
      key: "costAttribution",
      source: "Cost attribution",
      run: () => managementApi.fetchManagementCostAttribution({
        period: REVIEW_PERIOD,
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

  const load = useCallback(async () => {
    setState("loading");
    setError(undefined);
    try {
      const nextSnapshot = await loadManagementPerformanceReviewSnapshot();
      setSnapshot(nextSnapshot);
      const everySourceFailed = nextSnapshot.issues.length >= 10
        && !nextSnapshot.personaLeague
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
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const issueCount = snapshot?.issues.length ?? 0;
  const panelStatus = state === "error" ? "error" : issueCount > 0 ? "degraded" : "ok";
  const quarter = textFrom(snapshot?.quarterlyRanking?.data.quarter_window?.label ?? snapshot?.quarterlyRanking?.data.summary.quarter, "Current quarter");
  const source = snapshot ? firstSurfaceSource(snapshot) : "BFF";
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
            {quarter ? <Badge variant="outline">{quarter}</Badge> : null}
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
          description="The review sources returned successfully, but no ranking, attribution, portfolio, cost, or trading rows were available."
          cta={{ label: "Refresh", onClick: load }}
        />
      ) : null}

      {state === "ready" && snapshot && hasData ? (
        <>
          {issueCount > 0 ? <DegradedBanner issues={snapshot.issues} /> : null}
          <ReviewSummary snapshot={snapshot} />
          <QuarterlyRankingSection snapshot={snapshot} />
          <AttributionSection snapshot={snapshot} />
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
  const exposureSummary = snapshot.portfolioExposure?.summary ?? snapshot.portfolioExposure?.data.summary;
  const positionSummary = snapshot.portfolioPositions?.summary ?? snapshot.portfolioPositions?.data.summary;
  const personaAttributionSummary = snapshot.attributionByPersona?.data.summary;
  const costSummary = snapshot.costAttribution?.data.summary;
  const tradingSummary = snapshot.tradingPulse?.data.summary;
  const moverSummary = snapshot.personaMovers?.data.summary;

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
        label="Persona League"
        primary={`${formatInteger(personaSummary?.persona_count)} personas`}
        secondary={`${formatInteger(moverSummary?.up_count)} up / ${formatInteger(moverSummary?.down_count)} down`}
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
              <th className="px-2 py-2 font-medium">Persona</th>
              <th className="px-2 py-2 font-medium">Tier</th>
              <th className="px-2 py-2 font-medium">Score</th>
              <th className="px-2 py-2 font-medium">Risk</th>
              <th className="px-2 py-2 font-medium">Formula</th>
              <th className="px-2 py-2 font-medium">Top Components</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={row.id} className="border-b border-border/60" data-testid={rowId("quarterly-ranking-row", row.persona_id, index)}>
                <td className="px-2 py-2 font-mono">{formatInteger(row.rank)}</td>
                <td className="px-2 py-2">
                  <div className="font-medium">{textFrom(row.name ?? row.persona_id)}</div>
                  <div className="text-muted-foreground">{textFrom(row.owner, "unassigned")}</div>
                </td>
                <td className="px-2 py-2">
                  <Badge variant="outline">{textFrom(row.tier_label ?? row.tier)}</Badge>
                </td>
                <td className="px-2 py-2 font-medium">{formatNumber(row.score ?? row.overall_score)}</td>
                <td className="px-2 py-2">
                  <Badge variant="outline" className={statusTone(row.risk)}>{labelFrom(row.risk)}</Badge>
                </td>
                <td className="px-2 py-2">{textFrom(row.formula_version)}</td>
                <td className="px-2 py-2">{componentSummary(row.components)}</td>
              </tr>
            ))}
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

function AttributionSection({ snapshot }: { snapshot: ManagementPerformanceReviewSnapshot }) {
  const personaRows = personaAttributionItems(snapshot);
  const poolRows = poolAttributionItems(snapshot);
  if (personaRows.length === 0 && poolRows.length === 0) return null;
  const summary = snapshot.attributionByPersona?.data.summary ?? snapshot.attributionByPool?.data.summary;
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
            {rows.map((row, index) => (
              <tr key={row.id} className="border-b border-border/60" data-testid={rowId("attribution-row", row.dimension_key, index)}>
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
