import React, { useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, History, RotateCcw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { DashboardRecipeV2 } from "@/lib/bff-v1/agora/types";
import { cn } from "@/lib/utils";

import type {
  DashboardConcurrencyError,
  DashboardMutationRequestEnvelope,
} from "./DashboardProposalPreview";

export interface DashboardRecipeVersionSummary {
  version: number;
  previous_version?: number | null;
  status: DashboardRecipeV2["status"];
  content_sha256?: string;
  generated_by?: DashboardRecipeV2["generated_by"] | string;
  change_reason?: string;
  created_at: string;
}

export interface DashboardRollbackRequestBody {
  expected_version: number;
  target_version: number;
  reason?: string;
}

export type DashboardRollbackAction = DashboardMutationRequestEnvelope<DashboardRollbackRequestBody>;

export interface DashboardChangeLogProps {
  recipeId: string;
  activeVersion: number;
  versions: DashboardRecipeVersionSummary[];
  etag?: string;
  idempotencyKey?: string;
  concurrencyError?: DashboardConcurrencyError | null;
  onRollback?: (request: DashboardRollbackAction) => void | Promise<void>;
  className?: string;
}

function makeIdempotencyKey(provided?: string): string {
  if (provided) return provided;
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  const suffix = Math.random().toString(16).slice(2, 14).padEnd(12, "0");
  return `00000000-0000-4000-8000-${suffix}`;
}

function stableString(value: unknown): string {
  if (value === undefined || value === null) return "-";
  if (typeof value === "string") return value || "-";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toISOString().replace("T", " ").slice(0, 16) + "Z";
}

function shortSha(value: string | undefined): string {
  return value ? value.slice(0, 8) : "-";
}

function ConcurrencyNotice({ error }: { error?: DashboardConcurrencyError | null }) {
  if (!error) return null;
  const details = error.details ?? {};
  return (
    <div className="rounded-md border border-primary bg-primary/10 p-3 text-sm text-foreground" data-testid="dashboard-changelog-concurrency-error">
      <div className="flex items-center gap-2 font-semibold text-primary">
        <AlertTriangle className="h-4 w-4" />
        {error.code ?? "CONCURRENT_MODIFICATION"}
      </div>
      <p className="mt-1">{error.message}</p>
      <dl className="mt-2 grid gap-2 text-xs sm:grid-cols-2">
        <div>
          <dt className="text-primary">Expected version</dt>
          <dd className="font-medium">{stableString(details.expected_version)}</dd>
        </div>
        <div>
          <dt className="text-primary">Current version</dt>
          <dd className="font-medium">{stableString(details.current_version)}</dd>
        </div>
        <div>
          <dt className="text-primary">Current ETag</dt>
          <dd className="break-all font-medium">{stableString(details.current_etag)}</dd>
        </div>
        <div>
          <dt className="text-primary">Latest</dt>
          <dd className="break-all font-medium">{stableString(details.latest_href)}</dd>
        </div>
      </dl>
    </div>
  );
}

function VersionStatusBadge({ status }: { status: DashboardRecipeVersionSummary["status"] }) {
  const classes: Record<DashboardRecipeVersionSummary["status"], string> = {
    active: "border-primary bg-primary/10 text-primary",
    archived: "border-border bg-muted text-muted-foreground",
    proposal: "border-ring bg-ring/10 text-foreground",
    rolled_back: "border-primary bg-primary/10 text-primary",
  };
  return (
    <Badge className={classes[status]} variant="outline">
      {status}
    </Badge>
  );
}

export function DashboardChangeLog({
  activeVersion,
  className,
  concurrencyError,
  etag,
  idempotencyKey,
  onRollback,
  recipeId,
  versions,
}: DashboardChangeLogProps) {
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  const orderedVersions = useMemo(
    () => [...versions].sort((a, b) => b.version - a.version),
    [versions],
  );
  const selected = versions.find((version) => version.version === selectedVersion);
  const canRollback = Boolean(onRollback && etag && selected && selected.version < activeVersion && !busy);

  const runRollback = async () => {
    if (!onRollback || !etag || !selected || selected.version >= activeVersion) return;
    setBusy(true);
    try {
      const body: DashboardRollbackRequestBody = {
        expected_version: activeVersion,
        target_version: selected.version,
      };
      const trimmedReason = reason.trim();
      if (trimmedReason) body.reason = trimmedReason;
      await onRollback({
        recipe_id: recipeId,
        headers: {
          "If-Match": etag,
          "Idempotency-Key": makeIdempotencyKey(idempotencyKey),
        },
        body,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className={cn("space-y-4 rounded-md border border-border bg-card p-4", className)} data-testid="dashboard-change-log">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <History className="h-4 w-4 text-foreground" />
            <h2 className="text-base font-semibold text-foreground">Dashboard Change Log</h2>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">{recipeId}</p>
        </div>
        <Badge className="border-border bg-muted text-foreground" variant="outline">
          active v{activeVersion}
        </Badge>
      </div>

      <ConcurrencyNotice error={concurrencyError} />

      <div className="overflow-hidden rounded-md border border-border bg-background">
        <table className="w-full border-collapse text-left text-xs" data-testid="dashboard-version-table">
          <thead className="bg-muted text-muted-foreground">
            <tr>
              <th className="w-24 px-3 py-2 font-semibold">Version</th>
              <th className="px-3 py-2 font-semibold">Status</th>
              <th className="px-3 py-2 font-semibold">Reason</th>
              <th className="px-3 py-2 font-semibold">Generated by</th>
              <th className="px-3 py-2 font-semibold">Created</th>
              <th className="px-3 py-2 font-semibold">Hash</th>
              <th className="w-28 px-3 py-2 font-semibold">Rollback</th>
            </tr>
          </thead>
          <tbody>
            {orderedVersions.map((version) => {
              const isActive = version.version === activeVersion;
              const isHistorical = version.version < activeVersion;
              const checked = selectedVersion === version.version;
              return (
                <tr
                  className={cn(
                    "border-t border-border align-top",
                    checked ? "bg-accent" : "",
                    isActive ? "bg-primary/5" : "",
                  )}
                  key={version.version}
                >
                  <td className="px-3 py-2 font-medium text-foreground">
                    v{version.version}
                    {isActive ? <span className="ml-1 text-primary">(active)</span> : null}
                  </td>
                  <td className="px-3 py-2">
                    <VersionStatusBadge status={version.status} />
                  </td>
                  <td className="break-words px-3 py-2 text-muted-foreground">{version.change_reason ?? "-"}</td>
                  <td className="px-3 py-2 text-muted-foreground">{version.generated_by ?? "-"}</td>
                  <td className="px-3 py-2 text-muted-foreground">{formatTimestamp(version.created_at)}</td>
                  <td className="px-3 py-2 font-mono text-muted-foreground">{shortSha(version.content_sha256)}</td>
                  <td className="px-3 py-2">
                    <button
                      aria-pressed={checked}
                      className="inline-flex items-center gap-1 rounded-md border border-border bg-card px-2 py-1 text-xs font-medium text-foreground hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                      disabled={!isHistorical}
                      onClick={() => setSelectedVersion(version.version)}
                      type="button"
                    >
                      <CheckCircle2 className="h-3 w-3" />
                      Select
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="rounded-md border border-border bg-card p-3">
        <div className="grid gap-3 lg:grid-cols-[1fr_auto] lg:items-end">
          <div>
            <label className="text-xs font-medium uppercase text-muted-foreground" htmlFor="dashboard-rollback-reason">
              Rollback reason
            </label>
            <textarea
              className="mt-2 min-h-20 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-ring focus:ring-1 focus:ring-ring/30"
              id="dashboard-rollback-reason"
              onChange={(event) => setReason(event.target.value)}
              value={reason}
            />
          </div>
          <button
            className="inline-flex items-center justify-center gap-2 rounded-md border border-primary bg-primary/10 px-3 py-2 text-sm font-medium text-primary hover:bg-primary/20 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!canRollback}
            onClick={runRollback}
            type="button"
          >
            <RotateCcw className="h-4 w-4" />
            Rollback
          </button>
        </div>
        <p className="mt-2 text-xs text-muted-foreground" data-testid="dashboard-rollback-selection">
          Target version: {selected ? `v${selected.version}` : "-"}
        </p>
      </div>
    </section>
  );
}

export default DashboardChangeLog;
