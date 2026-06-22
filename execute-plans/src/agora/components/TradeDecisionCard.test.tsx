import React from "react";
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TradeDecisionCard } from "./TradeDecisionCard";
import type { TradingDecisionEvent } from "@/lib/bff-v1/agora/tradingRoom";

// Mock the BFF client
vi.mock("@/lib/bff-v1/agora/tradingRoom", () => ({
  decideOnEvent: vi.fn(),
}));

import { decideOnEvent } from "@/lib/bff-v1/agora/tradingRoom";
const mockDecideOnEvent = decideOnEvent as ReturnType<typeof vi.fn>;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const baseEvent: TradingDecisionEvent = {
  spec_version: "1.0",
  decision_event_id: "evt-001",
  event_kind: "entry",
  origin: "strategy_signal",
  strategy_id: "strat-alpha-001",
  strategy_spec_registry_id: "reg-001",
  subject: { symbol: "AAPL", asset_class: "equity", venue: "NASDAQ" },
  state: "pending_review",
  triggered_at: "2026-06-22T10:00:00Z",
  confidence: {
    value: 0.78,
    basis: "model",
    calibration_state: "calibrated",
    sample_size: 1200,
  },
  probability: {
    value: 0.62,
    target_outcome: "price_up_5pct",
    horizon: "10d",
    ci_lower: 0.55,
    ci_upper: 0.69,
  },
  expected_value: {
    horizon: "10d",
    unit: "pct_return",
    gross: 0.052,
    cost: 0.003,
    net: 0.049,
    downside: -0.018,
  },
  rationale: [
    { claim: "Strong momentum signal from last 3 quarters", confidence: 0.85 },
    { claim: "Low correlation with existing positions", confidence: 0.72 },
  ],
  risk_notes: [
    {
      severity: "watch",
      domain: "macro",
      summary: "FOMC decision pending",
      mitigation: "Size down if uncertainty persists",
    },
  ],
  evidence_refs: [
    {
      ref_type: "evidence_bundle",
      ref_id: "eb-alpha-q2-2026",
      summary: "Q2 alpha evidence bundle",
    },
  ],
  invalidation: {
    current_state: "valid",
    conditions: ["Price closes below 200-day MA", "RSI drops below 30"],
  },
  suggested_action: "enter",
  suggested_size: { size_hint: "medium", portfolio_pct: 0.04, non_binding: true },
  data_cutoff: "2026-06-22T09:00:00Z",
  no_order_route_proof: "agora_decision_support_only",
};

describe("TradeDecisionCard", () => {
  it("renders with correct data-testid", () => {
    render(<TradeDecisionCard event={baseEvent} />);
    expect(screen.getByTestId("trade-decision-card-evt-001")).toBeDefined();
  });

  it("displays event kind badge with correct label", () => {
    render(<TradeDecisionCard event={baseEvent} />);
    const badge = screen.getByTestId("trade-decision-card-kind-evt-001");
    expect(badge.textContent).toBe("Entry");
  });

  it("displays symbol, asset class and venue in the header", () => {
    render(<TradeDecisionCard event={baseEvent} />);
    expect(screen.getByTestId("trade-decision-card-symbol-evt-001").textContent).toBe("AAPL");
    expect(screen.getByText("equity")).toBeDefined();
    expect(screen.getByText("NASDAQ")).toBeDefined();
  });

  it("displays state label", () => {
    render(<TradeDecisionCard event={baseEvent} />);
    const stateEl = screen.getByTestId("trade-decision-card-state-evt-001");
    expect(stateEl.textContent).toBe("Pending Review");
  });

  it("displays confidence section (evidence quality) distinct from probability", () => {
    render(<TradeDecisionCard event={baseEvent} />);
    const confEl = screen.getByTestId("trade-decision-card-confidence-evt-001");
    expect(confEl.textContent).toContain("78%");
    expect(confEl.textContent).toContain("model");
  });

  it("displays calibration_state", () => {
    render(<TradeDecisionCard event={baseEvent} />);
    const calEl = screen.getByTestId("trade-decision-card-calibration-evt-001");
    expect(calEl.textContent).toContain("calibrated");
  });

  it("displays probability section (outcome forecast) as separate field from confidence", () => {
    render(<TradeDecisionCard event={baseEvent} />);
    const probEl = screen.getByTestId("trade-decision-card-probability-evt-001");
    expect(probEl.textContent).toContain("62%");
    expect(probEl.textContent).toContain("price_up_5pct");
    expect(probEl.textContent).toContain("10d");
  });

  it("displays CI bounds on probability", () => {
    render(<TradeDecisionCard event={baseEvent} />);
    const ciEl = screen.getByTestId("trade-decision-card-probability-ci-evt-001");
    expect(ciEl.textContent).toContain("55%");
    expect(ciEl.textContent).toContain("69%");
  });

  it("displays all four EV fields: gross, cost, net, downside", () => {
    render(<TradeDecisionCard event={baseEvent} />);
    const evEl = screen.getByTestId("trade-decision-card-ev-evt-001");
    expect(evEl.textContent).toContain("Gross");
    expect(evEl.textContent).toContain("Cost");
    expect(evEl.textContent).toContain("Net");
    expect(evEl.textContent).toContain("Downside");
  });

  it("colors net EV green for positive values", () => {
    render(<TradeDecisionCard event={baseEvent} />);
    const netEl = screen.getByTestId("trade-decision-card-ev-net-evt-001");
    expect(netEl.style.color).toBe("rgb(22, 163, 74)"); // #16a34a
  });

  it("colors net EV red for negative values", () => {
    const negEvent = {
      ...baseEvent,
      decision_event_id: "evt-002",
      expected_value: { ...baseEvent.expected_value, net: -0.01 },
    };
    render(<TradeDecisionCard event={negEvent} />);
    const netEl = screen.getByTestId("trade-decision-card-ev-net-evt-002");
    expect(netEl.style.color).toBe("rgb(220, 38, 38)"); // #dc2626
  });

  it("displays rationale list", () => {
    render(<TradeDecisionCard event={baseEvent} />);
    const rationaleEl = screen.getByTestId("trade-decision-card-rationale-evt-001");
    expect(rationaleEl.textContent).toContain("Strong momentum signal");
    expect(rationaleEl.textContent).toContain("Low correlation");
    expect(rationaleEl.textContent).toContain("85%");
  });

  it("displays risk notes with severity", () => {
    render(<TradeDecisionCard event={baseEvent} />);
    const riskEl = screen.getByTestId("trade-decision-card-risk-notes-evt-001");
    expect(riskEl.textContent).toContain("[watch]");
    expect(riskEl.textContent).toContain("FOMC decision pending");
    expect(riskEl.textContent).toContain("Size down if uncertainty persists");
  });

  it("displays evidence refs", () => {
    render(<TradeDecisionCard event={baseEvent} />);
    const evEl = screen.getByTestId("trade-decision-card-evidence-evt-001");
    expect(evEl.textContent).toContain("eb-alpha-q2-2026");
    expect(evEl.textContent).toContain("Q2 alpha evidence bundle");
  });

  it("displays invalidation state and conditions", () => {
    render(<TradeDecisionCard event={baseEvent} />);
    const invEl = screen.getByTestId("trade-decision-card-invalidation-evt-001");
    expect(invEl.textContent).toContain("valid");
    expect(invEl.textContent).toContain("Price closes below 200-day MA");
  });

  it("displays suggested action and size", () => {
    render(<TradeDecisionCard event={baseEvent} />);
    const sugEl = screen.getByTestId("trade-decision-card-suggested-evt-001");
    expect(sugEl.textContent).toContain("enter");
    expect(sugEl.textContent).toContain("medium");
    expect(sugEl.textContent).toContain("non-binding");
  });

  it("displays no_order_route_proof", () => {
    render(<TradeDecisionCard event={baseEvent} />);
    const proofEl = screen.getByTestId("trade-decision-card-no-order-route-evt-001");
    expect(proofEl.textContent).toBe("agora_decision_support_only");
  });

  it("displays governed TradingIntent notice box", () => {
    render(<TradeDecisionCard event={baseEvent} />);
    const noticeEl = screen.getByTestId("trade-decision-card-intent-notice-evt-001");
    expect(noticeEl.textContent).toContain("TradingIntent");
    expect(noticeEl.textContent).toContain("AG-BE-TR-002");
    expect(noticeEl.textContent).toContain("No order is placed");
  });

  it("renders all four trader decision buttons", () => {
    render(<TradeDecisionCard event={baseEvent} />);
    expect(screen.getByTestId("trade-decision-card-decide-approve-evt-001")).toBeDefined();
    expect(screen.getByTestId("trade-decision-card-decide-reject-evt-001")).toBeDefined();
    expect(screen.getByTestId("trade-decision-card-decide-defer-evt-001")).toBeDefined();
    expect(screen.getByTestId("trade-decision-card-decide-modify-evt-001")).toBeDefined();
  });

  it("disables decision buttons when event is decided", () => {
    const decidedEvent = { ...baseEvent, state: "decided" as const };
    render(<TradeDecisionCard event={decidedEvent} />);
    const approveBtn = screen.getByTestId(
      "trade-decision-card-decide-approve-evt-001",
    ) as HTMLButtonElement;
    expect(approveBtn.disabled).toBe(true);
  });

  it("calls decideOnEvent and shows success state on approve", async () => {
    mockDecideOnEvent.mockResolvedValueOnce({});
    const onDecisionRecorded = vi.fn();
    render(
      <TradeDecisionCard
        event={baseEvent}
        etag='"evt-etag-v1"'
        onDecisionRecorded={onDecisionRecorded}
      />,
    );
    fireEvent.click(screen.getByTestId("trade-decision-card-decide-approve-evt-001"));
    await waitFor(() => {
      expect(screen.getByTestId("trade-decision-card-confirmed-evt-001")).toBeDefined();
    });
    expect(mockDecideOnEvent).toHaveBeenCalledWith(
      "evt-001",
      { decision: "approve" },
      expect.objectContaining({
        ifMatch: '"evt-etag-v1"',
        idempotencyKey: expect.any(String),
        requestId: expect.any(String),
      }),
    );
    expect(onDecisionRecorded).toHaveBeenCalledWith("approve", "evt-001");
  });

  it("shows error state when decideOnEvent rejects", async () => {
    mockDecideOnEvent.mockRejectedValueOnce(new Error("Network error"));
    render(<TradeDecisionCard event={baseEvent} etag='"evt-etag-v1"' />);
    fireEvent.click(screen.getByTestId("trade-decision-card-decide-reject-evt-001"));
    await waitFor(() => {
      expect(screen.getByTestId("trade-decision-card-error-evt-001")).toBeDefined();
    });
    expect(screen.getByTestId("trade-decision-card-error-evt-001").textContent).toContain(
      "Network error",
    );
  });

  it("shows position snapshot when present", () => {
    const withSnapshot = {
      ...baseEvent,
      position_snapshot: { shares: 100, avg_cost: 182.5 },
    };
    render(<TradeDecisionCard event={withSnapshot} />);
    expect(screen.getByTestId("trade-decision-card-position-evt-001")).toBeDefined();
  });

  it("does not render position snapshot section when absent", () => {
    render(<TradeDecisionCard event={baseEvent} />);
    expect(
      screen.queryByTestId("trade-decision-card-position-evt-001"),
    ).toBeNull();
  });
});
