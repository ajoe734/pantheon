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

const servantReconstructionCard: WorkshopCard = {
  spec_version: "1.0",
  card_id: "card-src-001",
  card_type: "servant_reconstruction",
  workshop_id: "ws-001",
  sequence_no: 2,
  status: "completed",
  title: "Winner Branch Reconstruction",
  summary: "Servant reconstructed the dense strategy description.",
  payload: {
    strategy_title: "贏家分點策略族",
    explicit_definitions: [
      "識別具有持續獲利能力或資訊領先特徵的券商分點群組。",
      "Winner Branch Score",
      "Branch Migration Risk",
    ],
    causal_chain: [
      {
        step_id: "step-1",
        premise: "關係人持股變化",
        mechanism: "對照分點淨買賣與事件時序",
        expected_observation: "估計關係人與分點的可能對應關係",
        confidence: 0.76,
      },
    ],
    servant_inferences: [
      {
        statement: "分點 cluster 應以同券商與歷史共現先行推定。",
        confidence: 0.68,
        needs_confirmation: true,
      },
    ],
    uncertainties: ["公開資料只能建立資訊領先代理。"],
    contradictions: ["公告後才可見的資料不能宣稱為事前訊號。"],
    proposed_next_actions: ["裁示 Winner Branch Score 的主要目標。"],
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

describe("WorkshopCardRenderer — V10 Strategy Reconstruction", () => {
  it("renders servant_reconstruction as a Strategy Reconstruction Card", () => {
    render(<WorkshopCardRenderer card={servantReconstructionCard} />);

    expect(screen.getByTestId("strategy-reconstruction-card-card-src-001")).toBeDefined();
    expect(screen.getByText("策略重構卡 · Strategy Reconstruction")).toBeDefined();
    expect(screen.getByTestId("strategy-reconstruction-card-card-src-001-core").textContent).toContain(
      "識別具有持續獲利能力",
    );
    expect(screen.getByTestId("workshop-card-servant-card-src-001-chain").textContent).toContain(
      "估計關係人與分點",
    );
    expect(screen.getByTestId("workshop-card-servant-card-src-001-inferences").textContent).toContain(
      "分點 cluster",
    );
    expect(screen.getByTestId("strategy-reconstruction-card-card-src-001-limitations").textContent).toContain(
      "不能作身份或違法行為認定",
    );
    expect(screen.getByTestId("workshop-card-servant-card-src-001-contradictions").textContent).toContain(
      "不能宣稱為事前訊號",
    );
  });
});
