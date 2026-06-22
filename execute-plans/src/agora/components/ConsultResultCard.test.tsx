import React from "react";
import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConsultResultCard } from "./ConsultResultCard";
import type { WorkshopCard } from "@/lib/bff-v1/agora/workshops";

afterEach(cleanup);

const baseCard: WorkshopCard = {
  spec_version: "1.0",
  card_id: "card-cr-001",
  card_type: "consult_result",
  workshop_id: "ws-001",
  sequence_no: 7,
  status: "completed",
  title: "Risk Committee Consultation",
  summary: "Committee review on momentum strategy concentration risk.",
  payload: {
    consultation_id: "consult-001",
    consultation_type: "risk_committee",
    participant_persona_refs: ["persona-risk-a", "persona-risk-b"],
    status: "completed",
    consensus_summary: "Agreed: strategy acceptable with position size cap at 2% per name.",
    disagreements: ["Persona B prefers 1.5% cap due to illiquidity tail risk"],
    risk_notes: ["Liquidity deteriorates beyond $5M notional in small-cap names"],
    conditions: ["Implement hard stop at -8% drawdown", "Review quarterly"],
    freshness: "fresh",
  },
  created_at: "2026-06-22T00:00:00Z",
};

describe("ConsultResultCard", () => {
  it("renders with correct testid", () => {
    render(<ConsultResultCard card={baseCard} />);
    expect(screen.getByTestId("consult-result-card-card-cr-001")).toBeDefined();
  });

  it("displays card title", () => {
    render(<ConsultResultCard card={baseCard} />);
    expect(screen.getByText("Risk Committee Consultation")).toBeDefined();
  });

  it("shows status badge from payload.status", () => {
    render(<ConsultResultCard card={baseCard} />);
    expect(screen.getByTestId("consult-result-card-card-cr-001-status").textContent).toBe("completed");
  });

  it("shows freshness badge", () => {
    render(<ConsultResultCard card={baseCard} />);
    expect(screen.getByTestId("consult-result-card-card-cr-001-freshness").textContent).toBe("Fresh");
  });

  it("renders consensus summary", () => {
    render(<ConsultResultCard card={baseCard} />);
    const consensusEl = screen.getByTestId("consult-result-card-card-cr-001-consensus");
    expect(consensusEl.textContent).toContain("position size cap at 2%");
  });

  it("renders disagreements list", () => {
    render(<ConsultResultCard card={baseCard} />);
    const disagreementsEl = screen.getByTestId("consult-result-card-card-cr-001-disagreements");
    expect(disagreementsEl.textContent).toContain("1.5% cap");
  });

  it("renders risk notes section", () => {
    render(<ConsultResultCard card={baseCard} />);
    const riskNotesEl = screen.getByTestId("consult-result-card-card-cr-001-risk-notes");
    expect(riskNotesEl.textContent).toContain("Liquidity deteriorates");
  });

  it("renders conditions list", () => {
    render(<ConsultResultCard card={baseCard} />);
    const conditionsEl = screen.getByTestId("consult-result-card-card-cr-001-conditions");
    expect(conditionsEl.textContent).toContain("hard stop at -8%");
  });

  it("does not show disagreements section when empty", () => {
    const card: WorkshopCard = {
      ...baseCard,
      payload: { ...baseCard.payload, disagreements: [] },
    };
    render(<ConsultResultCard card={card} />);
    expect(screen.queryByTestId("consult-result-card-card-cr-001-disagreements")).toBeNull();
  });

  it("does not show risk notes section when missing", () => {
    const card: WorkshopCard = {
      ...baseCard,
      payload: { ...baseCard.payload, risk_notes: undefined },
    };
    render(<ConsultResultCard card={card} />);
    expect(screen.queryByTestId("consult-result-card-card-cr-001-risk-notes")).toBeNull();
  });

  it("renders Ask Servant button when onContinueDiscussion provided", () => {
    render(<ConsultResultCard card={baseCard} onContinueDiscussion={() => undefined} />);
    expect(screen.getByTestId("consult-result-card-card-cr-001-discuss")).toBeDefined();
  });

  it("calls onContinueDiscussion with card_id on click", () => {
    const handler = vi.fn();
    render(<ConsultResultCard card={baseCard} onContinueDiscussion={handler} />);
    fireEvent.click(screen.getByTestId("consult-result-card-card-cr-001-discuss"));
    expect(handler).toHaveBeenCalledWith("card-cr-001");
  });

  it("handles in_progress status", () => {
    const card: WorkshopCard = {
      ...baseCard,
      payload: { ...baseCard.payload, status: "in_progress" },
    };
    render(<ConsultResultCard card={card} />);
    expect(screen.getByTestId("consult-result-card-card-cr-001-status").textContent).toBe("in_progress");
  });
});
