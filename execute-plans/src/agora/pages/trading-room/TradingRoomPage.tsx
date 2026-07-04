import React, { useEffect, useState } from "react";
import {
  getTradingRoom,
  listDecisionEvents,
  decideOnEvent,
  type TradingRoomAggregate,
  type TradingRoomStrategyEntry,
  type TradingDecisionEvent,
  type DecisionChoice,
  type TradingRoomBffDiagnostic,
} from "@/lib/bff-v1/agora/tradingRoom";

function newUUID(): string {
  return crypto.randomUUID();
}
import { getDashboardRecipeById } from "@/lib/bff-v1/agora/dashboard";
import type { DashboardRecipeV2, WidgetSpecV2 } from "@/lib/bff-v1/agora/types";
import { DashboardGridEditor } from "@/agora/dashboard/DashboardGridEditor";
import type { WidgetPlacement } from "@/agora/dashboard/DashboardGridEditor";

/* ── Dark AGORA palette ─────────────────────────────────────────────────────── */
const C = {
  bg: "#111417",
  surface: "#171b22",
  elevated: "#1e2330",
  expandedRow: "#1a2030",
  border: "#2a2e38",
  text: "#f0ece4",
  secondary: "#8c96a6",
  muted: "#737d8e",
  amber: "#e8b750",
  green: "#4ade80",
  red: "#f87171",
  riskWatch: "#1e1c0e",
  riskWarning: "#231808",
  riskCritical: "#230e0e",
  riskNoteCritical: "#2a1010",
  riskNoteWatch: "#1e1a0a",
  approveBtn: "rgba(74,222,128,0.12)",
  approveBtnText: "#4ade80",
  rejectBtn: "rgba(248,113,113,0.12)",
  rejectBtnText: "#f87171",
} as const;

function recordFrom(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function isTradingRoomBffDiagnostic(value: unknown): value is TradingRoomBffDiagnostic {
  const record = recordFrom(value);
  return (
    typeof record.method === "string" &&
    typeof record.url === "string" &&
    typeof record.status === "number" &&
    typeof record.code === "string" &&
    typeof record.message === "string"
  );
}

function diagnosticFromUnknown(error: unknown): TradingRoomBffDiagnostic {
  const diagnostic = recordFrom(error).diagnostic;
  if (isTradingRoomBffDiagnostic(diagnostic)) return diagnostic;
  return {
    method: "GET",
    url: "/bff/agora/trading-room",
    status: 0,
    code: "TRADING_ROOM_CLIENT_ERROR",
    message: error instanceof Error ? error.message : "Trading Room load failed",
    requestId: null,
    correlationId: null,
    retryable: true,
  };
}

function sanitizeDiagnosticText(value: string): string {
  return value
    .replace(/(Bearer\s+)[A-Za-z0-9._~+/-]+=*/gi, "$1[redacted]")
    .replace(/((?:access_)?token=)[^&\s]+/gi, "$1[redacted]")
    .replace(/(password=)[^&\s]+/gi, "$1[redacted]")
    .slice(0, 240);
}

function endpointFromUrl(url: string): string {
  try {
    const base = typeof window !== "undefined" ? window.location.origin : "https://pantheon.local";
    return new URL(url, base).pathname;
  } catch {
    return "/bff/agora/trading-room";
  }
}

function buildSafeReloadHref(): string {
  if (typeof window === "undefined") return "/agora/trading-room?pantheon_reload=latest";
  const next = new URL(window.location.href);
  next.searchParams.set("pantheon_reload", String(Date.now()));
  return next.toString();
}

function safeReloadTradingRoom(href: string): void {
  if (typeof window === "undefined") return;
  window.location.assign(href);
}

function ErrorDiagnosticRow({ label, value }: { label: string; value: string | null }): JSX.Element {
  return (
    <div style={{ display: "flex", gap: 8, minWidth: 0 }}>
      <span style={{ width: 90, color: C.muted, flexShrink: 0 }}>{label}</span>
      <span style={{ color: C.text, overflowWrap: "anywhere" }}>{value || "unavailable"}</span>
    </div>
  );
}

interface TradingRoomErrorStateProps {
  diagnostic: TradingRoomBffDiagnostic;
  onRetry: () => void;
}

function TradingRoomErrorState({ diagnostic, onRetry }: TradingRoomErrorStateProps): JSX.Element {
  const reloadHref = buildSafeReloadHref();
  const endpoint = endpointFromUrl(diagnostic.url);
  const statusLabel = diagnostic.status > 0 ? `HTTP ${diagnostic.status}` : "Network failure";

  return (
    <div
      data-testid="trading-room-error"
      data-bff-status={diagnostic.status}
      data-bff-code={diagnostic.code}
      data-request-id={diagnostic.requestId ?? ""}
      data-correlation-id={diagnostic.correlationId ?? ""}
      style={{
        display: "flex",
        height: "100%",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
        background: C.bg,
        color: C.text,
      }}
    >
      <div style={{ width: "min(680px, 100%)", border: `1px solid ${C.border}`, background: C.surface, padding: 18 }}>
        <div style={{ color: C.red, fontSize: 14, fontWeight: 700, marginBottom: 6 }}>
          Trading Room load failed
        </div>
        <div data-testid="trading-room-error-summary" style={{ fontSize: 13, color: C.secondary, marginBottom: 14 }}>
          {statusLabel} · {diagnostic.code}
        </div>
        <div data-testid="trading-room-error-message" style={{ fontSize: 13, color: C.text, marginBottom: 14 }}>
          {sanitizeDiagnosticText(diagnostic.message)}
        </div>
        <div
          data-testid="trading-room-error-diagnostics"
          style={{ display: "grid", gap: 6, fontSize: 12, marginBottom: 16 }}
        >
          <ErrorDiagnosticRow label="Endpoint" value={endpoint} />
          <ErrorDiagnosticRow label="Request ID" value={diagnostic.requestId} />
          <ErrorDiagnosticRow label="Correlation" value={diagnostic.correlationId} />
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          <button
            type="button"
            data-testid="trading-room-retry"
            onClick={onRetry}
            style={{
              padding: "7px 12px",
              border: `1px solid ${C.amber}`,
              background: "rgba(232,183,80,0.12)",
              color: C.amber,
              cursor: "pointer",
              fontSize: 13,
            }}
          >
            Retry
          </button>
          <button
            type="button"
            data-testid="trading-room-safe-reload"
            data-reload-href={reloadHref}
            onClick={() => safeReloadTradingRoom(reloadHref)}
            style={{
              padding: "7px 12px",
              border: `1px solid ${C.border}`,
              background: C.elevated,
              color: C.text,
              cursor: "pointer",
              fontSize: 13,
            }}
          >
            Reload latest bundle
          </button>
        </div>
      </div>
    </div>
  );
}

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
        background: C.surface,
        borderBottom: `1px solid ${C.border}`,
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
          fontSize: 13,
          fontWeight: activeStrategyId === undefined ? 600 : 400,
          color: activeStrategyId === undefined ? C.amber : C.secondary,
          borderBottom: activeStrategyId === undefined ? `2px solid ${C.amber}` : "2px solid transparent",
          whiteSpace: "nowrap",
        }}
      >
        Workbench Entry
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
            fontSize: 13,
            fontWeight: activeStrategyId === s.strategy_id ? 600 : 400,
            color: activeStrategyId === s.strategy_id ? C.amber : C.secondary,
            borderBottom:
              activeStrategyId === s.strategy_id
                ? `2px solid ${C.amber}`
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

const RISK_BG: Record<string, string> = {
  watch: C.riskWatch,
  warning: C.riskWarning,
  critical: C.riskCritical,
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
        background: RISK_BG[state] ?? C.riskWarning,
        borderBottom: `1px solid ${C.border}`,
        fontSize: 13,
        color: C.text,
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
        background: C.surface,
        borderBottom: `1px solid ${C.border}`,
        fontSize: 12,
        color: C.muted,
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
      <td colSpan={5} style={{ padding: "8px 16px", background: C.expandedRow, borderBottom: `2px solid ${C.border}` }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12, fontSize: 12, color: C.text }}>

          {/* Signal Quality */}
          <div data-testid="detail-confidence">
            <div style={{ fontWeight: 600, color: C.secondary, marginBottom: 4 }}>Signal Quality</div>
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
            <div style={{ fontWeight: 600, color: C.secondary, marginBottom: 4 }}>Expected Value</div>
            <div>Horizon: {ev.expected_value.horizon} ({ev.expected_value.unit})</div>
            <div>Gross: {ev.expected_value.gross > 0 ? "+" : ""}{ev.expected_value.gross.toFixed(4)}</div>
            <div>Cost: {ev.expected_value.cost.toFixed(4)}</div>
            <div>Net: {ev.expected_value.net > 0 ? "+" : ""}{ev.expected_value.net.toFixed(4)}</div>
            <div>Downside: {ev.expected_value.downside.toFixed(4)}</div>
          </div>

          {/* Suggested Action */}
          <div data-testid="detail-suggested-action">
            <div style={{ fontWeight: 600, color: C.secondary, marginBottom: 4 }}>Suggested Action</div>
            <div style={{ textTransform: "capitalize", fontWeight: 500 }}>{ev.suggested_action}</div>
            {ev.suggested_size && (
              <>
                {ev.suggested_size.size_hint && <div>Size hint: {ev.suggested_size.size_hint}</div>}
                {ev.suggested_size.portfolio_pct != null && (
                  <div>Portfolio %: {(ev.suggested_size.portfolio_pct * 100).toFixed(1)}%</div>
                )}
                <div style={{ color: C.muted, fontSize: 11 }}>Non-binding</div>
              </>
            )}
            {ev.data_cutoff && (
              <div style={{ marginTop: 4, color: C.secondary }}>Data cutoff: {ev.data_cutoff}</div>
            )}
            <div
              data-testid="detail-no-order-route"
              style={{ marginTop: 4, fontSize: 11, color: C.green, fontWeight: 500 }}
            >
              {ev.no_order_route_proof}
            </div>
          </div>

          {/* Invalidation */}
          <div data-testid="detail-invalidation">
            <div style={{ fontWeight: 600, color: C.secondary, marginBottom: 4 }}>Invalidation</div>
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
          <div data-testid="detail-rationale" style={{ marginTop: 10, fontSize: 12, color: C.text }}>
            <div style={{ fontWeight: 600, color: C.secondary, marginBottom: 4 }}>Rationale</div>
            {ev.rationale.map((r, i) => (
              <div key={i} style={{ display: "flex", gap: 8, marginBottom: 2 }}>
                <span style={{ color: C.muted, minWidth: 32 }}>{(r.confidence * 100).toFixed(0)}%</span>
                <span>{r.claim}</span>
              </div>
            ))}
          </div>
        )}

        {/* Risk Notes */}
        {ev.risk_notes.length > 0 && (
          <div data-testid="detail-risk-notes" style={{ marginTop: 10, fontSize: 12 }}>
            <div style={{ fontWeight: 600, color: C.secondary, marginBottom: 4 }}>Risk Notes</div>
            {ev.risk_notes.map((rn, i) => (
              <div
                key={i}
                style={{
                  padding: "4px 8px",
                  background: rn.severity === "critical" || rn.severity === "high" ? C.riskNoteCritical : C.riskNoteWatch,
                  borderRadius: 4,
                  marginBottom: 2,
                  color: C.text,
                }}
              >
                <span style={{ fontWeight: 500 }}>[{rn.severity}] {rn.domain}:</span> {rn.summary}
                {rn.mitigation && <span style={{ color: C.secondary }}> — {rn.mitigation}</span>}
              </div>
            ))}
          </div>
        )}

        {/* Evidence Refs */}
        {ev.evidence_refs.length > 0 && (
          <div data-testid="detail-evidence-refs" style={{ marginTop: 10, fontSize: 12 }}>
            <div style={{ fontWeight: 600, color: C.secondary, marginBottom: 4 }}>
              Evidence ({ev.evidence_refs.length})
            </div>
            {ev.evidence_refs.map((ref, i) => (
              <div key={i} style={{ color: C.secondary }}>
                <span style={{ color: C.muted }}>{ref.ref_type}</span> {ref.ref_id}
                {ref.summary ? ` — ${ref.summary}` : null}
              </div>
            ))}
          </div>
        )}

        {/* Trader Decision Actions */}
        <div data-testid="detail-trader-actions" style={{ marginTop: 12, display: "flex", gap: 8, alignItems: "center" }}>
          {callState === "success" ? (
            <span data-testid="detail-decision-confirmed" style={{ fontSize: 12, color: C.green, fontWeight: 500 }}>
              Decision recorded: {decidedChoice}
            </span>
          ) : (
            <>
              <span style={{ fontSize: 12, color: C.secondary, marginRight: 4 }}>Trader decision:</span>
              {(["approve", "reject", "defer", "modify"] as DecisionChoice[]).map((choice) => (
                <button
                  key={choice}
                  data-testid={`decide-${choice}-${ev.decision_event_id}`}
                  disabled={!canDecide}
                  onClick={() => handleDecide(choice)}
                  style={{
                    padding: "3px 10px",
                    fontSize: 12,
                    border: `1px solid ${C.border}`,
                    borderRadius: 4,
                    cursor: canDecide ? "pointer" : "not-allowed",
                    background: choice === "approve" ? C.approveBtn : choice === "reject" ? C.rejectBtn : C.elevated,
                    color: choice === "approve" ? C.approveBtnText : choice === "reject" ? C.rejectBtnText : C.secondary,
                    opacity: canDecide ? 1 : 0.5,
                  }}
                >
                  {choice.charAt(0).toUpperCase() + choice.slice(1)}
                </button>
              ))}
              {callState === "loading" && (
                <span data-testid="detail-decision-loading" style={{ fontSize: 12, color: C.muted }}>
                  Sending…
                </span>
              )}
              {callState === "error" && callError && (
                <span data-testid="detail-decision-error" style={{ fontSize: 12, color: C.red }}>
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
      <div style={{ padding: "8px 16px", fontWeight: 600, fontSize: 13, borderBottom: `1px solid ${C.border}`, color: C.text }}>
        Decision Event Queue
      </div>
      {loading ? (
        <div data-testid="event-queue-loading" style={{ padding: 16, fontSize: 13, color: C.muted }}>
          Loading events…
        </div>
      ) : events.length === 0 ? (
        <div data-testid="event-queue-empty" style={{ padding: 16, fontSize: 13, color: C.muted }}>
          No pending decision events.
        </div>
      ) : (
        <table
          data-testid="event-queue-table"
          style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, color: C.text }}
        >
          <thead>
            <tr style={{ borderBottom: `1px solid ${C.border}` }}>
              <th style={{ textAlign: "left", padding: "6px 16px", fontWeight: 500, color: C.secondary }}>Symbol</th>
              <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 500, color: C.secondary }}>Kind</th>
              <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 500, color: C.secondary }}>State</th>
              <th style={{ textAlign: "right", padding: "6px 8px", fontWeight: 500, color: C.secondary }}>Confidence</th>
              <th style={{ textAlign: "right", padding: "6px 16px", fontWeight: 500, color: C.secondary }}>EV (net)</th>
            </tr>
          </thead>
          <tbody>
            {events.map((ev) => (
              <React.Fragment key={ev.decision_event_id}>
                <tr
                  data-testid={`event-row-${ev.decision_event_id}`}
                  aria-expanded={expandedId === ev.decision_event_id}
                  style={{
                    borderBottom: expandedId === ev.decision_event_id ? "none" : `1px solid ${C.border}`,
                    cursor: "pointer",
                    background: expandedId === ev.decision_event_id ? C.expandedRow : undefined,
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
      style={{ borderLeft: `1px solid ${C.border}`, width: 240, overflow: "auto", flexShrink: 0, background: C.surface }}
    >
      <div style={{ padding: "8px 12px", fontWeight: 600, fontSize: 13, borderBottom: `1px solid ${C.border}`, color: C.text }}>
        Position Actions
      </div>
      {positionSummaries.length === 0 ? (
        <div style={{ padding: 12, fontSize: 13, color: C.muted }}>No open positions.</div>
      ) : (
        <ul style={{ margin: 0, padding: "8px 12px", listStyle: "none" }}>
          {positionSummaries.map((p, i) => (
            <li key={i} style={{ fontSize: 13, borderBottom: `1px solid ${C.border}`, padding: "4px 0", color: C.text }}>
              {JSON.stringify(p)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ── Default Dynamic Entry (no explicit strategy selected) ────────────────────

function pendingEventTotal(strategy: TradingRoomStrategyEntry): number {
  return (
    (strategy.pending_event_counts.entry ?? 0) +
    (strategy.pending_event_counts.add ?? 0) +
    (strategy.pending_event_counts.reduce ?? 0) +
    (strategy.pending_event_counts.exit ?? 0) +
    (strategy.pending_event_counts.review ?? 0)
  );
}

const MONITORING_PRIORITY: Record<TradingRoomStrategyEntry["monitoring_state"], number> = {
  monitoring: 5,
  paper_requested: 4,
  shadow: 3,
  paused: 2,
  inactive: 1,
};

function selectDefaultReadyStrategy(
  strategies: TradingRoomStrategyEntry[],
): TradingRoomStrategyEntry | undefined {
  return strategies
    .filter((strategy) => strategy.readiness_state === "ready")
    .slice()
    .sort((a, b) => {
      const recipeDiff = Number(Boolean(b.dashboard_recipe_id)) - Number(Boolean(a.dashboard_recipe_id));
      if (recipeDiff !== 0) return recipeDiff;
      const pendingDiff = pendingEventTotal(b) - pendingEventTotal(a);
      if (pendingDiff !== 0) return pendingDiff;
      const monitoringDiff = MONITORING_PRIORITY[b.monitoring_state] - MONITORING_PRIORITY[a.monitoring_state];
      if (monitoringDiff !== 0) return monitoringDiff;
      return a.title.localeCompare(b.title);
    })[0];
}

function readinessReason(strategy: TradingRoomStrategyEntry): string {
  if (strategy.readiness_state === "conditional") {
    return "Conditional readiness: continue Strategy Workshop validation before proposal generation.";
  }
  if (strategy.readiness_state === "stale") {
    return strategy.staleness_reasons?.[0] ?? "Readiness is stale; refresh workshop evidence.";
  }
  return "Blocked readiness: Strategy Workshop must close the missing gate before Trading Room entry.";
}

interface TradingRoomDefaultEntryProps {
  aggregate: TradingRoomAggregate;
  onOpenWorkshop?: () => void;
  onStrategySelect: (strategyId: string) => void;
}

function TradingRoomDefaultEntry({
  aggregate,
  onOpenWorkshop,
  onStrategySelect,
}: TradingRoomDefaultEntryProps): JSX.Element {
  const strategies = aggregate.strategies;
  const pendingTotal = strategies.reduce((total, strategy) => total + pendingEventTotal(strategy), 0);
  const entryState = strategies.length === 0 ? "empty" : "no-ready-strategy";
  const readinessRows = strategies
    .slice()
    .sort((a, b) => {
      const readinessOrder: Record<TradingRoomStrategyEntry["readiness_state"], number> = {
        conditional: 0,
        stale: 1,
        blocked: 2,
        ready: 3,
      };
      const orderDiff = readinessOrder[a.readiness_state] - readinessOrder[b.readiness_state];
      if (orderDiff !== 0) return orderDiff;
      return (b.candidate_count ?? 0) - (a.candidate_count ?? 0);
    });

  return (
    <div
      data-entry-state={entryState}
      data-testid="trading-room-default-entry"
      style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}
    >
      <QueueSummaryStrip {...aggregate.queue_summary} />
      <RiskBanner
        state={aggregate.risk_summary.state}
        summary={aggregate.risk_summary.summary}
        alerts={aggregate.risk_summary.alerts}
      />

      <div style={{ flex: 1, overflow: "auto", padding: 18 }}>
        <section
          style={{
            background: C.surface,
            border: `1px solid ${C.border}`,
            borderRadius: 8,
            display: "grid",
            gap: 14,
            padding: 18,
          }}
        >
          <div>
            <div style={{ color: C.secondary, fontSize: 12, fontWeight: 700 }}>Dynamic Entry</div>
            <h2 style={{ color: C.text, fontSize: 20, fontWeight: 800, letterSpacing: 0, margin: "4px 0 0" }}>
              {strategies.length === 0
                ? "Strategy Workshop is the next step"
                : "No strategy is ready for proposal generation yet"}
            </h2>
            <p style={{ color: C.secondary, fontSize: 13, lineHeight: 1.55, margin: "8px 0 0", maxWidth: 860 }}>
              {strategies.length === 0
                ? "The BFF returned no user-scoped Trading Room strategies, so the default route starts from workshop intake instead of an empty table shell."
                : "The BFF returned strategies, but none has reached the trading_room readiness gate. Continue the readiness workflow before opening a generated V11 workspace."}
            </p>
          </div>

          <div
            data-testid="trading-room-default-snapshot"
            style={{
              color: C.secondary,
              display: "flex",
              flexWrap: "wrap",
              fontSize: 12,
              gap: 12,
            }}
          >
            <span>Strategies: {strategies.length}</span>
            <span>Ready: 0</span>
            <span>Pending decisions: {pendingTotal}</span>
            <span>Snapshot: {aggregate.snapshot_at || "unavailable"}</span>
            <span>Data cutoff: {aggregate.data_cutoff || "unavailable"}</span>
          </div>

          <div>
            <button
              data-testid="trading-room-open-workshop"
              disabled={!onOpenWorkshop}
              onClick={onOpenWorkshop}
              style={{
                background: onOpenWorkshop ? C.amber : C.elevated,
                border: `1px solid rgba(232,183,80,0.45)`,
                borderRadius: 6,
                color: onOpenWorkshop ? C.bg : C.muted,
                cursor: onOpenWorkshop ? "pointer" : "not-allowed",
                fontSize: 13,
                fontWeight: 800,
                padding: "8px 12px",
              }}
              type="button"
            >
              Open Strategy Workshop
            </button>
          </div>
        </section>

        {strategies.length > 0 ? (
          <section
            data-testid="trading-room-readiness-entry"
            style={{
              display: "grid",
              gap: 10,
              gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
              marginTop: 14,
            }}
          >
            {readinessRows.map((strategy) => (
              <article
                data-testid={`trading-room-readiness-${strategy.strategy_id}`}
                key={strategy.strategy_id}
                style={{
                  background: C.surface,
                  border: `1px solid ${C.border}`,
                  borderRadius: 8,
                  color: C.text,
                  padding: 14,
                }}
              >
                <div style={{ color: C.secondary, fontSize: 11, fontWeight: 700, textTransform: "uppercase" }}>
                  {strategy.readiness_state} · {strategy.monitoring_state}
                </div>
                <h3 style={{ fontSize: 15, fontWeight: 800, margin: "4px 0 0" }}>{strategy.title}</h3>
                <p style={{ color: C.secondary, fontSize: 12, lineHeight: 1.45, margin: "8px 0 0" }}>
                  {readinessReason(strategy)}
                </p>
                <div style={{ color: C.muted, display: "flex", flexWrap: "wrap", fontSize: 12, gap: 10, marginTop: 10 }}>
                  <span>Version: {strategy.strategy_spec_registry_id}</span>
                  <span>Candidates: {strategy.candidate_count ?? 0}</span>
                  <span>Pending: {pendingEventTotal(strategy)}</span>
                </div>
                <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
                  <button
                    data-testid={`trading-room-open-workshop-${strategy.strategy_id}`}
                    disabled={!onOpenWorkshop}
                    onClick={onOpenWorkshop}
                    style={{
                      background: "transparent",
                      border: `1px solid ${C.border}`,
                      borderRadius: 6,
                      color: onOpenWorkshop ? C.amber : C.muted,
                      cursor: onOpenWorkshop ? "pointer" : "not-allowed",
                      fontSize: 12,
                      padding: "6px 10px",
                    }}
                    type="button"
                  >
                    Review readiness
                  </button>
                  {strategy.readiness_state === "ready" && (
                    <button
                      data-testid={`trading-room-open-strategy-${strategy.strategy_id}`}
                      onClick={() => onStrategySelect(strategy.strategy_id)}
                      style={{
                        background: C.elevated,
                        border: `1px solid ${C.border}`,
                        borderRadius: 6,
                        color: C.text,
                        cursor: "pointer",
                        fontSize: 12,
                        padding: "6px 10px",
                      }}
                      type="button"
                    >
                      Open workspace
                    </button>
                  )}
                </div>
              </article>
            ))}
          </section>
        ) : (
          <section
            data-testid="trading-room-workshop-empty-entry"
            style={{
              background: C.surface,
              border: `1px solid ${C.border}`,
              borderRadius: 8,
              color: C.secondary,
              fontSize: 13,
              lineHeight: 1.5,
              marginTop: 14,
              padding: 14,
            }}
          >
            No BFF strategy records were available for this scope. Continue in the Strategy Workshop to create
            or restore a strategy-specific readiness packet.
          </section>
        )}
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
          style={{ display: "flex", gap: 4, marginBottom: 8, borderBottom: `1px solid ${C.border}`, paddingBottom: 4 }}
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
                color: idx === activeViewIdx ? C.amber : C.secondary,
                borderBottom: idx === activeViewIdx ? `2px solid ${C.amber}` : "2px solid transparent",
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
      <div style={{ padding: "8px 16px", borderBottom: `1px solid ${C.border}`, fontSize: 13, flexShrink: 0, color: C.text }}>
        <strong>{strategy?.title ?? strategyId}</strong>
        {strategy && (
          <span style={{ marginLeft: 12, color: C.secondary }}>
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
              style={{ padding: 16, fontSize: 13, color: C.muted }}
            >
              Loading strategy workspace…
            </div>
          ) : recipe ? (
            <StrategyRecipeSection key={strategyId} recipe={recipe} />
          ) : (
            <div
              data-testid="strategy-recipe-unavailable"
              style={{ padding: 16, fontSize: 13, color: C.muted }}
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
  onOpenWorkshop?: () => void;
}

export function TradingRoomPage({
  strategyId,
  onStrategySelect,
  onOpenWorkshop,
}: TradingRoomPageProps): JSX.Element {
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [aggregate, setAggregate] = useState<TradingRoomAggregate | null>(null);
  const [loadError, setLoadError] = useState<TradingRoomBffDiagnostic | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [events, setEvents] = useState<TradingDecisionEvent[]>([]);
  const [eventsLoading, setEventsLoading] = useState(true);
  const [eventsEtag, setEventsEtag] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoadState("loading");
    setLoadError(null);

    getTradingRoom()
      .then((agg) => {
        if (cancelled) return;
        setAggregate(agg);
        setLoadState("loaded");
      })
      .catch((error) => {
        if (cancelled) return;
        setLoadError(diagnosticFromUnknown(error));
        setLoadState("error");
      });

    return () => {
      cancelled = true;
    };
  }, [loadAttempt]);

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
        style={{ display: "flex", height: "100%", alignItems: "center", justifyContent: "center", fontSize: 13, color: C.muted }}
      >
        Loading Trading Room…
      </div>
    );
  }

  if (loadState === "error" || !aggregate) {
    return (
      <TradingRoomErrorState
        diagnostic={loadError ?? diagnosticFromUnknown(new Error("Trading Room aggregate missing"))}
        onRetry={() => setLoadAttempt((attempt) => attempt + 1)}
      />
    );
  }

  const defaultReadyStrategy =
    !strategyId && aggregate ? selectDefaultReadyStrategy(aggregate.strategies) : undefined;
  const effectiveStrategyId = strategyId ?? defaultReadyStrategy?.strategy_id;
  const activeStrategy = effectiveStrategyId
    ? aggregate.strategies.find((s) => s.strategy_id === effectiveStrategyId)
    : undefined;

  return (
    <div
      data-testid="trading-room-page"
      style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden", background: C.bg }}
    >
      <StrategyLensSwitcher
        strategies={aggregate.strategies}
        activeStrategyId={effectiveStrategyId}
        onSelect={handleStrategySelect}
      />

      {effectiveStrategyId ? (
        <StrategyWorkspaceView
          strategyId={effectiveStrategyId}
          strategy={activeStrategy}
          aggregate={aggregate}
          events={events}
          eventsLoading={eventsLoading}
          eventsEtag={eventsEtag}
        />
      ) : (
        <TradingRoomDefaultEntry
          aggregate={aggregate}
          onOpenWorkshop={onOpenWorkshop}
          onStrategySelect={handleStrategySelect}
        />
      )}
    </div>
  );
}
