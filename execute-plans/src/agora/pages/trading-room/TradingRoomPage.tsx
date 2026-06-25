import React, { useEffect, useState } from "react";
import {
  getTradingRoom,
  listDecisionEvents,
  decideOnEvent,
  type TradingRoomAggregate,
  type TradingRoomStrategyEntry,
  type TradingDecisionEvent,
  type DecisionChoice,
} from "@/lib/bff-v1/agora/tradingRoom";

function newUUID(): string {
  return crypto.randomUUID();
}
import { getDashboardRecipeById } from "@/lib/bff-v1/agora/dashboard";
import type { DashboardRecipeV2, WidgetSpecV2 } from "@/lib/bff-v1/agora/types";
import { DashboardGridEditor } from "@/agora/dashboard/DashboardGridEditor";
import type { WidgetPlacement } from "@/agora/dashboard/DashboardGridEditor";

// ── Strategy Lens Switcher ────────────────────────────────────────────────────

interface StrategyLensSwitcherProps {
  strategies: TradingRoomStrategyEntry[];
  activeStrategyId?: string;
  onSelect: (strategyId: string | undefined) => void;
}

function StrategyLensSwitcher({
  strategies,
  activeStrategyId,
  onSelect,
}: StrategyLensSwitcherProps): JSX.Element {
  return (
    <div
      data-testid="strategy-lens-switcher"
      role="listbox"
      aria-label="Strategy workspace switcher"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "0 16px",
        borderBottom: "1px solid #e2e8f0",
        overflowX: "auto",
        flexShrink: 0,
      }}
    >
      <button
        role="option"
        aria-selected={activeStrategyId === undefined}
        data-testid="strategy-lens-all"
        onClick={() => onSelect(undefined)}
        style={{
          padding: "6px 12px",
          background: "none",
          border: "none",
          cursor: "pointer",
          fontWeight: activeStrategyId === undefined ? 600 : 400,
          borderBottom: activeStrategyId === undefined ? "2px solid #2563eb" : "2px solid transparent",
          whiteSpace: "nowrap",
        }}
      >
        All Strategies
      </button>
      {strategies.map((s) => (
        <button
          key={s.strategy_id}
          role="option"
          aria-selected={activeStrategyId === s.strategy_id}
          data-testid={`strategy-lens-${s.strategy_id}`}
          onClick={() => onSelect(s.strategy_id)}
          style={{
            padding: "6px 12px",
            background: "none",
            border: "none",
            cursor: "pointer",
            fontWeight: activeStrategyId === s.strategy_id ? 600 : 400,
            borderBottom:
              activeStrategyId === s.strategy_id
                ? "2px solid #2563eb"
                : "2px solid transparent",
            whiteSpace: "nowrap",
          }}
        >
          {s.title}
        </button>
      ))}
    </div>
  );
}

// ── Risk Banner ───────────────────────────────────────────────────────────────

const RISK_COLORS: Record<string, string> = {
  normal: "#f0fdf4",
  watch: "#fefce8",
  warning: "#fff7ed",
  critical: "#fef2f2",
};

interface RiskBannerProps {
  state: string;
  summary?: string;
  alerts?: string[];
}

function RiskBanner({ state, summary, alerts }: RiskBannerProps): JSX.Element | null {
  if (state === "normal") return null;
  return (
    <div
      data-testid="risk-banner"
      data-risk-state={state}
      style={{
        padding: "6px 16px",
        background: RISK_COLORS[state] ?? RISK_COLORS.warning,
        borderBottom: "1px solid #e2e8f0",
        fontSize: 13,
      }}
    >
      <strong>Risk: {state}</strong>
      {summary ? ` — ${summary}` : null}
      {alerts && alerts.length > 0 ? (
        <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
          {alerts.map((a, i) => (
            <li key={i}>{a}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

// ── Queue Summary Strip ───────────────────────────────────────────────────────

interface QueueSummaryStripProps {
  entry: number;
  add: number;
  reduce: number;
  exit: number;
  review: number;
}

function QueueSummaryStrip({ entry, add, reduce, exit, review }: QueueSummaryStripProps): JSX.Element {
  return (
    <div
      data-testid="queue-summary-strip"
      style={{
        display: "flex",
        gap: 16,
        padding: "4px 16px",
        borderBottom: "1px solid #e2e8f0",
        fontSize: 12,
        color: "#64748b",
      }}
    >
      <span data-testid="queue-entry-count">Entry: {entry}</span>
      <span data-testid="queue-add-count">Add: {add}</span>
      <span data-testid="queue-reduce-count">Reduce: {reduce}</span>
      <span data-testid="queue-exit-count">Exit: {exit}</span>
      <span data-testid="queue-review-count">Review: {review}</span>
    </div>
  );
}

// ── Decision Event Detail Panel ───────────────────────────────────────────────

type DecisionCallState = "idle" | "loading" | "success" | "error";

interface DecisionEventDetailPanelProps {
  event: TradingDecisionEvent;
  /** ETag from the listDecisionEvents response — forwarded as If-Match to decideOnEvent. */
  etag?: string | null;
}

function DecisionEventDetailPanel({ event, etag }: DecisionEventDetailPanelProps): JSX.Element {
  const [callState, setCallState] = useState<DecisionCallState>("idle");
  const [callError, setCallError] = useState<string | null>(null);
  const [decidedChoice, setDecidedChoice] = useState<DecisionChoice | null>(null);

  const canDecide =
    callState !== "loading" &&
    callState !== "success" &&
    (event.state === "pending_review" || event.state === "triggered" || event.state === "approaching");

  async function handleDecide(choice: DecisionChoice) {
    setCallState("loading");
    setCallError(null);
    try {
      await decideOnEvent(
        event.decision_event_id,
        { decision: choice },
        { ifMatch: etag ?? undefined, idempotencyKey: newUUID(), requestId: newUUID() },
      );
      setDecidedChoice(choice);
      setCallState("success");
    } catch (err) {
      setCallError(err instanceof Error ? err.message : "Decision failed");
      setCallState("error");
    }
  }

  const ev = event;

  return (
    <tr data-testid={`event-detail-${ev.decision_event_id}`}>
      <td colSpan={5} style={{ padding: "8px 16px", background: "#f8fafc", borderBottom: "2px solid #e2e8f0" }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12, fontSize: 12 }}>

          {/* Signal Quality */}
          <div data-testid="detail-confidence">
            <div style={{ fontWeight: 600, color: "#475569", marginBottom: 4 }}>Signal Quality</div>
            <div>Confidence: {(ev.confidence.value * 100).toFixed(0)}% ({ev.confidence.basis})</div>
            <div data-testid="detail-calibration">Calibration: {ev.confidence.calibration_state}</div>
            {ev.confidence.sample_size != null && (
              <div>Sample size: {ev.confidence.sample_size}</div>
            )}
            <div data-testid="detail-probability">
              Probability: {(ev.probability.value * 100).toFixed(0)}% — {ev.probability.target_outcome}
            </div>
            <div>Horizon: {ev.probability.horizon}</div>
            {ev.probability.ci_lower != null && ev.probability.ci_upper != null && (
              <div data-testid="detail-probability-interval">
                CI: [{(ev.probability.ci_lower * 100).toFixed(0)}%, {(ev.probability.ci_upper * 100).toFixed(0)}%]
              </div>
            )}
          </div>

          {/* Expected Value */}
          <div data-testid="detail-expected-value">
            <div style={{ fontWeight: 600, color: "#475569", marginBottom: 4 }}>Expected Value</div>
            <div>Horizon: {ev.expected_value.horizon} ({ev.expected_value.unit})</div>
            <div>Gross: {ev.expected_value.gross > 0 ? "+" : ""}{ev.expected_value.gross.toFixed(4)}</div>
            <div>Cost: {ev.expected_value.cost.toFixed(4)}</div>
            <div>Net: {ev.expected_value.net > 0 ? "+" : ""}{ev.expected_value.net.toFixed(4)}</div>
            <div>Downside: {ev.expected_value.downside.toFixed(4)}</div>
          </div>

          {/* Suggested Action */}
          <div data-testid="detail-suggested-action">
            <div style={{ fontWeight: 600, color: "#475569", marginBottom: 4 }}>Suggested Action</div>
            <div style={{ textTransform: "capitalize", fontWeight: 500 }}>{ev.suggested_action}</div>
            {ev.suggested_size && (
              <>
                {ev.suggested_size.size_hint && <div>Size hint: {ev.suggested_size.size_hint}</div>}
                {ev.suggested_size.portfolio_pct != null && (
                  <div>Portfolio %: {(ev.suggested_size.portfolio_pct * 100).toFixed(1)}%</div>
                )}
                <div style={{ color: "#94a3b8", fontSize: 11 }}>Non-binding</div>
              </>
            )}
            {ev.data_cutoff && (
              <div style={{ marginTop: 4, color: "#64748b" }}>Data cutoff: {ev.data_cutoff}</div>
            )}
            <div
              data-testid="detail-no-order-route"
              style={{ marginTop: 4, fontSize: 11, color: "#22c55e", fontWeight: 500 }}
            >
              {ev.no_order_route_proof}
            </div>
          </div>

          {/* Invalidation */}
          <div data-testid="detail-invalidation">
            <div style={{ fontWeight: 600, color: "#475569", marginBottom: 4 }}>Invalidation</div>
            <div>State: <span style={{ fontWeight: 500 }}>{ev.invalidation.current_state}</span></div>
            {ev.invalidation.conditions.length > 0 && (
              <ul style={{ margin: "4px 0 0 12px", padding: 0 }}>
                {ev.invalidation.conditions.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            )}
          </div>

        </div>

        {/* Rationale */}
        {ev.rationale.length > 0 && (
          <div data-testid="detail-rationale" style={{ marginTop: 10, fontSize: 12 }}>
            <div style={{ fontWeight: 600, color: "#475569", marginBottom: 4 }}>Rationale</div>
            {ev.rationale.map((r, i) => (
              <div key={i} style={{ display: "flex", gap: 8, marginBottom: 2 }}>
                <span style={{ color: "#94a3b8", minWidth: 32 }}>{(r.confidence * 100).toFixed(0)}%</span>
                <span>{r.claim}</span>
              </div>
            ))}
          </div>
        )}

        {/* Risk Notes */}
        {ev.risk_notes.length > 0 && (
          <div data-testid="detail-risk-notes" style={{ marginTop: 10, fontSize: 12 }}>
            <div style={{ fontWeight: 600, color: "#475569", marginBottom: 4 }}>Risk Notes</div>
            {ev.risk_notes.map((rn, i) => (
              <div
                key={i}
                style={{
                  padding: "4px 8px",
                  background: rn.severity === "critical" || rn.severity === "high" ? "#fef2f2" : "#fefce8",
                  borderRadius: 4,
                  marginBottom: 2,
                }}
              >
                <span style={{ fontWeight: 500 }}>[{rn.severity}] {rn.domain}:</span> {rn.summary}
                {rn.mitigation && <span style={{ color: "#64748b" }}> — {rn.mitigation}</span>}
              </div>
            ))}
          </div>
        )}

        {/* Evidence Refs */}
        {ev.evidence_refs.length > 0 && (
          <div data-testid="detail-evidence-refs" style={{ marginTop: 10, fontSize: 12 }}>
            <div style={{ fontWeight: 600, color: "#475569", marginBottom: 4 }}>
              Evidence ({ev.evidence_refs.length})
            </div>
            {ev.evidence_refs.map((ref, i) => (
              <div key={i} style={{ color: "#475569" }}>
                <span style={{ color: "#94a3b8" }}>{ref.ref_type}</span> {ref.ref_id}
                {ref.summary ? ` — ${ref.summary}` : null}
              </div>
            ))}
          </div>
        )}

        {/* Trader Decision Actions */}
        <div data-testid="detail-trader-actions" style={{ marginTop: 12, display: "flex", gap: 8, alignItems: "center" }}>
          {callState === "success" ? (
            <span data-testid="detail-decision-confirmed" style={{ fontSize: 12, color: "#22c55e", fontWeight: 500 }}>
              Decision recorded: {decidedChoice}
            </span>
          ) : (
            <>
              <span style={{ fontSize: 12, color: "#64748b", marginRight: 4 }}>Trader decision:</span>
              {(["approve", "reject", "defer", "modify"] as DecisionChoice[]).map((choice) => (
                <button
                  key={choice}
                  data-testid={`decide-${choice}-${ev.decision_event_id}`}
                  disabled={!canDecide}
                  onClick={() => handleDecide(choice)}
                  style={{
                    padding: "3px 10px",
                    fontSize: 12,
                    border: "1px solid #e2e8f0",
                    borderRadius: 4,
                    cursor: canDecide ? "pointer" : "not-allowed",
                    background: choice === "approve" ? "#f0fdf4" : choice === "reject" ? "#fef2f2" : "#fff",
                    color: choice === "approve" ? "#16a34a" : choice === "reject" ? "#dc2626" : "#475569",
                    opacity: canDecide ? 1 : 0.5,
                  }}
                >
                  {choice.charAt(0).toUpperCase() + choice.slice(1)}
                </button>
              ))}
              {callState === "loading" && (
                <span data-testid="detail-decision-loading" style={{ fontSize: 12, color: "#94a3b8" }}>
                  Sending…
                </span>
              )}
              {callState === "error" && callError && (
                <span data-testid="detail-decision-error" style={{ fontSize: 12, color: "#dc2626" }}>
                  {callError}
                </span>
              )}
            </>
          )}
        </div>
      </td>
    </tr>
  );
}

// ── Trading Event Queue ───────────────────────────────────────────────────────

const EVENT_KIND_LABEL: Record<string, string> = {
  entry: "Entry",
  add: "Add",
  reduce: "Reduce",
  exit: "Exit",
  review: "Review",
};

const STATE_LABEL: Record<string, string> = {
  approaching: "Approaching",
  triggered: "Triggered",
  pending_review: "Pending Review",
  decided: "Decided",
  expired: "Expired",
  invalidated: "Invalidated",
  superseded: "Superseded",
};

interface TradingEventQueueProps {
  events: TradingDecisionEvent[];
  loading: boolean;
  /** ETag from listDecisionEvents — forwarded to each DecisionEventDetailPanel as If-Match. */
  eventsEtag?: string | null;
}

function TradingEventQueue({ events, loading, eventsEtag }: TradingEventQueueProps): JSX.Element {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  function toggleExpand(id: string) {
    setExpandedId((prev) => (prev === id ? null : id));
  }

  return (
    <div data-testid="trading-event-queue" style={{ flex: 1, overflow: "auto" }}>
      <div style={{ padding: "8px 16px", fontWeight: 600, fontSize: 13, borderBottom: "1px solid #e2e8f0" }}>
        Decision Event Queue
      </div>
      {loading ? (
        <div data-testid="event-queue-loading" style={{ padding: 16, fontSize: 13, color: "#94a3b8" }}>
          Loading events…
        </div>
      ) : events.length === 0 ? (
        <div data-testid="event-queue-empty" style={{ padding: 16, fontSize: 13, color: "#94a3b8" }}>
          No pending decision events.
        </div>
      ) : (
        <table
          data-testid="event-queue-table"
          style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}
        >
          <thead>
            <tr style={{ borderBottom: "1px solid #e2e8f0" }}>
              <th style={{ textAlign: "left", padding: "6px 16px", fontWeight: 500, color: "#64748b" }}>Symbol</th>
              <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 500, color: "#64748b" }}>Kind</th>
              <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 500, color: "#64748b" }}>State</th>
              <th style={{ textAlign: "right", padding: "6px 8px", fontWeight: 500, color: "#64748b" }}>Confidence</th>
              <th style={{ textAlign: "right", padding: "6px 16px", fontWeight: 500, color: "#64748b" }}>EV (net)</th>
            </tr>
          </thead>
          <tbody>
            {events.map((ev) => (
              <React.Fragment key={ev.decision_event_id}>
                <tr
                  data-testid={`event-row-${ev.decision_event_id}`}
                  aria-expanded={expandedId === ev.decision_event_id}
                  style={{
                    borderBottom: expandedId === ev.decision_event_id ? "none" : "1px solid #f1f5f9",
                    cursor: "pointer",
                    background: expandedId === ev.decision_event_id ? "#f8fafc" : undefined,
                  }}
                  onClick={() => toggleExpand(ev.decision_event_id)}
                >
                  <td style={{ padding: "6px 16px" }}>{ev.subject.symbol}</td>
                  <td style={{ padding: "6px 8px" }}>{EVENT_KIND_LABEL[ev.event_kind] ?? ev.event_kind}</td>
                  <td style={{ padding: "6px 8px" }}>{STATE_LABEL[ev.state] ?? ev.state}</td>
                  <td style={{ padding: "6px 8px", textAlign: "right" }}>
                    {(ev.confidence.value * 100).toFixed(0)}%
                  </td>
                  <td style={{ padding: "6px 16px", textAlign: "right" }}>
                    {ev.expected_value.net > 0 ? "+" : ""}
                    {ev.expected_value.net.toFixed(2)}
                  </td>
                </tr>
                {expandedId === ev.decision_event_id && (
                  <DecisionEventDetailPanel event={ev} etag={eventsEtag} />
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ── Position Action Queue ─────────────────────────────────────────────────────

interface PositionActionQueueProps {
  positionSummaries: unknown[];
}

function PositionActionQueue({ positionSummaries }: PositionActionQueueProps): JSX.Element {
  return (
    <div
      data-testid="position-action-queue"
      style={{ borderLeft: "1px solid #e2e8f0", width: 240, overflow: "auto", flexShrink: 0 }}
    >
      <div style={{ padding: "8px 12px", fontWeight: 600, fontSize: 13, borderBottom: "1px solid #e2e8f0" }}>
        Position Actions
      </div>
      {positionSummaries.length === 0 ? (
        <div style={{ padding: 12, fontSize: 13, color: "#94a3b8" }}>No open positions.</div>
      ) : (
        <ul style={{ margin: 0, padding: "8px 12px", listStyle: "none" }}>
          {positionSummaries.map((p, i) => (
            <li key={i} style={{ fontSize: 13, borderBottom: "1px solid #f1f5f9", padding: "4px 0" }}>
              {JSON.stringify(p)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ── Strategy List (aggregate view) ────────────────────────────────────────────

interface StrategyListProps {
  strategies: TradingRoomStrategyEntry[];
  onSelect: (strategyId: string) => void;
}

function StrategyList({ strategies, onSelect }: StrategyListProps): JSX.Element {
  return (
    <div data-testid="strategy-list" style={{ padding: "8px 16px" }}>
      {strategies.length === 0 ? (
        <div data-testid="strategy-list-empty" style={{ fontSize: 13, color: "#94a3b8" }}>
          No strategies in the Trading Room.
        </div>
      ) : (
        <table
          data-testid="strategy-list-table"
          style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}
        >
          <thead>
            <tr style={{ borderBottom: "1px solid #e2e8f0" }}>
              <th style={{ textAlign: "left", padding: "6px 0", fontWeight: 500, color: "#64748b" }}>Strategy</th>
              <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 500, color: "#64748b" }}>Readiness</th>
              <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 500, color: "#64748b" }}>Monitoring</th>
              <th style={{ textAlign: "right", padding: "6px 0", fontWeight: 500, color: "#64748b" }}>Pending</th>
            </tr>
          </thead>
          <tbody>
            {strategies.map((s) => {
              const total =
                (s.pending_event_counts.entry ?? 0) +
                (s.pending_event_counts.add ?? 0) +
                (s.pending_event_counts.reduce ?? 0) +
                (s.pending_event_counts.exit ?? 0) +
                (s.pending_event_counts.review ?? 0);
              return (
                <tr
                  key={s.strategy_id}
                  data-testid={`strategy-row-${s.strategy_id}`}
                  style={{ borderBottom: "1px solid #f1f5f9", cursor: "pointer" }}
                  onClick={() => onSelect(s.strategy_id)}
                >
                  <td style={{ padding: "6px 0" }}>{s.title}</td>
                  <td style={{ padding: "6px 8px" }}>{s.readiness_state}</td>
                  <td style={{ padding: "6px 8px" }}>{s.monitoring_state}</td>
                  <td style={{ padding: "6px 0", textAlign: "right" }}>{total > 0 ? total : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ── Aggregate View (no strategy selected) ────────────────────────────────────

interface AggregateViewProps {
  aggregate: TradingRoomAggregate;
  events: TradingDecisionEvent[];
  eventsLoading: boolean;
  eventsEtag: string | null;
  onStrategySelect: (strategyId: string) => void;
}

function AggregateView({
  aggregate,
  events,
  eventsLoading,
  eventsEtag,
  onStrategySelect,
}: AggregateViewProps): JSX.Element {
  return (
    <div
      data-testid="trading-room-aggregate-view"
      style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}
    >
      <QueueSummaryStrip {...aggregate.queue_summary} />
      <RiskBanner
        state={aggregate.risk_summary.state}
        summary={aggregate.risk_summary.summary}
        alerts={aggregate.risk_summary.alerts}
      />
      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <StrategyList strategies={aggregate.strategies} onSelect={onStrategySelect} />
          <TradingEventQueue events={events} loading={eventsLoading} eventsEtag={eventsEtag} />
        </div>
        <PositionActionQueue positionSummaries={aggregate.position_summaries ?? []} />
      </div>
    </div>
  );
}

// ── Strategy Recipe Section ───────────────────────────────────────────────────

interface StrategyRecipeSectionProps {
  recipe: DashboardRecipeV2;
}

function StrategyRecipeSection({ recipe }: StrategyRecipeSectionProps): JSX.Element {
  const [activeViewIdx, setActiveViewIdx] = useState(0);
  const [viewPlacements, setViewPlacements] = useState<Record<string, WidgetPlacement[]>>(
    () => Object.fromEntries(recipe.views.map((v) => [v.view_id, v.placements as WidgetPlacement[]]))
  );

  const activeView = recipe.views[activeViewIdx];
  const placements = (activeView ? viewPlacements[activeView.view_id] : undefined) ?? [];
  const widgets: WidgetSpecV2[] = activeView?.widgets ?? [];

  if (!activeView) return <></>;

  return (
    <div data-testid="strategy-recipe-workspace" style={{ flex: 1, overflow: "auto", padding: 8 }}>
      {recipe.views.length > 1 && (
        <div
          data-testid="recipe-view-tabs"
          style={{ display: "flex", gap: 4, marginBottom: 8, borderBottom: "1px solid #e2e8f0", paddingBottom: 4 }}
        >
          {recipe.views.map((v, idx) => (
            <button
              key={v.view_id}
              data-testid={`recipe-view-tab-${v.view_id}`}
              aria-selected={idx === activeViewIdx}
              onClick={() => setActiveViewIdx(idx)}
              style={{
                padding: "4px 12px",
                background: "none",
                border: "none",
                cursor: "pointer",
                fontSize: 12,
                fontWeight: idx === activeViewIdx ? 600 : 400,
                borderBottom: idx === activeViewIdx ? "2px solid #2563eb" : "2px solid transparent",
              }}
            >
              {v.title}
            </button>
          ))}
        </div>
      )}

      <DashboardGridEditor
        viewId={activeView.view_id}
        recipeId={recipe.recipe_id}
        placements={placements}
        widgets={widgets}
        operatorId="trading-room"
        onPlacementsChange={(newPlacements) =>
          setViewPlacements((prev) => ({ ...prev, [activeView.view_id]: newPlacements }))
        }
        onWidgetRemove={() => {}}
        onWidgetAdd={() => {}}
        onWidgetChartChange={() => {}}
        onPersonalizationEvent={() => {}}
      />
    </div>
  );
}

// ── Strategy Workspace View (specific strategy selected) ──────────────────────

interface StrategyWorkspaceViewProps {
  strategyId: string;
  strategy: TradingRoomStrategyEntry | undefined;
  aggregate: TradingRoomAggregate;
  events: TradingDecisionEvent[];
  eventsLoading: boolean;
  eventsEtag: string | null;
}

function StrategyWorkspaceView({
  strategyId,
  strategy,
  aggregate,
  events,
  eventsLoading,
  eventsEtag,
}: StrategyWorkspaceViewProps): JSX.Element {
  const filteredEvents = events.filter((ev) => ev.strategy_id === strategyId);

  const [recipe, setRecipe] = useState<DashboardRecipeV2 | null>(null);
  const [recipeLoading, setRecipeLoading] = useState(true);

  const recipeId = strategy?.dashboard_recipe_id;

  useEffect(() => {
    if (!recipeId) {
      setRecipe(null);
      setRecipeLoading(false);
      return;
    }

    let cancelled = false;
    setRecipe(null);
    setRecipeLoading(true);

    getDashboardRecipeById(recipeId)
      .then((r) => {
        if (cancelled) return;
        setRecipe(r);
        setRecipeLoading(false);
      })
      .catch(() => {
        if (!cancelled) setRecipeLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [recipeId]);

  return (
    <div
      data-testid={`strategy-workspace-${strategyId}`}
      style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}
    >
      <div style={{ padding: "8px 16px", borderBottom: "1px solid #e2e8f0", fontSize: 13, flexShrink: 0 }}>
        <strong>{strategy?.title ?? strategyId}</strong>
        {strategy && (
          <span style={{ marginLeft: 12, color: "#64748b" }}>
            {strategy.readiness_state} · {strategy.monitoring_state}
          </span>
        )}
      </div>
      <RiskBanner
        state={aggregate.risk_summary.state}
        summary={aggregate.risk_summary.summary}
        alerts={aggregate.risk_summary.alerts}
      />

      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
          {recipeLoading ? (
            <div
              data-testid="strategy-recipe-loading"
              style={{ padding: 16, fontSize: 13, color: "#94a3b8" }}
            >
              Loading strategy workspace…
            </div>
          ) : recipe ? (
            <StrategyRecipeSection key={strategyId} recipe={recipe} />
          ) : (
            <div
              data-testid="strategy-recipe-unavailable"
              style={{ padding: 16, fontSize: 13, color: "#94a3b8" }}
            >
              Dashboard recipe unavailable for this strategy.
            </div>
          )}

          <TradingEventQueue events={filteredEvents} loading={eventsLoading} eventsEtag={eventsEtag} />
        </div>
        <PositionActionQueue positionSummaries={aggregate.position_summaries ?? []} />
      </div>
    </div>
  );
}

// ── Root Page ─────────────────────────────────────────────────────────────────

type LoadState = "loading" | "loaded" | "error";

interface TradingRoomPageProps {
  strategyId?: string;
  onStrategySelect?: (strategyId: string | undefined) => void;
}

export function TradingRoomPage({ strategyId, onStrategySelect }: TradingRoomPageProps): JSX.Element {
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [aggregate, setAggregate] = useState<TradingRoomAggregate | null>(null);
  const [events, setEvents] = useState<TradingDecisionEvent[]>([]);
  const [eventsLoading, setEventsLoading] = useState(true);
  const [eventsEtag, setEventsEtag] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoadState("loading");

    getTradingRoom()
      .then((agg) => {
        if (cancelled) return;
        setAggregate(agg);
        setLoadState("loaded");
      })
      .catch(() => {
        if (!cancelled) setLoadState("error");
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setEventsLoading(true);

    listDecisionEvents()
      .then(({ items, etag }) => {
        if (cancelled) return;
        setEvents(items);
        setEventsEtag(etag);
        setEventsLoading(false);
      })
      .catch(() => {
        if (!cancelled) setEventsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const handleStrategySelect = (id: string | undefined) => {
    onStrategySelect?.(id);
  };

  if (loadState === "loading") {
    return (
      <div
        data-testid="trading-room-loading"
        style={{ display: "flex", height: "100%", alignItems: "center", justifyContent: "center", fontSize: 13, color: "#94a3b8" }}
      >
        Loading Trading Room…
      </div>
    );
  }

  if (loadState === "error" || !aggregate) {
    return (
      <div
        data-testid="trading-room-error"
        style={{ display: "flex", height: "100%", alignItems: "center", justifyContent: "center", fontSize: 13, color: "#ef4444" }}
      >
        Failed to load Trading Room.
      </div>
    );
  }

  const activeStrategy = strategyId
    ? aggregate.strategies.find((s) => s.strategy_id === strategyId)
    : undefined;

  return (
    <div
      data-testid="trading-room-page"
      style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}
    >
      <StrategyLensSwitcher
        strategies={aggregate.strategies}
        activeStrategyId={strategyId}
        onSelect={handleStrategySelect}
      />

      {strategyId ? (
        <StrategyWorkspaceView
          strategyId={strategyId}
          strategy={activeStrategy}
          aggregate={aggregate}
          events={events}
          eventsLoading={eventsLoading}
          eventsEtag={eventsEtag}
        />
      ) : (
        <AggregateView
          aggregate={aggregate}
          events={events}
          eventsLoading={eventsLoading}
          eventsEtag={eventsEtag}
          onStrategySelect={(id) => handleStrategySelect(id)}
        />
      )}
    </div>
  );
}
