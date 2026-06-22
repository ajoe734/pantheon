import React from "react";
import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BacktestResultCard } from "./BacktestResultCard";
import type { WorkshopCard } from "@/lib/bff-v1/agora/workshops";

afterEach(cleanup);

const baseCard: WorkshopCard = {
  spec_version: "1.0",
  card_id: "card-bt-001",
  card_type: "research_result",
  workshop_id: "ws-001",
  sequence_no: 8,
  status: "completed",
  title: "Momentum Strategy Backtest Result",
  summary: "In-sample backtest on OHLCV daily data 2014–2024.",
  payload: {
    run_id: "run-001",
    outcome: "pass",
    metrics: [
      { category: "performance", name: "Sharpe Ratio", value: 1.24, unit: "", gate_result: "pass" },
      { category: "performance", name: "Total Return", value: 0.87, unit: "%", gate_result: "pass" },
      { category: "risk", name: "Max Drawdown", value: -0.12, unit: "%", gate_result: "pass" },
    ],
    findings: [
      {
        finding_id: "f1",
        severity: "warning",
        summary: "Elevated drawdown in Q1 2020 regime shift",
        detail: "Max drawdown reached -18% for 3 consecutive weeks",
      },
      {
        finding_id: "f2",
        severity: "info",
        summary: "Win rate above threshold across all sub-periods",
      },
    ],
    warnings: ["Slippage model not applied"],
    blocking_reasons: [],
    gate_impacts: ["preliminary_research"],
    recommended_patch_proposal_refs: [],
    backend: { effective: "vectorbt", mode: "real" },
    data_cutoff: "2026-06-01T00:00:00Z",
    artifact_refs: ["artifact-001"],
    evidence_refs: [],
  },
  created_at: "2026-06-22T10:00:00Z",
};

describe("BacktestResultCard", () => {
  it("renders with correct testid", () => {
    render(<BacktestResultCard card={baseCard} />);
    expect(screen.getByTestId("backtest-result-card-card-bt-001")).toBeDefined();
  });

  it("displays card title and summary", () => {
    render(<BacktestResultCard card={baseCard} />);
    expect(screen.getByText("Momentum Strategy Backtest Result")).toBeDefined();
    expect(screen.getByText(/In-sample backtest/)).toBeDefined();
  });

  it("shows outcome badge", () => {
    render(<BacktestResultCard card={baseCard} />);
    expect(screen.getByTestId("backtest-result-card-card-bt-001-outcome").textContent).toBe("pass");
  });

  it("shows backend mode label with effective backend name", () => {
    render(<BacktestResultCard card={baseCard} />);
    const modeBadge = screen.getByTestId("backtest-result-card-card-bt-001-backend-mode");
    expect(modeBadge.textContent).toContain("vectorbt");
    expect(modeBadge.textContent).toContain("Real");
  });

  it("shows fixture label for fixture mode", () => {
    const card: WorkshopCard = {
      ...baseCard,
      payload: { ...baseCard.payload, backend: { effective: "vectorbt", mode: "fixture" } },
    };
    render(<BacktestResultCard card={card} />);
    expect(screen.getByTestId("backtest-result-card-card-bt-001-backend-mode").textContent).toContain(
      "Fixture"
    );
  });

  it("shows stub label for stub mode", () => {
    const card: WorkshopCard = {
      ...baseCard,
      payload: { ...baseCard.payload, backend: { effective: "vectorbt", mode: "stub" } },
    };
    render(<BacktestResultCard card={card} />);
    expect(screen.getByTestId("backtest-result-card-card-bt-001-backend-mode").textContent).toContain(
      "Stub"
    );
  });

  it("renders metrics section grouped by category", () => {
    render(<BacktestResultCard card={baseCard} />);
    const metricsEl = screen.getByTestId("backtest-result-card-card-bt-001-metrics");
    expect(metricsEl).toBeDefined();
    expect(metricsEl.textContent).toContain("Performance");
    expect(metricsEl.textContent).toContain("Sharpe Ratio");
    expect(metricsEl.textContent).toContain("Risk");
    expect(metricsEl.textContent).toContain("Max Drawdown");
  });

  it("does not render metrics section when metrics absent", () => {
    const card: WorkshopCard = {
      ...baseCard,
      payload: { ...baseCard.payload, metrics: [] },
    };
    render(<BacktestResultCard card={card} />);
    expect(screen.queryByTestId("backtest-result-card-card-bt-001-metrics")).toBeNull();
  });

  it("renders findings section with severity labels", () => {
    render(<BacktestResultCard card={baseCard} />);
    const findingsEl = screen.getByTestId("backtest-result-card-card-bt-001-findings");
    expect(findingsEl).toBeDefined();
    expect(findingsEl.textContent).toContain("warning");
    expect(findingsEl.textContent).toContain("Elevated drawdown in Q1 2020");
    expect(findingsEl.textContent).toContain("info");
    expect(findingsEl.textContent).toContain("Win rate above threshold");
  });

  it("does not render findings section when absent", () => {
    const card: WorkshopCard = {
      ...baseCard,
      payload: { ...baseCard.payload, findings: [] },
    };
    render(<BacktestResultCard card={card} />);
    expect(screen.queryByTestId("backtest-result-card-card-bt-001-findings")).toBeNull();
  });

  it("shows warnings section", () => {
    render(<BacktestResultCard card={baseCard} />);
    const warnEl = screen.getByTestId("backtest-result-card-card-bt-001-warnings");
    expect(warnEl.textContent).toContain("Slippage model not applied");
  });

  it("does not show warnings section when empty", () => {
    const card: WorkshopCard = {
      ...baseCard,
      payload: { ...baseCard.payload, warnings: [] },
    };
    render(<BacktestResultCard card={card} />);
    expect(screen.queryByTestId("backtest-result-card-card-bt-001-warnings")).toBeNull();
  });

  it("does not show blocking section when empty", () => {
    render(<BacktestResultCard card={baseCard} />);
    expect(screen.queryByTestId("backtest-result-card-card-bt-001-blocking")).toBeNull();
  });

  it("shows blocking reasons when present", () => {
    const card: WorkshopCard = {
      ...baseCard,
      payload: { ...baseCard.payload, blocking_reasons: ["Gate: full_validation requires OOS run"] },
    };
    render(<BacktestResultCard card={card} />);
    const blockingEl = screen.getByTestId("backtest-result-card-card-bt-001-blocking");
    expect(blockingEl.textContent).toContain("full_validation requires OOS run");
  });

  it("shows gate impacts", () => {
    render(<BacktestResultCard card={baseCard} />);
    const gateEl = screen.getByTestId("backtest-result-card-card-bt-001-gate-impacts");
    expect(gateEl.textContent).toContain("preliminary_research");
  });

  it("does not show gate impacts section when absent", () => {
    const card: WorkshopCard = {
      ...baseCard,
      payload: { ...baseCard.payload, gate_impacts: [] },
    };
    render(<BacktestResultCard card={card} />);
    expect(screen.queryByTestId("backtest-result-card-card-bt-001-gate-impacts")).toBeNull();
  });

  it("shows data cutoff", () => {
    render(<BacktestResultCard card={baseCard} />);
    const cutoffEl = screen.getByTestId("backtest-result-card-card-bt-001-data-cutoff");
    expect(cutoffEl.textContent).toContain("2026-06-01");
  });

  it("does not show data cutoff when absent", () => {
    const card: WorkshopCard = {
      ...baseCard,
      payload: { ...baseCard.payload, data_cutoff: undefined },
    };
    render(<BacktestResultCard card={card} />);
    expect(screen.queryByTestId("backtest-result-card-card-bt-001-data-cutoff")).toBeNull();
  });

  it("does not show backend mode badge when backend absent", () => {
    const card: WorkshopCard = {
      ...baseCard,
      payload: { ...baseCard.payload, backend: undefined },
    };
    render(<BacktestResultCard card={card} />);
    expect(screen.queryByTestId("backtest-result-card-card-bt-001-backend-mode")).toBeNull();
  });

  it("renders fail outcome correctly", () => {
    const card: WorkshopCard = {
      ...baseCard,
      payload: { ...baseCard.payload, outcome: "fail" },
    };
    render(<BacktestResultCard card={card} />);
    expect(screen.getByTestId("backtest-result-card-card-bt-001-outcome").textContent).toBe("fail");
  });

  it("renders Ask Servant button when onContinueDiscussion provided", () => {
    render(<BacktestResultCard card={baseCard} onContinueDiscussion={() => undefined} />);
    expect(screen.getByTestId("backtest-result-card-card-bt-001-discuss")).toBeDefined();
  });

  it("calls onContinueDiscussion with card_id on click", () => {
    const handler = vi.fn();
    render(<BacktestResultCard card={baseCard} onContinueDiscussion={handler} />);
    fireEvent.click(screen.getByTestId("backtest-result-card-card-bt-001-discuss"));
    expect(handler).toHaveBeenCalledWith("card-bt-001");
  });

  it("does not render Ask Servant when no callback provided", () => {
    render(<BacktestResultCard card={baseCard} />);
    expect(screen.queryByTestId("backtest-result-card-card-bt-001-discuss")).toBeNull();
  });
});
