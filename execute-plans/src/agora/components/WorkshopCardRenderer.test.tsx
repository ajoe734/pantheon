import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { WorkshopCardRenderer } from "./WorkshopCardRenderer";
import type { WorkshopCard } from "@/lib/bff-v1/agora/workshops";

afterEach(cleanup);

const researchProgressCard: WorkshopCard = {
  spec_version: "1.0",
  card_id: "card-rr-rend-001",
  card_type: "research_progress",
  workshop_id: "ws-001",
  sequence_no: 1,
  status: "running",
  title: "Running Backtest",
  summary: "Stage 1 prototype backtest.",
  payload: {
    run_id: "run-001",
    plan_id: "plan-001",
    stage_id: "s1",
    stage_type: "prototype_backtest",
    execution_status: "running",
    progress: 50,
    backend: "vectorbt",
    latest_progress_message: "Processing bars",
    warnings: [],
    blocking_reasons: [],
    started_at: "2026-06-22T10:00:00Z",
    updated_at: "2026-06-22T10:05:00Z",
  },
  created_at: "2026-06-22T10:00:00Z",
};

const researchResultCard: WorkshopCard = {
  spec_version: "1.0",
  card_id: "card-bt-rend-001",
  card_type: "research_result",
  workshop_id: "ws-001",
  sequence_no: 2,
  status: "completed",
  title: "Backtest Complete",
  summary: "In-sample result.",
  payload: {
    run_id: "run-001",
    outcome: "pass",
    metrics: [],
    findings: [],
    warnings: [],
    blocking_reasons: [],
    gate_impacts: [],
  },
  created_at: "2026-06-22T10:00:00Z",
};

describe("WorkshopCardRenderer — research_progress wiring", () => {
  it("renders ResearchRunCard (not inline ResearchProgressCard) for research_progress", () => {
    render(<WorkshopCardRenderer card={researchProgressCard} />);
    expect(screen.getByTestId("research-run-card-card-rr-rend-001")).toBeDefined();
    expect(screen.queryByTestId("workshop-card-research-prog-card-rr-rend-001")).toBeNull();
  });

  it("research_progress card shows execution-status from ResearchRunCard", () => {
    render(<WorkshopCardRenderer card={researchProgressCard} />);
    expect(
      screen.getByTestId("research-run-card-card-rr-rend-001-execution-status").textContent
    ).toBe("running");
  });

  it("research_progress card shows progress bar from ResearchRunCard", () => {
    render(<WorkshopCardRenderer card={researchProgressCard} />);
    expect(screen.getByTestId("research-run-card-card-rr-rend-001-progress-bar")).toBeDefined();
  });
});

describe("WorkshopCardRenderer — research_result wiring", () => {
  it("renders BacktestResultCard (not inline ResearchResultCard) for research_result", () => {
    render(<WorkshopCardRenderer card={researchResultCard} />);
    expect(screen.getByTestId("backtest-result-card-card-bt-rend-001")).toBeDefined();
    expect(screen.queryByTestId("workshop-card-research-result-card-bt-rend-001")).toBeNull();
  });

  it("research_result card shows outcome badge from BacktestResultCard", () => {
    render(<WorkshopCardRenderer card={researchResultCard} />);
    expect(
      screen.getByTestId("backtest-result-card-card-bt-rend-001-outcome").textContent
    ).toBe("pass");
  });
});
