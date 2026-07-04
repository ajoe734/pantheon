import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ClipboardCheck,
  FileSearch,
  Lock,
  RefreshCw,
  ShieldAlert,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import {
  fetchManagementGovernanceLedger,
  fetchManagementHiqBacklog,
  fetchManagementInterventionStream,
  fetchManagementSentinelPulse,
  type ManagementGovernanceLedgerItem,
  type ManagementGovernanceLedgerResponse,
  type ManagementHiqBacklogItem,
  type ManagementHiqBacklogResponse,
  type ManagementInterventionStreamItem,
  type ManagementInterventionStreamResponse,
  type ManagementSentinelPulseFinding,
  type ManagementSentinelPulseIntervention,
  type ManagementSentinelPulseResponse,
  type ManagementSurfaceRef,
} from "@/lib/bff-v1/management";
import { cn } from "@/lib/utils";
import { ManagementDenseTable } from "@/management/components/dense-table";

type LoadState = "loading" | "ready" | "error";

type DecisionSourceKey =
  | "governance"
  | "hiq"
  | "intervention"
  | "sentinel";

export interface DecisionWorkbenchFailure {
  key: DecisionSourceKey;
  label: string;
  message: string;
}

export interface DecisionWorkbenchSurface {
  key: DecisionSourceKey;
  label: string;
  status: string;
  source: string;
  message: string;
}

export interface DecisionWorkbenchRow {
  id: string;
  sourceKey: DecisionSourceKey;
  sourceLabel: string;
  queue: string;
  title: string;
  owner: string;
  severity: string;
  status: string;
  evidence: string;
  nextAction: string;
  target: string;
  updatedAt: string;
}

export interface DecisionWorkbenchModel {
  rows: DecisionWorkbenchRow[];
  surfaces: DecisionWorkbenchSurface[];
  failures: DecisionWorkbenchFailure[];
  degradedReasons: string[];
}

export interface DecisionWorkbenchResponses {
  governanceLedger?: ManagementGovernanceLedgerResponse;
  hiqBacklog?: ManagementHiqBacklogResponse;
  interventionStream?: ManagementInterventionStreamResponse;
  sentinelPulse?: ManagementSentinelPulseResponse;
  failures?: DecisionWorkbenchFailure[];
}

const sourceLabels: Record<DecisionSourceKey, string> = {
  governance: "Governance ledger",
  hiq: "HIQ backlog",
  intervention: "Intervention stream",
  sentinel: "Sentinel pulse",
};

const surfaceKeys: Record<DecisionSourceKey, string[]> = {
  governance: ["management_governance_ledger", "governance_ledger"],
  hiq: ["management_hiq_backlog", "hiq_backlog"],
  intervention: ["management_intervention_stream", "intervention_stream", "v5_interventions"],
  sentinel: ["management_sentinel_pulse", "sentinel_pulse", "sentinel_findings", "v5_interventions"],
};

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

function testIdPart(value: string): string {
  return value.replace(/[^A-Za-z0-9_-]+/g, "-");
}

function compactDate(value: unknown): string {
  const text = String(value ?? "").trim();
  if (!text) return "-";
  const parsed = new Date(text);
  if (Number.isNaN(parsed.getTime())) return text;
  return parsed.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function ownerFrom(...values: unknown[]): string {
  for (const value of values) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      const record = value as Record<string, unknown>;
      const nested = ownerFrom(record.owner, record.operator, record.persona_id, record.runtime_id, record.id);
      if (nested !== "unassigned") return nested;
      continue;
    }
    const text = String(value ?? "").trim();
    if (text) return text;
  }
  return "unassigned";
}

function targetLabel(value: unknown, fallback = "-"): string {
  const target = asRecord(value);
  if (Object.keys(target).length === 0) return fallback;
  const type = textFrom(target.type ?? target.target_type ?? target.kind, "");
  const id = textFrom(target.id ?? target.target_id ?? target.name ?? target.label, "");
  if (type && id) return `${labelFrom(type)} ${id}`;
  return id || type || fallback;
}

function evidenceValue(value: unknown): string {
  if (typeof value === "string") return value;
  const record = asRecord(value);
  return textFrom(
    record.path
      ?? record.href
      ?? record.url
      ?? record.id
      ?? record.ref_id
      ?? record.evidence_id
      ?? record.finding_id
      ?? record.intervention_id
      ?? record.incident_id
      ?? record.loop_run_id
      ?? record.runtime_id,
    "",
  );
}

function evidenceSummary(...values: unknown[]): string {
  const refs: string[] = [];
  for (const value of values) {
    if (Array.isArray(value)) {
      for (const item of value) {
        const ref = evidenceValue(item);
        if (ref) refs.push(ref);
      }
      continue;
    }
    const record = asRecord(value);
    if (Object.keys(record).length > 0) {
      for (const entry of Object.values(record)) {
        const ref = evidenceValue(entry);
        if (ref) refs.push(ref);
      }
      continue;
    }
    const ref = evidenceValue(value);
    if (ref) refs.push(ref);
  }
  const unique = Array.from(new Set(refs.filter(Boolean)));
  if (unique.length === 0) return "No refs";
  if (unique.length === 1) return unique[0];
  return `${unique.length} refs: ${unique.slice(0, 2).join(", ")}`;
}

function surfaceFromResponse(
  key: DecisionSourceKey,
  response?: { meta?: { surfaces?: Record<string, ManagementSurfaceRef | undefined> } },
): DecisionWorkbenchSurface {
  const surfaces = response?.meta?.surfaces ?? {};
  const surface = surfaceKeys[key]
    .map((surfaceKey) => surfaces[surfaceKey])
    .find((item): item is ManagementSurfaceRef => Boolean(item))
    ?? Object.values(surfaces).find((item): item is ManagementSurfaceRef => Boolean(item))
    ?? { status: "unknown", source: "unknown" };
  return {
    key,
    label: sourceLabels[key],
    status: textFrom(surface.status, "unknown").toLowerCase(),
    source: textFrom(surface.source, "unknown"),
    message: textFrom(surface.message ?? surface.note ?? surface.reason, ""),
  };
}

function statusTone(status: string): string {
  const normalized = status.toLowerCase();
  if (["ok", "ready", "closed", "resolved", "approved", "complete", "completed"].includes(normalized)) {
    return "bg-status-success/15 text-status-success border-status-success/30";
  }
  if (["critical", "failed", "blocked", "rejected", "breached"].includes(normalized)) {
    return "bg-status-failed/15 text-status-failed border-status-failed/30";
  }
  if (["degraded", "pending", "open", "active", "watch", "warning", "warn"].includes(normalized)) {
    return "bg-status-warning/15 text-status-warning border-status-warning/30";
  }
  return "bg-muted text-muted-foreground border-border";
}

function severityTone(severity: string): string {
  const normalized = severity.toLowerCase();
  if (["critical", "severe", "blocker"].includes(normalized)) {
    return "bg-status-failed/15 text-status-failed border-status-failed/30";
  }
  if (["high", "elevated"].includes(normalized)) {
    return "bg-status-warning/15 text-status-warning border-status-warning/30";
  }
  if (["medium", "moderate"].includes(normalized)) {
    return "bg-status-running/15 text-status-running border-status-running/30";
  }
  if (["low", "info", "informational"].includes(normalized)) {
    return "bg-status-success/15 text-status-success border-status-success/30";
  }
  return "bg-muted text-muted-foreground border-border";
}

function sourceTone(key: DecisionSourceKey): string {
  if (key === "governance") return "bg-primary/10 text-primary border-primary/30";
  if (key === "hiq") return "bg-accent/15 text-accent border-accent/30";
  if (key === "intervention") return "bg-status-running/15 text-status-running border-status-running/30";
  return "bg-status-warning/15 text-status-warning border-status-warning/30";
}

function severityRank(severity: string): number {
  const normalized = severity.toLowerCase();
  if (["critical", "severe", "blocker"].includes(normalized)) return 0;
  if (["high", "elevated"].includes(normalized)) return 1;
  if (["medium", "moderate"].includes(normalized)) return 2;
  if (["low", "info", "informational"].includes(normalized)) return 3;
  return 4;
}

function activeStatusRank(status: string): number {
  const normalized = status.toLowerCase();
  if (["pending", "open", "active", "blocked", "breached"].includes(normalized)) return 0;
  if (["watch", "degraded", "warning", "warn"].includes(normalized)) return 1;
  if (["ok", "ready", "closed", "resolved", "complete", "completed"].includes(normalized)) return 3;
  return 2;
}

function rawTime(row: DecisionWorkbenchRow): number {
  const parsed = new Date(row.updatedAt).getTime();
  return Number.isNaN(parsed) ? 0 : parsed;
}

function sortRows(rows: DecisionWorkbenchRow[]): DecisionWorkbenchRow[] {
  return [...rows].sort((a, b) => {
    const severityDelta = severityRank(a.severity) - severityRank(b.severity);
    if (severityDelta !== 0) return severityDelta;
    const statusDelta = activeStatusRank(a.status) - activeStatusRank(b.status);
    if (statusDelta !== 0) return statusDelta;
    return rawTime(b) - rawTime(a);
  });
}

function governanceNextAction(item: ManagementGovernanceLedgerItem): string {
  const status = String(item.status ?? item.outcome ?? "").toLowerCase();
  if (status === "pending" || status === "open") return "Confirm ledger evidence";
  if (item.risk_level === "critical" || item.risk_level === "high") return "Review governance risk";
  return "Audit ledger entry";
}

function hiqNextAction(item: ManagementHiqBacklogItem): string {
  const priority = String(item.priority ?? "").toLowerCase();
  const actionState = String(item.action_state ?? item.status ?? "").toLowerCase();
  if (actionState === "pending" || actionState === "open") return "Triage backlog item";
  if (priority === "critical" || priority === "high") return "Escalate owner review";
  return "Track to resolution";
}

function interventionNextAction(item: ManagementInterventionStreamItem): string {
  const status = String(item.status ?? "").toLowerCase();
  if (status === "pending" || status === "open" || status === "active") return "Inspect intervention";
  if (item.risk_level === "critical" || item.risk_level === "high") return "Check operator follow-up";
  return "Monitor intervention";
}

function sentinelFindingNextAction(item: ManagementSentinelPulseFinding): string {
  const status = String(item.status ?? "").toLowerCase();
  if (status === "active" || status === "open" || status === "pending") return "Verify finding evidence";
  if (item.severity === "critical" || item.severity === "high") return "Review linked intervention";
  return "Track sentinel pulse";
}

function sentinelInterventionNextAction(item: ManagementSentinelPulseIntervention): string {
  const status = String(item.status ?? "").toLowerCase();
  if (status === "pending" || status === "open" || status === "active") return "Review sentinel intervention";
  return "Monitor sentinel intervention";
}

function governanceRows(response?: ManagementGovernanceLedgerResponse): DecisionWorkbenchRow[] {
  return (response?.data.items ?? []).map((item) => ({
    id: item.id || item.entry_id || item.ledger_id,
    sourceKey: "governance",
    sourceLabel: sourceLabels.governance,
    queue: labelFrom(item.source_type, "ledger"),
    title: textFrom(item.title, item.entry_id || item.ledger_id),
    owner: ownerFrom(item.actor, item.audit_context, item.target_id),
    severity: textFrom(item.risk_level, "unknown").toLowerCase(),
    status: textFrom(item.status ?? item.outcome, "unknown").toLowerCase(),
    evidence: evidenceSummary(item.evidence_refs, item.links, item.href, item.ledger_id),
    nextAction: governanceNextAction(item),
    target: targetLabel({ type: item.target_type, id: item.target_id }, textFrom(item.target_id, "-")),
    updatedAt: textFrom(item.occurred_at ?? item.created_at, ""),
  }));
}

function hiqRows(response?: ManagementHiqBacklogResponse): DecisionWorkbenchRow[] {
  return (response?.data.items ?? []).map((item) => ({
    id: item.id || item.backlog_id,
    sourceKey: "hiq",
    sourceLabel: sourceLabels.hiq,
    queue: labelFrom(item.kind, "backlog"),
    title: textFrom(item.title, item.backlog_id),
    owner: ownerFrom(item.target, item.triggered_by, item.source_id),
    severity: textFrom(item.severity ?? item.risk_level ?? item.priority, "unknown").toLowerCase(),
    status: textFrom(item.action_state ?? item.status, "unknown").toLowerCase(),
    evidence: evidenceSummary(item.source_refs, item.links, item.correlation_id, item.source_id),
    nextAction: hiqNextAction(item),
    target: targetLabel(item.target, textFrom(item.source_id, "-")),
    updatedAt: textFrom(item.updated_at ?? item.created_at, ""),
  }));
}

function interventionRows(response?: ManagementInterventionStreamResponse): DecisionWorkbenchRow[] {
  return (response?.data.items ?? []).map((item) => ({
    id: item.id || item.event_id || item.intervention_id,
    sourceKey: "intervention",
    sourceLabel: sourceLabels.intervention,
    queue: labelFrom(item.kind, "intervention"),
    title: textFrom(item.title, item.intervention_id),
    owner: ownerFrom(item.actor, item.persona_id, item.runtime_id, item.strategy_id),
    severity: textFrom(item.severity ?? item.risk_level ?? item.priority, "unknown").toLowerCase(),
    status: textFrom(item.status, "unknown").toLowerCase(),
    evidence: evidenceSummary(item.source_refs, item.links, item.event_id, item.intervention_id),
    nextAction: interventionNextAction(item),
    target: targetLabel(item.target, ownerFrom(item.persona_id, item.runtime_id, item.strategy_id, "-")),
    updatedAt: textFrom(item.updated_at ?? item.occurred_at ?? item.created_at, ""),
  }));
}

function sentinelFindingRows(response?: ManagementSentinelPulseResponse): DecisionWorkbenchRow[] {
  return (response?.data.items ?? []).map((item) => ({
    id: item.id || item.finding_id,
    sourceKey: "sentinel",
    sourceLabel: sourceLabels.sentinel,
    queue: labelFrom(item.kind, "finding"),
    title: textFrom(item.title, item.finding_id),
    owner: ownerFrom(item.target, item.source_refs, item.finding_id),
    severity: textFrom(item.severity ?? item.risk_level, "unknown").toLowerCase(),
    status: textFrom(item.status, "unknown").toLowerCase(),
    evidence: evidenceSummary(item.source_refs, item.links, item.finding_id),
    nextAction: sentinelFindingNextAction(item),
    target: targetLabel(item.target, textFrom(item.source_refs?.runtime_id ?? item.finding_id, "-")),
    updatedAt: textFrom(item.updated_at ?? item.triggered_at ?? item.created_at, ""),
  }));
}

function sentinelInterventionRows(response?: ManagementSentinelPulseResponse): DecisionWorkbenchRow[] {
  return (response?.data.related?.interventions ?? []).map((item) => ({
    id: item.id || item.intervention_id,
    sourceKey: "sentinel",
    sourceLabel: sourceLabels.sentinel,
    queue: labelFrom(item.kind, "linked intervention"),
    title: textFrom(item.title, item.intervention_id),
    owner: ownerFrom(item.source_refs, item.intervention_id),
    severity: textFrom(item.severity ?? item.risk_level, "unknown").toLowerCase(),
    status: textFrom(item.status, "unknown").toLowerCase(),
    evidence: evidenceSummary(item.source_refs, item.intervention_id, item.finding_id),
    nextAction: sentinelInterventionNextAction(item),
    target: textFrom(item.source_refs?.runtime_id ?? item.source_refs?.finding_id, "-"),
    updatedAt: textFrom(item.triggered_at, ""),
  }));
}

function degradedReasonForSurface(surface: DecisionWorkbenchSurface): string | null {
  if (surface.status === "ok" || surface.status === "unknown") return null;
  const detail = surface.message ? `: ${surface.message}` : "";
  return `${surface.label} is ${labelFrom(surface.status)} via ${surface.source}${detail}`;
}

export function collectDecisionWorkbenchRows({
  governanceLedger,
  hiqBacklog,
  interventionStream,
  sentinelPulse,
  failures = [],
}: DecisionWorkbenchResponses): DecisionWorkbenchModel {
  const rows = sortRows([
    ...governanceRows(governanceLedger),
    ...hiqRows(hiqBacklog),
    ...interventionRows(interventionStream),
    ...sentinelFindingRows(sentinelPulse),
    ...sentinelInterventionRows(sentinelPulse),
  ]);
  const surfaces = [
    surfaceFromResponse("governance", governanceLedger),
    surfaceFromResponse("hiq", hiqBacklog),
    surfaceFromResponse("intervention", interventionStream),
    surfaceFromResponse("sentinel", sentinelPulse),
  ];
  const degradedReasons = [
    ...surfaces.map(degradedReasonForSurface).filter((item): item is string => item !== null),
    ...failures.map((failure) => `${failure.label} failed: ${failure.message}`),
  ];
  return {
    rows,
    surfaces,
    failures,
    degradedReasons,
  };
}

function failureMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

function highPriorityRows(rows: DecisionWorkbenchRow[]): DecisionWorkbenchRow[] {
  const urgent = rows.filter((row) => severityRank(row.severity) <= 1 || activeStatusRank(row.status) === 0);
  return (urgent.length > 0 ? urgent : rows).slice(0, 3);
}

function SummaryMetric({ label, value }: { label: string; value: number }) {
  return (
    <div className="min-w-24 rounded-md border border-border bg-background px-3 py-2">
      <div className="text-[11px] uppercase text-muted-foreground">{label}</div>
      <div className="text-lg font-semibold leading-6">{value}</div>
    </div>
  );
}

function SurfaceBadge({ surface }: { surface: DecisionWorkbenchSurface }) {
  return (
    <Badge
      variant="outline"
      className={cn("whitespace-nowrap capitalize", statusTone(surface.status))}
      data-testid={`decision-workbench-surface-${surface.key}`}
    >
      {surface.label}: {labelFrom(surface.status)}
    </Badge>
  );
}

function QueueCard({ row }: { row: DecisionWorkbenchRow }) {
  return (
    <article
      className="rounded-md border border-border bg-background p-3"
      data-testid={`decision-workbench-card-${testIdPart(row.id)}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" className={cn("text-[11px]", sourceTone(row.sourceKey))}>
              {row.sourceLabel}
            </Badge>
            <Badge variant="outline" className={cn("capitalize", severityTone(row.severity))}>
              {labelFrom(row.severity)}
            </Badge>
          </div>
          <h3 className="mt-2 line-clamp-2 text-sm font-semibold leading-5">{row.title}</h3>
        </div>
        <Badge variant="outline" className={cn("capitalize", statusTone(row.status))}>
          {labelFrom(row.status)}
        </Badge>
      </div>

      <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
        <div>
          <dt className="text-muted-foreground">Owner</dt>
          <dd className="font-medium">{row.owner}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Target</dt>
          <dd className="font-medium">{row.target}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Evidence</dt>
          <dd className="truncate font-medium" title={row.evidence}>{row.evidence}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Next action</dt>
          <dd className="font-medium">{row.nextAction}</dd>
        </div>
      </dl>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3">
        <span className="text-xs text-muted-foreground">Updated {compactDate(row.updatedAt)}</span>
        <button
          type="button"
          disabled
          title="Non-production read-only surface"
          className="inline-flex h-8 cursor-not-allowed items-center gap-2 rounded-md border border-border px-3 text-xs font-medium text-muted-foreground opacity-70"
          data-testid={`decision-workbench-disabled-action-${testIdPart(row.id)}`}
        >
          <Lock className="h-3.5 w-3.5" />
          Actions disabled
        </button>
      </div>
    </article>
  );
}

function QueueTable({ rows }: { rows: DecisionWorkbenchRow[] }) {
  return (
    <ManagementDenseTable minWidth={920} testId="decision-workbench-table">
      <table className="w-full min-w-[920px] table-fixed text-left text-xs">
        <thead className="border-b border-border text-muted-foreground">
          <tr>
            <th className="w-[260px] px-2 py-2 font-medium">Queue</th>
            <th className="w-[130px] px-2 py-2 font-medium">Owner</th>
            <th className="w-[110px] px-2 py-2 font-medium">Severity</th>
            <th className="w-[110px] px-2 py-2 font-medium">Status</th>
            <th className="w-[210px] px-2 py-2 font-medium">Evidence</th>
            <th className="w-[210px] px-2 py-2 font-medium">Next action</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.map((row) => (
            <tr
              key={`${row.sourceKey}-${row.id}`}
              data-testid={`decision-workbench-row-${testIdPart(row.id)}`}
            >
              <td className="px-2 py-2 align-top">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge variant="outline" className={cn("text-[11px]", sourceTone(row.sourceKey))}>
                    {row.sourceLabel}
                  </Badge>
                  <span className="font-medium">{row.queue}</span>
                </div>
                <div className="mt-1 line-clamp-2 text-muted-foreground">{row.title}</div>
              </td>
              <td className="px-2 py-2 align-top font-medium">{row.owner}</td>
              <td className="px-2 py-2 align-top">
                <Badge variant="outline" className={cn("capitalize", severityTone(row.severity))}>
                  {labelFrom(row.severity)}
                </Badge>
              </td>
              <td className="px-2 py-2 align-top">
                <Badge variant="outline" className={cn("capitalize", statusTone(row.status))}>
                  {labelFrom(row.status)}
                </Badge>
              </td>
              <td className="px-2 py-2 align-top">
                <span className="line-clamp-2" title={row.evidence}>{row.evidence}</span>
              </td>
              <td className="px-2 py-2 align-top">
                <span className="font-medium">{row.nextAction}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </ManagementDenseTable>
  );
}

export function ManagementDecisionWorkbenchPanel() {
  const [state, setState] = useState<LoadState>("loading");
  const [model, setModel] = useState<DecisionWorkbenchModel>(() => collectDecisionWorkbenchRows({}));
  const [error, setError] = useState<string | undefined>();

  const load = useCallback(async () => {
    setState("loading");
    setError(undefined);

    const settled = await Promise.allSettled([
      fetchManagementGovernanceLedger({ page_size: 12 }),
      fetchManagementHiqBacklog({ page_size: 12 }),
      fetchManagementInterventionStream({ page_size: 12, window_hours: 72 }),
      fetchManagementSentinelPulse({ page_size: 12 }),
    ]);

    const failures: DecisionWorkbenchFailure[] = [];
    const [governanceLedger, hiqBacklog, interventionStream, sentinelPulse] = settled.map((result, index) => {
      const key = (["governance", "hiq", "intervention", "sentinel"] as const)[index];
      if (result.status === "fulfilled") return result.value;
      failures.push({
        key,
        label: sourceLabels[key],
        message: failureMessage(result.reason),
      });
      return undefined;
    });

    if (failures.length === settled.length) {
      setModel(collectDecisionWorkbenchRows({ failures }));
      setError(failures.map((failure) => `${failure.label}: ${failure.message}`).join("; "));
      setState("error");
      return;
    }

    setModel(collectDecisionWorkbenchRows({
      governanceLedger: governanceLedger as ManagementGovernanceLedgerResponse | undefined,
      hiqBacklog: hiqBacklog as ManagementHiqBacklogResponse | undefined,
      interventionStream: interventionStream as ManagementInterventionStreamResponse | undefined,
      sentinelPulse: sentinelPulse as ManagementSentinelPulseResponse | undefined,
      failures,
    }));
    setState("ready");
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const metrics = useMemo(() => {
    const critical = model.rows.filter((row) => severityRank(row.severity) === 0).length;
    const high = model.rows.filter((row) => severityRank(row.severity) === 1).length;
    const pending = model.rows.filter((row) => activeStatusRank(row.status) === 0).length;
    return { critical, high, pending };
  }, [model.rows]);

  const priorityRows = useMemo(() => highPriorityRows(model.rows), [model.rows]);

  return (
    <section className="flex flex-col gap-4" data-testid="management-decision-workbench-panel">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <ClipboardCheck className="h-4 w-4 text-primary" />
            <h2 className="text-base font-semibold">Decision Workbench</h2>
            <Badge
              variant="outline"
              className="gap-1 bg-muted text-muted-foreground border-border"
              data-testid="decision-workbench-read-only"
            >
              <Lock className="h-3 w-3" />
              Read-only non-production
            </Badge>
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            {model.surfaces.map((surface) => (
              <SurfaceBadge key={surface.key} surface={surface} />
            ))}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <SummaryMetric label="Queue" value={model.rows.length} />
          <SummaryMetric label="Critical" value={metrics.critical} />
          <SummaryMetric label="High" value={metrics.high} />
          <SummaryMetric label="Pending" value={metrics.pending} />
          <button
            type="button"
            onClick={() => void load()}
            className="inline-flex h-8 items-center gap-2 rounded-md border border-border px-3 text-xs font-medium hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", state === "loading" ? "animate-spin" : "")} />
            Refresh
          </button>
        </div>
      </header>

      {state === "loading" ? (
        <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground" role="status">
          <RefreshCw className="h-4 w-4 animate-spin" />
          Loading decision workbench
        </div>
      ) : null}

      {state === "error" ? (
        <EmptyState
          icon={<AlertTriangle className="h-8 w-8" />}
          title="Decision workbench unavailable"
          description={error}
          cta={{ label: "Retry", onClick: load }}
        />
      ) : null}

      {state === "ready" && model.degradedReasons.length > 0 ? (
        <div
          className="rounded-md border border-status-warning/30 bg-status-warning/10 p-3 text-sm"
          data-testid="decision-workbench-degraded"
        >
          <div className="flex items-center gap-2 font-medium text-status-warning">
            <ShieldAlert className="h-4 w-4" />
            Degraded sources
          </div>
          <ul className="mt-2 grid gap-1 text-xs text-muted-foreground">
            {model.degradedReasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {state === "ready" && model.rows.length === 0 ? (
        <EmptyState
          icon={<FileSearch className="h-8 w-8" />}
          title="No decision queue items"
          description="The management decision sources returned no active ledger, backlog, intervention, or sentinel rows."
          cta={{ label: "Refresh", onClick: load }}
        />
      ) : null}

      {state === "ready" && model.rows.length > 0 ? (
        <>
          <div className="grid gap-3 lg:grid-cols-3" data-testid="decision-workbench-priority-cards">
            {priorityRows.map((row) => (
              <QueueCard key={`${row.sourceKey}-${row.id}`} row={row} />
            ))}
          </div>
          <QueueTable rows={model.rows} />
        </>
      ) : null}
    </section>
  );
}
