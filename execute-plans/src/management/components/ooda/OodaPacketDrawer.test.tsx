import { cleanup, render, screen, waitFor } from "@testing-library/react";
import type { ComponentProps } from "react";
import { I18nextProvider } from "react-i18next";
import { afterEach, describe, expect, it, vi } from "vitest";

import i18n from "@/i18n";
import { managementClient } from "@/lib/bff/client";
import type { OodaLoopPacket, OodaPacketMeta } from "@/lib/ooda/packets";
import { OodaPacketDrawer } from "./OodaPacketDrawer";

void i18n.changeLanguage("en-US");

const meta: OodaPacketMeta = {
  snapshot_at: "2026-05-15T16:00:00Z",
  surfaces: {
    ooda_packet_detail: {
      status: "ok",
      source: "service_store",
    },
  },
};

const completePacket: OodaLoopPacket = {
  packet_id: "ooda-paper-001",
  loop_type: "paper_strategy",
  status: "closed",
  environment: "paper",
  capital_pool_id: "pool-paper-001",
  strategy_id: "strategy-rs-003",
  persona_ids: ["persona-alpha", "persona-risk"],
  observe: {
    source_refs: ["source://search/rs-003"],
    telemetry_refs: ["telemetry://paper/heartbeat-001"],
  },
  orient: {
    regime_state_ref: "regime://tw-market/range-bound",
    evidence_bundle_refs: ["evidence://orientation/001"],
  },
  decide: {
    approval_decision_id: "approval-paper-001",
    deployment_plan_id: "deployment-plan-paper-001",
    policy_decision_refs: ["policy-decision://paper-gate/001"],
  },
  act: {
    runtime_binding_id: "runtime-binding-paper-001",
    command_receipt_refs: ["command-receipt://deploy/001"],
    broker_evidence_refs: ["broker-evidence://sandbox/readback-001"],
    live_capital_side_effects: false,
  },
  learn: {
    telemetry_refs: ["telemetry://paper/post-action-001"],
    evolution_followthrough_refs: ["evolution-followthrough://review/001"],
    observation_window: {
      start_at: "2026-05-15T15:00:00Z",
      end_at: "2026-05-15T16:00:00Z",
    },
  },
  audit_refs: ["audit://ooda-paper-001"],
  created_at: "2026-05-15T14:00:00Z",
  updated_at: "2026-05-15T16:00:00Z",
  closed_at: "2026-05-15T16:00:00Z",
};

function renderDrawer(props: Partial<ComponentProps<typeof OodaPacketDrawer>> = {}) {
  return render(
    <I18nextProvider i18n={i18n}>
      <OodaPacketDrawer
        open
        packet={completePacket}
        meta={meta}
        onOpenChange={() => {}}
        {...props}
      />
    </I18nextProvider>,
  );
}

describe("OodaPacketDrawer", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders the packet replay header, stages, and evidence refs", () => {
    renderDrawer();

    expect(screen.getByRole("dialog", { name: /OODA Packet/i })).toBeInTheDocument();
    expect(screen.getByText("ooda-paper-001 - strategy-rs-003")).toBeInTheDocument();
    expect(screen.getByText("paper strategy")).toBeInTheDocument();
    expect(screen.getByText("no live capital side effects")).toBeInTheDocument();
    expect(screen.getByText("service store:ok")).toBeInTheDocument();

    for (const label of ["Observe", "Orient", "Decide", "Act", "Learn"]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(screen.getAllByText("complete").length).toBeGreaterThanOrEqual(5);
    expect(screen.getByText("source://search/rs-003")).toBeInTheDocument();
    expect(screen.getAllByText("runtime-binding-paper-001").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("audit://ooda-paper-001")).toBeInTheDocument();
  });

  it("surfaces reached stages with missing evidence", () => {
    renderDrawer({
      packet: {
        packet_id: "ooda-empty-closed",
        loop_type: "paper_strategy",
        status: "closed",
        environment: "paper",
        observe: {},
        orient: {},
        decide: {},
        act: { live_capital_side_effects: false },
        learn: {},
      },
    });

    expect(screen.getAllByText("missing").length).toBeGreaterThanOrEqual(5);
    expect(screen.getAllByText("missing evidence").length).toBeGreaterThanOrEqual(5);
  });

  it("loads a packet from the management OODA client when only packetId is provided", async () => {
    const getSpy = vi
      .spyOn(managementClient.oodaPackets, "get")
      .mockResolvedValue({ packet: completePacket, meta });

    renderDrawer({
      packet: null,
      packetId: "ooda-paper-001",
    });

    expect(screen.getByRole("status")).toHaveTextContent("Loading OODA packet");
    await screen.findByText("ooda-paper-001 - strategy-rs-003");

    await waitFor(() => {
      expect(getSpy).toHaveBeenCalledWith("ooda-paper-001");
    });
  });

  it("shows live capital side effects badge (warning) for live-env packet with live_capital_side_effects=true", () => {
    renderDrawer({
      packet: {
        ...completePacket,
        packet_id: "ooda-live-001",
        environment: "live",
        act: { ...completePacket.act, live_capital_side_effects: true },
      },
    });

    expect(screen.getByText("live capital side effects")).toBeInTheDocument();
    expect(screen.queryByText("no live capital side effects")).not.toBeInTheDocument();
    expect(screen.queryByText("live side effects: non-live env")).not.toBeInTheDocument();
  });

  it("shows unsafe badge for non-live-env packet with live_capital_side_effects=true", () => {
    renderDrawer({
      packet: {
        ...completePacket,
        packet_id: "ooda-paper-unsafe-001",
        environment: "paper",
        act: { ...completePacket.act, live_capital_side_effects: true },
      },
    });

    expect(screen.getByText("live side effects: non-live env")).toBeInTheDocument();
    expect(screen.queryByText("no live capital side effects")).not.toBeInTheDocument();
    expect(screen.queryByText("live capital side effects")).not.toBeInTheDocument();
  });
});
