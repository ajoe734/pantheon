import React, { useEffect, useState } from "react";
import {
  getTradingRoom,
  listDecisionEvents,
  type TradingRoomAggregate,
  type TradingRoomStrategyEntry,
  type TradingDecisionEvent,
} from "@/lib/bff-v1/agora/tradingRoom";

interface TradingRoomPageProps {
  strategyId?: string;
  onStrategySelect?: (strategyId: string | undefined) => void;
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
}

function TradingEventQueue({ events, loading }: TradingEventQueueProps): JSX.Element {
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
              <tr
                key={ev.decision_event_id}
                data-testid={`event-row-${ev.decision_event_id}`}
                style={{ borderBottom: "1px solid #f1f5f9" }}
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
  onStrategySelect: (strategyId: string) => void;
}

function AggregateView({
  aggregate,
  events,
  eventsLoading,
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
          <TradingEventQueue events={events} loading={eventsLoading} />
        </div>
        <PositionActionQueue positionSummaries={aggregate.position_summaries ?? []} />
      </div>
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
}

function StrategyWorkspaceView({
  strategyId,
  strategy,
  aggregate,
  events,
  eventsLoading,
}: StrategyWorkspaceViewProps): JSX.Element {
  const filteredEvents = events.filter((ev) => ev.strategy_id === strategyId);
  return (
    <div
      data-testid={`strategy-workspace-${strategyId}`}
      style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}
    >
      <div style={{ padding: "8px 16px", borderBottom: "1px solid #e2e8f0", fontSize: 13 }}>
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
        <TradingEventQueue events={filteredEvents} loading={eventsLoading} />
        <PositionActionQueue positionSummaries={aggregate.position_summaries ?? []} />
      </div>
    </div>
  );
}

// ── Root Page ─────────────────────────────────────────────────────────────────

type LoadState = "loading" | "loaded" | "error";

export function TradingRoomPage({ strategyId, onStrategySelect }: TradingRoomPageProps): JSX.Element {
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [aggregate, setAggregate] = useState<TradingRoomAggregate | null>(null);
  const [events, setEvents] = useState<TradingDecisionEvent[]>([]);
  const [eventsLoading, setEventsLoading] = useState(true);

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
      .then((evs) => {
        if (cancelled) return;
        setEvents(evs);
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
        />
      ) : (
        <AggregateView
          aggregate={aggregate}
          events={events}
          eventsLoading={eventsLoading}
          onStrategySelect={(id) => handleStrategySelect(id)}
        />
      )}
    </div>
  );
}
