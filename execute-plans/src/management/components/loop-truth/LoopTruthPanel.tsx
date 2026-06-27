import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Database,
  FileStack,
  RefreshCw,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { managementClient } from "@/lib/bff/client";
import type {
  LoopHealthEntry,
  LoopOperatorTruth,
  LoopTruthLabel,
  LoopTruthSource,
} from "@/lib/bff/types";
import { cn } from "@/lib/utils";

type LoadState = "loading" | "ready" | "error";

const truthTone: Record<string, string> = {
  seed_fixture: "bg-muted text-muted-foreground border-border",
  snapshot: "bg-status-warning/15 text-status-warning border-status-warning/30",
  registry: "bg-primary/10 text-primary border-primary/30",
  scheduled: "bg-accent/15 text-accent border-accent/30",
  live_truth: "bg-status-success/15 text-status-success border-status-success/30",
};

const fallbackTruthLabels: LoopTruthLabel[] = [
  {
    truth_level: "seed_fixture",
    truth_bucket: "seed_fixture",
    source_type: "seed_fixture",
    rank: 0,
    label: "Seed / fixture",
    accepted_as_live: false,
  },
  {
    truth_level: "snapshot_fallback",
    truth_bucket: "snapshot",
    source_type: "snapshot",
    rank: 0,
    label: "Snapshot fallback",
    accepted_as_live: false,
  },
  {
    truth_level: "registry_metadata",
    truth_bucket: "registry",
    source_type: "registry",
    rank: 1,
    label: "Registry metadata",
    accepted_as_live: false,
  },
  {
    truth_level: "scheduled_tick",
    truth_bucket: "scheduled",
    source_type: "scheduled",
    rank: 2,
    label: "Scheduled tick",
    accepted_as_live: false,
  },
  {
    truth_level: "reconciled_live_proof",
    truth_bucket: "live_truth",
    source_type: "live_truth",
    rank: 3,
    label: "Live truth",
    accepted_as_live: true,
  },
];

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function textFrom(value: unknown, fallback = "-"): string {
  const text = String(value ?? "").trim();
  return text || fallback;
}

function labelFrom(value: unknown, fallback = "unknown"): string {
  return textFrom(value, fallback).replace(/_/g, " ");
}

function formatTime(value: unknown): string {
  const text = String(value ?? "").trim();
  if (!text) return "-";
  const parsed = new Date(text);
  return Number.isNaN(parsed.getTime()) ? text : parsed.toLocaleString();
}

function truthLabels(meta?: Record<string, unknown>): LoopTruthLabel[] {
  const labels = asRecord(meta?.truth_labels);
  const parsed = Object.values(labels)
    .filter((item): item is LoopTruthLabel => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return false;
      const record = item as Record<string, unknown>;
      return typeof record.truth_level === "string" && typeof record.label === "string";
    })
    .sort((a, b) => a.rank - b.rank);
  return parsed.length > 0 ? parsed : fallbackTruthLabels;
}

function operatorTruth(entry: LoopHealthEntry): LoopOperatorTruth {
  return entry.evidence_packet?.operator_truth ?? {
    truth_level: "missing",
    truth_bucket: "missing",
    source_type: "missing",
    label: "Missing truth",
    accepted_as_live: false,
    is_live_truth: false,
    degraded: true,
    degraded_reason: "No operator truth was returned.",
  };
}

function controllerStatus(entry: LoopHealthEntry): string {
  const health = asRecord(entry.controller_health);
  return labelFrom(health.status, "unknown");
}

function eventSummary(value: unknown): string {
  const event = asRecord(value);
  return formatTime(event.at ?? event.timestamp ?? event.captured_at);
}

function surfaceSummary(meta?: Record<string, unknown>): { status: string; source: string; truthLevel: string } {
  const surfaces = asRecord(meta?.surfaces);
  const loopHealth = asRecord(surfaces.loop_health);
  return {
    status: labelFrom(loopHealth.status, "unknown"),
    source: textFrom(loopHealth.source, "unknown"),
    truthLevel: labelFrom(loopHealth.truth_level, "unknown"),
  };
}

function TruthBadge({
  label,
  sourceType,
  live,
  testId,
}: {
  label: string;
  sourceType?: string;
  live?: boolean;
  testId?: string;
}) {
  const tone = truthTone[sourceType ?? ""] ?? "bg-muted text-muted-foreground border-border";
  return (
    <Badge
      variant="outline"
      className={cn("gap-1 whitespace-nowrap", tone)}
      data-testid={testId}
      data-live-proof={live ? "true" : "false"}
    >
      {live ? <CheckCircle2 className="h-3 w-3" /> : <AlertTriangle className="h-3 w-3" />}
      {label}
    </Badge>
  );
}

function TruthLegend({ labels }: { labels: LoopTruthLabel[] }) {
  return (
    <div className="flex flex-wrap gap-2" data-testid="loop-truth-legend">
      {labels.map((label) => (
        <TruthBadge
          key={label.truth_level}
          label={label.label}
          sourceType={label.source_type}
          live={label.accepted_as_live}
          testId={`truth-label-${label.truth_level}`}
        />
      ))}
    </div>
  );
}

function TruthLadder({ sources }: { sources: LoopTruthSource[] }) {
  return (
    <div className="flex flex-wrap gap-1" data-testid="truth-source-ladder">
      {sources.map((source) => (
        <Badge
          key={source.truth_level}
          variant="outline"
          className={cn(
            "whitespace-nowrap text-[11px] font-medium",
            source.status === "present"
              ? truthTone[source.source_type] ?? ""
              : "bg-muted text-muted-foreground border-border",
          )}
          data-truth-level={source.truth_level}
          data-truth-status={source.status}
        >
          {source.label}: {labelFrom(source.status)}
        </Badge>
      ))}
    </div>
  );
}

function LoopRow({ entry }: { entry: LoopHealthEntry }) {
  const truth = operatorTruth(entry);
  const packet = entry.evidence_packet;
  const liveLabel = truth.accepted_as_live ? "Live proof" : "No live proof";
  const rowLabel = entry.name ?? entry.loop_id;
  return (
    <article
      className="rounded-md border border-border bg-background p-3"
      data-testid={`loop-truth-row-${entry.loop_id}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold leading-5">{rowLabel}</h3>
            <Badge variant="outline" className="font-mono text-[11px]">
              {entry.loop_id}
            </Badge>
          </div>
          <div className="mt-1 text-xs text-muted-foreground">
            {labelFrom(entry.current_maturity)} to {labelFrom(entry.target_maturity)}
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <TruthBadge
            label={truth.label}
            sourceType={truth.source_type}
            live={truth.accepted_as_live}
            testId={`operator-truth-${entry.loop_id}`}
          />
          <Badge
            variant="outline"
            className={cn(
              "gap-1 whitespace-nowrap",
              truth.accepted_as_live
                ? "bg-status-success/15 text-status-success border-status-success/30"
                : "bg-status-warning/15 text-status-warning border-status-warning/30",
            )}
            data-testid={`live-proof-${entry.loop_id}`}
          >
            {truth.accepted_as_live ? <CheckCircle2 className="h-3 w-3" /> : <AlertTriangle className="h-3 w-3" />}
            {liveLabel}
          </Badge>
        </div>
      </div>

      <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-4">
        <div>
          <dt className="text-muted-foreground">Source</dt>
          <dd className="font-medium">{textFrom(truth.source)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Controller</dt>
          <dd className="font-medium">{controllerStatus(entry)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Last success</dt>
          <dd className="font-medium">{eventSummary(entry.last_success)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Evidence refs</dt>
          <dd className="font-medium">{packet?.refs?.length ?? 0}</dd>
        </div>
      </dl>

      {truth.degraded && truth.degraded_reason ? (
        <div
          className="mt-3 flex items-start gap-2 rounded-md border border-status-warning/30 bg-status-warning/10 p-2 text-xs text-status-warning"
          data-testid={`degraded-reason-${entry.loop_id}`}
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-none" />
          <span>{truth.degraded_reason}</span>
        </div>
      ) : null}

      {packet?.truth_sources?.length ? (
        <div className="mt-3">
          <TruthLadder sources={packet.truth_sources} />
        </div>
      ) : null}
    </article>
  );
}

export function LoopTruthPanel() {
  const [state, setState] = useState<LoadState>("loading");
  const [items, setItems] = useState<LoopHealthEntry[]>([]);
  const [meta, setMeta] = useState<Record<string, unknown> | undefined>();
  const [error, setError] = useState<string | undefined>();

  async function load() {
    setState("loading");
    setError(undefined);
    try {
      const response = await managementClient.loopHealth.list();
      setItems(response.items);
      setMeta(response.meta);
      setState("ready");
    } catch (err) {
      setItems([]);
      setMeta(undefined);
      setError(err instanceof Error ? err.message : "Loop health unavailable");
      setState("error");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const labels = useMemo(() => truthLabels(meta), [meta]);
  const surface = surfaceSummary(meta);
  const liveProofCount = items.filter((item) => operatorTruth(item).accepted_as_live).length;
  const degradedCount = items.length - liveProofCount;

  return (
    <main className="min-h-screen bg-background p-4 text-foreground" data-testid="loop-truth-panel">
      <section className="mx-auto flex max-w-6xl flex-col gap-4">
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-4">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <Database className="h-4 w-4 text-primary" />
              <h1 className="text-base font-semibold">Loop Truth</h1>
              <Badge variant="outline" className="capitalize">
                {surface.status}
              </Badge>
            </div>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <span>Source: {surface.source}</span>
              <span>Truth: {surface.truthLevel}</span>
              <span>Loops: {items.length}</span>
              <span>Live proof: {liveProofCount}</span>
              <span>Degraded: {degradedCount}</span>
            </div>
          </div>
          <button
            type="button"
            onClick={() => void load()}
            className="inline-flex h-8 items-center gap-2 rounded-md border border-border px-3 text-xs font-medium hover:bg-muted"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", state === "loading" ? "animate-spin" : "")} />
            Refresh
          </button>
        </header>

        <TruthLegend labels={labels} />

        {state === "loading" ? (
          <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground" role="status">
            <RefreshCw className="h-4 w-4 animate-spin" />
            Loading loop truth
          </div>
        ) : null}

        {state === "error" ? (
          <EmptyState
            icon={<AlertTriangle className="h-8 w-8" />}
            title="Loop health unavailable"
            description={error}
            cta={{ label: "Retry", onClick: load }}
          />
        ) : null}

        {state === "ready" && items.length === 0 ? (
          <EmptyState
            icon={<FileStack className="h-8 w-8" />}
            title="No loop health records"
            description="The BFF returned no loop health entries."
            cta={{ label: "Refresh", onClick: load }}
          />
        ) : null}

        {state === "ready" && items.length > 0 ? (
          <div className="grid gap-3">
            {items.map((entry) => (
              <LoopRow key={entry.loop_id} entry={entry} />
            ))}
          </div>
        ) : null}

      </section>
    </main>
  );
}
