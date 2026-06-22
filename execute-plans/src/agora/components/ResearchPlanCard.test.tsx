import React from "react";
import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ResearchPlanCard } from "./ResearchPlanCard";
import type { WorkshopCard } from "@/lib/bff-v1/agora/workshops";

afterEach(cleanup);

const baseCard: WorkshopCard = {
  spec_version: "1.0",
  card_id: "card-rp-001",
  card_type: "research_plan_proposal",
  workshop_id: "ws-001",
  sequence_no: 5,
  status: "action_required",
  title: "Momentum Factor Research Plan",
  summary: "Propose a 3-stage research run for the momentum factor hypothesis.",
  payload: {
    plan_id: "plan-001",
    objectives: ["Validate momentum persistence over 12M horizon", "Check sector correlation"],
    data_requirements: ["OHLCV daily 10Y", "sector classifications"],
    stages: [
      {
        stage_id: "s1",
        stage_type: "backtest",
        purpose: "In-sample momentum validation",
        preferred_backend: "vectorbt",
        dependencies: [],
      },
      {
        stage_id: "s2",
        stage_type: "oos_validation",
        purpose: "Walk-forward OOS check",
        preferred_backend: "vectorbt",
        dependencies: ["s1"],
      },
    ],
    evaluation_criteria: "Sharpe > 0.8, max drawdown < 15%",
    warnings: ["Data availability limited post-2023"],
    approval_requirement: "human",
  },
  created_at: "2026-06-22T00:00:00Z",
  allowed_actions: { approve: true, reject: true },
};

describe("ResearchPlanCard", () => {
  it("renders with correct testid", () => {
    render(<ResearchPlanCard card={baseCard} />);
    expect(screen.getByTestId("research-plan-card-card-rp-001")).toBeDefined();
  });

  it("displays card title and summary", () => {
    render(<ResearchPlanCard card={baseCard} />);
    expect(screen.getByText("Momentum Factor Research Plan")).toBeDefined();
    expect(screen.getByText(/Propose a 3-stage research run/)).toBeDefined();
  });

  it("shows status badge", () => {
    render(<ResearchPlanCard card={baseCard} />);
    expect(screen.getByTestId("research-plan-card-card-rp-001-status").textContent).toBe(
      "action_required"
    );
  });

  it("renders objectives section", () => {
    render(<ResearchPlanCard card={baseCard} />);
    const objectivesEl = screen.getByTestId("research-plan-card-card-rp-001-objectives");
    expect(objectivesEl).toBeDefined();
    expect(objectivesEl.textContent).toContain("Validate momentum persistence");
  });

  it("renders stages section with stage details", () => {
    render(<ResearchPlanCard card={baseCard} />);
    const stagesEl = screen.getByTestId("research-plan-card-card-rp-001-stages");
    expect(stagesEl).toBeDefined();
    expect(screen.getByTestId("research-plan-stage-s1")).toBeDefined();
    expect(screen.getByTestId("research-plan-stage-s2")).toBeDefined();
  });

  it("shows evaluation criteria", () => {
    render(<ResearchPlanCard card={baseCard} />);
    const evalEl = screen.getByTestId("research-plan-card-card-rp-001-evaluation");
    expect(evalEl.textContent).toContain("Sharpe > 0.8");
  });

  it("shows warnings section", () => {
    render(<ResearchPlanCard card={baseCard} />);
    const warnEl = screen.getByTestId("research-plan-card-card-rp-001-warnings");
    expect(warnEl.textContent).toContain("Data availability limited");
  });

  it("shows approval requirement", () => {
    render(<ResearchPlanCard card={baseCard} />);
    const approvalEl = screen.getByTestId("research-plan-card-card-rp-001-approval");
    expect(approvalEl.textContent).toContain("human");
  });

  it("renders approve and reject buttons when allowed_actions permits", () => {
    render(<ResearchPlanCard card={baseCard} />);
    expect(screen.getByTestId("research-plan-card-card-rp-001-approve")).toBeDefined();
    expect(screen.getByTestId("research-plan-card-card-rp-001-reject")).toBeDefined();
  });

  it("does not render approve button when allowed_actions.approve is falsy", () => {
    const card: WorkshopCard = { ...baseCard, allowed_actions: { approve: false } };
    render(<ResearchPlanCard card={card} />);
    expect(screen.queryByTestId("research-plan-card-card-rp-001-approve")).toBeNull();
  });

  it("renders Ask Servant button when onContinueDiscussion is provided", () => {
    render(<ResearchPlanCard card={baseCard} onContinueDiscussion={() => undefined} />);
    expect(screen.getByTestId("research-plan-card-card-rp-001-discuss")).toBeDefined();
  });

  it("calls onContinueDiscussion with card_id when Ask Servant is clicked", () => {
    const handler = vi.fn();
    render(<ResearchPlanCard card={baseCard} onContinueDiscussion={handler} />);
    fireEvent.click(screen.getByTestId("research-plan-card-card-rp-001-discuss"));
    expect(handler).toHaveBeenCalledWith("card-rp-001");
  });

  it("does not render Ask Servant button when no callback is provided", () => {
    render(<ResearchPlanCard card={baseCard} />);
    expect(screen.queryByTestId("research-plan-card-card-rp-001-discuss")).toBeNull();
  });
});
