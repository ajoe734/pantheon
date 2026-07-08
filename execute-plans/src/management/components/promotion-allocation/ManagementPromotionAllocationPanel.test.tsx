import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { managementClient } from "@/lib/bff/client";
import * as managementApi from "@/lib/bff-v1/management";
import type {
  ManagementPromotionReviewsResponse,
  ManagementQuarterlyRankingFormulaResponse,
  ManagementQuarterlyRankingRecommendationsResponse,
  ManagementQuarterlyRankingResponse,
} from "@/lib/bff-v1/management";

import { ManagementPromotionAllocationPanel } from "./index";

const surfaceOk = { status: "ok", source: "bff_composed" };

const quarterWindow = {
  quarter: "2026-Q3",
  year: 2026,
  quarter_number: 3,
  label: "Q3 2026",
  start_at: "2026-07-01T00:00:00Z",
  end_exclusive_at: "2026-10-01T00:00:00Z",
  timezone: "UTC" as const,
};

const formula = {
  id: "pm12-default",
  formula_id: "pm12-default",
  version: "pm12-default-v1",
  formula_version: "pm12-default-v1",
  weights: {
    pnl: 0.35,
    risk: 0.25,
    execution: 0.25,
    activity: 0.15,
  },
  score_field: "overall_score",
  components: [
    { key: "pnl", label: "PnL", weight: 0.35 },
    { key: "risk", label: "Risk", weight: 0.25 },
  ],
  basis: "weighted_score",
  policy: "read_only_governance_advisory",
  governance_evidence_refs: ["evidence-formula-1"],
  version_history: [
    {
      id: "pm12-default-v1",
      version: "pm12-default-v1",
      formula_version: "pm12-default-v1",
      effective_at: "2026-07-01T00:00:00Z",
      change_type: "governance_update",
      governance_evidence_refs: ["evidence-formula-1"],
    },
  ],
  change_control: {
    version_policy: "formula_version_changes_require_governance_evidence",
    requires_governance_evidence: true,
    governance_evidence_refs: ["evidence-formula-1"],
    authority: "governance_committee",
  },
};

const rankingResponse = {
  data: {
    id: "ranking-2026-q3",
    quarter: "2026-Q3",
    quarter_window: quarterWindow,
    formula,
    items: [],
    evidence_refs: [],
    summary: {
      quarter: "2026-Q3",
      formula_version: "pm12-default-v1",
      persona_count: 1,
      ranked_count: 1,
      returned_count: 1,
      evidence_ref_count: 1,
      redacted_evidence_count: 0,
      basis: "weighted_score",
    },
  },
  page_info: { next_page_token: null, total: 1, page_size: 8 },
  meta: { snapshot_at: "2026-07-08T00:00:00Z", surfaces: { quarterly_ranking: surfaceOk } },
} as ManagementQuarterlyRankingResponse;

const recommendationsResponse = {
  data: {
    id: "recommendations-2026-q3",
    quarter: "2026-Q3",
    quarter_window: quarterWindow,
    formula,
    items: [
      {
        id: "rec-alpha",
        recommendation_id: "rec-alpha",
        quarter: "2026-Q3",
        quarter_window: quarterWindow,
        persona_id: "persona-alpha",
        name: "Alpha Paper",
        owner: "research",
        state: "paper_running",
        risk: "ok",
        rank: 1,
        score: 92,
        tier: "S",
        tier_id: "tier-s",
        tier_label: "S",
        formula_version: "pm12-default-v1",
        action_id: "promote_to_canary_candidate",
        action_label: "Promote to canary candidate",
        recommendation_type: "governance_advisory",
        status: "recommended",
        priority: "high",
        risk_level: "medium",
        target: { type: "persona", id: "persona-alpha" },
        rationale: "Paper performance qualifies for review.",
        rationale_codes: ["paper_qualified"],
        metrics: {},
        components: {},
        evidence_refs: [],
        evidence_ref_ids: [],
        governance: {
          requires_human_gate_decision: true,
          destinations: ["human_inbox", "promotion_review"],
          human_inbox_route: "/management/human-inbox",
          governance_queue_route: "/management/promotion-reviews",
          decision_type: "paper_to_canary_candidate_review",
          live_capital_mutation: false,
        },
        requires_human_gate_decision: true,
        live_capital_mutation: false,
        policy: "read_only_governance_advisory",
        links: { human_inbox: "/management/human-inbox" },
      },
    ],
    evidence_refs: [],
    summary: {
      quarter: "2026-Q3",
      formula_version: "pm12-default-v1",
      persona_count: 1,
      ranked_count: 1,
      recommendation_count: 1,
      returned_count: 1,
      human_gate_decision_count: 1,
      live_capital_mutation_count: 0,
      evidence_ref_count: 0,
      redacted_evidence_count: 0,
      by_action: { promote_to_canary_candidate: 1 },
      allowed_actions: ["promote_to_canary_candidate"],
      basis: "weighted_score",
      policy: "read_only_governance_advisory",
    },
    policy: "read_only_governance_advisory",
    governance_destinations: ["human_inbox", "promotion_review"],
    allowed_actions: ["promote_to_canary_candidate"],
  },
  page_info: { next_page_token: null, total: 1, page_size: 8 },
  meta: { surfaces: { quarterly_ranking_recommendations: surfaceOk } },
} as ManagementQuarterlyRankingRecommendationsResponse;

const promotionReviewsResponse = {
  data: {
    id: "promotion-reviews-2026-q3",
    quarter: "2026-Q3",
    quarter_window: quarterWindow,
    items: [
      {
        review_id: "review-alpha",
        promotion_review_id: "review-alpha",
        recommendation_id: "rec-alpha",
        human_inbox_id: "human-alpha",
        quarter: "2026-Q3",
        persona_id: "persona-alpha",
        name: "Alpha Paper",
        owner: "research",
        state: "paper_running",
        rank: 1,
        score: 92,
        action_id: "promote_to_canary_candidate",
        action_label: "Promote to canary candidate",
        status: "submitted",
        decision_status: "pending",
        submitted: true,
        promotion_path: {
          from_stage: "paper_running",
          target_stage: "canary_candidate",
          eventual_live_stage: "live_running",
        },
        requires_human_gate_decision: true,
        live_capital_mutation: false,
        direct_live_capital_mutation: false,
        evidence_refs: [],
        links: { submit: "/bff/management/quarterly-ranking/recommendations/rec-alpha/submit" },
      },
    ],
    summary: {
      quarter: "2026-Q3",
      review_count: 1,
      returned_count: 1,
      pending_count: 1,
      decision_accepted_count: 0,
      live_capital_mutation_count: 0,
      requires_human_gate_decision: true,
      allowed_decisions: ["approve", "approve_with_conditions", "reject"],
      policy: "promotion_governance_human_gate_no_direct_live_capital",
    },
  },
  page_info: { next_page_token: null, total: 1, page_size: 8 },
  meta: { surfaces: { promotion_reviews: surfaceOk } },
} as ManagementPromotionReviewsResponse;

const formulaResponse = {
  data: formula,
  formula,
  version_history: formula.version_history,
  evidence_refs: [],
  summary: {
    formula_id: "pm12-default",
    formula_version: "pm12-default-v1",
    component_count: 4,
    weight_total: 1,
    evidence_ref_count: 1,
    basis: "weighted_score",
    policy: "read_only_governance_advisory",
  },
  meta: {
    surfaces: { quarterly_ranking_formula: surfaceOk },
    composition_sources: ["GET /bff/management/persona-league/rankings"],
    version_policy: "formula_version_changes_require_governance_evidence",
  },
} as ManagementQuarterlyRankingFormulaResponse;

function mockReaders() {
  vi.spyOn(managementApi, "fetchManagementQuarterlyRanking").mockResolvedValue(rankingResponse);
  vi.spyOn(managementApi, "fetchManagementQuarterlyRankingRecommendations").mockResolvedValue(recommendationsResponse);
  vi.spyOn(managementApi, "fetchManagementPromotionReviews").mockResolvedValue(promotionReviewsResponse);
  vi.spyOn(managementApi, "fetchManagementQuarterlyRankingFormula").mockResolvedValue(formulaResponse);
  vi.spyOn(managementClient.rebalances, "list").mockResolvedValue({
    items: [
      {
        id: "rb-q3",
        rebalance_id: "rb-q3",
        capital_pool_id: "pool-alpha",
        status: "reviewing",
        reason: "quarterly rebalance proposal",
      },
    ],
    cursor: {},
    pageSize: 1,
    totalCountExact: true,
    estimatedTotal: 1,
  });
}

describe("ManagementPromotionAllocationPanel", () => {
  beforeEach(() => {
    window.history.pushState({}, "", "/management/promotion-allocation");
    mockReaders();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders candidate, review, capital, and formula surfaces in one workbench", async () => {
    render(<ManagementPromotionAllocationPanel />);

    await screen.findByTestId("management-promotion-allocation-panel");
    expect(screen.getByTestId("promotion-allocation-workflow-rail").textContent).toContain(
      "/bff/management/quarterly-ranking/recommendations/{id}/submit",
    );

    expect(screen.getByTestId("promotion-allocation-table-paper-candidates").textContent).toContain("Alpha Paper");

    fireEvent.click(screen.getByRole("tab", { name: /promotion review/i }));
    expect(screen.getByTestId("promotion-allocation-table-review-queue").textContent).toContain(
      "/bff/management/promotion-reviews/review-alpha/decisions",
    );

    fireEvent.click(screen.getByRole("tab", { name: /quarterly capital/i }));
    expect(screen.getByTestId("promotion-allocation-table-rebalances").textContent).toContain("rb-q3");

    fireEvent.click(screen.getByRole("tab", { name: /formula policy/i }));
    expect(screen.getByTestId("promotion-allocation-table-formula-weights").textContent).toContain(
      "/bff/ranking-formulas + governance evidence",
    );
  });

  it("submits a paper candidate into promotion review without claiming live mutation", async () => {
    const submit = vi.spyOn(managementApi, "submitManagementQuarterlyRankingRecommendation").mockResolvedValue({
      data: {
        command_id: "cmd-submit-alpha",
        review_id: "review-alpha",
        promotion_review_id: "review-alpha",
        recommendation_id: "rec-alpha",
        persona_id: "persona-alpha",
        action_id: "promote_to_canary_candidate",
        status: "submitted",
        submitted: true,
        human_inbox_id: "human-alpha",
        requires_human_gate_decision: true,
        live_capital_mutation: false,
        direct_live_capital_mutation: false,
        runtime_mutation: false,
        links: {},
      },
      meta: {
        requires_human_gate_decision: true,
        live_capital_mutation: false,
      },
    });

    render(<ManagementPromotionAllocationPanel />);

    const row = await screen.findByTestId("promotion-candidate-row-rec-alpha");
    fireEvent.click(within(row).getByRole("button", { name: /submit/i }));

    await waitFor(() => expect(submit).toHaveBeenCalledWith(
      "rec-alpha",
      expect.objectContaining({ source_ui: "management_promotion_allocation" }),
    ));
    expect((await screen.findByTestId("promotion-allocation-submit-banner")).textContent).toContain("review-alpha");
    expect(screen.getByTestId("promotion-allocation-table-review-queue").textContent).toContain("review only");
  });

  it("opens the quarterly capital tab for legacy capital-binding live route", async () => {
    window.history.pushState({}, "", "/management/readiness/capital-binding-live");

    render(<ManagementPromotionAllocationPanel />);

    await screen.findByTestId("promotion-allocation-table-rebalances");
    expect(screen.getByRole("tab", { name: /quarterly capital/i }).getAttribute("aria-selected")).toBe("true");
  });
});
