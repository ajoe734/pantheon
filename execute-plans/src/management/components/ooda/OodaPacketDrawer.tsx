import { useCallback, useEffect, useMemo, useState, type RefObject } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  Clock3,
  FileText,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { managementClient } from "@/lib/bff/client";
import {
  collectOodaAuditRefs,
  deriveOodaStageRows,
  oodaCapitalSafetyState,
  oodaPacketDisplayName,
  oodaSourceState,
  type OodaCapitalSafetyState,
  type OodaEvidenceRef,
  type OodaLoopPacket,
  type OodaPacketDetail,
  type OodaPacketMeta,
  type OodaPacketSurfaceState,
  type OodaStageRow,
} from "@/lib/ooda/packets";
import { cn } from "@/lib/utils";

export interface OodaPacketDrawerProps {
  open: boolean;
  packetId?: string | null;
  packet?: OodaLoopPacket | null;
  meta?: OodaPacketMeta;
  onOpenChange: (open: boolean) => void;
  triggerRef?: RefObject<HTMLElement>;
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
  degraded: "bg-status-warning/15 text-status-warning border-status-warning/30",
};

const stageTone: Record<OodaStageRow["status"], string> = {
  complete: "bg-status-success/15 text-status-success border-status-success/30",
  current: "bg-status-running/15 text-status-running border-status-running/30",
  missing: "bg-status-failed/15 text-status-failed border-status-failed/30",
  pending: "bg-muted text-muted-foreground border-border",
};

const stageIcon: Record<OodaStageRow["status"], typeof CheckCircle2> = {
  complete: CheckCircle2,
  current: Clock3,
  missing: AlertTriangle,
  pending: Circle,
};

function labelFrom(value: unknown, fallback = "unknown"): string {
  const text = String(value ?? "").trim();
  if (!text) return fallback;
  return text.replace(/_/g, " ");
}

function formatTime(value: unknown): string {
  const text = String(value ?? "").trim();
  if (!text) return "-";
  const parsed = new Date(text);
  return Number.isNaN(parsed.getTime()) ? text : parsed.toLocaleString();
}

function fieldValue(value: unknown): string {
  if (Array.isArray(value)) return value.map((item) => String(item)).filter(Boolean).join(", ") || "-";
  const text = String(value ?? "").trim();
  return text || "-";
}

export function OodaPacketDrawer({
  open,
  packetId,
  packet,
  meta,
  onOpenChange,
  triggerRef,
}: OodaPacketDrawerProps) {
  const [loaded, setLoaded] = useState<OodaPacketDetail | undefined>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | undefined>();
  const effective = packet ? { packet, meta } : loaded;

  const load = useCallback(async () => {
    const id = String(packetId ?? "").trim();
    if (packet || !id) {
      setLoaded(undefined);
      setError(undefined);
      return;
    }
    setLoading(true);
    setError(undefined);
    try {
      const detail = await managementClient.oodaPackets.get(id);
      if (!detail) {
        setLoaded(undefined);
        setError("OODA packet unavailable");
      } else {
        setLoaded(detail);
      }
    } catch (err) {
      setLoaded(undefined);
      setError(err instanceof Error ? err.message : "OODA packet unavailable");
    } finally {
      setLoading(false);
    }
  }, [packet, packetId]);

  useEffect(() => {
    if (!open) return;
    void load();
  }, [load, open]);

  const rows = useMemo(
    () => (effective?.packet ? deriveOodaStageRows(effective.packet) : []),
    [effective?.packet],
  );
  const auditRefs = useMemo(
    () => (effective?.packet ? collectOodaAuditRefs(effective.packet) : []),
    [effective?.packet],
  );

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-full sm:max-w-2xl overflow-y-auto"
        aria-label="OODA packet drawer"
        onCloseAutoFocus={(event) => {
          const el = triggerRef?.current;
          if (el?.isConnected) {
            event.preventDefault();
            el.focus();
          }
        }}
      >
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2 text-base">
            <FileText className="h-4 w-4" />
            OODA Packet
          </SheetTitle>
          <SheetDescription>
            {effective?.packet
              ? `${effective.packet.packet_id} - ${oodaPacketDisplayName(effective.packet)}`
              : packetId || "No packet selected"}
          </SheetDescription>
        </SheetHeader>

        {loading && <LoadingState />}

        {!loading && error && (
          <EmptyState
            icon={<AlertTriangle className="h-8 w-8" />}
            title="Packet unavailable"
            description={error}
            cta={packetId ? { label: "Retry", onClick: load } : undefined}
          />
        )}

        {!loading && !error && !effective?.packet && (
          <EmptyState
            icon={<FileText className="h-8 w-8" />}
            title="No OODA packet"
            description="Select an OODA packet to inspect its replay evidence."
          />
        )}

        {!loading && !error && effective?.packet && (
          <div className="mt-5 space-y-5 text-sm" data-testid="ooda-packet-drawer-body">
            <PacketHeader packet={effective.packet} meta={effective.meta} />
            <StageTimeline rows={rows} />
            <PacketLinks packet={effective.packet} />
            <EvidenceSections rows={rows} auditRefs={auditRefs} />
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

function LoadingState() {
  return (
    <div className="mt-6 flex items-center gap-2 text-sm text-muted-foreground" role="status">
      <RefreshCw className="h-4 w-4 animate-spin" />
      Loading OODA packet
    </div>
  );
}

const capitalSafetyTone: Record<OodaCapitalSafetyState, string> = {
  no_side_effects: "bg-status-success/15 text-status-success border-status-success/30",
  live_asserted: "bg-status-warning/15 text-status-warning border-status-warning/30",
  non_live_unsafe: "bg-status-failed/15 text-status-failed border-status-failed/30",
};

const capitalSafetyLabel: Record<OodaCapitalSafetyState, string> = {
  no_side_effects: "no live capital side effects",
  live_asserted: "live capital side effects",
  non_live_unsafe: "live side effects: non-live env",
};

function PacketHeader({ packet, meta }: { packet: OodaLoopPacket; meta?: OodaPacketMeta }) {
  const status = String(packet.status ?? "open").toLowerCase();
  const source = oodaSourceState(meta);
  const safetyState = oodaCapitalSafetyState(packet);
  return (
    <section className="rounded-md border border-border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline" className={statusTone[status] ?? ""}>{labelFrom(status)}</Badge>
        <Badge variant="outline">{labelFrom(packet.loop_type, "loop")}</Badge>
        <Badge variant="outline">{labelFrom(packet.environment, "environment")}</Badge>
        <SourceStatusBadge source={source} />
        <Badge
          variant="outline"
          className={capitalSafetyTone[safetyState]}
          data-safety={safetyState}
        >
          {safetyState === "no_side_effects" ? (
            <ShieldCheck className="mr-1 h-3 w-3" />
          ) : (
            <ShieldAlert className="mr-1 h-3 w-3" />
          )}
          {capitalSafetyLabel[safetyState]}
        </Badge>
      </div>

      <dl className="mt-3 grid gap-3 sm:grid-cols-2">
        <Field label="Packet ID" value={packet.packet_id} mono />
        <Field label="Updated" value={formatTime(packet.updated_at)} />
        <Field label="Created" value={formatTime(packet.created_at)} />
        <Field label="Closed" value={formatTime(packet.closed_at)} />
      </dl>
    </section>
  );
}

function SourceStatusBadge({ source }: { source: OodaPacketSurfaceState }) {
  const status = String(source.status ?? "unknown").toLowerCase();
  const tone =
    status === "ok"
      ? "bg-status-success/15 text-status-success border-status-success/30"
      : status === "degraded" || status === "unavailable" || status === "fail_closed"
        ? "bg-status-warning/15 text-status-warning border-status-warning/30"
        : "bg-muted text-muted-foreground border-border";
  return (
    <Badge variant="outline" className={tone}>
      {labelFrom(source.source, "source")}:{labelFrom(status)}
    </Badge>
  );
}

function StageTimeline({ rows }: { rows: OodaStageRow[] }) {
  return (
    <section>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Replay stages
      </h3>
      <ol className="mt-2 grid gap-2 sm:grid-cols-5">
        {rows.map((row) => {
          const Icon = stageIcon[row.status];
          return (
            <li key={row.key} className="rounded-md border border-border p-2">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-medium">{row.label}</span>
                <Icon className="h-3.5 w-3.5 text-muted-foreground" />
              </div>
              <Badge variant="outline" className={cn("mt-2 text-[10px]", stageTone[row.status])}>
                {row.status}
              </Badge>
              <div className="mt-1 text-[10px] text-muted-foreground">
                {row.evidenceRefs.length} refs
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function PacketLinks({ packet }: { packet: OodaLoopPacket }) {
  const fields = [
    ["Strategy", packet.strategy_id ?? packet.strategy_ids],
    ["Runtime", packet.runtime_id ?? (packet.act as Record<string, unknown> | undefined)?.runtime_binding_id],
    ["Capital pool", packet.capital_pool_id],
    ["Personas", packet.persona_ids],
    ["Evolution program", packet.evolution_program_id ?? (packet.learn as Record<string, unknown> | undefined)?.evolution_program_id],
  ] as const;
  return (
    <section className="rounded-md border border-border p-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Linked objects
      </h3>
      <dl className="mt-3 grid gap-3 sm:grid-cols-2">
        {fields.map(([label, value]) => (
          <Field key={label} label={label} value={fieldValue(value)} mono={Array.isArray(value) || String(value ?? "").includes("-")} />
        ))}
      </dl>
    </section>
  );
}

function EvidenceSections({
  rows,
  auditRefs,
}: {
  rows: OodaStageRow[];
  auditRefs: OodaEvidenceRef[];
}) {
  return (
    <section>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Evidence references
      </h3>
      <ScrollArea className="mt-2 max-h-[42vh] pr-3">
        <div className="space-y-2">
          {rows.map((row) => (
            <EvidenceGroup key={row.key} title={row.label} refs={row.evidenceRefs} status={row.status} />
          ))}
          {auditRefs.length > 0 && (
            <EvidenceGroup title="Audit" refs={auditRefs} status="complete" />
          )}
        </div>
      </ScrollArea>
    </section>
  );
}

function EvidenceGroup({
  title,
  refs,
  status,
}: {
  title: string;
  refs: OodaEvidenceRef[];
  status: OodaStageRow["status"];
}) {
  return (
    <div className="rounded-md border border-border p-3">
      <div className="flex items-center justify-between gap-3">
        <h4 className="text-xs font-medium">{title}</h4>
        <Badge variant="outline" className={cn("text-[10px]", stageTone[status])}>{status}</Badge>
      </div>
      {refs.length === 0 ? (
        <p className="mt-2 text-xs text-muted-foreground">missing evidence</p>
      ) : (
        <ul className="mt-2 space-y-1.5">
          {refs.map((ref, index) => (
            <li key={`${ref.stage}-${ref.field}-${ref.value}-${index}`} className="text-xs">
              <span className="text-muted-foreground">{labelFrom(ref.field)}: </span>
              <code className="break-all rounded bg-muted px-1 py-0.5 text-[11px]">{ref.value}</code>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Field({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className={cn("mt-0.5 break-words text-xs", mono && "font-mono")}>{value}</dd>
    </div>
  );
}
