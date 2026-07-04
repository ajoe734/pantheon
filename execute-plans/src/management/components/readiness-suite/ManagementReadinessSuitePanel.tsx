import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  FileCheck2,
  FileStack,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { managementClient } from "@/lib/bff/client";
import type {
  ManagementReadinessCheck,
  ManagementReadinessEvidenceRef,
  ManagementReadinessResponse,
} from "@/lib/bff-v1/management";
import { ManagementDenseTable } from "@/management/components/dense-table";
import { cn } from "@/lib/utils";

type LoadState = "loading" | "ready" | "degraded" | "error";
type SuiteDecision = "go" | "no-go";

interface ReadinessSurfaceDefinition {
  id: string;
  label: string;
  title: string;
  surfaceKey: string;
  reader: () => Promise<ManagementReadinessResponse>;
}

export interface ManagementReadinessSurfaceFailure {
  id: string;
  label: string;
  title: string;
  message: string;
}

export interface ManagementReadinessSurfaceView {
  id: string;
  label: string;
  title: string;
  status: string;
  canProceed: boolean;
  checkCount: number;
  passedCheckCount: number;
  blockerCount: number;
  blockingReasons: string[];
  checks: ManagementReadinessCheck[];
  evidenceRefs: ManagementReadinessEvidenceRef[];
  evidenceRefCount: number;
  surfaceStatus: string;
  surfaceSource: string;
  surfaceReason: string;
  freshnessLabel: string;
  snapshotLabel: string;
  degraded: boolean;
}

export interface ManagementReadinessSuiteSummary {
  expectedCount: number;
  loadedCount: number;
  goCount: number;
  noGoCount: number;
  checkCount: number;
  passedCheckCount: number;
  blockerCount: number;
  evidenceRefCount: number;
  degradedSurfaceCount: number;
  failedReaderCount: number;
  decision: SuiteDecision;
  state: Exclude<LoadState, "loading" | "error"> | "empty";
}

const readinessSurfaces: ReadinessSurfaceDefinition[] = [
  {
    id: "ep5",
    label: "EP5",
    title: "EP5 Release Gate",
    surfaceKey: "management_readiness_ep5",
    reader: () => managementClient.readiness.ep5(),
  },
  {
    id: "broker-live",
    label: "Broker Live",
    title: "Broker Live Gate",
    surfaceKey: "management_readiness_broker_live",
    reader: () => managementClient.readiness.brokerLive(),
  },
  {
    id: "capital-binding-live",
    label: "Capital Binding",
    title: "Capital Binding Live Gate",
    surfaceKey: "management_readiness_capital_binding_live",
    reader: () => managementClient.readiness.capitalBindingLive(),
  },
  {
    id: "bff-ha",
    label: "BFF HA",
    title: "BFF High Availability Gate",
    surfaceKey: "management_readiness_bff_ha",
    reader: () => managementClient.readiness.bffHa(),
  },
  {
    id: "strict-publish",
    label: "Strict Publish",
    title: "Strict Publish Gate",
    surfaceKey: "management_readiness_strict_publish",
    reader: () => managementClient.readiness.strictPublish(),
  },
];

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function asNumber(value: unknown, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function booleanFrom(value: unknown, fallback = false): boolean {
  if (typeof value === "boolean") return value;
  if (value === undefined || value === null) return fallback;
  return ["1", "true", "yes", "on"].includes(String(value).trim().toLowerCase());
}

function textFrom(value: unknown, fallback = "-"): string {
  const text = String(value ?? "").trim();
  return text || fallback;
}

function labelFrom(value: unknown, fallback = "unknown"): string {
  return textFrom(value, fallback).replace(/_/g, " ");
}

function normalized(value: unknown): string {
  return String(value ?? "").trim().toLowerCase();
}

function formatTimestamp(value: unknown): string {
  const text = String(value ?? "").trim();
  if (!text) return "-";
  const parsed = new Date(text);
  if (Number.isNaN(parsed.getTime())) return text;
  return parsed.toISOString().replace(".000Z", "Z");
}

function formatDurationSeconds(value: unknown): string {
  const seconds = asNumber(value, Number.NaN);
  if (!Number.isFinite(seconds) || seconds < 0) return "";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = seconds / 60;
  if (minutes < 60) return `${minutes.toFixed(minutes < 10 ? 1 : 0)}m`;
  const hours = minutes / 60;
  return `${hours.toFixed(hours < 10 ? 1 : 0)}h`;
}

function statusTone(status: string, canProceed?: boolean): string {
  const statusKey = normalized(status);
  if (canProceed || ["ready", "pass", "passed", "ok", "success", "fresh", "healthy"].includes(statusKey)) {
    return "bg-status-success/15 text-status-success border-status-success/30";
  }
  if (["blocked", "fail", "failed", "error", "critical", "unavailable"].includes(statusKey)) {
    return "bg-status-failed/15 text-status-failed border-status-failed/30";
  }
  if (["degraded", "warn", "warning", "stale", "unknown"].includes(statusKey)) {
    return "bg-status-warning/15 text-status-warning border-status-warning/30";
  }
  return "bg-muted text-muted-foreground border-border";
}

function decisionTone(decision: SuiteDecision): string {
  return decision === "go"
    ? "bg-status-success/15 text-status-success border-status-success/30"
    : "bg-status-failed/15 text-status-failed border-status-failed/30";
}

function isSurfaceMetaDegraded(status: string, source: string): boolean {
  const statusKey = normalized(status);
  const sourceKey = normalized(source);
  return (
    ["degraded", "unavailable", "stale", "error", "missing", "unknown"].includes(statusKey) ||
    ["mock", "seed_fixture", "snapshot", "snapshot_fallback"].includes(sourceKey)
  );
}

function isReadinessDegraded(status: string): boolean {
  return ["degraded", "unknown"].includes(normalized(status));
}

function responseChecks(response: ManagementReadinessResponse): ManagementReadinessCheck[] {
  if (Array.isArray(response.checks) && response.checks.length > 0) return response.checks;
  if (Array.isArray(response.data?.checks)) return response.data.checks;
  if (Array.isArray(response.items)) return response.items;
  return [];
}

function responseEvidenceRefs(response: ManagementReadinessResponse): ManagementReadinessEvidenceRef[] {
  if (Array.isArray(response.evidence_refs) && response.evidence_refs.length > 0) return response.evidence_refs;
  if (Array.isArray(response.data?.evidence_refs) && response.data.evidence_refs.length > 0) {
    return response.data.evidence_refs;
  }
  if (Array.isArray(response.data?.evidenceRefs)) return response.data.evidenceRefs;
  return [];
}

function blockingReasonsFrom(response: ManagementReadinessResponse): string[] {
  const summary = asRecord(response.summary);
  const data = asRecord(response.data);
  const raw = Array.isArray(summary.blockingReasons)
    ? summary.blockingReasons
    : Array.isArray(summary.blocking_reasons)
      ? summary.blocking_reasons
      : Array.isArray(data.blockingReasons)
        ? data.blockingReasons
        : Array.isArray(data.blocking_reasons)
          ? data.blocking_reasons
          : [];
  return raw.map((item) => textFrom(item, "")).filter(Boolean);
}

function isBlockingCheck(check: ManagementReadinessCheck): boolean {
  return booleanFrom(check.blocking) || ["blocked", "fail", "failed", "error"].includes(normalized(check.status));
}

function surfaceMeta(
  response: ManagementReadinessResponse,
  definition: ReadinessSurfaceDefinition,
): Record<string, unknown> {
  const surfaces = asRecord(response.meta?.surfaces);
  return asRecord(surfaces[definition.surfaceKey] ?? surfaces[definition.id]);
}

function freshnessValue(
  response: ManagementReadinessResponse,
  definition: ReadinessSurfaceDefinition,
): unknown {
  const staleness = asRecord(response.meta?.staleness);
  if (definition.surfaceKey in staleness) return staleness[definition.surfaceKey];
  if (definition.id in staleness) return staleness[definition.id];
  const details = asRecord(response.data?.details);
  return details.freshness ?? details.staleness ?? details.source_freshness ?? details.sourceFreshness;
}

function freshnessLabelFrom(
  response: ManagementReadinessResponse,
  definition: ReadinessSurfaceDefinition,
): string {
  const freshness = freshnessValue(response, definition);
  if (freshness === undefined || freshness === null) {
    return response.meta?.snapshot_at ? `snapshot ${formatTimestamp(response.meta.snapshot_at)}` : "-";
  }
  if (typeof freshness === "string") return labelFrom(freshness);
  if (typeof freshness === "number") return `age ${formatDurationSeconds(freshness)}`;

  const record = asRecord(freshness);
  const status = textFrom(record.status ?? record.state ?? record.freshness, "");
  const age = formatDurationSeconds(record.age_seconds ?? record.ageSeconds ?? record.age);
  const asOf = textFrom(
    record.as_of ?? record.asOf ?? record.snapshot_at ?? record.captured_at ?? record.updated_at ?? record.data_cutoff,
    "",
  );
  const reason = textFrom(record.reason ?? record.message, "");
  const parts = [
    status ? labelFrom(status) : "",
    age ? `age ${age}` : "",
    asOf ? `as of ${formatTimestamp(asOf)}` : "",
    reason,
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(" ") : "-";
}

export function buildManagementReadinessSurfaceView(
  definition: Omit<ReadinessSurfaceDefinition, "reader">,
  response: ManagementReadinessResponse,
): ManagementReadinessSurfaceView {
  const summary = asRecord(response.summary);
  const data = asRecord(response.data);
  const checks = responseChecks(response);
  const evidenceRefs = responseEvidenceRefs(response);
  const readinessStatus = textFrom(
    summary.readinessStatus ?? summary.readiness_status ?? data.readinessStatus ?? data.readiness_status,
    "unknown",
  );
  const rawCanProceed =
    summary.canProceed ?? summary.can_proceed ?? data.canProceed ?? data.can_proceed;
  const canProceed = rawCanProceed === undefined
    ? normalized(readinessStatus) === "ready"
    : booleanFrom(rawCanProceed);
  const blockingReasons = blockingReasonsFrom(response);
  const blockingCheckCount = checks.filter(isBlockingCheck).length;
  const blockerCount = Math.max(
    asNumber(summary.blockingReasonCount ?? summary.blocking_reason_count, 0),
    blockingReasons.length,
    blockingCheckCount,
  );
  const checkCount = Math.max(
    asNumber(summary.checkCount ?? summary.check_count, 0),
    checks.length,
  );
  const passedCheckCount = Math.max(
    asNumber(summary.passedCheckCount ?? summary.passed_check_count, 0),
    checks.filter((check) => ["pass", "passed", "ok", "ready"].includes(normalized(check.status))).length,
  );
  const meta = surfaceMeta(response, definition as ReadinessSurfaceDefinition);
  const surfaceStatus = labelFrom(meta.status ?? meta.state, "unknown");
  const surfaceSource = textFrom(meta.source ?? meta.origin, "unknown");
  const surfaceReason = textFrom(meta.reason ?? meta.message, "");
  const freshnessLabel = freshnessLabelFrom(response, definition as ReadinessSurfaceDefinition);
  const snapshotLabel = response.meta?.snapshot_at ? formatTimestamp(response.meta.snapshot_at) : "-";
  const degraded = isReadinessDegraded(readinessStatus) || isSurfaceMetaDegraded(surfaceStatus, surfaceSource);

  return {
    id: textFrom(data.id ?? data.readiness_id ?? definition.id, definition.id),
    label: definition.label,
    title: textFrom(data.title, definition.title),
    status: readinessStatus,
    canProceed,
    checkCount,
    passedCheckCount,
    blockerCount,
    blockingReasons,
    checks,
    evidenceRefs,
    evidenceRefCount: evidenceRefs.length,
    surfaceStatus,
    surfaceSource,
    surfaceReason,
    freshnessLabel,
    snapshotLabel,
    degraded,
  };
}

export function buildManagementReadinessSuiteSummary(
  surfaces: ManagementReadinessSurfaceView[],
  failures: ManagementReadinessSurfaceFailure[],
  expectedCount = readinessSurfaces.length,
): ManagementReadinessSuiteSummary {
  const loadedCount = surfaces.length;
  const goCount = surfaces.filter((surface) => surface.canProceed).length;
  const noGoCount = loadedCount - goCount + failures.length + Math.max(0, expectedCount - loadedCount - failures.length);
  const failedReaderCount = failures.length;
  const degradedSurfaceCount = surfaces.filter((surface) => surface.degraded).length;
  const decision: SuiteDecision =
    loadedCount === expectedCount &&
    failedReaderCount === 0 &&
    surfaces.every((surface) => surface.canProceed)
      ? "go"
      : "no-go";
  const state = loadedCount === 0
    ? "empty"
    : failedReaderCount > 0 || degradedSurfaceCount > 0
      ? "degraded"
      : "ready";

  return {
    expectedCount,
    loadedCount,
    goCount,
    noGoCount,
    checkCount: surfaces.reduce((sum, surface) => sum + surface.checkCount, 0),
    passedCheckCount: surfaces.reduce((sum, surface) => sum + surface.passedCheckCount, 0),
    blockerCount: surfaces.reduce((sum, surface) => sum + surface.blockerCount, 0) + failures.length,
    evidenceRefCount: surfaces.reduce((sum, surface) => sum + surface.evidenceRefCount, 0),
    degradedSurfaceCount,
    failedReaderCount,
    decision,
    state,
  };
}

function failureMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : textFrom(reason, "Readiness reader failed");
}

function evidenceLabel(ref: ManagementReadinessEvidenceRef): string {
  return textFrom(ref.label ?? ref.id ?? ref.path, "evidence");
}

function evidencePath(ref: ManagementReadinessEvidenceRef): string {
  return textFrom(ref.path ?? ref.href ?? ref.id, "-");
}

function CheckStatusBadge({ status }: { status: string }) {
  return (
    <Badge variant="outline" className={cn("capitalize", statusTone(status))}>
      {labelFrom(status)}
    </Badge>
  );
}

function SurfaceRow({ surface }: { surface: ManagementReadinessSurfaceView }) {
  const DecisionIcon = surface.canProceed ? ShieldCheck : ShieldAlert;
  const evidencePreview = surface.evidenceRefs.slice(0, 3);
  return (
    <article
      className="rounded-md border border-border bg-background p-3"
      data-testid={`readiness-suite-surface-${surface.id}`}
      data-readiness-state={surface.canProceed ? "go" : "no-go"}
      data-source-state={surface.degraded ? "degraded" : "ready"}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold leading-5">{surface.title}</h3>
            <Badge variant="outline" className="font-mono text-[11px]">
              {surface.id}
            </Badge>
            {surface.degraded ? (
              <Badge
                variant="outline"
                className="bg-status-warning/15 text-status-warning border-status-warning/30"
              >
                Degraded source
              </Badge>
            ) : null}
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>Source: {surface.surfaceSource}</span>
            <span>Surface: {surface.surfaceStatus}</span>
            <span>Freshness: {surface.freshnessLabel}</span>
            <span>Snapshot: {surface.snapshotLabel}</span>
            <span>Evidence: {surface.evidenceRefCount}</span>
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <Badge
            variant="outline"
            className={cn("gap-1 whitespace-nowrap", decisionTone(surface.canProceed ? "go" : "no-go"))}
          >
            <DecisionIcon className="h-3 w-3" />
            {surface.canProceed ? "Go" : "No-go"}
          </Badge>
          <Badge variant="outline" className={cn("capitalize", statusTone(surface.status, surface.canProceed))}>
            {labelFrom(surface.status)}
          </Badge>
        </div>
      </div>

      <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-4">
        <div>
          <dt className="text-muted-foreground">Checks</dt>
          <dd className="font-medium">{surface.passedCheckCount}/{surface.checkCount}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Blockers</dt>
          <dd className="font-medium">{surface.blockerCount}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Evidence refs</dt>
          <dd className="font-medium">{surface.evidenceRefCount}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Readiness</dt>
          <dd className="font-medium">{surface.canProceed ? "Go" : "No-go"}</dd>
        </div>
      </dl>

      {surface.surfaceReason ? (
        <div
          className="mt-3 flex items-start gap-2 rounded-md border border-status-warning/30 bg-status-warning/10 p-2 text-xs text-status-warning"
          data-testid={`readiness-suite-surface-reason-${surface.id}`}
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-none" />
          <span>{surface.surfaceReason}</span>
        </div>
      ) : null}

      {surface.blockingReasons.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-1.5" data-testid={`readiness-suite-blockers-${surface.id}`}>
          {surface.blockingReasons.map((reason) => (
            <Badge
              key={reason}
              variant="outline"
              className="bg-status-failed/15 text-status-failed border-status-failed/30"
            >
              {labelFrom(reason)}
            </Badge>
          ))}
        </div>
      ) : null}

      {evidencePreview.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-1.5" data-testid={`readiness-suite-evidence-preview-${surface.id}`}>
          {evidencePreview.map((ref) => (
            <Badge key={`${ref.id}-${evidencePath(ref)}`} variant="outline" className="max-w-full font-mono text-[10px]">
              {evidencePath(ref)}
            </Badge>
          ))}
        </div>
      ) : null}

      {surface.checks.length > 0 ? (
        <ManagementDenseTable className="mt-3" testId={`readiness-suite-checks-${surface.id}`}>
          <table className="w-full min-w-[680px] text-left text-xs">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-1 pr-3 font-medium">Check</th>
                <th className="py-1 pr-3 font-medium">Status</th>
                <th className="py-1 pr-3 font-medium">Blocking</th>
                <th className="py-1 pr-3 font-medium">Evidence</th>
                <th className="py-1 pr-3 font-medium">Message</th>
              </tr>
            </thead>
            <tbody>
              {surface.checks.map((check) => (
                <tr key={check.id} className="border-t border-border">
                  <td className="py-1.5 pr-3">
                    <div className="font-medium">{textFrom(check.label ?? check.id, check.id)}</div>
                    <div className="font-mono text-[10px] text-muted-foreground">{check.id}</div>
                  </td>
                  <td className="py-1.5 pr-3">
                    <CheckStatusBadge status={textFrom(check.status, "unknown")} />
                  </td>
                  <td className="py-1.5 pr-3">{booleanFrom(check.blocking) ? "yes" : "no"}</td>
                  <td className="py-1.5 pr-3">{check.evidence_refs?.length ?? 0}</td>
                  <td className="max-w-[28rem] py-1.5 pr-3 text-muted-foreground">
                    {textFrom(check.message, "-")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </ManagementDenseTable>
      ) : null}

      {surface.evidenceRefs.length > 0 ? (
        <ManagementDenseTable className="mt-3" minWidth={620} testId={`readiness-suite-evidence-${surface.id}`}>
          <table className="w-full min-w-[620px] text-left text-xs">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-1 pr-3 font-medium">Evidence</th>
                <th className="py-1 pr-3 font-medium">Path</th>
                <th className="py-1 pr-3 font-medium">Exists</th>
              </tr>
            </thead>
            <tbody>
              {surface.evidenceRefs.map((ref) => (
                <tr key={`${ref.id}-${evidencePath(ref)}`} className="border-t border-border">
                  <td className="py-1.5 pr-3 font-medium">{evidenceLabel(ref)}</td>
                  <td className="py-1.5 pr-3 font-mono text-[11px]">{evidencePath(ref)}</td>
                  <td className="py-1.5 pr-3">{ref.exists === false ? "missing" : "available"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ManagementDenseTable>
      ) : null}
    </article>
  );
}

function FailureRow({ failure }: { failure: ManagementReadinessSurfaceFailure }) {
  return (
    <article
      className="rounded-md border border-status-warning/30 bg-status-warning/10 p-3 text-sm"
      data-testid={`readiness-suite-failure-${failure.id}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="font-medium">{failure.title}</div>
          <div className="mt-1 text-xs text-muted-foreground">{failure.id}</div>
        </div>
        <Badge variant="outline" className="bg-status-warning/15 text-status-warning border-status-warning/30">
          Reader failed
        </Badge>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">{failure.message}</p>
    </article>
  );
}

export function ManagementReadinessSuitePanel() {
  const [state, setState] = useState<LoadState>("loading");
  const [surfaces, setSurfaces] = useState<ManagementReadinessSurfaceView[]>([]);
  const [failures, setFailures] = useState<ManagementReadinessSurfaceFailure[]>([]);
  const [error, setError] = useState<string | undefined>();

  const load = useCallback(async () => {
    setState("loading");
    setError(undefined);
    const settled = await Promise.allSettled(
      readinessSurfaces.map(async (definition) => ({
        definition,
        response: await definition.reader(),
      })),
    );

    const nextSurfaces: ManagementReadinessSurfaceView[] = [];
    const nextFailures: ManagementReadinessSurfaceFailure[] = [];

    settled.forEach((result, index) => {
      const definition = readinessSurfaces[index];
      if (result.status === "fulfilled") {
        if (result.value.response) {
          nextSurfaces.push(buildManagementReadinessSurfaceView(definition, result.value.response));
        }
        return;
      }
      nextFailures.push({
        id: definition.id,
        label: definition.label,
        title: definition.title,
        message: failureMessage(result.reason),
      });
    });

    setSurfaces(nextSurfaces);
    setFailures(nextFailures);

    if (nextSurfaces.length === 0 && nextFailures.length > 0) {
      setError(nextFailures.map((failure) => `${failure.label}: ${failure.message}`).join("; "));
      setState("error");
      return;
    }

    const nextSummary = buildManagementReadinessSuiteSummary(nextSurfaces, nextFailures);
    setState(nextSummary.state === "degraded" ? "degraded" : "ready");
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const summary = useMemo(
    () => buildManagementReadinessSuiteSummary(surfaces, failures),
    [surfaces, failures],
  );
  const displayState = state === "ready" && summary.state === "degraded" ? "degraded" : state;
  const DecisionIcon = summary.decision === "go" ? CheckCircle2 : ShieldAlert;

  return (
    <section
      className="flex flex-col gap-4"
      data-testid="management-readiness-suite-panel"
      data-suite-state={displayState}
      data-suite-decision={summary.decision}
    >
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <FileCheck2 className="h-4 w-4 text-primary" />
            <h2 className="text-base font-semibold">Management Readiness</h2>
            <Badge variant="outline" className={cn("gap-1 whitespace-nowrap", decisionTone(summary.decision))}>
              <DecisionIcon className="h-3 w-3" />
              {summary.decision === "go" ? "Go" : "No-go"}
            </Badge>
            {displayState === "degraded" ? (
              <Badge
                variant="outline"
                className="bg-status-warning/15 text-status-warning border-status-warning/30"
              >
                Degraded
              </Badge>
            ) : null}
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>Loaded: {summary.loadedCount}/{summary.expectedCount}</span>
            <span>Go: {summary.goCount}/{summary.expectedCount}</span>
            <span>No-go: {summary.noGoCount}</span>
            <span>Checks: {summary.passedCheckCount}/{summary.checkCount}</span>
            <span>Blockers: {summary.blockerCount}</span>
            <span>Evidence refs: {summary.evidenceRefCount}</span>
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

      {displayState === "loading" ? (
        <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground" role="status">
          <RefreshCw className="h-4 w-4 animate-spin" />
          Loading readiness suite
        </div>
      ) : null}

      {displayState === "error" ? (
        <EmptyState
          icon={<AlertTriangle className="h-8 w-8" />}
          title="Readiness suite unavailable"
          description={error}
          cta={{ label: "Retry", onClick: load }}
        />
      ) : null}

      {displayState !== "loading" && displayState !== "error" && summary.state === "empty" ? (
        <EmptyState
          icon={<FileStack className="h-8 w-8" />}
          title="No readiness aggregates"
          description="The readiness readers returned no aggregate responses."
          cta={{ label: "Refresh", onClick: load }}
        />
      ) : null}

      {displayState === "degraded" && summary.loadedCount > 0 ? (
        <div
          className="flex items-start gap-2 rounded-md border border-status-warning/30 bg-status-warning/10 p-3 text-xs text-status-warning"
          data-testid="readiness-suite-degraded-banner"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-none" />
          <span>
            Degraded readiness data: {summary.failedReaderCount} reader failure(s), {summary.degradedSurfaceCount} degraded source(s).
          </span>
        </div>
      ) : null}

      {displayState !== "loading" && displayState !== "error" && surfaces.length > 0 ? (
        <div className="grid gap-3">
          {surfaces.map((surface) => (
            <SurfaceRow key={surface.id} surface={surface} />
          ))}
          {failures.map((failure) => (
            <FailureRow key={failure.id} failure={failure} />
          ))}
        </div>
      ) : null}
    </section>
  );
}
