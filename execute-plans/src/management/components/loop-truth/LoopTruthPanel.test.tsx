import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { managementClient } from "@/lib/bff/client";
import type { ListEnvelope } from "@/lib/bff-v1";
import type { LoopHealthEntry, LoopTruthLabel, LoopTruthSource } from "@/lib/bff/types";
import { LoopTruthPanel } from "./LoopTruthPanel";

const labels: Record<string, LoopTruthLabel> = {
  seed_fixture: {
    truth_level: "seed_fixture",
    truth_bucket: "seed_fixture",
    source_type: "seed_fixture",
    rank: 0,
    label: "Seed / fixture",
    accepted_as_live: false,
  },
  snapshot_fallback: {
    truth_level: "snapshot_fallback",
    truth_bucket: "snapshot",
    source_type: "snapshot",
    rank: 0,
    label: "Snapshot fallback",
    accepted_as_live: false,
  },
  registry_metadata: {
    truth_level: "registry_metadata",
    truth_bucket: "registry",
    source_type: "registry",
    rank: 1,
    label: "Registry metadata",
    accepted_as_live: false,
  },
  scheduled_tick: {
    truth_level: "scheduled_tick",
    truth_bucket: "scheduled",
    source_type: "scheduled",
    rank: 2,
    label: "Scheduled tick",
    accepted_as_live: false,
  },
  reconciled_live_proof: {
    truth_level: "reconciled_live_proof",
    truth_bucket: "live_truth",
    source_type: "live_truth",
    rank: 3,
    label: "Reconciled live truth",
    accepted_as_live: true,
  },
};

function truthSource(
  truthLevel: keyof typeof labels,
  status: string,
  source: string,
): LoopTruthSource {
  const label = labels[truthLevel];
  return {
    ...label,
    status,
    source,
    refs: [],
    operator_visibility: label.accepted_as_live && status === "present" ? "live_proof" : "not_live_proof",
  };
}

const snapshotLoop: LoopHealthEntry = {
  id: "bff_health_monitoring",
  loop_id: "bff_health_monitoring",
  name: "BFF Health Monitoring",
  current_maturity: "api-only",
  target_maturity: "reconciled",
  controller_health: { status: "observed", source: "local_snapshot" },
  last_success: { at: "2026-06-27T06:10:00Z" },
  last_failure: null,
  evidence_packet: {
    id: "loop-health-bff_health_monitoring",
    packet_id: "loop-health-bff_health_monitoring",
    loop_id: "bff_health_monitoring",
    source: "local_snapshot",
    registry_ref: "docs/deployment/loop-catalog.registry.json",
    current_maturity: "api-only",
    target_maturity: "reconciled",
    highest_truth_level: "reconciled_live_proof",
    highest_truth_rank: 3,
    accepted_live_liveness: false,
    can_claim_reconciled: false,
    can_claim_proven_live: false,
    refs: [],
    truth_sources: [
      truthSource("seed_fixture", "missing", "missing"),
      truthSource("snapshot_fallback", "present", "local_snapshot"),
      truthSource("registry_metadata", "present", "bff_local_registry"),
      truthSource("scheduled_tick", "missing", "missing"),
      truthSource("reconciled_live_proof", "present", "local_snapshot"),
    ],
    operator_truth: {
      truth_level: "snapshot_fallback",
      truth_bucket: "snapshot",
      source_type: "snapshot",
      source: "local_snapshot",
      rank: 0,
      status: "present",
      label: "Snapshot fallback",
      accepted_as_live: false,
      is_live_truth: false,
      degraded: true,
      degraded_reason: "Local snapshot fallback is not live proof.",
      highest_available_truth_level: "reconciled_live_proof",
      highest_available_source: "local_snapshot",
      highest_available_label: "Reconciled live truth",
      truth_labels: labels,
    },
  },
};

const liveLoop: LoopHealthEntry = {
  id: "source_ingestion",
  loop_id: "source_ingestion",
  name: "Source Ingestion",
  current_maturity: "scheduled",
  target_maturity: "reconciled",
  controller_health: { status: "healthy", source: "service_store" },
  last_success: { at: "2026-06-27T06:20:00Z" },
  last_failure: null,
  evidence_packet: {
    id: "packet-source-live",
    packet_id: "packet-source-live",
    loop_id: "source_ingestion",
    source: "service_store",
    registry_ref: "docs/deployment/loop-catalog.registry.json",
    current_maturity: "scheduled",
    target_maturity: "reconciled",
    highest_truth_level: "reconciled_live_proof",
    highest_truth_rank: 3,
    accepted_live_liveness: true,
    can_claim_reconciled: true,
    can_claim_proven_live: false,
    refs: ["docs/deployment/evidence/source-live.json"],
    truth_sources: [
      truthSource("seed_fixture", "missing", "missing"),
      truthSource("snapshot_fallback", "missing", "missing"),
      truthSource("registry_metadata", "present", "bff_local_registry"),
      truthSource("scheduled_tick", "present", "service_store"),
      truthSource("reconciled_live_proof", "present", "service_store"),
    ],
    operator_truth: {
      truth_level: "reconciled_live_proof",
      truth_bucket: "live_truth",
      source_type: "live_truth",
      source: "service_store",
      rank: 3,
      status: "present",
      label: "Reconciled live truth",
      accepted_as_live: true,
      is_live_truth: true,
      degraded: false,
      degraded_reason: null,
      highest_available_truth_level: "reconciled_live_proof",
      highest_available_source: "service_store",
      highest_available_label: "Reconciled live truth",
      truth_labels: labels,
    },
  },
};

const envelope: ListEnvelope<LoopHealthEntry> = {
  items: [snapshotLoop, liveLoop],
  cursor: {},
  pageSize: 2,
  totalCountExact: true,
  estimatedTotal: 2,
  meta: {
    surfaces: {
      loop_health: {
        status: "degraded",
        source: "bff_composed",
        truth_level: "partial_controller_snapshot",
      },
    },
    truth_labels: labels,
  },
};

describe("LoopTruthPanel", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("labels seed, snapshot, registry, scheduled, and live truth separately", async () => {
    const listSpy = vi.spyOn(managementClient.loopHealth, "list").mockResolvedValue(envelope);

    render(<LoopTruthPanel />);

    await waitFor(() => expect(listSpy).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId("truth-label-seed_fixture").textContent).toContain("Seed / fixture");
    expect(screen.getByTestId("truth-label-snapshot_fallback").textContent).toContain("Snapshot fallback");
    expect(screen.getByTestId("truth-label-registry_metadata").textContent).toContain("Registry metadata");
    expect(screen.getByTestId("truth-label-scheduled_tick").textContent).toContain("Scheduled tick");
    expect(screen.getByTestId("truth-label-reconciled_live_proof").textContent).toContain("Reconciled live truth");
    expect(screen.getByText("Source: bff_composed")).toBeTruthy();
    expect(screen.getByText("Degraded: 1")).toBeTruthy();

    const snapshotRow = screen.getByTestId("loop-truth-row-bff_health_monitoring");
    expect(within(snapshotRow).getByTestId("operator-truth-bff_health_monitoring").textContent).toContain("Snapshot fallback");
    expect(within(snapshotRow).getByTestId("live-proof-bff_health_monitoring").textContent).toContain("No live proof");
    expect(within(snapshotRow).getByTestId("degraded-reason-bff_health_monitoring").textContent).toContain(
      "Local snapshot fallback is not live proof.",
    );
    expect(within(snapshotRow).getByText("Reconciled live truth: present")).toBeTruthy();

    const liveRow = screen.getByTestId("loop-truth-row-source_ingestion");
    expect(within(liveRow).getByTestId("operator-truth-source_ingestion").textContent).toContain("Reconciled live truth");
    expect(within(liveRow).getByTestId("live-proof-source_ingestion").textContent).toContain("Live proof");
    expect(within(liveRow).queryByTestId("degraded-reason-source_ingestion")).toBeNull();
  });
});
