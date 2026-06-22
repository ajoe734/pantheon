import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { StrategyCompletenessRail } from "./StrategyCompletenessRail";
import type { StrategyCompleteness } from "@/lib/bff-v1/agora/workshops";
import type { StrategyReadinessAssessment } from "@/lib/bff-v1/agora/workshops";
import type { WorkshopCard } from "@/lib/bff-v1/agora/workshops";

afterEach(cleanup);

const mockCompleteness: StrategyCompleteness = {
  spec_version: "1.0",
  completeness_id: "comp-001",
  strategy_ref: "strat-001",
  assessed_by_persona_id: "persona-a",
  overall_grade: "partial",
  dimensions: [
    { dimension: "hypothesis", grade: "complete", gaps: [], required_actions: [] },
    { dimension: "data_dependencies", grade: "partial", gaps: ["missing PIT"], required_actions: [] },
    { dimension: "market_scope", grade: "missing", gaps: [], required_actions: [] },
  ],
  blockers: [],
  research_ready: false,
  assessed_at: "2026-06-22T00:00:00Z",
} as unknown as StrategyCompleteness;

const mockReadiness: StrategyReadinessAssessment = {
  spec_version: "1.0",
  assessment_id: "ready_001",
  workshop_id: "ws-001",
  strategy_id: "strat-001",
  workshop_version_id: "v-001",
  strategy_spec_registry_id: "reg-001",
  assessment_version: 1,
  gates: [
    {
      gate: "preliminary_research",
      state: "conditional",
      requirements: [],
      blocking_requirement_ids: [],
    },
    {
      gate: "full_validation",
      state: "not_assessed",
      requirements: [],
      blocking_requirement_ids: [],
    },
    {
      gate: "trading_room",
      state: "not_assessed",
      requirements: [],
      blocking_requirement_ids: [],
    },
  ],
  highest_ready_gate: null,
  assessed_at: "2026-06-22T00:00:00Z",
};

const mockNextQuestionCard: WorkshopCard = {
  spec_version: "1.0",
  card_id: "card-nq-001",
  card_type: "next_question",
  workshop_id: "ws-001",
  sequence_no: 3,
  status: "action_required",
  title: "Next Question",
  payload: {
    question_id: "q-001",
    question: "What is the entry signal definition?",
    why_now: "Missing definition blocks research dispatch.",
    score_total: 0.87,
  },
  created_at: "2026-06-22T00:00:00Z",
};

describe("StrategyCompletenessRail", () => {
  it("renders the rail container", () => {
    render(
      <StrategyCompletenessRail
        completeness={null}
        readiness={null}
        nextQuestion={null}
      />
    );
    expect(screen.getByTestId("strategy-completeness-rail")).toBeDefined();
  });

  it("shows empty states when no data is provided", () => {
    render(
      <StrategyCompletenessRail
        completeness={null}
        readiness={null}
        nextQuestion={null}
      />
    );
    expect(screen.getByTestId("completeness-empty")).toBeDefined();
    expect(screen.getByTestId("readiness-gates-empty")).toBeDefined();
  });

  it("renders overall grade from completeness data", () => {
    render(
      <StrategyCompletenessRail
        completeness={mockCompleteness}
        readiness={null}
        nextQuestion={null}
      />
    );
    const grade = screen.getByTestId("completeness-overall-grade");
    expect(grade.textContent).toBe("partial");
  });

  it("renders all three dimensions with their grades", () => {
    render(
      <StrategyCompletenessRail
        completeness={mockCompleteness}
        readiness={null}
        nextQuestion={null}
      />
    );
    expect(screen.getByTestId("completeness-dimension-hypothesis")).toBeDefined();
    expect(screen.getByTestId("completeness-dimension-data_dependencies")).toBeDefined();
    expect(screen.getByTestId("completeness-dimension-market_scope")).toBeDefined();

    expect(screen.getByTestId("completeness-dimension-hypothesis-grade").textContent).toBe("Complete");
    expect(screen.getByTestId("completeness-dimension-data_dependencies-grade").textContent).toBe("Partial");
    expect(screen.getByTestId("completeness-dimension-market_scope-grade").textContent).toBe("Missing");
  });

  it("renders three readiness gates from readiness data", () => {
    render(
      <StrategyCompletenessRail
        completeness={null}
        readiness={mockReadiness}
        nextQuestion={null}
      />
    );
    expect(screen.getByTestId("readiness-gate-preliminary_research")).toBeDefined();
    expect(screen.getByTestId("readiness-gate-full_validation")).toBeDefined();
    expect(screen.getByTestId("readiness-gate-trading_room")).toBeDefined();

    expect(screen.getByTestId("readiness-gate-preliminary_research-state").textContent).toBe("Conditional");
    expect(screen.getByTestId("readiness-gate-full_validation-state").textContent).toBe("Not Assessed");
  });

  it("renders next question when provided", () => {
    render(
      <StrategyCompletenessRail
        completeness={null}
        readiness={null}
        nextQuestion={mockNextQuestionCard}
      />
    );
    expect(screen.getByTestId("next-question-section")).toBeDefined();
    expect(screen.getByTestId("next-question-text").textContent).toBe(
      "What is the entry signal definition?"
    );
    expect(screen.getByTestId("next-question-score")).toBeDefined();
  });

  it("does not render next question section when card is not next_question type", () => {
    const nonNQCard: WorkshopCard = {
      ...mockNextQuestionCard,
      card_type: "completeness_update",
    };
    render(
      <StrategyCompletenessRail
        completeness={null}
        readiness={null}
        nextQuestion={nonNQCard}
      />
    );
    expect(screen.queryByTestId("next-question-section")).toBeNull();
  });
});
