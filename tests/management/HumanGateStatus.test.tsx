import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  HumanGateStatus,
  type HumanGateDecision,
} from "../../apps/management/src/screens/HumanGate";

// ---------- Fixtures ----------

const evidenceHash = "sha256:aaaa1234567890abcdef1234567890abcdef1234567890abcdef1234567890ab";

const approvedDecision: HumanGateDecision = {
  decision_id: "hgd-001",
  target_type: "deployment_plan",
  target_id: "dp-paper-001",
  target_environment: "paper",
  required_roles: ["risk_owner", "operator"],
  evidence_reviewed: [
    {
      key: "canary_plan",
      evidence_hash: evidenceHash,
      source_ref: "docs/deployment/evidence/ep5-dual-vm/canary-deployment-plan.json",
      status: "passed",
      reviewed_at: "2026-05-19T10:00:00Z",
    },
    {
      key: "rollback_drill",
      evidence_hash: evidenceHash,
      source_ref: "docs/deployment/evidence/ep5-dual-vm/rollback-drill-summary.json",
      status: "passed",
      reviewed_at: "2026-05-19T10:05:00Z",
    },
  ],
  can_proceed_input: {
    readiness_packet_ref: "pkt-001",
    readiness_packet_can_proceed: true,
    required_evidence: ["canary_plan", "rollback_drill"],
    missing_evidence: [],
    blocking_reasons: [],
    unsafe_true_flags: [],
    gate_results_blocking: [],
  },
  signatures: [
    {
      signature_id: "hgs-001",
      role: "risk_owner",
      actor_id: "alice@pantheon",
      signed_at: "2026-05-19T10:10:00Z",
      evidence_hash: evidenceHash,
      evidence_reviewed: ["canary_plan", "rollback_drill"],
      meaning: "approved",
      conditions: [],
    },
    {
      signature_id: "hgs-002",
      role: "operator",
      actor_id: "bob@pantheon",
      signed_at: "2026-05-19T10:15:00Z",
      evidence_hash: evidenceHash,
      evidence_reviewed: ["canary_plan", "rollback_drill"],
      meaning: "approved",
      conditions: [],
    },
  ],
  status: "approved",
  can_proceed: true,
  reason: "All required human gate signatures are bound to the reviewed evidence.",
  created_at: "2026-05-19T09:00:00Z",
  updated_at: "2026-05-19T10:15:00Z",
};

const pendingDecision: HumanGateDecision = {
  ...approvedDecision,
  decision_id: "hgd-002",
  signatures: [approvedDecision.signatures[0]],
  status: "pending",
  can_proceed: false,
  reason: "Human gate is not ready: missing_signature:operator",
};

const blockedDecision: HumanGateDecision = {
  ...approvedDecision,
  decision_id: "hgd-003",
  signatures: [],
  status: "blocked",
  can_proceed: false,
  can_proceed_input: {
    ...approvedDecision.can_proceed_input,
    readiness_packet_can_proceed: false,
    missing_evidence: ["datasource_smoke"],
    blocking_reasons: ["operator_checklist_failed"],
    unsafe_true_flags: ["live_capital_unsafe_flag"],
    gate_results_blocking: ["paper_gate:fail"],
  },
  reason: "Human gate is not ready: readiness_packet_can_proceed=false",
};

// ---------- Tests ----------

describe("HumanGateStatus", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders loading state", () => {
    render(<HumanGateStatus loading />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading human gate status");
  });

  it("renders error state with retry button", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    render(<HumanGateStatus error="Fetch failed" onRetry={onRetry} />);

    expect(screen.getByRole("alert")).toHaveTextContent("Fetch failed");
    await user.click(screen.getByTestId("retry-button"));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("renders empty state when no decision is provided", () => {
    render(<HumanGateStatus />);
    expect(screen.getByTestId("human-gate-empty")).toBeInTheDocument();
  });

  it("renders approved decision with all signatures and can-proceed badge", () => {
    render(<HumanGateStatus decision={approvedDecision} />);

    expect(screen.getByTestId("human-gate-status")).toBeInTheDocument();
    expect(screen.getByTestId("decision-id")).toHaveTextContent("hgd-001");
    expect(screen.getByTestId("decision-status")).toHaveTextContent("approved");
    expect(screen.getByTestId("target-info")).toHaveTextContent(
      "deployment_plan / dp-paper-001 (paper)",
    );
    expect(screen.getByTestId("can-proceed-badge")).toHaveTextContent("Can Proceed");

    expect(screen.getByTestId("role-signed-risk_owner")).toHaveTextContent(
      "alice@pantheon",
    );
    expect(screen.getByTestId("role-signed-operator")).toHaveTextContent(
      "bob@pantheon",
    );

    expect(screen.queryByTestId("blocking-reasons")).not.toBeInTheDocument();
    expect(screen.getByTestId("decision-reason")).toBeInTheDocument();
  });

  it("renders pending decision with one pending signature and pending count", () => {
    render(<HumanGateStatus decision={pendingDecision} />);

    expect(screen.getByTestId("decision-status")).toHaveTextContent("pending");
    expect(screen.queryByTestId("can-proceed-badge")).not.toBeInTheDocument();

    expect(screen.getByTestId("role-signed-risk_owner")).toBeInTheDocument();
    expect(screen.getByTestId("role-pending-operator")).toHaveTextContent("Pending");

    expect(screen.getByTestId("pending-count")).toHaveTextContent("1 signature pending");

    expect(screen.getByTestId("blocking-reasons")).toBeInTheDocument();
    expect(screen.getByTestId("blocking-reasons").textContent).toContain(
      "missing_signature: operator",
    );
  });

  it("renders blocked decision with all blocking reasons", () => {
    render(<HumanGateStatus decision={blockedDecision} />);

    expect(screen.getByTestId("decision-status")).toHaveTextContent("blocked");

    const blockingList = screen.getByTestId("blocking-reasons");
    expect(blockingList.textContent).toContain("readiness_packet_can_proceed=false");
    expect(blockingList.textContent).toContain("missing_evidence: datasource_smoke");
    expect(blockingList.textContent).toContain("blocking_reason: operator_checklist_failed");
    expect(blockingList.textContent).toContain("unsafe_true_flag: live_capital_unsafe_flag");
    expect(blockingList.textContent).toContain("gate_result_blocking: paper_gate:fail");
    expect(blockingList.textContent).toContain("missing_signature: risk_owner");
    expect(blockingList.textContent).toContain("missing_signature: operator");
  });

  it("renders evidence reviewed items", () => {
    render(<HumanGateStatus decision={approvedDecision} />);

    expect(screen.getByTestId("evidence-row-canary_plan")).toBeInTheDocument();
    expect(screen.getByTestId("evidence-status-canary_plan")).toHaveTextContent("passed");
    expect(screen.getByTestId("evidence-ref-canary_plan")).toHaveTextContent(
      "docs/deployment/evidence/ep5-dual-vm/canary-deployment-plan.json",
    );

    expect(screen.getByTestId("evidence-row-rollback_drill")).toBeInTheDocument();
    expect(screen.getByTestId("evidence-status-rollback_drill")).toHaveTextContent("passed");
  });

  it("renders expiry countdown when expires_at is in the future", () => {
    const futureExpiry = new Date(Date.now() + 3_600_000 * 2 + 60_000 * 30).toISOString();
    render(
      <HumanGateStatus
        decision={{ ...approvedDecision, expires_at: futureExpiry }}
      />,
    );
    const countdown = screen.getByTestId("expiry-countdown");
    expect(countdown.textContent).toMatch(/\d+h \d+m remaining/);
  });

  it("shows 'Expired' when expires_at is in the past", () => {
    const pastExpiry = new Date(Date.now() - 1000).toISOString();
    render(
      <HumanGateStatus
        decision={{ ...approvedDecision, expires_at: pastExpiry }}
      />,
    );
    expect(screen.getByTestId("expiry-countdown")).toHaveTextContent("Expired");
  });

  it("does not render expiry countdown when expires_at is absent", () => {
    render(<HumanGateStatus decision={approvedDecision} />);
    expect(screen.queryByTestId("expiry-countdown")).not.toBeInTheDocument();
  });

  it("shows revoked signature as inactive and still counts role as pending", () => {
    const revokedDecision: HumanGateDecision = {
      ...approvedDecision,
      decision_id: "hgd-revoked",
      status: "revoked",
      can_proceed: false,
      signatures: [
        {
          ...approvedDecision.signatures[0],
          revoked_at: "2026-05-19T11:00:00Z",
        },
        approvedDecision.signatures[1],
      ],
    };
    render(<HumanGateStatus decision={revokedDecision} />);

    expect(screen.getByTestId("decision-status")).toHaveTextContent("revoked");
    expect(screen.getByTestId("role-pending-risk_owner")).toHaveTextContent("Pending");
    expect(screen.getByTestId("role-signed-operator")).toBeInTheDocument();
  });
});
