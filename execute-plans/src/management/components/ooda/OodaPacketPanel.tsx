import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  FileText,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { managementClient } from "@/lib/bff/client";
import {
  deriveOodaStageRows,
  oodaCapitalSafetyState,
  oodaPacketDisplayName,
  oodaSourceState,
  type OodaLoopPacket,
  type OodaPacketMeta,
} from "@/lib/ooda/packets";
import { cn } from "@/lib/utils";

import { OodaPacketDrawer } from "./OodaPacketDrawer";

type LoadState = "loading" | "ready" | "error";

function labelFrom(value: unknown, fallback = "unknown"): string {
  const text = String(value ?? "").trim();
  if (!text) return fallback;
  return text.replace(/_/g, " ");
}

function surfaceMeta(meta: Record<string, unknown> | undefined): OodaPacketMeta | undefined {
  return meta as OodaPacketMeta | undefined;
}

function initialPacketId(): string {
  if (typeof window === "undefined") return "";
  const params = new URLSearchParams(window.location.search);
  return String(params.get("packet") ?? params.get("packet_id") ?? "").trim();
}

function packetIdentity(packet: OodaLoopPacket): string {
  return packet.packet_id || oodaPacketDisplayName(packet);
}

const statusTone: Record<string, string> = {
  open: "bg-muted text-muted-foreground border-border",
  observing: "bg-status-running/15 text-status-running border-status-running/30",
  oriented: "bg-status-running/15 text-status-running border-status-running/30",
  decided: "bg-status-warning/15 text-status-warning border-status-warning/30",
  acted: "bg-accent/15 text-accent border-accent/30",
  evolving: "bg-primary/10 text-primary border-primary/30",
  closed: "bg-status-success/15 text-status-success border-status-success/30",
  failed: "bg-status-failed/15 text-status-failed border-status-failed/30",
};

const safetyTone = {
  no_side_effects: "bg-status-success/15 text-status-success border-status-success/30",
  live_asserted: "bg-status-warning/15 text-status-warning border-status-warning/30",
  non_live_unsafe: "bg-status-failed/15 text-status-failed border-status-failed/30",
};

export function OodaPacketPanel() {
  const [state, setState] = useState<LoadState>("loading");
  const [items, setItems] = useState<OodaLoopPacket[]>([]);
  const [meta, setMeta] = useState<Record<string, unknown> | undefined>();
  const [error, setError] = useState<string | undefined>();
  const [draftPacketId, setDraftPacketId] = useState(initialPacketId);
  const [selectedPacket, setSelectedPacket] = useState<OodaLoopPacket | null>(null);
  const [selectedPacketId, setSelectedPacketId] = useState<string>(initialPacketId);
  const [drawerOpen, setDrawerOpen] = useState(Boolean(initialPacketId()));

  const load = useCallback(async () => {
    setState("loading");
    setError(undefined);
    try {
      const response = await managementClient.oodaPackets.list({ page_size: 25 });
      setItems(response.items);
      setMeta(response.meta);
      setState("ready");
    } catch (err) {
      setItems([]);
      setMeta(undefined);
      setError(err instanceof Error ? err.message : "OODA packets unavailable");
      setState("error");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const oodaMeta = useMemo(() => surfaceMeta(meta), [meta]);
  const surface = oodaSourceState(oodaMeta);
  const unsafeCount = items.filter((packet) => oodaCapitalSafetyState(packet) === "non_live_unsafe").length;
  const packetsWithMissingEvidence = items.filter((packet) =>
    deriveOodaStageRows(packet).some((row) => row.status === "missing"),
  ).length;

  function openPacket(packet: OodaLoopPacket) {
    setSelectedPacket(packet);
    setSelectedPacketId(packet.packet_id);
    setDrawerOpen(true);
  }

  function openPacketId() {
    const id = draftPacketId.trim();
    if (!id) return;
    setSelectedPacket(null);
    setSelectedPacketId(id);
    setDrawerOpen(true);
  }

  return (
    <section className="flex flex-col gap-4" data-testid="ooda-packet-panel">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <FileText className="h-4 w-4 text-primary" />
            <h2 className="text-base font-semibold">OODA Packets</h2>
            <Badge variant="outline" className="capitalize">
              {labelFrom(surface.status)}
            </Badge>
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>Source: {labelFrom(surface.source)}</span>
            <span>Packets: {items.length}</span>
            <span>Missing evidence: {packetsWithMissingEvidence}</span>
            <span>Unsafe side effects: {unsafeCount}</span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="sr-only" htmlFor="ooda-packet-id-input">OODA packet id</label>
          <input
            id="ooda-packet-id-input"
            className="h-8 w-56 rounded-md border border-border bg-background px-2 text-xs outline-none focus:ring-2 focus:ring-ring"
            value={draftPacketId}
            onChange={(event) => setDraftPacketId(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") openPacketId();
            }}
            placeholder="packet id"
          />
          <button
            type="button"
            onClick={openPacketId}
            className="inline-flex h-8 items-center gap-2 rounded-md border border-border px-3 text-xs font-medium hover:bg-muted"
          >
            <FileText className="h-3.5 w-3.5" />
            Open
          </button>
          <button
            type="button"
            onClick={() => void load()}
            className="inline-flex h-8 items-center gap-2 rounded-md border border-border px-3 text-xs font-medium hover:bg-muted"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", state === "loading" ? "animate-spin" : "")} />
            Refresh
          </button>
        </div>
      </header>

      {state === "loading" ? (
        <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground" role="status">
          <RefreshCw className="h-4 w-4 animate-spin" />
          Loading OODA packets
        </div>
      ) : null}

      {state === "error" ? (
        <EmptyState
          icon={<AlertTriangle className="h-8 w-8" />}
          title="OODA packets unavailable"
          description={error}
          cta={{ label: "Retry", onClick: load }}
        />
      ) : null}

      {state === "ready" && items.length === 0 ? (
        <EmptyState
          icon={<FileText className="h-8 w-8" />}
          title="No OODA packets"
          description="The BFF returned no OODA packet records."
          cta={{ label: "Refresh", onClick: load }}
        />
      ) : null}

      {state === "ready" && items.length > 0 ? (
        <div className="grid gap-3">
          {items.map((packet) => (
            <OodaPacketRow key={packetIdentity(packet)} packet={packet} onOpen={openPacket} />
          ))}
        </div>
      ) : null}

      <OodaPacketDrawer
        open={drawerOpen}
        packet={selectedPacket}
        packetId={selectedPacket ? selectedPacket.packet_id : selectedPacketId}
        meta={selectedPacket ? oodaMeta : undefined}
        onOpenChange={setDrawerOpen}
      />
    </section>
  );
}

function OodaPacketRow({
  packet,
  onOpen,
}: {
  packet: OodaLoopPacket;
  onOpen: (packet: OodaLoopPacket) => void;
}) {
  const rows = deriveOodaStageRows(packet);
  const complete = rows.filter((row) => row.status === "complete").length;
  const missing = rows.filter((row) => row.status === "missing").length;
  const current = rows.find((row) => row.status === "current") ?? rows.find((row) => row.status === "missing");
  const status = String(packet.status ?? "open").toLowerCase();
  const safety = oodaCapitalSafetyState(packet);
  const SafetyIcon = safety === "no_side_effects" ? ShieldCheck : ShieldAlert;

  return (
    <article
      className="rounded-md border border-border p-3"
      data-testid={`ooda-packet-row-${packet.packet_id}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs font-semibold">{packet.packet_id}</span>
            <Badge variant="outline" className={statusTone[status] ?? ""}>
              {labelFrom(status)}
            </Badge>
            <Badge variant="outline">{labelFrom(packet.environment, "environment")}</Badge>
            <Badge variant="outline" className={safetyTone[safety]} data-safety={safety}>
              <SafetyIcon className="mr-1 h-3 w-3" />
              {labelFrom(safety)}
            </Badge>
          </div>
          <div className="mt-2 text-sm font-medium">{oodaPacketDisplayName(packet)}</div>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>Stage: {current?.label ?? labelFrom(packet.current_stage ?? packet.stage, "not started")}</span>
            <span>Complete: {complete}/5</span>
            <span>Missing: {missing}</span>
          </div>
        </div>
        <button
          type="button"
          onClick={() => onOpen(packet)}
          className="inline-flex h-8 items-center rounded-md border border-border px-3 text-xs font-medium hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring"
        >
          Open packet
        </button>
      </div>
    </article>
  );
}
