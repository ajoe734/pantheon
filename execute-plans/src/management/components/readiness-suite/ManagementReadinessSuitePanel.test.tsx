import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { managementClient } from "@/lib/bff/client";
import type {
  ManagementReadinessCheck,
  ManagementReadinessEvidenceRef,
  ManagementReadinessResponse,
} from "@/lib/bff-v1/management";
import {
  buildManagementReadinessSuiteSummary,
  buildManagementReadinessSurfaceView,
  ManagementReadinessSuitePanel,
} from "./ManagementReadinessSuitePanel";

type ReadinessReaderKey =
  | "ep5"
  | "brokerLive"
  | "capitalBindingLive"
  | "bffHa"
  | "strictPublish";

interface ReadinessFixtureOptions {
  id: string;
  title: string;
  status?: string;
  canProceed?: boolean;
  checks?: ManagementReadinessCheck[];
  blockingReasons?: string[];
  evidenceRefs?: ManagementReadinessEvidenceRef[];
  surfaceKey?: string;
  surfaceStatus?: string;
  surfaceSource?: string;
  surfaceReason?: string;
  staleness?: unknown;
  snapshotAt?: string;
}

function passCheck(id: string, label: string, evidenceRefs: string[] = []): ManagementReadinessCheck {
  return {
    id,
    label,
    status: "pass",
    blocking: false,
    message: `${label} passed.`,
    evidence_refs: evidenceRefs,
  };
}

function failCheck(id: string, label: string, message: string): ManagementReadinessCheck {
  return {
    id,
    label,
    status: "fail",
    blocking: true,
    message,
    evidence_refs: [`evidence://${id}`],
  };
}

function surfaceKeyFor(id: string): string {
  return `management_readiness_${id.replace(/-/g, "_")}`;
}

function makeResponse({
  id,
  title,
  status = "ready",
  canProceed = true,
  checks = [passCheck(`${id}-check`, `${title} check`)],
  blockingReasons = [],
  evidenceRefs = [],
  surfaceKey = surfaceKeyFor(id),
  surfaceStatus = "ok",
  surfaceSource = "bff_composed",
  surfaceReason,
  staleness,
  snapshotAt = "2026-07-03T12:00:00Z",
}: ReadinessFixtureOptions): ManagementReadinessResponse {
  const passed = checks.filter((check) => check.status === "pass").length;
  return {
    data: {
      id,
      readinessId: id,
      readiness_id: id,
      title,
      readinessStatus: status,
      readiness_status: status,
      canProceed,
      can_proceed: canProceed,
      blockingReasons,
      blocking_reasons: blockingReasons,
      checks,
      evidenceRefs,
      evidence_refs: evidenceRefs,
      links: {},
      details: {},
    },
    summary: {
      readinessStatus: status,
      readiness_status: status,
      canProceed,
      can_proceed: canProceed,
      checkCount: checks.length,
      check_count: checks.length,
      passedCheckCount: passed,
      passed_check_count: passed,
      blockingReasonCount: blockingReasons.length,
      blocking_reason_count: blockingReasons.length,
      blockingReasons,
      blocking_reasons: blockingReasons,
      byStatus: checks.reduce<Record<string, number>>((counts, check) => {
        counts[check.status] = (counts[check.status] ?? 0) + 1;
        return counts;
      }, {}),
      by_status: checks.reduce<Record<string, number>>((counts, check) => {
        counts[check.status] = (counts[check.status] ?? 0) + 1;
        return counts;
      }, {}),
    },
    checks,
    items: checks,
    evidence_refs: evidenceRefs,
    meta: {
      snapshot_at: snapshotAt,
      staleness: staleness === undefined ? {} : { [surfaceKey]: staleness },
      surfaces: {
        [surfaceKey]: {
          status: surfaceStatus,
          source: surfaceSource,
          reason: surfaceReason,
        },
      },
    },
  };
}

function mockReadinessReaders(
  values: Record<ReadinessReaderKey, ManagementReadinessResponse | Error | undefined>,
) {
  const spies = {
    ep5: vi.spyOn(managementClient.readiness, "ep5"),
    brokerLive: vi.spyOn(managementClient.readiness, "brokerLive"),
    capitalBindingLive: vi.spyOn(managementClient.readiness, "capitalBindingLive"),
    bffHa: vi.spyOn(managementClient.readiness, "bffHa"),
    strictPublish: vi.spyOn(managementClient.readiness, "strictPublish"),
  };

  (Object.keys(spies) as ReadinessReaderKey[]).forEach((key) => {
    const value = values[key];
    if (value instanceof Error) {
      spies[key].mockRejectedValue(value);
      return;
    }
    spies[key].mockResolvedValue(value as ManagementReadinessResponse);
  });

  return spies;
}

const readyResponses: Record<ReadinessReaderKey, ManagementReadinessResponse> = {
  ep5: makeResponse({
    id: "ep5",
    title: "EP5 Release Gate",
    checks: [
      passCheck("ep5-live-window", "Live window", ["evidence://ep5-window"]),
      passCheck("ep5-evidence-bound", "Evidence bound", ["evidence://ep5-bound"]),
    ],
    evidenceRefs: [
      {
        id: "ep5-proof",
        label: "EP5 proof",
        path: "evidence/ep5-release-gate.json",
        exists: true,
      },
    ],
    staleness: { status: "fresh", age_seconds: 30 },
  }),
  brokerLive: makeResponse({
    id: "broker-live",
    title: "Broker Live Gate",
  }),
  capitalBindingLive: makeResponse({
    id: "capital-binding-live",
    title: "Capital Binding Live Gate",
  }),
  bffHa: makeResponse({
    id: "bff-ha",
    title: "BFF High Availability Gate",
  }),
  strictPublish: makeResponse({
    id: "strict-publish",
    title: "Strict Publish Gate",
  }),
};

describe("ManagementReadinessSuitePanel", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("summarizes five readiness surfaces with go/no-go, counts, freshness, and evidence refs", async () => {
    const brokerBlocked = makeResponse({
      id: "broker-live",
      title: "Broker Live Gate",
      status: "blocked",
      canProceed: false,
      checks: [
        passCheck("broker-preflight", "Broker preflight", ["evidence://broker-preflight"]),
        failCheck("risk_owner_pending", "Risk owner approval", "Risk owner approval is still pending."),
      ],
      blockingReasons: ["risk_owner_pending"],
      evidenceRefs: [
        {
          id: "broker-proof",
          label: "Broker live proof",
          path: "evidence/broker-live.json",
          exists: true,
        },
      ],
      staleness: "fresh",
    });
    const spies = mockReadinessReaders({
      ...readyResponses,
      brokerLive: brokerBlocked,
    });

    render(<ManagementReadinessSuitePanel />);

    await waitFor(() => expect(spies.strictPublish).toHaveBeenCalledTimes(1));
    Object.values(spies).forEach((spy) => expect(spy).toHaveBeenCalledTimes(1));

    const panel = screen.getByTestId("management-readiness-suite-panel");
    expect(panel.getAttribute("data-suite-decision")).toBe("no-go");
    expect(panel.getAttribute("data-suite-state")).toBe("ready");
    expect(within(panel).getByText("Loaded: 5/5")).toBeTruthy();
    expect(within(panel).getByText("Go: 4/5")).toBeTruthy();
    expect(within(panel).getByText("No-go: 1")).toBeTruthy();
    expect(within(panel).getByText("Checks: 6/7")).toBeTruthy();
    expect(within(panel).getByText("Blockers: 1")).toBeTruthy();
    expect(within(panel).getByText("Evidence refs: 2")).toBeTruthy();

    const ep5 = screen.getByTestId("readiness-suite-surface-ep5");
    expect(within(ep5).getByText("EP5 Release Gate")).toBeTruthy();
    expect(within(ep5).getByText("Freshness: fresh age 30s")).toBeTruthy();
    expect(within(ep5).getAllByText("evidence/ep5-release-gate.json")).toHaveLength(2);
    expect(within(ep5).getByTestId("readiness-suite-checks-ep5").getAttribute("data-management-dense-table")).toBe("true");

    const broker = screen.getByTestId("readiness-suite-surface-broker-live");
    expect(broker.getAttribute("data-readiness-state")).toBe("no-go");
    expect(within(broker).getByText("Broker Live Gate")).toBeTruthy();
    expect(within(broker).getByText("risk owner pending")).toBeTruthy();
    expect(within(broker).getByText("Risk owner approval is still pending.")).toBeTruthy();
    expect(within(broker).getAllByText("evidence/broker-live.json")).toHaveLength(2);
  });

  it("marks the suite degraded when a readiness reader fails but other surfaces load", async () => {
    const spies = mockReadinessReaders({
      ...readyResponses,
      brokerLive: new Error("Broker transport down"),
    });

    render(<ManagementReadinessSuitePanel />);

    await waitFor(() => expect(spies.brokerLive).toHaveBeenCalledTimes(1));
    const panel = screen.getByTestId("management-readiness-suite-panel");
    await screen.findByTestId("readiness-suite-degraded-banner");

    expect(panel.getAttribute("data-suite-state")).toBe("degraded");
    expect(panel.getAttribute("data-suite-decision")).toBe("no-go");
    expect(within(panel).getByText("Loaded: 4/5")).toBeTruthy();
    expect(within(panel).getByText("Blockers: 1")).toBeTruthy();
    expect(within(panel).getByText("Degraded readiness data: 1 reader failure(s), 0 degraded source(s).")).toBeTruthy();

    const failure = screen.getByTestId("readiness-suite-failure-broker-live");
    expect(within(failure).getByText("Broker Live Gate")).toBeTruthy();
    expect(within(failure).getByText("Broker transport down")).toBeTruthy();
  });

  it("shows the error state when every readiness reader fails", async () => {
    mockReadinessReaders({
      ep5: new Error("ep5 down"),
      brokerLive: new Error("broker down"),
      capitalBindingLive: new Error("capital down"),
      bffHa: new Error("bff down"),
      strictPublish: new Error("publish down"),
    });

    render(<ManagementReadinessSuitePanel />);

    await screen.findByText("Readiness suite unavailable");
    const panel = screen.getByTestId("management-readiness-suite-panel");
    expect(panel.getAttribute("data-suite-state")).toBe("error");
    expect(screen.getByText(/EP5: ep5 down/)).toBeTruthy();
  });

  it("shows the empty state when readers return no aggregate responses", async () => {
    mockReadinessReaders({
      ep5: undefined,
      brokerLive: undefined,
      capitalBindingLive: undefined,
      bffHa: undefined,
      strictPublish: undefined,
    });

    render(<ManagementReadinessSuitePanel />);

    await screen.findByText("No readiness aggregates");
    const panel = screen.getByTestId("management-readiness-suite-panel");
    expect(panel.getAttribute("data-suite-state")).toBe("ready");
    expect(within(panel).getByText("Loaded: 0/5")).toBeTruthy();
  });

  it("normalizes degraded source metadata separately from no-go blockers", () => {
    const response = makeResponse({
      id: "strict-publish",
      title: "Strict Publish Gate",
      status: "blocked",
      canProceed: false,
      blockingReasons: ["forbidden_path_scan"],
      surfaceStatus: "degraded",
      surfaceSource: "snapshot",
      surfaceReason: "Live store missing freshness evidence.",
      staleness: { status: "stale", age_seconds: 7200 },
    });
    const surface = buildManagementReadinessSurfaceView({
      id: "strict-publish",
      label: "Strict Publish",
      title: "Strict Publish Gate",
      surfaceKey: "management_readiness_strict_publish",
    }, response);
    const summary = buildManagementReadinessSuiteSummary([surface], [], 1);

    expect(surface.canProceed).toBe(false);
    expect(surface.blockerCount).toBe(1);
    expect(surface.degraded).toBe(true);
    expect(surface.freshnessLabel).toBe("stale age 2.0h");
    expect(surface.surfaceReason).toBe("Live store missing freshness evidence.");
    expect(summary.decision).toBe("no-go");
    expect(summary.state).toBe("degraded");
  });
});
