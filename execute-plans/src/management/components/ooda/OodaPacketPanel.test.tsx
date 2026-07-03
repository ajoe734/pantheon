import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { managementClient } from "@/lib/bff/client";
import type { ListEnvelope } from "@/lib/bff-v1";
import type { OodaLoopPacket, OodaPacketMeta } from "@/lib/ooda/packets";

import { OodaPacketPanel } from "./OodaPacketPanel";

const meta: OodaPacketMeta = {
  snapshot_at: "2026-05-15T16:00:00Z",
  surfaces: {
    ooda_packets: {
      status: "ok",
      source: "service_store",
    },
  },
};

const closedPacket: OodaLoopPacket = {
  packet_id: "ooda-paper-001",
  loop_type: "paper_strategy",
  status: "closed",
  environment: "paper",
  strategy_id: "strategy-rs-003",
  observe: { source_refs: ["source://search/rs-003"] },
  orient: { evidence_bundle_refs: ["evidence://orientation/001"] },
  decide: { approval_decision_id: "approval-paper-001" },
  act: { runtime_binding_id: "runtime-binding-paper-001", live_capital_side_effects: false },
  learn: { telemetry_refs: ["telemetry://paper/post-action-001"] },
  audit_refs: ["audit://ooda-paper-001"],
};

const unsafePacket: OodaLoopPacket = {
  packet_id: "ooda-paper-unsafe-001",
  loop_type: "paper_strategy",
  status: "acted",
  environment: "paper",
  strategy_id: "strategy-unsafe-001",
  observe: {},
  orient: {},
  decide: {},
  act: { live_capital_side_effects: true },
};

const envelope: ListEnvelope<OodaLoopPacket> = {
  items: [closedPacket, unsafePacket],
  cursor: {},
  pageSize: 2,
  totalCountExact: true,
  estimatedTotal: 2,
  meta,
};

const emptyEnvelope: ListEnvelope<OodaLoopPacket> = {
  items: [],
  cursor: {},
  pageSize: 0,
  totalCountExact: true,
  estimatedTotal: 0,
  meta,
};

describe("OodaPacketPanel", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    window.history.pushState({}, "", "/");
  });

  it("lists OODA packets and opens the replay drawer from a row", async () => {
    const listSpy = vi.spyOn(managementClient.oodaPackets, "list").mockResolvedValue(envelope);

    render(<OodaPacketPanel />);

    await waitFor(() => expect(listSpy).toHaveBeenCalledWith({ page_size: 25 }));
    await screen.findByText("OODA Packets");
    expect(screen.getByText("Source: service store")).toBeTruthy();
    expect(screen.getByText("Packets: 2")).toBeTruthy();
    expect(screen.getByText("Missing evidence: 1")).toBeTruthy();
    expect(screen.getByText("Unsafe side effects: 1")).toBeTruthy();

    const row = screen.getByTestId("ooda-packet-row-ooda-paper-001");
    expect(within(row).getByText("strategy-rs-003")).toBeTruthy();
    expect(within(row).getByText("Complete: 5/5")).toBeTruthy();

    fireEvent.click(within(row).getByRole("button", { name: /Open packet/i }));

    expect(screen.getByRole("dialog", { name: /OODA packet/i })).toBeTruthy();
    expect(screen.getByText("ooda-paper-001 - strategy-rs-003")).toBeTruthy();
    expect(screen.getByText("audit://ooda-paper-001")).toBeTruthy();
  });

  it("opens a packet directly from the management URL query", async () => {
    window.history.pushState({}, "", "/management/ooda?packet=ooda-query-001");
    vi.spyOn(managementClient.oodaPackets, "list").mockResolvedValue(emptyEnvelope);
    const getSpy = vi
      .spyOn(managementClient.oodaPackets, "get")
      .mockResolvedValue({
        packet: {
          ...closedPacket,
          packet_id: "ooda-query-001",
          strategy_id: "strategy-query-001",
        },
        meta,
      });

    render(<OodaPacketPanel />);

    await waitFor(() => expect(getSpy).toHaveBeenCalledWith("ooda-query-001"));
    await screen.findByText("ooda-query-001 - strategy-query-001");
  });
});
