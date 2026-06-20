import React, { useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  CopyPlus,
  GitCompareArrows,
  LayoutDashboard,
  XCircle,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import type { DashboardRecipeV2 } from "@/lib/bff-v1/agora/types";
import { cn } from "@/lib/utils";

import type { ChartDataRow } from "../widgets/ChartSpecRenderer";
import { WidgetRenderer } from "../widgets/WidgetRenderer";
import type { WidgetSensitivity } from "../widgets/registry";

type DashboardWidget = DashboardRecipeV2["views"][number]["widgets"][number];

export interface DashboardAcceptRequestBody {
  expected_version: number;
  note?: string;
}

export interface DashboardMutationRequestEnvelope<TBody> {
  recipe_id: string;
  headers: {
    "If-Match": string;
    "Idempotency-Key": string;
  };
  body: TBody;
}

export type DashboardAcceptAction = DashboardMutationRequestEnvelope<DashboardAcceptRequestBody>;

export interface DashboardRecipeDecisionContext {
  activeRecipe?: DashboardRecipeV2 | null;
  proposal: DashboardRecipeV2;
}

export interface DashboardConcurrencyDetails {
  expected_version?: number;
  current_version?: number;
  current_etag?: string;
  latest_href?: string;
  [key: string]: unknown;
}

export interface DashboardConcurrencyError {
  code?: string;
  message: string;
  details?: DashboardConcurrencyDetails;
}

export interface DashboardProposalPreviewProps {
  activeRecipe?: DashboardRecipeV2 | null;
  proposal?: DashboardRecipeV2 | null;
  etag?: string;
  expectedVersion?: number;
  idempotencyKey?: string;
  widgetData?: Record<string, ChartDataRow[]>;
  allowedSensitivities?: WidgetSensitivity[];
  concurrencyError?: DashboardConcurrencyError | null;
  onAccept?: (request: DashboardAcceptAction) => void | Promise<void>;
  onReject?: (context: DashboardRecipeDecisionContext) => void | Promise<void>;
  onKeepBoth?: (context: DashboardRecipeDecisionContext) => void | Promise<void>;
  className?: string;
}

interface RecipeSummary {
  views: number;
  widgets: number;
  chartKinds: string[];
}

interface ProposalDelta {
  added: DashboardWidget[];
  removed: DashboardWidget[];
  changed: Array<{ before: DashboardWidget; after: DashboardWidget }>;
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

function formatTimestamp(value: string | undefined): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toISOString().replace("T", " ").slice(0, 16) + "Z";
}

function shortSha(value: string | undefined): string {
  return value ? value.slice(0, 8) : "-";
}

function allWidgets(recipe?: DashboardRecipeV2 | null): DashboardWidget[] {
  return recipe?.views.flatMap((view) => view.widgets) ?? [];
}

function summarizeRecipe(recipe?: DashboardRecipeV2 | null): RecipeSummary {
  const widgets = allWidgets(recipe);
  const chartKinds = [...new Set(widgets.map((widget) => widget.chart_spec.kind))].sort();
  return {
    views: recipe?.views.length ?? 0,
    widgets: widgets.length,
    chartKinds,
  };
}

function summarizeDelta(activeRecipe?: DashboardRecipeV2 | null, proposal?: DashboardRecipeV2 | null): ProposalDelta {
  const before = new Map(allWidgets(activeRecipe).map((widget) => [widget.widget_id, widget]));
  const after = new Map(allWidgets(proposal).map((widget) => [widget.widget_id, widget]));
  const added: DashboardWidget[] = [];
  const removed: DashboardWidget[] = [];
  const changed: Array<{ before: DashboardWidget; after: DashboardWidget }> = [];

  for (const [widgetId, nextWidget] of after.entries()) {
    const previousWidget = before.get(widgetId);
    if (!previousWidget) {
      added.push(nextWidget);
      continue;
    }
    if (stableString(previousWidget) !== stableString(nextWidget)) {
      changed.push({ before: previousWidget, after: nextWidget });
    }
  }
  for (const [widgetId, previousWidget] of before.entries()) {
    if (!after.has(widgetId)) removed.push(previousWidget);
  }
  return { added, removed, changed };
}

function hasMatchingScope(activeRecipe?: DashboardRecipeV2 | null, proposal?: DashboardRecipeV2 | null): boolean {
  if (!activeRecipe || !proposal) return true;
  return (
    activeRecipe.strategy_id === proposal.strategy_id &&
    activeRecipe.workspace === proposal.workspace &&
    activeRecipe.phase === proposal.phase
  );
}

function RecipeStats({ label, recipe }: { label: string; recipe?: DashboardRecipeV2 | null }) {
  const summary = summarizeRecipe(recipe);
  return (
    <div className="rounded-md border border-slate-200 bg-white p-3" data-testid={`recipe-stats-${label.toLowerCase()}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-xs font-medium uppercase text-slate-500">{label}</div>
          <div className="mt-1 text-sm font-semibold text-slate-950">{recipe?.change_reason ?? "No recipe"}</div>
        </div>
        {recipe ? (
          <Badge className="border-slate-300 bg-slate-50 text-slate-700" variant="outline">
            v{recipe.version} {recipe.status}
          </Badge>
        ) : null}
      </div>
      <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-3">
        <div>
          <dt className="text-slate-500">Views</dt>
          <dd className="font-medium text-slate-950">{summary.views}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Widgets</dt>
          <dd className="font-medium text-slate-950">{summary.widgets}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Charts</dt>
          <dd className="font-medium text-slate-950">{summary.chartKinds.join(", ") || "-"}</dd>
        </div>
      </dl>
      <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
        <div>
          <span className="text-slate-500">Updated </span>
          <span className="font-medium text-slate-800">{formatTimestamp(recipe?.updated_at)}</span>
        </div>
        <div>
          <span className="text-slate-500">Hash </span>
          <span className="font-medium text-slate-800">{shortSha(recipe?.content_sha256)}</span>
        </div>
      </div>
    </div>
  );
}

function DeltaList({ delta }: { delta: ProposalDelta }) {
  const rows = [
    ...delta.added.map((widget) => ({ kind: "Added", label: widget.title, detail: widget.widget_type })),
    ...delta.removed.map((widget) => ({ kind: "Removed", label: widget.title, detail: widget.widget_type })),
    ...delta.changed.map(({ before, after }) => ({
      kind: "Changed",
      label: after.title,
      detail: `${before.chart_spec.kind} to ${after.chart_spec.kind}`,
    })),
  ];

  if (!rows.length) {
    return (
      <div className="rounded-md border border-slate-200 bg-white p-3 text-sm text-slate-600" data-testid="proposal-delta-empty">
        No visible DashboardRecipe field changes.
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-md border border-slate-200 bg-white" data-testid="proposal-delta-list">
      <table className="w-full border-collapse text-left text-xs">
        <thead className="bg-slate-100 text-slate-600">
          <tr>
            <th className="w-28 px-3 py-2 font-semibold">Change</th>
            <th className="px-3 py-2 font-semibold">Widget</th>
            <th className="px-3 py-2 font-semibold">Detail</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr className="border-t border-slate-200 align-top" key={`${row.kind}-${row.label}-${index}`}>
              <td className="px-3 py-2 font-medium text-slate-700">{row.kind}</td>
              <td className="break-words px-3 py-2 text-slate-950">{row.label}</td>
              <td className="break-words px-3 py-2 text-slate-600">{row.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function WidgetPreviewColumn({
  allowedSensitivities,
  data,
  label,
  recipe,
}: {
  allowedSensitivities?: WidgetSensitivity[];
  data?: Record<string, ChartDataRow[]>;
  label: string;
  recipe?: DashboardRecipeV2 | null;
}) {
  const widgets = allWidgets(recipe).slice(0, 2);
  return (
    <section className="space-y-3" data-testid={`widget-preview-${label.toLowerCase()}`}>
      <div className="flex items-center gap-2 text-sm font-semibold text-slate-950">
        <LayoutDashboard className="h-4 w-4" />
        {label}
      </div>
      {widgets.length ? (
        widgets.map((widget) => (
          <WidgetRenderer
            allowedSensitivities={allowedSensitivities}
            data={data?.[widget.widget_id]}
            key={widget.widget_id}
            widget={widget}
          />
        ))
      ) : (
        <div className="rounded-md border border-slate-200 bg-white p-3 text-sm text-slate-600">
          No widgets in this recipe snapshot.
        </div>
      )}
    </section>
  );
}

function ConcurrencyNotice({ error }: { error?: DashboardConcurrencyError | null }) {
  if (!error) return null;
  const details = error.details ?? {};
  return (
    <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950" data-testid="dashboard-concurrency-error">
      <div className="flex items-center gap-2 font-semibold">
        <AlertTriangle className="h-4 w-4" />
        {error.code ?? "CONCURRENT_MODIFICATION"}
      </div>
      <p className="mt-1">{error.message}</p>
      <dl className="mt-2 grid gap-2 text-xs sm:grid-cols-2">
        <div>
          <dt className="text-amber-800">Expected version</dt>
          <dd className="font-medium">{stableString(details.expected_version)}</dd>
        </div>
        <div>
          <dt className="text-amber-800">Current version</dt>
          <dd className="font-medium">{stableString(details.current_version)}</dd>
        </div>
        <div>
          <dt className="text-amber-800">Current ETag</dt>
          <dd className="break-all font-medium">{stableString(details.current_etag)}</dd>
        </div>
        <div>
          <dt className="text-amber-800">Latest</dt>
          <dd className="break-all font-medium">{stableString(details.latest_href)}</dd>
        </div>
      </dl>
    </div>
  );
}

export function DashboardProposalPreview({
  activeRecipe,
  allowedSensitivities,
  className,
  concurrencyError,
  etag,
  expectedVersion,
  idempotencyKey,
  onAccept,
  onKeepBoth,
  onReject,
  proposal,
  widgetData,
}: DashboardProposalPreviewProps) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"accept" | "reject" | "keepBoth" | null>(null);
  const delta = useMemo(() => summarizeDelta(activeRecipe, proposal), [activeRecipe, proposal]);
  const matchingScope = hasMatchingScope(activeRecipe, proposal);
  const acceptReady = Boolean(proposal && etag && matchingScope && proposal.status === "proposal" && onAccept);

  const runAccept = async () => {
    if (!proposal || !etag || !onAccept || !acceptReady) return;
    setBusy("accept");
    try {
      const body: DashboardAcceptRequestBody = {
        expected_version: expectedVersion ?? proposal.version,
      };
      const trimmedNote = note.trim();
      if (trimmedNote) body.note = trimmedNote;
      await onAccept({
        recipe_id: proposal.recipe_id,
        headers: {
          "If-Match": etag,
          "Idempotency-Key": makeIdempotencyKey(idempotencyKey),
        },
        body,
      });
    } finally {
      setBusy(null);
    }
  };

  const runDecision = async (kind: "reject" | "keepBoth") => {
    if (!proposal) return;
    const handler = kind === "reject" ? onReject : onKeepBoth;
    if (!handler) return;
    setBusy(kind);
    try {
      await handler({ activeRecipe, proposal });
    } finally {
      setBusy(null);
    }
  };

  if (!proposal) {
    return (
      <section className={cn("rounded-md border border-slate-200 bg-slate-50", className)} data-testid="dashboard-proposal-preview">
        <EmptyState
          icon={<GitCompareArrows className="h-5 w-5" />}
          title="No dashboard proposal"
          description="No DashboardRecipe proposal snapshot is available."
        />
      </section>
    );
  }

  return (
    <section className={cn("space-y-4 rounded-md border border-slate-200 bg-slate-50 p-4", className)} data-testid="dashboard-proposal-preview">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <GitCompareArrows className="h-4 w-4 text-slate-700" />
            <h2 className="text-base font-semibold text-slate-950">Dashboard Proposal Preview</h2>
          </div>
          <p className="mt-1 text-sm text-slate-600">{proposal.workspace} / {proposal.phase}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge className="border-slate-300 bg-white text-slate-700" variant="outline">
            {proposal.generated_by}
          </Badge>
          <Badge className="border-slate-300 bg-white text-slate-700" variant="outline">
            v{proposal.version}
          </Badge>
        </div>
      </div>

      {!matchingScope ? (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-900" data-testid="proposal-scope-mismatch">
          <div className="flex items-center gap-2 font-semibold">
            <AlertTriangle className="h-4 w-4" />
            Proposal scope mismatch
          </div>
          <p className="mt-1">The proposal strategy, workspace, or phase differs from the active recipe snapshot.</p>
        </div>
      ) : null}

      <ConcurrencyNotice error={concurrencyError} />

      <div className="grid gap-3 lg:grid-cols-2">
        <RecipeStats label="Before" recipe={activeRecipe} />
        <RecipeStats label="After" recipe={proposal} />
      </div>

      <DeltaList delta={delta} />

      <div className="grid gap-4 lg:grid-cols-2" data-testid="dashboard-before-after-preview">
        <WidgetPreviewColumn allowedSensitivities={allowedSensitivities} data={widgetData} label="Before" recipe={activeRecipe} />
        <WidgetPreviewColumn allowedSensitivities={allowedSensitivities} data={widgetData} label="After" recipe={proposal} />
      </div>

      <div className="rounded-md border border-slate-200 bg-white p-3">
        <label className="text-xs font-medium uppercase text-slate-500" htmlFor="dashboard-proposal-note">
          Note
        </label>
        <textarea
          className="mt-2 min-h-20 w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-950 outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
          id="dashboard-proposal-note"
          onChange={(event) => setNote(event.target.value)}
          value={note}
        />
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            className="inline-flex items-center gap-2 rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-800 hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!acceptReady || busy !== null}
            onClick={runAccept}
            type="button"
          >
            <CheckCircle2 className="h-4 w-4" />
            Accept
          </button>
          <button
            className="inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!onKeepBoth || busy !== null}
            onClick={() => runDecision("keepBoth")}
            type="button"
          >
            <CopyPlus className="h-4 w-4" />
            Keep both
          </button>
          <button
            className="inline-flex items-center gap-2 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm font-medium text-red-800 hover:bg-red-100 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!onReject || busy !== null}
            onClick={() => runDecision("reject")}
            type="button"
          >
            <XCircle className="h-4 w-4" />
            Reject
          </button>
        </div>
      </div>
    </section>
  );
}

export default DashboardProposalPreview;
