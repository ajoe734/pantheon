import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/bff-v1/agora/tradingRoom", () => ({
  getTradingRoom: vi.fn(),
  listDecisionEvents: vi.fn(),
  decideOnEvent: vi.fn(),
}));

vi.mock("@/lib/bff-v1/agora/dashboard", () => ({
  getDashboardRecipeById: vi.fn(),
}));

import { TradingRoomPage } from "./TradingRoomPage";
import * as tradingRoomModule from "@/lib/bff-v1/agora/tradingRoom";
import * as dashboardModule from "@/lib/bff-v1/agora/dashboard";

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
      dashboard_recipe_id: "recipe-001",
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
  state: "pending_review" as const,
  triggered_at: "2026-06-22T09:50:00Z",
  confidence: {
    value: 0.78,
    basis: "model" as const,
    calibration_state: "calibrated" as const,
    sample_size: 120,
  },
  probability: {
    target_outcome: "price_increase_5pct",
    horizon: "5d",
    value: 0.72,
    ci_lower: 0.65,
    ci_upper: 0.79,
  },
  expected_value: {
    horizon: "5d",
    unit: "pct_return" as const,
    gross: 0.05,
    cost: 0.002,
    net: 0.048,
    downside: -0.02,
  },
  rationale: [
    { claim: "Strong momentum signal confirmed by volume.", confidence: 0.78 },
  ],
  risk_notes: [
    { severity: "watch" as const, domain: "volatility", summary: "IV elevated 10% above 30d avg" },
  ],
  evidence_refs: [
    { ref_type: "evidence_bundle" as const, ref_id: "eb-001", summary: "Momentum backtest bundle" },
  ],
  invalidation: {
    conditions: ["Gap up >5% before entry"],
    current_state: "valid" as const,
  },
  suggested_action: "enter" as const,
  suggested_size: {
    size_hint: "medium" as const,
    portfolio_pct: 0.04,
    non_binding: true as const,
  },
  data_cutoff: "2026-06-22T09:55:00Z",
  no_order_route_proof: "agora_decision_support_only" as const,
};

const MOCK_RECIPE: import("@/lib/bff-v1/agora/types").DashboardRecipeV2 = {
  recipe_id: "recipe-001",
  spec_version: "2.0",
  tenant_id: "tenant-001",
  user_id: "user-001",
  strategy_id: "strat-001",
  strategy_version_id: "v1",
  workspace: "trading_room",
  phase: "monitoring",
  generated_by: "system_default",
  change_reason: "initial",
  version: 1,
  status: "active",
  created_at: "2026-06-22T00:00:00Z",
  updated_at: "2026-06-22T00:00:00Z",
  views: [
    {
      view_id: "view-001",
      title: "Monitoring",
      purpose: "Trading room monitoring view",
      layout_template_id: "default",
      breakpoints: {},
      placements: [],
      widgets: [],
    },
    {
      view_id: "view-002",
      title: "Performance",
      purpose: "Performance review view",
      layout_template_id: "default",
      breakpoints: {},
      placements: [],
      widgets: [],
    },
  ],
};

afterEach(cleanup);

describe("TradingRoomPage", () => {
  beforeEach(() => {
    vi.mocked(tradingRoomModule.getTradingRoom).mockResolvedValue(MOCK_AGGREGATE);
    vi.mocked(tradingRoomModule.listDecisionEvents).mockResolvedValue({
      items: [MOCK_DECISION_EVENT],
      etag: '"events-etag-v1"',
    });
    vi.mocked(dashboardModule.getDashboardRecipeById).mockResolvedValue(MOCK_RECIPE);
    vi.mocked(tradingRoomModule.decideOnEvent).mockResolvedValue({});
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows loading state before data resolves", () => {
    vi.mocked(tradingRoomModule.getTradingRoom).mockReturnValue(new Promise(() => {}));
    vi.mocked(tradingRoomModule.listDecisionEvents).mockReturnValue(
      new Promise<{ items: typeof MOCK_DECISION_EVENT[], etag: null }>(() => {}),
    );
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
    vi.mocked(tradingRoomModule.listDecisionEvents).mockReturnValue(
      new Promise<{ items: typeof MOCK_DECISION_EVENT[], etag: null }>(() => {}),
    );
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
    vi.mocked(tradingRoomModule.listDecisionEvents).mockResolvedValue({
      items: [MOCK_DECISION_EVENT, otherEvent],
      etag: null,
    });
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

  // ── Strategy Recipe Workspace ──────────────────────────────────────────────

  it("calls getDashboardRecipeById with the strategy's dashboard_recipe_id", async () => {
    render(<TradingRoomPage strategyId="strat-001" />);
    await screen.findByTestId("strategy-workspace-strat-001");
    expect(dashboardModule.getDashboardRecipeById).toHaveBeenCalledWith("recipe-001");
  });

  it("renders the strategy recipe workspace when recipe is available", async () => {
    render(<TradingRoomPage strategyId="strat-001" />);
    await screen.findByTestId("strategy-recipe-workspace");
  });

  it("renders recipe view tabs when multiple views are present", async () => {
    render(<TradingRoomPage strategyId="strat-001" />);
    await screen.findByTestId("recipe-view-tabs");
    expect(screen.getByTestId("recipe-view-tab-view-001")).toBeDefined();
    expect(screen.getByTestId("recipe-view-tab-view-002")).toBeDefined();
  });

  it("does not render view tabs when only one view is present", async () => {
    vi.mocked(dashboardModule.getDashboardRecipeById).mockResolvedValue({
      ...MOCK_RECIPE,
      views: [MOCK_RECIPE.views[0]],
    });
    render(<TradingRoomPage strategyId="strat-001" />);
    await screen.findByTestId("strategy-recipe-workspace");
    expect(screen.queryByTestId("recipe-view-tabs")).toBeNull();
  });

  it("shows recipe unavailable placeholder when strategy has no dashboard_recipe_id", async () => {
    render(<TradingRoomPage strategyId="strat-002" />);
    await screen.findByTestId("strategy-recipe-unavailable");
  });

  it("shows recipe unavailable placeholder when getDashboardRecipeById returns null", async () => {
    vi.mocked(dashboardModule.getDashboardRecipeById).mockResolvedValue(null);
    render(<TradingRoomPage strategyId="strat-001" />);
    await screen.findByTestId("strategy-recipe-unavailable");
  });

  it("shows loading state while recipe is being fetched", async () => {
    vi.mocked(dashboardModule.getDashboardRecipeById).mockReturnValue(new Promise(() => {}));
    render(<TradingRoomPage strategyId="strat-001" />);
    await screen.findByTestId("strategy-workspace-strat-001");
    expect(screen.getByTestId("strategy-recipe-loading")).toBeDefined();
  });

  // ── Decision Event Detail ──────────────────────────────────────────────────

  it("expands event detail panel when row is clicked", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("event-row-evt-001");
    fireEvent.click(screen.getByTestId("event-row-evt-001"));
    expect(screen.getByTestId("event-detail-evt-001")).toBeDefined();
  });

  it("shows confidence and calibration in expanded detail", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("event-row-evt-001");
    fireEvent.click(screen.getByTestId("event-row-evt-001"));
    expect(screen.getByTestId("detail-confidence").textContent).toContain("78%");
    expect(screen.getByTestId("detail-calibration").textContent).toContain("calibrated");
  });

  it("shows probability and CI interval in expanded detail", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("event-row-evt-001");
    fireEvent.click(screen.getByTestId("event-row-evt-001"));
    expect(screen.getByTestId("detail-probability").textContent).toContain("72%");
    expect(screen.getByTestId("detail-probability-interval").textContent).toContain("65%");
  });

  it("shows expected value breakdown in expanded detail", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("event-row-evt-001");
    fireEvent.click(screen.getByTestId("event-row-evt-001"));
    const ev = screen.getByTestId("detail-expected-value");
    expect(ev.textContent).toContain("pct_return");
    expect(ev.textContent).toContain("5d");
  });

  it("shows rationale claims in expanded detail", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("event-row-evt-001");
    fireEvent.click(screen.getByTestId("event-row-evt-001"));
    expect(screen.getByTestId("detail-rationale").textContent).toContain("Strong momentum");
  });

  it("shows risk notes in expanded detail", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("event-row-evt-001");
    fireEvent.click(screen.getByTestId("event-row-evt-001"));
    expect(screen.getByTestId("detail-risk-notes").textContent).toContain("volatility");
  });

  it("shows evidence refs in expanded detail", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("event-row-evt-001");
    fireEvent.click(screen.getByTestId("event-row-evt-001"));
    expect(screen.getByTestId("detail-evidence-refs").textContent).toContain("eb-001");
  });

  it("shows invalidation conditions in expanded detail", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("event-row-evt-001");
    fireEvent.click(screen.getByTestId("event-row-evt-001"));
    expect(screen.getByTestId("detail-invalidation").textContent).toContain("Gap up");
  });

  it("shows no_order_route_proof in expanded detail", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("event-row-evt-001");
    fireEvent.click(screen.getByTestId("event-row-evt-001"));
    expect(screen.getByTestId("detail-no-order-route").textContent).toContain(
      "agora_decision_support_only",
    );
  });

  it("shows trader decision buttons in expanded detail", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("event-row-evt-001");
    fireEvent.click(screen.getByTestId("event-row-evt-001"));
    expect(screen.getByTestId("decide-approve-evt-001")).toBeDefined();
    expect(screen.getByTestId("decide-reject-evt-001")).toBeDefined();
    expect(screen.getByTestId("decide-defer-evt-001")).toBeDefined();
    expect(screen.getByTestId("decide-modify-evt-001")).toBeDefined();
  });

  it("calls decideOnEvent with ifMatch, idempotencyKey, and requestId when trader clicks approve", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("event-row-evt-001");
    fireEvent.click(screen.getByTestId("event-row-evt-001"));
    fireEvent.click(screen.getByTestId("decide-approve-evt-001"));
    await waitFor(() =>
      expect(tradingRoomModule.decideOnEvent).toHaveBeenCalledWith(
        "evt-001",
        { decision: "approve" },
        expect.objectContaining({
          ifMatch: '"events-etag-v1"',
          idempotencyKey: expect.any(String),
          requestId: expect.any(String),
        }),
      ),
    );
  });

  it("shows confirmation after successful trader decision", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("event-row-evt-001");
    fireEvent.click(screen.getByTestId("event-row-evt-001"));
    fireEvent.click(screen.getByTestId("decide-approve-evt-001"));
    await screen.findByTestId("detail-decision-confirmed");
    expect(screen.getByTestId("detail-decision-confirmed").textContent).toContain("approve");
  });

  it("shows error message when decideOnEvent fails", async () => {
    vi.mocked(tradingRoomModule.decideOnEvent).mockRejectedValue(new Error("Server error"));
    render(<TradingRoomPage />);
    await screen.findByTestId("event-row-evt-001");
    fireEvent.click(screen.getByTestId("event-row-evt-001"));
    fireEvent.click(screen.getByTestId("decide-reject-evt-001"));
    await screen.findByTestId("detail-decision-error");
    expect(screen.getByTestId("detail-decision-error").textContent).toContain("Server error");
  });

  it("collapses event detail when row is clicked again", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("event-row-evt-001");
    fireEvent.click(screen.getByTestId("event-row-evt-001"));
    expect(screen.getByTestId("event-detail-evt-001")).toBeDefined();
    fireEvent.click(screen.getByTestId("event-row-evt-001"));
    expect(screen.queryByTestId("event-detail-evt-001")).toBeNull();
  });

  it("shows suggested action in expanded detail", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("event-row-evt-001");
    fireEvent.click(screen.getByTestId("event-row-evt-001"));
    expect(screen.getByTestId("detail-suggested-action").textContent).toContain("enter");
  });
});
