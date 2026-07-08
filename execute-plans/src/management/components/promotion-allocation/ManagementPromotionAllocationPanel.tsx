import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  GitPullRequestArrow,
  ListChecks,
  RefreshCw,
  Scale,
  Send,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { managementClient } from "@/lib/bff/client";
import type { Rebalance } from "@/lib/bff/types";
import * as managementApi from "@/lib/bff-v1/management";
import type {
  ManagementPromotionReviewItem,
  ManagementPromotionReviewsResponse,
  ManagementQuarterlyRankingFormula,
  ManagementQuarterlyRankingFormulaResponse,
  ManagementQuarterlyRankingRecommendationItem,
  ManagementQuarterlyRankingRecommendationsResponse,
  ManagementQuarterlyRankingResponse,
  ManagementSurfaceRef,
} from "@/lib/bff-v1/management";
import type { ListEnvelope } from "@/lib/bff-v1";
import { ManagementDenseTable } from "@/management/components/dense-table";
import {
  DATA_CONFIDENCE_EMPTY_COPY,
  DATA_CONFIDENCE_LABELS,
  DATA_CONFIDENCE_TONE,
  aggregateDataConfidence,
  asManagementRecord,
  dataConfidenceFromSurface,
  displayInteger as safeDisplayInteger,
  displayLabel as safeDisplayLabel,
  displayNumber as safeDisplayNumber,
  displayPercent as safeDisplayPercent,
  displayText as safeDisplayText,
  displayTime as safeDisplayTime,
  displayTitle as safeDisplayTitle,
  firstSurface,
  surfaceIssueMessage,
  type ManagementDataConfidenceState,
  cn,
} from "@/lib/utils";

type LoadState = "loading" | "ready" | "error";
type WorkbenchTab = "paper-candidates" | "promotion-review" | "quarterly-capital" | "formula-policy";

const LIMIT = 8;

interface WorkbenchIssue {
  source: string;
  message: string;
}

interface PromotionAllocationSnapshot {
  ranking?: ManagementQuarterlyRankingResponse;
  recommendations?: ManagementQuarterlyRankingRecommendationsResponse;
  promotionReviews?: ManagementPromotionReviewsResponse;
  formula?: ManagementQuarterlyRankingFormulaResponse;
  rebalances?: ListEnvelope<Rebalance>;
  issues: WorkbenchIssue[];
}

interface SnapshotTask<T> {
  key: Exclude<keyof PromotionAllocationSnapshot, "issues">;
  source: string;
  run: () => Promise<T>;
}

interface SubmitState {
  recommendationId?: string;
  reviewId?: string;
  message?: string;
  error?: string;
}

const tabs: Array<{ id: WorkbenchTab; label: string; icon: React.ReactNode }> = [
  { id: "paper-candidates", label: "Paper Candidates", icon: <GitPullRequestArrow className="h-3.5 w-3.5" /> },
  { id: "promotion-review", label: "Promotion Review", icon: <ShieldCheck className="h-3.5 w-3.5" /> },
  { id: "quarterly-capital", label: "Quarterly Capital", icon: <Scale className="h-3.5 w-3.5" /> },
  { id: "formula-policy", label: "Formula Policy", icon: <Settings2 className="h-3.5 w-3.5" /> },
];

function tabFromLocation(): WorkbenchTab {
  if (typeof window === "undefined") return "paper-candidates";
  const path = window.location.pathname;
  const tab = new URLSearchParams(window.location.search).get("tab");
  if (tab === "promotion-review" || tab === "quarterly-capital" || tab === "formula-policy" || tab === "paper-candidates") {
    return tab;
  }
  if (path.includes("promotion-reviews") || path.includes("human-inbox")) return "promotion-review";
  if (path.includes("rebalance") || path.includes("capital-binding-live") || path.startsWith("/management/capital")) return "quarterly-capital";
  if (path.includes("ranking/formulas") || path.includes("ranking-formulas") || path.endsWith("/ranking")) return "formula-policy";
  return "paper-candidates";
}

function asRecord(value: unknown): Record<string, unknown> {
  return asManagementRecord(value);
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

function formatPercent(value: unknown): string {
  return safeDisplayPercent(value);
}

function formatTime(value: unknown): string {
  return safeDisplayTime(value);
}

function statusTone(status: unknown): string {
  const normalized = String(status ?? "").toLowerCase();
  if (["ok", "ready", "recommended", "submitted", "accepted", "approved", "active", "healthy"].includes(normalized)) {
    return "bg-status-success/15 text-status-success border-status-success/30";
  }
  if (["pending", "recommended_not_submitted", "reviewing", "watch", "degraded", "draft", "queued"].includes(normalized)) {
    return "bg-status-warning/15 text-status-warning border-status-warning/30";
  }
  if (["rejected", "failed", "error", "blocked", "suspended", "retired", "unavailable"].includes(normalized)) {
    return "bg-status-failed/15 text-status-failed border-status-failed/30";
  }
  return "bg-muted text-muted-foreground border-border";
}

function DataConfidenceBadge({ state }: { state: ManagementDataConfidenceState }) {
  return (
    <Badge
      variant="outline"
      className={cn("whitespace-nowrap", DATA_CONFIDENCE_TONE[state])}
      data-confidence-state={state}
      title={DATA_CONFIDENCE_LABELS[state]}
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

function surfaceIssues(source: string, response: { meta?: { surfaces?: Record<string, ManagementSurfaceRef | undefined> } } | undefined): WorkbenchIssue[] {
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

async function settleTask<T>(
  task: SnapshotTask<T>,
): Promise<{ key: SnapshotTask<T>["key"]; source: string; value?: T; issue?: WorkbenchIssue }> {
  try {
    return { key: task.key, source: task.source, value: await task.run() };
  } catch (error) {
    return { key: task.key, source: task.source, issue: { source: task.source, message: safeErrorMessage(error) } };
  }
}

export async function loadPromotionAllocationSnapshot(): Promise<PromotionAllocationSnapshot> {
  const tasks: SnapshotTask<unknown>[] = [
    {
      key: "ranking",
      source: "Quarterly ranking",
      run: () => managementApi.fetchManagementQuarterlyRanking({ page_size: LIMIT }),
    },
    {
      key: "recommendations",
      source: "Quarterly recommendations",
      run: () => managementApi.fetchManagementQuarterlyRankingRecommendations({ page_size: LIMIT }),
    },
    {
      key: "promotionReviews",
      source: "Promotion reviews",
      run: () => managementApi.fetchManagementPromotionReviews({ page_size: LIMIT }),
    },
    {
      key: "formula",
      source: "Ranking formula",
      run: () => managementApi.fetchManagementQuarterlyRankingFormula(),
    },
    {
      key: "rebalances",
      source: "Rebalance proposals",
      run: () => managementClient.rebalances.list(),
    },
  ];

  const results = await Promise.all(tasks.map(settleTask));
  const snapshot: PromotionAllocationSnapshot = { issues: [] };
  for (const result of results) {
    if (result.issue) {
      snapshot.issues.push(result.issue);
    } else {
      snapshot[result.key] = result.value as never;
    }
  }
  snapshot.issues.push(
    ...surfaceIssues("Quarterly ranking", snapshot.ranking),
    ...surfaceIssues("Quarterly recommendations", snapshot.recommendations),
    ...surfaceIssues("Promotion reviews", snapshot.promotionReviews),
    ...surfaceIssues("Ranking formula", snapshot.formula),
  );
  return snapshot;
}

function hasWorkbenchData(snapshot: PromotionAllocationSnapshot): boolean {
  return [
    snapshot.ranking?.data.items,
    snapshot.recommendations?.data.items,
    snapshot.promotionReviews?.data.items,
    snapshot.rebalances?.items,
    snapshot.formula?.data?.components,
  ].some((items) => Array.isArray(items) && items.length > 0);
}

function snapshotDataConfidence(snapshot: PromotionAllocationSnapshot | null, state: LoadState): ManagementDataConfidenceState {
  if (state === "error" || !snapshot) return "unavailable";
  const responses = [
    snapshot.ranking,
    snapshot.recommendations,
    snapshot.promotionReviews,
    snapshot.formula,
  ].filter(Boolean);
  if (responses.length === 0) return "unavailable";
  const aggregate = aggregateDataConfidence(
    responses.map((response) => dataConfidenceFromSurface(firstSurface(response?.meta?.surfaces))),
  );
  return snapshot.issues.length > 0 && aggregate === "formal" ? "partial" : aggregate;
}

export function ManagementPromotionAllocationPanel({ className }: { className?: string }) {
  const [activeTab, setActiveTab] = useState<WorkbenchTab>(() => tabFromLocation());
  const [state, setState] = useState<LoadState>("loading");
  const [snapshot, setSnapshot] = useState<PromotionAllocationSnapshot | null>(null);
  const [error, setError] = useState<string | undefined>();
  const [submitState, setSubmitState] = useState<SubmitState>({});

  const load = useCallback(async () => {
    setState("loading");
    setError(undefined);
    try {
      const nextSnapshot = await loadPromotionAllocationSnapshot();
      setSnapshot(nextSnapshot);
      const everySourceFailed = nextSnapshot.issues.length >= 5
        && !nextSnapshot.ranking
        && !nextSnapshot.recommendations
        && !nextSnapshot.promotionReviews
        && !nextSnapshot.formula
        && !nextSnapshot.rebalances;
      if (everySourceFailed) {
        setError("Every promotion and allocation source failed.");
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

  const submitRecommendation = useCallback(async (recommendation: ManagementQuarterlyRankingRecommendationItem) => {
    const recommendationId = recommendation.recommendation_id || recommendation.id;
    setSubmitState({ recommendationId });
    try {
      const response = await managementApi.submitManagementQuarterlyRankingRecommendation(recommendationId, {
        quarter: recommendation.quarter,
        source_ui: "management_promotion_allocation",
      });
      setSubmitState({
        recommendationId,
        reviewId: response.data.review_id,
        message: `Submitted to review ${response.data.review_id}`,
      });
      setActiveTab("promotion-review");
      await load();
    } catch (err) {
      setSubmitState({ recommendationId, error: safeErrorMessage(err) });
    }
  }, [load]);

  const issueCount = snapshot?.issues.length ?? 0;
  const panelStatus = state === "error" ? "error" : issueCount > 0 ? "degraded" : "ok";
  const confidenceState = snapshotDataConfidence(snapshot, state);
  const quarter = textFrom(
    snapshot?.recommendations?.data.quarter_window?.label
      ?? snapshot?.ranking?.data.quarter_window?.label
      ?? snapshot?.promotionReviews?.data.quarter_window?.label,
    "Current quarter",
  );
  const hasData = snapshot ? hasWorkbenchData(snapshot) : false;

  return (
    <section className={cn("flex flex-col gap-4", className)} data-testid="management-promotion-allocation-panel">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <SlidersHorizontal className="h-4 w-4 text-primary" />
            <h2 className="text-base font-semibold">Promotion & Allocation</h2>
            <Badge variant="outline" className={cn("capitalize", statusTone(panelStatus))}>{panelStatus}</Badge>
            <DataConfidenceBadge state={confidenceState} />
            <Badge variant="outline">{quarter}</Badge>
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>Ranking: {textFrom(snapshot?.formula?.summary?.formula_version ?? snapshot?.ranking?.data.summary?.formula_version)}</span>
            <span>Promotion submit: recommendation to human review</span>
            <span>Capital apply: rebalance approval only</span>
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

      <WorkflowRail />

      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Promotion and allocation tabs">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={activeTab === tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={cn(
              "inline-flex h-8 items-center gap-2 rounded-md border px-3 text-xs font-medium",
              activeTab === tab.id ? "border-primary bg-primary/10 text-primary" : "border-border hover:bg-muted",
            )}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>

      {state === "loading" ? (
        <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground" role="status">
          <RefreshCw className="h-4 w-4 animate-spin" />
          Loading promotion and allocation
        </div>
      ) : null}

      {state === "error" ? (
        <EmptyState
          icon={<AlertTriangle className="h-8 w-8" />}
          title="Promotion and allocation unavailable"
          description={error}
          cta={{ label: "Retry", onClick: load }}
        />
      ) : null}

      {state === "ready" && snapshot && !hasData ? (
        <>
          {issueCount > 0 ? <DegradedBanner issues={snapshot.issues} /> : null}
          <EmptyState
            icon={<ListChecks className="h-8 w-8" />}
            title="No promotion or allocation records"
            description={DATA_CONFIDENCE_EMPTY_COPY[confidenceState]}
            cta={{ label: "Refresh", onClick: load }}
          />
          <FormulaPolicySection formula={snapshot.formula?.data} response={snapshot.formula} />
        </>
      ) : null}

      {state === "ready" && snapshot && hasData ? (
        <>
          {issueCount > 0 ? <DegradedBanner issues={snapshot.issues} /> : null}
          <Summary snapshot={snapshot} />
          {submitState.message || submitState.error ? <SubmitBanner state={submitState} /> : null}
          {activeTab === "paper-candidates" ? (
            <PaperCandidatesSection
              recommendations={snapshot.recommendations?.data.items ?? []}
              submitState={submitState}
              onSubmit={submitRecommendation}
            />
          ) : null}
          {activeTab === "promotion-review" ? (
            <PromotionReviewSection reviews={snapshot.promotionReviews?.data.items ?? []} />
          ) : null}
          {activeTab === "quarterly-capital" ? (
            <QuarterlyCapitalSection
              rebalances={snapshot.rebalances?.items ?? []}
              ranking={snapshot.ranking}
              recommendations={snapshot.recommendations}
            />
          ) : null}
          {activeTab === "formula-policy" ? (
            <FormulaPolicySection formula={snapshot.formula?.data} response={snapshot.formula} />
          ) : null}
        </>
      ) : null}
    </section>
  );
}

function WorkflowRail() {
  const steps = [
    {
      label: "Ranking source",
      value: "/bff/management/quarterly-ranking",
      detail: "formula + evidence snapshot",
    },
    {
      label: "Promotion submit",
      value: "/bff/management/quarterly-ranking/recommendations/{id}/submit",
      detail: "creates promotion review",
    },
    {
      label: "Human review",
      value: "/bff/management/promotion-reviews",
      detail: "approve, condition, or reject",
    },
    {
      label: "Capital proposal",
      value: "/bff/rebalances",
      detail: "rebalance proposal then approval",
    },
  ];
  return (
    <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4" data-testid="promotion-allocation-workflow-rail">
      {steps.map((step, index) => (
        <div key={step.label} className="rounded-md border border-border p-3">
          <div className="flex items-center justify-between gap-2">
            <div className="text-xs font-semibold">{step.label}</div>
            {index < steps.length - 1 ? <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" /> : <CheckCircle2 className="h-3.5 w-3.5 text-status-success" />}
          </div>
          <div className="mt-2 break-all font-mono text-[11px] text-muted-foreground">{step.value}</div>
          <div className="mt-1 text-xs text-muted-foreground">{step.detail}</div>
        </div>
      ))}
    </div>
  );
}

function DegradedBanner({ issues }: { issues: WorkbenchIssue[] }) {
  return (
    <div className="rounded-md border border-status-warning/30 bg-status-warning/10 p-3 text-xs" data-testid="promotion-allocation-degraded">
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

function Summary({ snapshot }: { snapshot: PromotionAllocationSnapshot }) {
  const recommendationSummary = snapshot.recommendations?.data.summary;
  const reviewSummary = snapshot.promotionReviews?.data.summary;
  const formulaSummary = snapshot.formula?.summary;
  const rebalanceCount = snapshot.rebalances?.items.length ?? 0;
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4" data-testid="promotion-allocation-summary">
      <Metric label="Paper candidates" primary={formatInteger(recommendationSummary?.by_action?.promote_to_canary_candidate)} secondary={`${formatInteger(recommendationSummary?.recommendation_count)} recommendations`} />
      <Metric label="Review queue" primary={formatInteger(reviewSummary?.pending_count)} secondary={`${formatInteger(reviewSummary?.review_count)} reviews / ${formatInteger(reviewSummary?.decision_accepted_count)} decided`} />
      <Metric label="Quarterly capital" primary={formatInteger(rebalanceCount)} secondary="rebalance proposals" />
      <Metric label="Formula policy" primary={textFrom(formulaSummary?.formula_version)} secondary={`${formatInteger(formulaSummary?.component_count)} components / weight ${formatPercent(formulaSummary?.weight_total)}`} />
    </div>
  );
}

function Metric({ label, primary, secondary }: { label: string; primary: string; secondary: string }) {
  return (
    <div className="rounded-md border border-border p-3">
      <div className="text-xs font-medium text-muted-foreground">{label}</div>
      <div className="mt-2 text-lg font-semibold">{primary}</div>
      <div className="mt-1 text-xs text-muted-foreground">{secondary}</div>
    </div>
  );
}

function SubmitBanner({ state }: { state: SubmitState }) {
  const ok = Boolean(state.message);
  return (
    <div
      className={cn(
        "rounded-md border p-3 text-xs",
        ok ? "border-status-success/30 bg-status-success/10" : "border-status-failed/30 bg-status-failed/10",
      )}
      data-testid="promotion-allocation-submit-banner"
    >
      <div className={cn("flex items-center gap-2 font-semibold", ok ? "text-status-success" : "text-status-failed")}>
        {ok ? <CheckCircle2 className="h-4 w-4" /> : <AlertTriangle className="h-4 w-4" />}
        {state.message ?? state.error}
      </div>
      {state.reviewId ? <div className="mt-1 font-mono text-muted-foreground">/management/promotion-reviews/{state.reviewId}</div> : null}
    </div>
  );
}

function PaperCandidatesSection({
  recommendations,
  submitState,
  onSubmit,
}: {
  recommendations: ManagementQuarterlyRankingRecommendationItem[];
  submitState: SubmitState;
  onSubmit: (recommendation: ManagementQuarterlyRankingRecommendationItem) => void;
}) {
  const rows = useMemo(
    () => recommendations.filter((row) => row.action_id === "promote_to_canary_candidate"),
    [recommendations],
  );
  if (rows.length === 0) {
    return (
      <EmptyState
        icon={<GitPullRequestArrow className="h-8 w-8" />}
        title="No paper promotion candidates"
        description="No recommendation currently qualifies for promote_to_canary_candidate."
      />
    );
  }
  return (
    <PanelSection title="Paper To Canary Candidates" icon={<GitPullRequestArrow className="h-4 w-4" />} summary="Recommendation submit creates review; it does not switch to real capital.">
      <ManagementDenseTable minWidth={1040} testId="promotion-allocation-table-paper-candidates">
        <table className="w-full min-w-[1040px] text-left text-xs">
          <thead className="border-b border-border text-muted-foreground">
            <tr>
              <th className="px-2 py-2 font-medium">Persona</th>
              <th className="px-2 py-2 font-medium">Rank</th>
              <th className="px-2 py-2 font-medium">Score</th>
              <th className="px-2 py-2 font-medium">Action</th>
              <th className="px-2 py-2 font-medium">Review Target</th>
              <th className="px-2 py-2 font-medium">Live Mutation</th>
              <th className="px-2 py-2 font-medium">Submit</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => {
              const submitting = submitState.recommendationId === row.recommendation_id && !submitState.message && !submitState.error;
              return (
                <tr key={row.recommendation_id} className="border-b border-border/60" data-testid={rowId("promotion-candidate-row", row.recommendation_id, index)}>
                  <td className="px-2 py-2">
                    <div className="font-medium">{textFrom(row.name ?? row.persona_id)}</div>
                    <div className="text-muted-foreground">{textFrom(row.owner, "unassigned")} / {textFrom(row.persona_id)}</div>
                  </td>
                  <td className="px-2 py-2 font-mono">{formatInteger(row.rank)}</td>
                  <td className="px-2 py-2 font-medium">{formatNumber(row.score)}</td>
                  <td className="px-2 py-2">{titleFrom(row.action_id)}</td>
                  <td className="px-2 py-2">
                    <div>{textFrom(row.governance?.decision_type, "promotion_review")}</div>
                    <div className="font-mono text-muted-foreground">{textFrom(row.governance?.human_inbox_route ?? row.links?.human_inbox, "/management/human-inbox")}</div>
                  </td>
                  <td className="px-2 py-2">
                    <Badge variant="outline" className={statusTone(row.live_capital_mutation ? "blocked" : "ok")}>
                      {row.live_capital_mutation ? "blocked" : "none"}
                    </Badge>
                  </td>
                  <td className="px-2 py-2">
                    <button
                      type="button"
                      onClick={() => onSubmit(row)}
                      disabled={submitting}
                      className="inline-flex h-8 items-center gap-2 rounded-md border border-border px-3 font-medium hover:bg-muted disabled:cursor-wait disabled:opacity-60"
                    >
                      <Send className="h-3.5 w-3.5" />
                      {submitting ? "Submitting" : "Submit"}
                    </button>
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

function PromotionReviewSection({ reviews }: { reviews: ManagementPromotionReviewItem[] }) {
  if (reviews.length === 0) {
    return (
      <EmptyState
        icon={<ShieldCheck className="h-8 w-8" />}
        title="No promotion reviews"
        description="Submitted paper-to-canary recommendations will appear in this review queue."
      />
    );
  }
  return (
    <PanelSection title="Promotion Review Queue" icon={<ShieldCheck className="h-4 w-4" />} summary="Adjustments happen through approve, approve_with_conditions, or reject decisions.">
      <ManagementDenseTable minWidth={1040} testId="promotion-allocation-table-review-queue">
        <table className="w-full min-w-[1040px] text-left text-xs">
          <thead className="border-b border-border text-muted-foreground">
            <tr>
              <th className="px-2 py-2 font-medium">Review</th>
              <th className="px-2 py-2 font-medium">Persona</th>
              <th className="px-2 py-2 font-medium">Path</th>
              <th className="px-2 py-2 font-medium">Status</th>
              <th className="px-2 py-2 font-medium">Decision Endpoint</th>
              <th className="px-2 py-2 font-medium">Capital Effect</th>
            </tr>
          </thead>
          <tbody>
            {reviews.map((review, index) => (
              <tr key={review.review_id} className="border-b border-border/60" data-testid={rowId("promotion-review-row", review.review_id, index)}>
                <td className="px-2 py-2">
                  <div className="font-medium">{textFrom(review.review_id)}</div>
                  <div className="text-muted-foreground">{textFrom(review.recommendation_id)}</div>
                </td>
                <td className="px-2 py-2">
                  <div className="font-medium">{textFrom(review.name ?? review.persona_id)}</div>
                  <div className="text-muted-foreground">rank {formatInteger(review.rank)} / score {formatNumber(review.score)}</div>
                </td>
                <td className="px-2 py-2">
                  {textFrom(review.promotion_path?.from_stage, "paper")} <span className="text-muted-foreground">to</span> {textFrom(review.promotion_path?.target_stage, "canary")}
                </td>
                <td className="px-2 py-2">
                  <div className="flex flex-wrap gap-1">
                    <Badge variant="outline" className={statusTone(review.status)}>{labelFrom(review.status)}</Badge>
                    <Badge variant="outline" className={statusTone(review.decision_status)}>{labelFrom(review.decision_status, "pending")}</Badge>
                  </div>
                </td>
                <td className="px-2 py-2 font-mono text-muted-foreground">
                  /bff/management/promotion-reviews/{review.review_id}/decisions
                </td>
                <td className="px-2 py-2">
                  <Badge variant="outline" className={statusTone(review.live_capital_mutation ? "blocked" : "ok")}>
                    {review.live_capital_mutation ? "blocked" : "review only"}
                  </Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </ManagementDenseTable>
    </PanelSection>
  );
}

function QuarterlyCapitalSection({
  rebalances,
  ranking,
  recommendations,
}: {
  rebalances: Rebalance[];
  ranking?: ManagementQuarterlyRankingResponse;
  recommendations?: ManagementQuarterlyRankingRecommendationsResponse;
}) {
  const capitalActions = recommendations?.data.items.filter((row) =>
    ["reduce_capital_access", "freeze_persona", "suspend_persona", "retire_persona"].includes(row.action_id),
  ) ?? [];
  return (
    <div className="grid gap-4">
      <PanelSection
        title="Quarterly Capital Proposal"
        icon={<Scale className="h-4 w-4" />}
        summary={`Ranking ${textFrom(ranking?.data.summary.formula_version)} / apply requires approval`}
      >
        {rebalances.length > 0 ? (
          <ManagementDenseTable minWidth={980} testId="promotion-allocation-table-rebalances">
            <table className="w-full min-w-[980px] text-left text-xs">
              <thead className="border-b border-border text-muted-foreground">
                <tr>
                  <th className="px-2 py-2 font-medium">Proposal</th>
                  <th className="px-2 py-2 font-medium">Pool</th>
                  <th className="px-2 py-2 font-medium">Status</th>
                  <th className="px-2 py-2 font-medium">Reason</th>
                  <th className="px-2 py-2 font-medium">Adjust Endpoint</th>
                </tr>
              </thead>
              <tbody>
                {rebalances.map((rebalance, index) => {
                  const record = asRecord(rebalance);
                  const id = textFrom(record.rebalance_id ?? record.id, `rebalance-${index}`);
                  return (
                    <tr key={id} className="border-b border-border/60" data-testid={rowId("rebalance-row", id, index)}>
                      <td className="px-2 py-2 font-medium">{id}</td>
                      <td className="px-2 py-2">{textFrom(record.capital_pool_id ?? record.pool_id)}</td>
                      <td className="px-2 py-2"><Badge variant="outline" className={statusTone(record.status)}>{labelFrom(record.status)}</Badge></td>
                      <td className="px-2 py-2">{textFrom(record.reason ?? record.title ?? record.name)}</td>
                      <td className="px-2 py-2 font-mono text-muted-foreground">/bff/rebalances/{id}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </ManagementDenseTable>
        ) : (
          <EmptyState
            icon={<Scale className="h-8 w-8" />}
            title="No rebalance proposals"
            description="Quarterly ranking target weights should emit proposals here before any capital apply command."
          />
        )}
      </PanelSection>

      {capitalActions.length > 0 ? (
        <PanelSection title="Risk And Reduction Recommendations" icon={<AlertTriangle className="h-4 w-4" />} summary="Emergency and reduction actions do not increase allocation.">
          <ManagementDenseTable minWidth={900} testId="promotion-allocation-table-capital-actions">
            <table className="w-full min-w-[900px] text-left text-xs">
              <thead className="border-b border-border text-muted-foreground">
                <tr>
                  <th className="px-2 py-2 font-medium">Persona</th>
                  <th className="px-2 py-2 font-medium">Action</th>
                  <th className="px-2 py-2 font-medium">Risk</th>
                  <th className="px-2 py-2 font-medium">Rationale</th>
                </tr>
              </thead>
              <tbody>
                {capitalActions.map((row, index) => (
                  <tr key={row.recommendation_id} className="border-b border-border/60" data-testid={rowId("capital-action-row", row.recommendation_id, index)}>
                    <td className="px-2 py-2">{textFrom(row.name ?? row.persona_id)}</td>
                    <td className="px-2 py-2">{titleFrom(row.action_id)}</td>
                    <td className="px-2 py-2"><Badge variant="outline" className={statusTone(row.risk_level)}>{labelFrom(row.risk_level)}</Badge></td>
                    <td className="px-2 py-2">{textFrom(row.rationale)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ManagementDenseTable>
        </PanelSection>
      ) : null}
    </div>
  );
}

function FormulaPolicySection({
  formula,
  response,
}: {
  formula?: ManagementQuarterlyRankingFormula;
  response?: ManagementQuarterlyRankingFormulaResponse;
}) {
  if (!formula) {
    return (
      <EmptyState
        icon={<Settings2 className="h-8 w-8" />}
        title="Formula policy unavailable"
        description="The quarterly ranking formula endpoint did not return a formula payload."
      />
    );
  }
  const weights = Object.entries(formula.weights ?? {});
  return (
    <div className="grid gap-4">
      <PanelSection
        title="Ranking Formula"
        icon={<Settings2 className="h-4 w-4" />}
        summary={`${textFrom(formula.formula_version)} / ${textFrom(formula.basis)} / ${textFrom(formula.policy)}`}
      >
        <div className="grid gap-3 md:grid-cols-3">
          <Metric label="Formula ID" primary={textFrom(formula.formula_id)} secondary={textFrom(formula.score_field, "score field")} />
          <Metric label="Change Authority" primary={textFrom(formula.change_control?.authority)} secondary={textFrom(formula.change_control?.version_policy)} />
          <Metric label="Evidence" primary={formatInteger(response?.summary?.evidence_ref_count)} secondary={formula.change_control?.requires_governance_evidence ? "governance required" : "governance optional"} />
        </div>
        <ManagementDenseTable minWidth={760} testId="promotion-allocation-table-formula-weights">
          <table className="w-full min-w-[760px] text-left text-xs">
            <thead className="border-b border-border text-muted-foreground">
              <tr>
                <th className="px-2 py-2 font-medium">Component</th>
                <th className="px-2 py-2 font-medium">Weight</th>
                <th className="px-2 py-2 font-medium">Adjustment Route</th>
              </tr>
            </thead>
            <tbody>
              {weights.map(([key, weight]) => (
                <tr key={key} className="border-b border-border/60" data-testid={`formula-weight-${key}`}>
                  <td className="px-2 py-2 font-medium">{titleFrom(key)}</td>
                  <td className="px-2 py-2">{formatPercent(weight)}</td>
                  <td className="px-2 py-2 font-mono text-muted-foreground">/bff/ranking-formulas + governance evidence</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ManagementDenseTable>
      </PanelSection>

      {response?.version_history?.length ? (
        <PanelSection title="Formula Version History" icon={<ListChecks className="h-4 w-4" />} summary={textFrom(response.meta?.version_policy)}>
          <ManagementDenseTable minWidth={860} testId="promotion-allocation-table-formula-history">
            <table className="w-full min-w-[860px] text-left text-xs">
              <thead className="border-b border-border text-muted-foreground">
                <tr>
                  <th className="px-2 py-2 font-medium">Version</th>
                  <th className="px-2 py-2 font-medium">Effective</th>
                  <th className="px-2 py-2 font-medium">Change</th>
                  <th className="px-2 py-2 font-medium">Evidence</th>
                </tr>
              </thead>
              <tbody>
                {response.version_history.map((version) => (
                  <tr key={`${version.id}-${version.formula_version}`} className="border-b border-border/60">
                    <td className="px-2 py-2 font-medium">{textFrom(version.formula_version)}</td>
                    <td className="px-2 py-2">{formatTime(version.effective_at)}</td>
                    <td className="px-2 py-2">{titleFrom(version.change_type)}</td>
                    <td className="px-2 py-2">{version.governance_evidence_refs.join(", ") || "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ManagementDenseTable>
        </PanelSection>
      ) : null}
    </div>
  );
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
