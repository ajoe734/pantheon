import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/bff-v1/agora/tradingRoom", () => ({
  acceptWidgetRevisionProposal: vi.fn(),
  acceptWorkspaceProposal: vi.fn(),
  createWidgetRevisionProposal: vi.fn(),
  createWorkspaceProposal: vi.fn(),
  getTradingRoom: vi.fn(),
  getTradingRoomStrategy: vi.fn(),
  getTradingRoomWorkspace: vi.fn(),
  listTradingRoomWorkspaceVersions: vi.fn(),
  listDecisionEvents: vi.fn(),
  patchTradingRoomWorkspaceLayout: vi.fn(),
  rollbackTradingRoomWorkspaceVersion: vi.fn(),
  decideOnEvent: vi.fn(),
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

const MOCK_WORKSPACE: tradingRoomModule.TradingRoomWorkspace = {
  id: "trw-001",
  userId: "user-001",
  strategyId: "strat-001",
  strategyVersion: "v1",
  dashboardVersion: 1,
  activeViewId: "strategy_overview",
  status: "active",
  generatedBy: "trading_servant",
  createdAt: "2026-06-22T00:00:00Z",
  updatedAt: "2026-06-22T00:00:00Z",
  views: [
    {
      id: "strategy_overview",
      title: "Strategy Overview",
      purpose: "Trading room monitoring view",
      widgetCount: 1,
      widgets: [
        {
          id: "overview_candidate_funnel",
          widgetType: "candidate_funnel",
          title: "Candidate Funnel",
          purpose: "Compare candidate states.",
          whyIncluded: "Shows candidate conversion.",
          dataSource: "agora.candidate.members",
          query: { filters: {}, sort: {}, limit: 250, window: "20d" },
          chartSpec: {
            spec_version: "1.0",
            kind: "bar",
            encodings: {
              x: { field: "label", type: "nominal" },
              y: { field: "value", type: "quantitative" },
            },
          },
          interactions: [{ kind: "request_widget_revision" }],
          placement: { x: 0, y: 0, width: 4, height: 4, minWidth: 2, minHeight: 2 },
          minSize: { width: 2, height: 2 },
          maxSize: { width: 8, height: 8 },
          sensitivity: "user_private",
          visible: true,
        },
      ],
    },
    {
      id: "winner_branch_intelligence",
      title: "Performance",
      purpose: "Performance review view",
      widgetCount: 0,
      widgets: [],
    },
  ],
};

const MOCK_VERSION: tradingRoomModule.TradingRoomWorkspaceVersion = {
  id: "trwv-001",
  workspaceId: "trw-001",
  dashboardVersion: 1,
  strategyId: "strat-001",
  strategyVersion: "v1",
  views: MOCK_WORKSPACE.views,
  changeSummary: "v1 - trading servant initial workspace proposal",
};

const MOCK_PROPOSAL: tradingRoomModule.TradingRoomWorkspaceProposal = {
  strategyId: "strat-001",
  strategyVersion: "v1",
  proposalId: "trp-001",
  generatedAt: "2026-06-22T00:00:00Z",
  status: "preview",
  views: MOCK_WORKSPACE.views,
};

const MOCK_REVISION_PROPOSAL: tradingRoomModule.TradingRoomWidgetRevisionProposal = {
  id: "wrp-001",
  workspaceId: "trw-001",
  viewId: "strategy_overview",
  widgetId: "overview_candidate_funnel",
  instruction: "Convert the widget to a faster comparison view.",
  beforeSpec: MOCK_WORKSPACE.views[0].widgets[0],
  proposedSpec: {
    ...MOCK_WORKSPACE.views[0].widgets[0],
    title: "Candidate Funnel Live Revision",
  },
  rationale: "Live revision.",
  warnings: [],
  dataAvailability: "partial",
  status: "preview",
};

function workspaceWithVersion(version: number): tradingRoomModule.TradingRoomWorkspace {
  return {
    ...MOCK_WORKSPACE,
    dashboardVersion: version,
    updatedAt: `2026-06-22T00:0${version}:00Z`,
  };
}

function diagnosticError(status: number, code: string, message = "diagnostic failure"): Error {
  return Object.assign(new Error(message), {
    diagnostic: {
      method: "GET",
      url: "https://bff.example.test/bff/agora/trading-room",
      status,
      code,
      message,
      requestId: status > 0 ? "req-123" : null,
      correlationId: status > 0 ? "corr-123" : null,
      retryable: true,
    },
  });
}

afterEach(cleanup);

describe("TradingRoomPage", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.mocked(tradingRoomModule.getTradingRoom).mockResolvedValue(MOCK_AGGREGATE);
    vi.mocked(tradingRoomModule.getTradingRoomStrategy).mockResolvedValue({
      strategy_id: "strat-001",
      strategy_version: "v1",
      evidence_refs: [{ ref_id: "ev-001" }],
      highest_ready_gate: "trading_room",
    });
    vi.mocked(tradingRoomModule.getTradingRoomWorkspace).mockResolvedValue(null);
    vi.mocked(tradingRoomModule.createWorkspaceProposal).mockResolvedValue({
      proposal: MOCK_PROPOSAL,
      etag: '"tr-proposal:trp-001"',
    });
    vi.mocked(tradingRoomModule.acceptWorkspaceProposal).mockResolvedValue({
      workspaceId: "trw-001",
      workspace: MOCK_WORKSPACE,
      version: MOCK_VERSION,
      etag: '"tr-workspace:trw-001:v1"',
    });
    vi.mocked(tradingRoomModule.listTradingRoomWorkspaceVersions).mockResolvedValue([MOCK_VERSION]);
    vi.mocked(tradingRoomModule.patchTradingRoomWorkspaceLayout).mockResolvedValue({
      workspace: workspaceWithVersion(2),
      etag: '"tr-workspace:trw-001:v2"',
      versionId: "trwv-002",
    });
    vi.mocked(tradingRoomModule.createWidgetRevisionProposal).mockResolvedValue({
      proposal: MOCK_REVISION_PROPOSAL,
      etag: '"tr-widget-revision:wrp-001:preview"',
    });
    vi.mocked(tradingRoomModule.acceptWidgetRevisionProposal).mockResolvedValue({
      proposal: { ...MOCK_REVISION_PROPOSAL, status: "accepted" },
      workspace: workspaceWithVersion(2),
      version: { ...MOCK_VERSION, id: "trwv-002", dashboardVersion: 2 },
      appliedAction: "apply",
      etag: '"tr-workspace:trw-001:v2"',
    });
    vi.mocked(tradingRoomModule.rollbackTradingRoomWorkspaceVersion).mockResolvedValue({
      workspace: workspaceWithVersion(3),
      version: { ...MOCK_VERSION, id: "trwv-003", dashboardVersion: 3 },
      rollbackOfVersion: MOCK_VERSION,
      etag: '"tr-workspace:trw-001:v3"',
    });
    vi.mocked(tradingRoomModule.listDecisionEvents).mockResolvedValue({
      items: [MOCK_DECISION_EVENT],
      etag: '"events-etag-v1"',
    });
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

  it("shows workbench entry button in the switcher", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("trading-room-page");
    expect(screen.getByTestId("strategy-lens-all").textContent).toContain("Workbench Entry");
  });

  it("renders each strategy as a selectable lens", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("trading-room-page");
    expect(screen.getByTestId("strategy-lens-strat-001")).toBeDefined();
    expect(screen.getByTestId("strategy-lens-strat-002")).toBeDefined();
  });

  it("enters the highest-value ready strategy workspace by default", async () => {
    render(<TradingRoomPage />);
    await screen.findByTestId("strategy-workspace-strat-001");
    await screen.findByTestId("live-dynamic-workspace");
    expect(tradingRoomModule.createWorkspaceProposal).toHaveBeenCalledWith(
      "strat-001",
      expect.objectContaining({ strategyVersion: "v1", tradingRoomReady: true }),
      expect.objectContaining({ idempotencyKey: expect.any(String), requestId: expect.any(String) }),
    );
    expect(tradingRoomModule.acceptWorkspaceProposal).toHaveBeenCalledWith(
      "strat-001",
      "trp-001",
      expect.objectContaining({ idempotencyKey: expect.any(String), requestId: expect.any(String) }),
    );
    expect(screen.queryByTestId("trading-room-aggregate-view")).toBeNull();
    expect(screen.getByTestId("strategy-lens-strat-001").getAttribute("aria-selected")).toBe("true");
  });

  it("shows queue summary strip in the default entry when no strategy is ready", async () => {
    vi.mocked(tradingRoomModule.getTradingRoom).mockResolvedValue({
      ...MOCK_AGGREGATE,
      strategies: [
        {
          ...MOCK_AGGREGATE.strategies[0],
          readiness_state: "conditional",
          dashboard_recipe_id: undefined,
        },
        MOCK_AGGREGATE.strategies[1],
      ],
    });
    render(<TradingRoomPage />);
    await screen.findByTestId("trading-room-default-entry");
    await screen.findByTestId("queue-summary-strip");
    expect(screen.getByTestId("queue-entry-count").textContent).toContain("2");
    expect(screen.getByTestId("queue-reduce-count").textContent).toContain("1");
    expect(screen.getByTestId("queue-review-count").textContent).toContain("1");
  });

  it("renders readiness rows instead of an inert aggregate table when no strategy is ready", async () => {
    vi.mocked(tradingRoomModule.getTradingRoom).mockResolvedValue({
      ...MOCK_AGGREGATE,
      strategies: [
        {
          ...MOCK_AGGREGATE.strategies[0],
          readiness_state: "conditional",
          dashboard_recipe_id: undefined,
        },
        MOCK_AGGREGATE.strategies[1],
      ],
    });
    render(<TradingRoomPage />);
    await screen.findByTestId("trading-room-readiness-entry");
    expect(screen.getByTestId("trading-room-readiness-strat-001")).toBeDefined();
    expect(screen.getByTestId("trading-room-readiness-strat-002")).toBeDefined();
    expect(screen.queryByTestId("trading-room-aggregate-view")).toBeNull();
  });

  it("renders workshop intake as the default entry when the BFF returns no strategies", async () => {
    vi.mocked(tradingRoomModule.getTradingRoom).mockResolvedValue({
      ...MOCK_AGGREGATE,
      strategies: [],
      queue_summary: { entry: 0, add: 0, reduce: 0, exit: 0, review: 0 },
    });
    render(<TradingRoomPage />);
    const entry = await screen.findByTestId("trading-room-default-entry");
    expect(entry.getAttribute("data-entry-state")).toBe("empty");
    expect(screen.getByTestId("trading-room-workshop-empty-entry")).toBeDefined();
    expect(screen.queryByText("No strategies in the Trading Room.")).toBeNull();
  });

  it("routes the default entry workshop CTA through the parent callback", async () => {
    const onOpenWorkshop = vi.fn();
    vi.mocked(tradingRoomModule.getTradingRoom).mockResolvedValue({
      ...MOCK_AGGREGATE,
      strategies: [
        {
          ...MOCK_AGGREGATE.strategies[0],
          readiness_state: "conditional",
          dashboard_recipe_id: undefined,
        },
      ],
    });
    render(<TradingRoomPage onOpenWorkshop={onOpenWorkshop} />);
    await screen.findByTestId("trading-room-default-entry");
    fireEvent.click(screen.getByTestId("trading-room-open-workshop"));
    expect(onOpenWorkshop).toHaveBeenCalledTimes(1);
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
    vi.mocked(tradingRoomModule.getTradingRoom).mockRejectedValue(
      diagnosticError(500, "TRADING_ROOM_INTERNAL", "backend unavailable"),
    );
    render(<TradingRoomPage />);
    const error = await screen.findByTestId("trading-room-error");
    expect(error.getAttribute("data-bff-status")).toBe("500");
    expect(error.getAttribute("data-bff-code")).toBe("TRADING_ROOM_INTERNAL");
    expect(error.getAttribute("data-request-id")).toBe("req-123");
    expect(error.getAttribute("data-correlation-id")).toBe("corr-123");
    expect(screen.getByTestId("trading-room-error-message").textContent).toContain(
      "backend unavailable",
    );
    expect(screen.getByTestId("trading-room-retry")).toBeDefined();
    expect(screen.getByTestId("trading-room-safe-reload")).toBeDefined();
    expect(screen.queryByText("Failed to load Trading Room.")).toBeNull();
  });

  it.each([
    [401, "AUTH_REQUIRED"],
    [403, "FORBIDDEN"],
    [404, "TRADING_ROOM_NOT_FOUND"],
    [409, "TRADING_ROOM_CONFLICT"],
    [412, "TRADING_ROOM_PRECONDITION_FAILED"],
    [500, "TRADING_ROOM_INTERNAL"],
  ])("renders safe BFF diagnostics for HTTP %i", async (status, code) => {
    vi.mocked(tradingRoomModule.getTradingRoom).mockRejectedValue(
      diagnosticError(status, code, `load failed with ${status}`),
    );
    render(<TradingRoomPage />);
    const error = await screen.findByTestId("trading-room-error");
    expect(error.getAttribute("data-bff-status")).toBe(String(status));
    expect(error.getAttribute("data-bff-code")).toBe(code);
    expect(screen.getByTestId("trading-room-error-summary").textContent).toContain(
      `HTTP ${status}`,
    );
    expect(screen.getByTestId("trading-room-error-diagnostics").textContent).toContain(
      "req-123",
    );
    expect(screen.getByTestId("trading-room-error-diagnostics").textContent).toContain(
      "corr-123",
    );
  });

  it("renders network diagnostics without request identifiers", async () => {
    vi.mocked(tradingRoomModule.getTradingRoom).mockRejectedValue(
      diagnosticError(0, "BFF_NETWORK_ERROR", "fetch failed"),
    );
    render(<TradingRoomPage />);
    const error = await screen.findByTestId("trading-room-error");
    expect(error.getAttribute("data-bff-status")).toBe("0");
    expect(error.getAttribute("data-bff-code")).toBe("BFF_NETWORK_ERROR");
    expect(screen.getByTestId("trading-room-error-summary").textContent).toContain(
      "Network failure",
    );
  });

  it("retries the aggregate load from the error state", async () => {
    vi.mocked(tradingRoomModule.getTradingRoom)
      .mockRejectedValueOnce(diagnosticError(500, "TRADING_ROOM_INTERNAL"))
      .mockResolvedValueOnce(MOCK_AGGREGATE);
    render(<TradingRoomPage />);
    await screen.findByTestId("trading-room-error");
    fireEvent.click(screen.getByTestId("trading-room-retry"));
    await screen.findByTestId("trading-room-page");
    expect(tradingRoomModule.getTradingRoom).toHaveBeenCalledTimes(2);
  });

  it("offers a cache-busting safe reload target", async () => {
    vi.mocked(tradingRoomModule.getTradingRoom).mockRejectedValue(
      diagnosticError(404, "TRADING_ROOM_NOT_FOUND"),
    );
    render(<TradingRoomPage />);
    await screen.findByTestId("trading-room-error");
    expect(screen.getByTestId("trading-room-safe-reload").getAttribute("data-reload-href")).toContain(
      "pantheon_reload=",
    );
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

  // ── Live Dynamic Workspace ────────────────────────────────────────────────

  it("creates and accepts a live workspace for the selected ready strategy", async () => {
    render(<TradingRoomPage strategyId="strat-001" />);
    await screen.findByTestId("strategy-workspace-strat-001");
    await screen.findByTestId("live-dynamic-workspace");
    expect(tradingRoomModule.getTradingRoomStrategy).toHaveBeenCalledWith("strat-001");
    expect(tradingRoomModule.createWorkspaceProposal).toHaveBeenCalledWith(
      "strat-001",
      expect.objectContaining({
        strategyVersion: "v1",
        evidenceRefs: ["ev-001"],
        tradingRoomReady: true,
      }),
      expect.any(Object),
    );
    expect(tradingRoomModule.acceptWorkspaceProposal).toHaveBeenCalledWith("strat-001", "trp-001", expect.any(Object));
  });

  it("renders the live workspace when proposal acceptance returns a workspace", async () => {
    render(<TradingRoomPage strategyId="strat-001" />);
    await screen.findByTestId("live-dynamic-workspace");
    expect(screen.getByTestId("live-workspace-version").textContent).toContain("Workspace 1");
  });

  it("renders live workspace view tabs when multiple views are present", async () => {
    render(<TradingRoomPage strategyId="strat-001" />);
    await screen.findByTestId("live-workspace-view-tabs");
    expect(screen.getByTestId("live-workspace-view-tab-strategy_overview")).toBeDefined();
    expect(screen.getByTestId("live-workspace-view-tab-winner_branch_intelligence")).toBeDefined();
  });

  it("loads a stored live workspace instead of creating a new proposal", async () => {
    window.localStorage.setItem("pantheon.agora.tradingRoom.workspace.strat-001.v1", "trw-001");
    vi.mocked(tradingRoomModule.getTradingRoomWorkspace).mockResolvedValue({
      workspace: MOCK_WORKSPACE,
      etag: '"tr-workspace:trw-001:v1"',
    });
    render(<TradingRoomPage strategyId="strat-001" />);
    await screen.findByTestId("live-dynamic-workspace");
    expect(tradingRoomModule.getTradingRoomWorkspace).toHaveBeenCalledWith("trw-001");
    expect(tradingRoomModule.createWorkspaceProposal).not.toHaveBeenCalled();
  });

  it("blocks live workspace generation when the selected strategy is not ready", async () => {
    render(<TradingRoomPage strategyId="strat-002" />);
    await screen.findByTestId("live-workspace-blocked");
    expect(tradingRoomModule.createWorkspaceProposal).not.toHaveBeenCalled();
  });

  it("shows live workspace error state when proposal creation fails", async () => {
    vi.mocked(tradingRoomModule.createWorkspaceProposal).mockRejectedValue(new Error("proposal failed"));
    render(<TradingRoomPage strategyId="strat-001" />);
    await screen.findByTestId("live-workspace-error");
    expect(screen.getByTestId("live-workspace-error").textContent).toContain("proposal failed");
  });

  it("shows loading state while the live workspace is being created", async () => {
    vi.mocked(tradingRoomModule.createWorkspaceProposal).mockReturnValue(new Promise(() => {}));
    render(<TradingRoomPage strategyId="strat-001" />);
    await screen.findByTestId("strategy-workspace-strat-001");
    expect(screen.getByTestId("live-workspace-loading")).toBeDefined();
  });

  it("patches layout through the live BFF workspace route with If-Match", async () => {
    render(<TradingRoomPage strategyId="strat-001" />);
    await screen.findByTestId("live-layout-patch");
    fireEvent.click(screen.getByTestId("live-layout-patch"));
    await waitFor(() =>
      expect(tradingRoomModule.patchTradingRoomWorkspaceLayout).toHaveBeenCalledWith(
        "trw-001",
        expect.arrayContaining([
          expect.objectContaining({ kind: "move_widget", widgetId: "overview_candidate_funnel" }),
        ]),
        expect.objectContaining({ ifMatch: '"tr-workspace:trw-001:v1"' }),
      ),
    );
  });

  it("requests and accepts a live widget revision", async () => {
    render(<TradingRoomPage strategyId="strat-001" />);
    await screen.findByTestId("live-widget-revision-request");
    fireEvent.click(screen.getByTestId("live-widget-revision-request"));
    await waitFor(() =>
      expect(tradingRoomModule.createWidgetRevisionProposal).toHaveBeenCalledWith(
        "trw-001",
        "overview_candidate_funnel",
        expect.objectContaining({ dataAvailability: "partial" }),
        expect.any(Object),
      ),
    );
    fireEvent.click(screen.getByTestId("live-widget-revision-accept"));
    await waitFor(() =>
      expect(tradingRoomModule.acceptWidgetRevisionProposal).toHaveBeenCalledWith(
        "wrp-001",
        expect.objectContaining({ ifMatch: '"tr-workspace:trw-001:v1"', acceptanceAction: "apply" }),
      ),
    );
  });

  it("lists versions and rolls back through the live BFF route", async () => {
    vi.mocked(tradingRoomModule.listTradingRoomWorkspaceVersions).mockResolvedValue([
      MOCK_VERSION,
      { ...MOCK_VERSION, id: "trwv-002", dashboardVersion: 2 },
    ]);
    render(<TradingRoomPage strategyId="strat-001" />);
    await screen.findByTestId("live-workspace-version-trwv-002");
    fireEvent.click(screen.getByTestId("live-workspace-rollback"));
    await waitFor(() =>
      expect(tradingRoomModule.rollbackTradingRoomWorkspaceVersion).toHaveBeenCalledWith(
        "trw-001",
        "trwv-001",
        expect.objectContaining({ ifMatch: '"tr-workspace:trw-001:v1"' }),
      ),
    );
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
