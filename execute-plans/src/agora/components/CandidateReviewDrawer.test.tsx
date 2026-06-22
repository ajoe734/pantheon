import React from "react";
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CandidateReviewDrawer } from "./CandidateReviewDrawer";
import type {
  CandidateScoreResult,
  CandidatePoolMember,
} from "@/lib/bff-v1/agora/candidatePool";

// Mock the BFF client
vi.mock("@/lib/bff-v1/agora/candidatePool", () => ({
  getCandidatePoolScore: vi.fn(),
  listCandidatePoolMembers: vi.fn(),
  reviewCandidateMember: vi.fn(),
}));

import {
  getCandidatePoolScore,
  listCandidatePoolMembers,
  reviewCandidateMember,
} from "@/lib/bff-v1/agora/candidatePool";

const mockGetScore = getCandidatePoolScore as ReturnType<typeof vi.fn>;
const mockListMembers = listCandidatePoolMembers as ReturnType<typeof vi.fn>;
const mockReview = reviewCandidateMember as ReturnType<typeof vi.fn>;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const baseScore: CandidateScoreResult = {
  candidate_id: "cand-001",
  pool_id: "pool-alpha",
  recipe_id: "winner_branch_v1",
  recipe_version: 3,
  raw_score: 72.4,
  penalty_score: 8.2,
  evidence_confidence: 0.81,
  effective_score: 68.1,
  rank: 1,
  band: "priority_review",
  components: [
    {
      component_id: "expected_value",
      label: "Expected Value",
      category: "alpha",
      raw_value: 0.052,
      normalized_value: 0.88,
      transform: "sigmoid",
      direction: "higher_better",
      weight: 0.18,
      contribution: 15.84,
      missing_policy: "score_zero",
      evidence_refs: ["eb-ev-001"],
      explanation: "Strong EV relative to benchmark",
    },
    {
      component_id: "data_quality",
      label: "Data Quality",
      category: "data_quality",
      raw_value: 0.92,
      normalized_value: 0.92,
      transform: "identity",
      direction: "higher_better",
      weight: 0.10,
      contribution: 9.2,
      missing_policy: "score_zero",
      evidence_refs: [],
      explanation: "High data completeness",
    },
    {
      component_id: "liquidity_cap",
      label: "Liquidity Cap",
      category: "liquidity",
      raw_value: 0.15,
      normalized_value: 0.15,
      transform: "identity",
      direction: "lower_better",
      weight: 0.08,
      contribution: 4.5,
      missing_policy: "cap_at_max",
      evidence_refs: [],
      explanation: "Market impact constraint",
    },
  ],
  blockers: [],
  data_cutoff: "2026-06-22T09:00:00Z",
  scored_at: "2026-06-22T09:05:00Z",
  override_reason: null,
};

const baseMember: CandidatePoolMember = {
  artifact_id: "cand-001",
  strategy_ref: "strat-momentum-001",
  title: "Momentum Alpha v2",
  lifecycle_state: "candidate",
  producing_persona_id: "persona-momentum",
  created_at: "2026-06-20T00:00:00Z",
};

describe("CandidateReviewDrawer — closed state", () => {
  it("renders nothing when open=false", () => {
    render(
      <CandidateReviewDrawer poolId="pool-alpha" open={false} onClose={() => undefined} />,
    );
    expect(screen.queryByTestId("candidate-review-drawer-pool-alpha")).toBeNull();
  });
});

describe("CandidateReviewDrawer — loading state", () => {
  it("shows loading indicator while fetching", () => {
    // Never resolve — listCandidatePoolMembers now returns { items, etag }
    mockGetScore.mockReturnValue(new Promise(() => undefined));
    mockListMembers.mockReturnValue(new Promise(() => undefined));
    render(
      <CandidateReviewDrawer poolId="pool-alpha" open={true} onClose={() => undefined} />,
    );
    expect(screen.getByTestId("candidate-review-drawer-loading")).toBeDefined();
  });
});

describe("CandidateReviewDrawer — error state", () => {
  it("shows error message when fetch fails", async () => {
    mockGetScore.mockRejectedValueOnce(new Error("BFF unavailable"));
    mockListMembers.mockResolvedValueOnce({ items: [], etag: null });
    render(
      <CandidateReviewDrawer poolId="pool-alpha" open={true} onClose={() => undefined} />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("candidate-review-drawer-error")).toBeDefined();
    });
  });
});

describe("CandidateReviewDrawer — empty state", () => {
  it("shows empty message when score list is empty (pool not yet scored)", async () => {
    mockGetScore.mockResolvedValueOnce([]);
    mockListMembers.mockResolvedValueOnce({ items: [baseMember], etag: null });
    render(
      <CandidateReviewDrawer poolId="pool-alpha" open={true} onClose={() => undefined} />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("candidate-review-drawer-empty")).toBeDefined();
    });
  });
});

describe("CandidateReviewDrawer — loaded state", () => {
  beforeEach(async () => {
    mockGetScore.mockResolvedValue([baseScore]);
    mockListMembers.mockResolvedValue({ items: [baseMember], etag: '"pool-etag-v1"' });
  });

  function renderOpen() {
    return render(
      <CandidateReviewDrawer poolId="pool-alpha" open={true} onClose={() => undefined} />,
    );
  }

  it("renders the drawer panel", async () => {
    renderOpen();
    await waitFor(() => {
      expect(screen.getByTestId("candidate-review-drawer-pool-alpha")).toBeDefined();
    });
  });

  it("renders candidate table", async () => {
    renderOpen();
    await waitFor(() => {
      expect(screen.getByTestId("candidate-review-table")).toBeDefined();
    });
  });

  it("renders candidate row with correct testid", async () => {
    renderOpen();
    await waitFor(() => {
      expect(screen.getByTestId("candidate-row-cand-001")).toBeDefined();
    });
  });

  it("displays candidate title from members list", async () => {
    renderOpen();
    await waitFor(() => {
      expect(screen.getByText("Momentum Alpha v2")).toBeDefined();
    });
  });

  it("displays effective score as clickable button", async () => {
    renderOpen();
    await waitFor(() => {
      const scoreBtn = screen.getByTestId("candidate-score-btn-cand-001");
      expect(scoreBtn.textContent).toBe("68.1");
    });
  });

  it("displays evidence_confidence percentage", async () => {
    renderOpen();
    await waitFor(() => {
      const confEl = screen.getByTestId("candidate-confidence-cand-001");
      expect(confEl.textContent).toBe("81%");
    });
  });

  it("displays top positive drivers", async () => {
    renderOpen();
    await waitFor(() => {
      const driversEl = screen.getByTestId("candidate-top-drivers-cand-001");
      expect(driversEl.textContent).toContain("Expected Value");
    });
  });

  it("displays top penalty drivers", async () => {
    renderOpen();
    await waitFor(() => {
      const penEl = screen.getByTestId("candidate-top-penalties-cand-001");
      expect(penEl.textContent).toContain("Liquidity Cap");
    });
  });

  it("displays data quality badge from data_quality component", async () => {
    renderOpen();
    await waitFor(() => {
      const dqEl = screen.getByTestId("candidate-dq-badge-cand-001");
      expect(dqEl.textContent).toBe("92%");
    });
  });

  it("displays band badge", async () => {
    renderOpen();
    await waitFor(() => {
      const bandEl = screen.getByTestId("candidate-band-cand-001");
      expect(bandEl.textContent).toContain("priority");
    });
  });

  it("renders decision buttons for candidate", async () => {
    renderOpen();
    await waitFor(() => {
      expect(
        screen.getByTestId("candidate-decide-approve_for_monitoring-cand-001"),
      ).toBeDefined();
      expect(screen.getByTestId("candidate-decide-send_to_shadow-cand-001")).toBeDefined();
      expect(screen.getByTestId("candidate-decide-needs_more_research-cand-001")).toBeDefined();
      expect(screen.getByTestId("candidate-decide-park-cand-001")).toBeDefined();
      expect(screen.getByTestId("candidate-decide-reject-cand-001")).toBeDefined();
    });
  });

  it("opens score decomposition panel on score click", async () => {
    renderOpen();
    await waitFor(() => {
      expect(screen.getByTestId("candidate-score-btn-cand-001")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("candidate-score-btn-cand-001"));
    expect(screen.getByTestId("score-decomposition-cand-001")).toBeDefined();
  });

  it("decomposition panel shows raw_score, penalty_score, evidence_confidence, effective_score", async () => {
    renderOpen();
    await waitFor(() => {
      fireEvent.click(screen.getByTestId("candidate-score-btn-cand-001"));
    });
    expect(screen.getByTestId("score-summary-raw-score-cand-001").textContent).toBe("72.4");
    expect(screen.getByTestId("score-summary-penalty-score-cand-001").textContent).toBe("8.2");
    expect(screen.getByTestId("score-summary-evidence-confidence-cand-001").textContent).toBe(
      "81%",
    );
    expect(screen.getByTestId("score-summary-effective-score-cand-001").textContent).toBe("68.1");
  });

  it("decomposition panel shows component rows", async () => {
    renderOpen();
    await waitFor(() => {
      fireEvent.click(screen.getByTestId("candidate-score-btn-cand-001"));
    });
    expect(screen.getByTestId("component-row-cand-001-expected_value")).toBeDefined();
    expect(screen.getByTestId("component-contribution-cand-001-expected_value").textContent).toBe(
      "+15.84",
    );
    expect(
      screen.getByTestId("component-normalized-cand-001-expected_value").textContent,
    ).toBe("0.8800");
  });

  it("closes decomposition panel on close button click", async () => {
    renderOpen();
    await waitFor(() => {
      fireEvent.click(screen.getByTestId("candidate-score-btn-cand-001"));
    });
    expect(screen.getByTestId("score-decomposition-cand-001")).toBeDefined();
    fireEvent.click(screen.getByTestId("score-decomposition-close-cand-001"));
    expect(screen.queryByTestId("score-decomposition-cand-001")).toBeNull();
  });

  it("closes drawer on backdrop click", async () => {
    const onClose = vi.fn();
    render(<CandidateReviewDrawer poolId="pool-alpha" open={true} onClose={onClose} />);
    await waitFor(() => {
      expect(screen.getByTestId("candidate-review-drawer-backdrop")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("candidate-review-drawer-backdrop"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("closes drawer on close button click", async () => {
    const onClose = vi.fn();
    render(<CandidateReviewDrawer poolId="pool-alpha" open={true} onClose={onClose} />);
    await waitFor(() => {
      expect(screen.getByTestId("candidate-review-drawer-close")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("candidate-review-drawer-close"));
    expect(onClose).toHaveBeenCalledOnce();
  });
});

describe("CandidateReviewDrawer — decision flow", () => {
  beforeEach(async () => {
    mockGetScore.mockResolvedValue([baseScore]);
    mockListMembers.mockResolvedValue({ items: [baseMember], etag: '"pool-etag-v1"' });
  });

  it("selecting approve_for_monitoring and confirming calls reviewCandidateMember", async () => {
    mockReview.mockResolvedValueOnce({});
    const onDecisionRecorded = vi.fn();
    render(
      <CandidateReviewDrawer
        poolId="pool-alpha"
        open={true}
        onClose={() => undefined}
        onDecisionRecorded={onDecisionRecorded}
      />,
    );
    await waitFor(() => {
      expect(
        screen.getByTestId("candidate-decide-approve_for_monitoring-cand-001"),
      ).toBeDefined();
    });
    fireEvent.click(
      screen.getByTestId("candidate-decide-approve_for_monitoring-cand-001"),
    );
    // Confirm button should appear (no rationale needed)
    await waitFor(() => {
      expect(screen.getByTestId("candidate-confirm-cand-001")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("candidate-confirm-cand-001"));
    await waitFor(() => {
      expect(screen.getByTestId("candidate-decision-confirmed-cand-001")).toBeDefined();
    });
    expect(mockReview).toHaveBeenCalledWith(
      "pool-alpha",
      "cand-001",
      expect.objectContaining({ decision: "approve_for_monitoring" }),
      expect.objectContaining({
        ifMatch: '"pool-etag-v1"',
        idempotencyKey: expect.any(String),
        requestId: expect.any(String),
      }),
    );
    expect(onDecisionRecorded).toHaveBeenCalledWith("cand-001", "approve_for_monitoring");
  });

  it("park decision requires rationale input before confirming", async () => {
    mockReview.mockResolvedValueOnce({});
    render(
      <CandidateReviewDrawer poolId="pool-alpha" open={true} onClose={() => undefined} />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("candidate-decide-park-cand-001")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("candidate-decide-park-cand-001"));
    // Rationale input should appear
    await waitFor(() => {
      expect(screen.getByTestId("candidate-rationale-cand-001")).toBeDefined();
    });
    // Confirm button is disabled without rationale
    const confirmBtn = screen.getByTestId("candidate-confirm-cand-001") as HTMLButtonElement;
    expect(confirmBtn.disabled).toBe(true);
    // Type rationale
    fireEvent.change(screen.getByTestId("candidate-rationale-cand-001"), {
      target: { value: "Weak liquidity profile" },
    });
    expect(confirmBtn.disabled).toBe(false);
    fireEvent.click(confirmBtn);
    await waitFor(() => {
      expect(mockReview).toHaveBeenCalledWith(
        "pool-alpha",
        "cand-001",
        expect.objectContaining({ decision: "park", rationale: "Weak liquidity profile" }),
        expect.objectContaining({
          ifMatch: '"pool-etag-v1"',
          idempotencyKey: expect.any(String),
          requestId: expect.any(String),
        }),
      );
    });
  });

  it("cancel button dismisses pending decision without submitting", async () => {
    render(
      <CandidateReviewDrawer poolId="pool-alpha" open={true} onClose={() => undefined} />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("candidate-decide-reject-cand-001")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("candidate-decide-reject-cand-001"));
    await waitFor(() => {
      expect(screen.getByTestId("candidate-cancel-cand-001")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("candidate-cancel-cand-001"));
    expect(screen.queryByTestId("candidate-cancel-cand-001")).toBeNull();
    expect(mockReview).not.toHaveBeenCalled();
  });

  it("shows decision error when reviewCandidateMember rejects", async () => {
    mockReview.mockRejectedValueOnce(new Error("AUTHZ_DENIED"));
    render(
      <CandidateReviewDrawer poolId="pool-alpha" open={true} onClose={() => undefined} />,
    );
    await waitFor(() => {
      expect(
        screen.getByTestId("candidate-decide-approve_for_monitoring-cand-001"),
      ).toBeDefined();
    });
    fireEvent.click(
      screen.getByTestId("candidate-decide-approve_for_monitoring-cand-001"),
    );
    await waitFor(() => {
      expect(screen.getByTestId("candidate-confirm-cand-001")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("candidate-confirm-cand-001"));
    await waitFor(() => {
      expect(screen.getByTestId("candidate-decision-error-cand-001")).toBeDefined();
    });
    expect(
      screen.getByTestId("candidate-decision-error-cand-001").textContent,
    ).toContain("AUTHZ_DENIED");
  });
});

describe("CandidateReviewDrawer — score decomposition missing_policy", () => {
  it("shows missing_policy when non-default cap", async () => {
    const scoreWithCap: CandidateScoreResult = {
      ...baseScore,
      candidate_id: "cand-cap",
      components: [
        {
          ...baseScore.components[0],
          component_id: "capped_comp",
          label: "Capped Component",
          normalized_value: null,
          missing_policy: "cap_at_max",
        },
      ],
    };
    const memberWithCap: CandidatePoolMember = {
      ...baseMember,
      artifact_id: "cand-cap",
      title: "Capped Candidate",
    };
    mockGetScore.mockResolvedValueOnce([scoreWithCap]);
    mockListMembers.mockResolvedValueOnce({ items: [memberWithCap], etag: null });
    render(
      <CandidateReviewDrawer poolId="pool-alpha" open={true} onClose={() => undefined} />,
    );
    await waitFor(() => {
      fireEvent.click(screen.getByTestId("candidate-score-btn-cand-cap"));
    });
    expect(screen.getByTestId("component-missing-cand-cap-capped_comp")).toBeDefined();
    expect(
      screen.getByTestId("component-missing-cand-cap-capped_comp").textContent,
    ).toContain("cap_at_max");
  });
});
