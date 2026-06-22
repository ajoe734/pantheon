import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/bff-v1/agora/tradingRoom", () => ({
  getTradingRoom: vi.fn(),
  listDecisionEvents: vi.fn(),
}));

import { TradingRoomPage } from "./TradingRoomPage";
import * as tradingRoomModule from "@/lib/bff-v1/agora/tradingRoom";

const MOCK_AGGREGATE = {
  spec_version: "1.0" as const,
  user_scope_ref: "scope-001",
  strategies: [
    {
      strategy_id: "strat-001",
      strategy_spec_registry_id: "reg-001",
      title: "Alpha Momentum",
      readiness_state: "ready" as const,
      monitoring_state: "monitoring" as const,
      pending_event_counts: { entry: 2, add: 0, reduce: 1, exit: 0, review: 1 },
    },
    {
      strategy_id: "strat-002",
      strategy_spec_registry_id: "reg-002",
      title: "Pairs Arbitrage",
      readiness_state: "conditional" as const,
      monitoring_state: "shadow" as const,
      pending_event_counts: { entry: 0, add: 0, reduce: 0, exit: 0, review: 0 },
    },
  ],
  queue_summary: { entry: 2, add: 0, reduce: 1, exit: 0, review: 1 },
  risk_summary: { state: "normal" as const },
  snapshot_at: "2026-06-22T10:00:00Z",
  data_cutoff: "2026-06-22T09:55:00Z",
};

const MOCK_DECISION_EVENT = {
  spec_version: "1.0" as const,
  decision_event_id: "evt-001",
  event_kind: "entry" as const,
  origin: "strategy_signal" as const,
  strategy_id: "strat-001",
  strategy_spec_registry_id: "reg-001",
  subject: { symbol: "AAPL", asset_class: "equity" },
  state: "triggered" as const,
  triggered_at: "2026-06-22T09:50:00Z",
  confidence: { value: 0.78, basis: "model" as const, calibration_state: "calibrated" as const },
  probability: { target_outcome: "price_increase_5pct", horizon: "5d", value: 0.72 },
  expected_value: { horizon: "5d", unit: "pct_return" as const, gross: 0.05, cost: 0.002, net: 0.048, downside: -0.02 },
  rationale: [{ claim: "Strong momentum signal confirmed by volume.", confidence: 0.78 }],
  risk_notes: [],
  evidence_refs: [],
  invalidation: { conditions: ["Gap up >5% before entry"], current_state: "valid" as const },
  suggested_action: "enter" as const,
  no_order_route_proof: "agora_decision_support_only" as const,
};

afterEach(cleanup);

describe("TradingRoomPage", () => {
  beforeEach(() => {
    vi.mocked(tradingRoomModule.getTradingRoom).mockResolvedValue(MOCK_AGGREGATE);
    vi.mocked(tradingRoomModule.listDecisionEvents).mockResolvedValue([MOCK_DECISION_EVENT]);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows loading state before data resolves", () => {
    vi.mocked(tradingRoomModule.getTradingRoom).mockReturnValue(new Promise(() => {}));
    vi.mocked(tradingRoomModule.listDecisionEvents).mockReturnValue(new Promise(() => {}));
    render(<TradingRoomPage />);
    expect(screen.getByTestId("trading-room-loading")).toBeDefined();
  });

  it("renders the page with strategy lens switcher after load", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("trading-room-page");
    expect(screen.getByTestId("strategy-lens-switcher")).toBeDefined();
  });

  it("shows all-strategies button in the switcher", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("trading-room-page");
    expect(screen.getByTestId("strategy-lens-all")).toBeDefined();
  });

  it("renders each strategy as a selectable lens", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("trading-room-page");
    expect(screen.getByTestId("strategy-lens-strat-001")).toBeDefined();
    expect(screen.getByTestId("strategy-lens-strat-002")).toBeDefined();
  });

  it("renders the aggregate view by default (no strategyId)", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("trading-room-aggregate-view");
  });

  it("shows queue summary strip in the aggregate view", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("queue-summary-strip");
    expect(screen.getByTestId("queue-entry-count").textContent).toContain("2");
    expect(screen.getByTestId("queue-reduce-count").textContent).toContain("1");
    expect(screen.getByTestId("queue-review-count").textContent).toContain("1");
  });

  it("renders strategy list table in the aggregate view", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("strategy-list-table");
    expect(screen.getByTestId("strategy-row-strat-001")).toBeDefined();
    expect(screen.getByTestId("strategy-row-strat-002")).toBeDefined();
  });

  it("renders the decision event queue with loaded events", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("event-queue-table");
    expect(screen.getByTestId("event-row-evt-001")).toBeDefined();
  });

  it("shows loading state for event queue while events are pending", async () => {
    vi.mocked(tradingRoomModule.listDecisionEvents).mockReturnValue(new Promise(() => {}));
    render(<TradingRoomPage />);
    await screen.findByTestId("trading-room-page");
    expect(screen.getByTestId("event-queue-loading")).toBeDefined();
  });

  it("renders a strategy workspace view when strategyId is provided", async () => {
    render(<TradingRoomPage strategyId="strat-001" />);
    await screen.findByTestId("strategy-workspace-strat-001");
  });

  it("marks the correct strategy as selected in the switcher", async () => {
    render(<TradingRoomPage strategyId="strat-001" />);
    await screen.findByTestId("trading-room-page");
    const btn = screen.getByTestId("strategy-lens-strat-001");
    expect(btn.getAttribute("aria-selected")).toBe("true");
  });

  it("filters events to the selected strategy in the workspace view", async () => {
    const otherEvent = {
      ...MOCK_DECISION_EVENT,
      decision_event_id: "evt-002",
      strategy_id: "strat-002",
    };
    vi.mocked(tradingRoomModule.listDecisionEvents).mockResolvedValue([
      MOCK_DECISION_EVENT,
      otherEvent,
    ]);
    render(<TradingRoomPage strategyId="strat-001" />);
    await screen.findByTestId("strategy-workspace-strat-001");
    expect(screen.getByTestId("event-row-evt-001")).toBeDefined();
    expect(screen.queryByTestId("event-row-evt-002")).toBeNull();
  });

  it("shows error state when getTradingRoom fails", async () => {
    vi.mocked(tradingRoomModule.getTradingRoom).mockRejectedValue(new Error("Network error"));
    render(<TradingRoomPage />);
    await screen.findByTestId("trading-room-error");
  });

  it("calls getTradingRoom via the BFF module (not direct fetch)", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("trading-room-page");
    expect(tradingRoomModule.getTradingRoom).toHaveBeenCalled();
  });

  it("calls listDecisionEvents via the BFF module (not direct fetch)", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("trading-room-page");
    expect(tradingRoomModule.listDecisionEvents).toHaveBeenCalled();
  });

  it("shows position action queue panel", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("trading-room-page");
    expect(screen.getByTestId("position-action-queue")).toBeDefined();
  });

  it("does not show risk banner when risk state is normal", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("trading-room-page");
    expect(screen.queryByTestId("risk-banner")).toBeNull();
  });

  it("shows risk banner when risk state is not normal", async () => {
    vi.mocked(tradingRoomModule.getTradingRoom).mockResolvedValue({
      ...MOCK_AGGREGATE,
      risk_summary: { state: "warning", summary: "Volatility elevated" },
    });
    render(<TradingRoomPage />);
    await screen.findByTestId("risk-banner");
    const banner = screen.getByTestId("risk-banner");
    expect(banner.getAttribute("data-risk-state")).toBe("warning");
  });
});
