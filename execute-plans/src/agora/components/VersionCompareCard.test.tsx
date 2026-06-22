import React from "react";
import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VersionCompareCard } from "./VersionCompareCard";
import type { WorkshopCard } from "@/lib/bff-v1/agora/workshops";

afterEach(cleanup);

const baseCard: WorkshopCard = {
  spec_version: "1.0",
  card_id: "card-vc-001",
  card_type: "version_compare",
  workshop_id: "ws-001",
  sequence_no: 5,
  status: "informational",
  title: "Version Comparison: v1 vs v2",
  summary: "Comparing base strategy against refined candidate.",
  payload: {
    base_version: {
      workshop_version_id: "v-base-001",
      strategy_spec_registry_id: "reg-base-001",
      label: "v1 (Base)",
    },
    candidate_versions: [
      {
        workshop_version_id: "v-cand-001",
        strategy_spec_registry_id: "reg-cand-001",
        label: "v2 (Candidate)",
      },
    ],
    field_diffs: [
      {
        path: "entry_signal.lookback_period",
        change_kind: "changed",
        candidate_version_id: "v-cand-001",
        materiality: "high",
      },
      {
        path: "exit_signal.stop_loss_pct",
        change_kind: "added",
        candidate_version_id: "v-cand-001",
        materiality: "medium",
      },
    ],
    metric_diffs: [
      {
        metric: "sharpe_ratio",
        candidate_version_id: "v-cand-001",
        base_value: 1.2,
        candidate_value: 1.45,
        absolute_delta: 0.25,
        evidence_class: "backtested_oos",
      },
      {
        metric: "max_drawdown",
        candidate_version_id: "v-cand-001",
        base_value: -0.18,
        candidate_value: -0.14,
        absolute_delta: 0.04,
        evidence_class: "predicted",
      },
    ],
    risk_diffs: [
      {
        candidate_version_id: "v-cand-001",
        risk_domain: "volatility",
        change: "improved",
        summary: "Reduced daily vol by 15%",
      },
    ],
    readiness_diffs: [
      {
        candidate_version_id: "v-cand-001",
        gate: "preliminary_research",
        base_state: "conditional",
        candidate_state: "ready",
      },
    ],
    recommendation: {
      recommended_version_id: "v-cand-001",
      rationale: "Candidate improves sharpe and reduces drawdown with acceptable predicted effects.",
      confidence: 0.78,
      limitations: ["Predicted drawdown improvement not yet backtested"],
    },
  },
  created_at: "2026-06-22T00:00:00Z",
};

describe("VersionCompareCard", () => {
  it("renders with correct root testid", () => {
    render(<VersionCompareCard card={baseCard} />);
    expect(screen.getByTestId("version-compare-card-card-vc-001")).toBeDefined();
  });

  it("displays card title", () => {
    render(<VersionCompareCard card={baseCard} />);
    expect(screen.getByText("Version Comparison: v1 vs v2")).toBeDefined();
  });

  it("displays card summary", () => {
    render(<VersionCompareCard card={baseCard} />);
    expect(screen.getByText("Comparing base strategy against refined candidate.")).toBeDefined();
  });

  it("displays sequence number", () => {
    render(<VersionCompareCard card={baseCard} />);
    expect(screen.getByText("#5")).toBeDefined();
  });

  it("renders version breadcrumb with base and candidate labels", () => {
    render(<VersionCompareCard card={baseCard} />);
    const versions = screen.getByTestId("version-compare-card-card-vc-001-versions");
    expect(versions.textContent).toContain("v1 (Base)");
    expect(versions.textContent).toContain("v2 (Candidate)");
  });

  it("renders field diffs section with count", () => {
    render(<VersionCompareCard card={baseCard} />);
    const fieldDiffs = screen.getByTestId("version-compare-card-card-vc-001-field-diffs");
    expect(fieldDiffs).toBeDefined();
    expect(fieldDiffs.textContent).toContain("Field Changes (2)");
    expect(fieldDiffs.textContent).toContain("entry_signal.lookback_period");
    expect(fieldDiffs.textContent).toContain("exit_signal.stop_loss_pct");
  });

  it("renders metric diffs section", () => {
    render(<VersionCompareCard card={baseCard} />);
    const metricDiffs = screen.getByTestId("version-compare-card-card-vc-001-metric-diffs");
    expect(metricDiffs).toBeDefined();
  });

  it("shows observed metrics before predicted metrics", () => {
    render(<VersionCompareCard card={baseCard} />);
    // backtested_oos metric should be rendered (not styled as predicted)
    const sharpeEl = screen.getByTestId("metric-diff-sharpe_ratio");
    expect(sharpeEl.style.fontStyle).toBe("normal");
    // predicted metric should be rendered with italic style
    const drawdownEl = screen.getByTestId("metric-diff-max_drawdown");
    expect(drawdownEl.style.fontStyle).toBe("italic");
  });

  it("shows predicted separator label when both observed and predicted metrics exist", () => {
    render(<VersionCompareCard card={baseCard} />);
    expect(
      screen.getByText(/Predicted effects \(not observed — subject to uncertainty\)/)
    ).toBeDefined();
  });

  it("does not show predicted separator when only predicted metrics exist", () => {
    const card: WorkshopCard = {
      ...baseCard,
      payload: {
        ...baseCard.payload,
        metric_diffs: [
          {
            metric: "alpha",
            candidate_version_id: "v-cand-001",
            base_value: null,
            candidate_value: 0.05,
            absolute_delta: null,
            evidence_class: "predicted",
          },
        ],
      },
    };
    render(<VersionCompareCard card={card} />);
    expect(
      screen.queryByText(/Predicted effects \(not observed/)
    ).toBeNull();
  });

  it("renders risk diffs section", () => {
    render(<VersionCompareCard card={baseCard} />);
    const riskDiffs = screen.getByTestId("version-compare-card-card-vc-001-risk-diffs");
    expect(riskDiffs).toBeDefined();
    expect(riskDiffs.textContent).toContain("volatility");
    expect(riskDiffs.textContent).toContain("improved");
  });

  it("renders readiness diffs section with gate state transition", () => {
    render(<VersionCompareCard card={baseCard} />);
    const readinessDiffs = screen.getByTestId("version-compare-card-card-vc-001-readiness-diffs");
    expect(readinessDiffs).toBeDefined();
    expect(readinessDiffs.textContent).toContain("Preliminary Research");
    expect(readinessDiffs.textContent).toContain("conditional");
    expect(readinessDiffs.textContent).toContain("ready");
  });

  it("renders recommendation section with rationale", () => {
    render(<VersionCompareCard card={baseCard} />);
    const recommendation = screen.getByTestId("version-compare-card-card-vc-001-recommendation");
    expect(recommendation.textContent).toContain("improves sharpe and reduces drawdown");
  });

  it("shows confidence percentage in recommendation", () => {
    render(<VersionCompareCard card={baseCard} />);
    const recommendation = screen.getByTestId("version-compare-card-card-vc-001-recommendation");
    expect(recommendation.textContent).toContain("78%");
  });

  it("shows trader decision authority attribution in recommendation", () => {
    render(<VersionCompareCard card={baseCard} />);
    expect(screen.getByTestId("version-compare-card-card-vc-001-decision-authority").textContent).toContain(
      "Decision authority: Trader"
    );
  });

  it("shows recommendation limitations", () => {
    render(<VersionCompareCard card={baseCard} />);
    const recommendation = screen.getByTestId("version-compare-card-card-vc-001-recommendation");
    expect(recommendation.textContent).toContain("Predicted drawdown improvement not yet backtested");
  });

  it("does not render recommendation section when rationale is absent", () => {
    const card: WorkshopCard = {
      ...baseCard,
      payload: {
        ...baseCard.payload,
        recommendation: { recommended_version_id: "v-cand-001" },
      },
    };
    render(<VersionCompareCard card={card} />);
    expect(
      screen.queryByTestId("version-compare-card-card-vc-001-recommendation")
    ).toBeNull();
  });

  it("renders Ask Servant button when onContinueDiscussion is provided", () => {
    render(<VersionCompareCard card={baseCard} onContinueDiscussion={() => undefined} />);
    expect(screen.getByTestId("version-compare-card-card-vc-001-discuss")).toBeDefined();
  });

  it("calls onContinueDiscussion with card_id when clicked", () => {
    const handler = vi.fn();
    render(<VersionCompareCard card={baseCard} onContinueDiscussion={handler} />);
    fireEvent.click(screen.getByTestId("version-compare-card-card-vc-001-discuss"));
    expect(handler).toHaveBeenCalledWith("card-vc-001");
  });

  it("does not render Ask Servant button when onContinueDiscussion is not provided", () => {
    render(<VersionCompareCard card={baseCard} />);
    expect(
      screen.queryByTestId("version-compare-card-card-vc-001-discuss")
    ).toBeNull();
  });

  it("does not render field diffs section when field_diffs is empty", () => {
    const card: WorkshopCard = {
      ...baseCard,
      payload: { ...baseCard.payload, field_diffs: [] },
    };
    render(<VersionCompareCard card={card} />);
    expect(
      screen.queryByTestId("version-compare-card-card-vc-001-field-diffs")
    ).toBeNull();
  });

  it("does not render metric diffs section when metric_diffs is empty", () => {
    const card: WorkshopCard = {
      ...baseCard,
      payload: { ...baseCard.payload, metric_diffs: [] },
    };
    render(<VersionCompareCard card={card} />);
    expect(
      screen.queryByTestId("version-compare-card-card-vc-001-metric-diffs")
    ).toBeNull();
  });

  it("does not render risk diffs section when risk_diffs is empty or absent", () => {
    const card: WorkshopCard = {
      ...baseCard,
      payload: { ...baseCard.payload, risk_diffs: [] },
    };
    render(<VersionCompareCard card={card} />);
    expect(
      screen.queryByTestId("version-compare-card-card-vc-001-risk-diffs")
    ).toBeNull();
  });

  it("does not render readiness diffs section when readiness_diffs is empty", () => {
    const card: WorkshopCard = {
      ...baseCard,
      payload: { ...baseCard.payload, readiness_diffs: [] },
    };
    render(<VersionCompareCard card={card} />);
    expect(
      screen.queryByTestId("version-compare-card-card-vc-001-readiness-diffs")
    ).toBeNull();
  });

  describe("multi-candidate grouping", () => {
    const multiCandidateCard: WorkshopCard = {
      ...baseCard,
      card_id: "card-vc-multi",
      payload: {
        base_version: {
          workshop_version_id: "v-base-001",
          strategy_spec_registry_id: "reg-base-001",
          label: "v1 (Base)",
        },
        candidate_versions: [
          {
            workshop_version_id: "v-cand-001",
            strategy_spec_registry_id: "reg-cand-001",
            label: "v2",
          },
          {
            workshop_version_id: "v-cand-002",
            strategy_spec_registry_id: "reg-cand-002",
            label: "v3",
          },
        ],
        field_diffs: [
          {
            path: "lookback",
            change_kind: "changed",
            candidate_version_id: "v-cand-001",
            materiality: "medium",
          },
          {
            path: "threshold",
            change_kind: "added",
            candidate_version_id: "v-cand-002",
            materiality: "low",
          },
        ],
        metric_diffs: [],
        readiness_diffs: [],
      },
    };

    it("shows candidate group headers when multiple candidates exist", () => {
      render(<VersionCompareCard card={multiCandidateCard} />);
      const fieldDiffs = screen.getByTestId("version-compare-card-card-vc-multi-field-diffs");
      expect(fieldDiffs.textContent).toContain("v2");
      expect(fieldDiffs.textContent).toContain("v3");
    });

    it("shows comma-separated candidate labels in version breadcrumb", () => {
      render(<VersionCompareCard card={multiCandidateCard} />);
      const versions = screen.getByTestId("version-compare-card-card-vc-multi-versions");
      expect(versions.textContent).toContain("v2, v3");
    });
  });
});
