import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  fetchManagementGovernanceLedger,
  fetchManagementHiqBacklog,
  fetchManagementInterventionStream,
  fetchManagementSentinelPulse,
  type ManagementGovernanceLedgerResponse,
  type ManagementHiqBacklogResponse,
  type ManagementInterventionStreamResponse,
  type ManagementSentinelPulseResponse,
} from "@/lib/bff-v1/management";

import {
  collectDecisionWorkbenchRows,
  ManagementDecisionWorkbenchPanel,
} from "./ManagementDecisionWorkbenchPanel";

vi.mock("@/lib/bff-v1/management", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/bff-v1/management")>();
  return {
    ...actual,
    fetchManagementGovernanceLedger: vi.fn(),
    fetchManagementHiqBacklog: vi.fn(),
    fetchManagementInterventionStream: vi.fn(),
    fetchManagementSentinelPulse: vi.fn(),
  };
});

const governanceResponse: ManagementGovernanceLedgerResponse = {
  data: {
    id: "management-governance-ledger",
    items: [
      {
        id: "gov-1",
        entry_id: "gov-1",
        ledger_id: "ledger-1",
        source_type: "override",
        source_dataset: "approvals",
        event_type: "decision_required",
        status: "pending",
        outcome: null,
        actor: "ops-lead",
        target_type: "strategy",
        target_id: "STR-1",
        risk_level: "high",
        occurred_at: "2026-07-03T10:05:00Z",
        created_at: "2026-07-03T10:04:00Z",
        title: "Override requires governance confirmation",
        summary: "Operator needs to inspect the linked approval evidence.",
        href: "/audit/gov-1",
        links: { audit: "/audit/gov-1" },
        evidence_refs: [{ id: "ledger-evidence-1" }],
      },
    ],
    summary: {
      ledger_count: 1,
      returned_ledger_count: 1,
      approval_count: 0,
      intervention_count: 0,
      override_count: 1,
      by_source_type: { override: 1 },
      by_status: { pending: 1 },
      by_event_type: { decision_required: 1 },
      latest_at: "2026-07-03T10:05:00Z",
      policy: "read_only",
      basis: "fixture",
    },
  },
  page_info: { next_page_token: null, total: 1, page_size: 12 },
  meta: {
    snapshot_at: "2026-07-03T10:06:00Z",
    surfaces: {
      management_governance_ledger: { status: "ok", source: "bff_composed" },
    },
  },
};

const hiqResponse: ManagementHiqBacklogResponse = {
  data: {
    id: "management-hiq-backlog",
    items: [
      {
        id: "hiq-1",
        backlog_id: "hiq-1",
        source_type: "sentinel_finding",
        source_id: "finding-9",
        human_inbox_id: "inbox-1",
        kind: "capital_guardrail",
        status: "open",
        action_state: "pending",
        priority: "critical",
        risk_level: "critical",
        severity: "critical",
        title: "Capital guardrail needs operator review",
        summary: "Runtime drift crossed the manual-review threshold.",
        created_at: "2026-07-03T10:02:00Z",
        updated_at: "2026-07-03T10:07:00Z",
        target: { type: "runtime", id: "RT-9", owner: "runtime-owner" },
        triggered_by: "sentinel",
        correlation_id: "corr-9",
        source_refs: { finding_id: "finding-9", runtime_id: "RT-9" },
        links: { finding: "/sentinel/finding-9" },
        allowed_actions: { decide: false },
      },
    ],
    summary: {
      backlog_count: 1,
      returned_backlog_count: 1,
      intervention_count: 0,
      sentinel_finding_count: 1,
      pending_count: 1,
      critical_count: 1,
      high_count: 0,
      by_source_type: { sentinel_finding: 1 },
      by_status: { open: 1 },
      by_kind: { capital_guardrail: 1 },
      by_priority: { critical: 1 },
      latest_at: "2026-07-03T10:07:00Z",
      policy: "read_only",
      basis: "fixture",
    },
  },
  page_info: { next_page_token: null, total: 1, page_size: 12 },
  meta: {
    snapshot_at: "2026-07-03T10:07:00Z",
    surfaces: {
      management_hiq_backlog: { status: "ok", source: "bff_composed" },
    },
  },
};

const interventionResponse: ManagementInterventionStreamResponse = {
  data: {
    id: "management-intervention-stream",
    items: [
      {
        id: "int-event-1",
        event_id: "event-1",
        event_type: "intervention_state",
        event_source: "v5",
        source_type: "intervention",
        source_dataset: "v5_interventions",
        intervention_id: "INT-1",
        persona_id: "persona-alpha",
        runtime_id: "RT-1",
        strategy_id: "STR-1",
        kind: "rollback_review",
        status: "active",
        priority: "medium",
        risk_level: "medium",
        severity: "medium",
        occurred_at: "2026-07-03T09:57:00Z",
        created_at: "2026-07-03T09:55:00Z",
        updated_at: "2026-07-03T10:01:00Z",
        stream_sequence: 44,
        actor: "operator-a",
        title: "Rollback review intervention active",
        summary: "Operator is reviewing rollback readiness.",
        target: { type: "runtime", id: "RT-1" },
        source_refs: { intervention_id: "INT-1", runtime_id: "RT-1" },
        links: { intervention: "/interventions/INT-1" },
      },
    ],
    summary: {
      event_count: 1,
      returned_event_count: 1,
      intervention_count: 1,
      persona_count: 1,
      window_hours: 72,
      window_start_at: "2026-06-30T10:00:00Z",
      window_end_at: "2026-07-03T10:00:00Z",
      latest_at: "2026-07-03T10:01:00Z",
      by_persona: { "persona-alpha": 1 },
      by_status: { active: 1 },
      by_kind: { rollback_review: 1 },
      by_event_source: { v5: 1 },
      policy: "read_only",
      basis: "fixture",
    },
  },
  page_info: { next_page_token: null, total: 1, page_size: 12 },
  meta: {
    snapshot_at: "2026-07-03T10:01:00Z",
    surfaces: {
      management_intervention_stream: { status: "ok", source: "bff_composed" },
    },
  },
};

const sentinelResponse: ManagementSentinelPulseResponse = {
  data: {
    id: "management-sentinel-pulse",
    snapshot_at: "2026-07-03T10:08:00Z",
    items: [
      {
        id: "finding-1",
        finding_id: "finding-1",
        kind: "runtime_drift",
        severity: "critical",
        risk_level: "critical",
        status: "active",
        title: "Runtime drift breached critical threshold",
        summary: "Sentinel observed drift on RT-9.",
        triggered_at: "2026-07-03T10:08:00Z",
        created_at: "2026-07-03T10:08:00Z",
        updated_at: "2026-07-03T10:09:00Z",
        target: { type: "runtime", id: "RT-9", owner: "sentinel-owner" },
        source_refs: {
          finding_id: "finding-1",
          loop_run_id: "loop-1",
          runtime_id: "RT-9",
          intervention_id: "SINT-1",
        },
        links: { finding: "/sentinel/finding-1" },
      },
    ],
    related: {
      interventions: [
        {
          id: "sentinel-int-1",
          intervention_id: "SINT-1",
          finding_id: "finding-1",
          kind: "risk_review",
          severity: "high",
          risk_level: "high",
          status: "pending",
          title: "Risk review opened from sentinel pulse",
          summary: "Linked intervention awaits operator review.",
          triggered_at: "2026-07-03T10:10:00Z",
          source_refs: {
            finding_id: "finding-1",
            runtime_id: "RT-9",
            intervention_id: "SINT-1",
          },
        },
      ],
    },
    cards: [],
    summary: {
      finding_count: 1,
      returned_finding_count: 1,
      active_finding_count: 1,
      critical_finding_count: 1,
      intervention_count: 1,
      pending_intervention_count: 1,
      highest_severity: "critical",
      by_status: { active: 1 },
      by_severity: { critical: 1 },
      by_kind: { runtime_drift: 1 },
      policy: "read_only",
      basis: "fixture",
    },
  },
  page_info: { next_page_token: null, total: 1, page_size: 12 },
  meta: {
    snapshot_at: "2026-07-03T10:10:00Z",
    surfaces: {
      management_sentinel_pulse: {
        status: "degraded",
        source: "sentinel_snapshot",
        note: "partial v5 intervention coverage",
      },
    },
  },
};

const emptyGovernanceResponse: ManagementGovernanceLedgerResponse = {
  ...governanceResponse,
  data: {
    ...governanceResponse.data,
    items: [],
    summary: { ...governanceResponse.data.summary, ledger_count: 0, returned_ledger_count: 0 },
  },
  page_info: { next_page_token: null, total: 0, page_size: 12 },
};

const emptyInterventionResponse: ManagementInterventionStreamResponse = {
  ...interventionResponse,
  data: {
    ...interventionResponse.data,
    items: [],
    summary: { ...interventionResponse.data.summary, event_count: 0, returned_event_count: 0 },
  },
  page_info: { next_page_token: null, total: 0, page_size: 12 },
};

const emptySentinelResponse: ManagementSentinelPulseResponse = {
  ...sentinelResponse,
  data: {
    ...sentinelResponse.data,
    items: [],
    related: { interventions: [] },
    summary: { ...sentinelResponse.data.summary, finding_count: 0, returned_finding_count: 0 },
  },
  page_info: { next_page_token: null, total: 0, page_size: 12 },
  meta: {
    ...sentinelResponse.meta,
    surfaces: {
      management_sentinel_pulse: { status: "ok", source: "bff_composed" },
    },
  },
};

const mockGovernance = vi.mocked(fetchManagementGovernanceLedger);
const mockHiq = vi.mocked(fetchManagementHiqBacklog);
const mockIntervention = vi.mocked(fetchManagementInterventionStream);
const mockSentinel = vi.mocked(fetchManagementSentinelPulse);

describe("ManagementDecisionWorkbenchPanel", () => {
  beforeEach(() => {
    mockGovernance.mockResolvedValue(governanceResponse);
    mockHiq.mockResolvedValue(hiqResponse);
    mockIntervention.mockResolvedValue(interventionResponse);
    mockSentinel.mockResolvedValue(sentinelResponse);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("normalizes decision queue rows from governance, HIQ, intervention, and sentinel sources", () => {
    const model = collectDecisionWorkbenchRows({
      governanceLedger: governanceResponse,
      hiqBacklog: hiqResponse,
      interventionStream: interventionResponse,
      sentinelPulse: sentinelResponse,
    });

    expect(model.rows.map((row) => row.id)).toEqual([
      "finding-1",
      "hiq-1",
      "sentinel-int-1",
      "gov-1",
      "int-event-1",
    ]);
    expect(model.rows.find((row) => row.id === "hiq-1")).toMatchObject({
      sourceLabel: "HIQ backlog",
      owner: "runtime-owner",
      severity: "critical",
      status: "pending",
      evidence: "4 refs: finding-9, RT-9",
      nextAction: "Triage backlog item",
    });
    expect(model.degradedReasons[0]).toContain("Sentinel pulse is degraded via sentinel_snapshot");
  });

  it("renders compact read-only cards and table rows without enabled write controls", async () => {
    render(<ManagementDecisionWorkbenchPanel />);

    await waitFor(() => expect(mockGovernance).toHaveBeenCalledWith({ page_size: 12 }));
    expect(mockHiq).toHaveBeenCalledWith({ page_size: 12 });
    expect(mockIntervention).toHaveBeenCalledWith({ page_size: 12, window_hours: 72 });
    expect(mockSentinel).toHaveBeenCalledWith({ page_size: 12 });

    expect(screen.getByText("Decision Workbench")).toBeTruthy();
    expect(screen.getByTestId("decision-workbench-read-only").textContent).toContain("Read-only non-production");
    expect(screen.getByTestId("decision-workbench-surface-sentinel").textContent).toContain("Sentinel pulse: degraded");
    expect(screen.getByTestId("decision-workbench-degraded").textContent).toContain("partial v5 intervention coverage");
    expect(screen.getAllByText("Queue").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Critical").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Pending").length).toBeGreaterThan(0);

    const hiqCard = screen.getByTestId("decision-workbench-card-hiq-1");
    expect(within(hiqCard).getByText("Capital guardrail needs operator review")).toBeTruthy();
    expect(within(hiqCard).getByText("runtime-owner")).toBeTruthy();
    expect(within(hiqCard).getByText("Triage backlog item")).toBeTruthy();
    const disabledButtons = screen.getAllByRole("button", { name: "Actions disabled" });
    expect(disabledButtons.length).toBeGreaterThan(0);
    expect(disabledButtons.every((button) => button.hasAttribute("disabled"))).toBe(true);

    const table = screen.getByTestId("decision-workbench-table");
    expect(table.getAttribute("data-management-dense-table")).toBe("true");
    const governanceRow = screen.getByTestId("decision-workbench-row-gov-1");
    expect(within(governanceRow).getByText("ops-lead")).toBeTruthy();
    expect(within(governanceRow).getByText("Confirm ledger evidence")).toBeTruthy();
    expect(screen.getByText("Rollback review intervention active")).toBeTruthy();
    expect(screen.getAllByText("Runtime drift breached critical threshold").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Risk review opened from sentinel pulse").length).toBeGreaterThan(0);
  });

  it("keeps partial data as degraded when one decision source fails", async () => {
    mockGovernance.mockResolvedValue(emptyGovernanceResponse);
    mockHiq.mockRejectedValue(new Error("HIQ aggregate timed out"));
    mockIntervention.mockResolvedValue(emptyInterventionResponse);
    mockSentinel.mockResolvedValue(emptySentinelResponse);

    render(<ManagementDecisionWorkbenchPanel />);

    await screen.findByText("No decision queue items");
    expect(screen.getByTestId("decision-workbench-degraded").textContent).toContain(
      "HIQ backlog failed: HIQ aggregate timed out",
    );
    expect(screen.queryByText("Decision workbench unavailable")).toBeNull();
  });

  it("shows a hard error state when every decision source fails", async () => {
    mockGovernance.mockRejectedValue(new Error("ledger down"));
    mockHiq.mockRejectedValue(new Error("hiq down"));
    mockIntervention.mockRejectedValue(new Error("stream down"));
    mockSentinel.mockRejectedValue(new Error("sentinel down"));

    render(<ManagementDecisionWorkbenchPanel />);

    await screen.findByText("Decision workbench unavailable");
    expect(screen.getByText(/Governance ledger: ledger down/)).toBeTruthy();
    expect(screen.queryByText("No decision queue items")).toBeNull();
  });
});
