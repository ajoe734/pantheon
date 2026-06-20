import React, { useCallback, useEffect, useMemo, useState, type RefObject } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Columns3,
  CopyPlus,
  RefreshCw,
  Send,
  X,
  XCircle,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import type { AgoraWidgetValidationResult } from "@/lib/bff-v1/agora/dashboard";
import { validateAgoraWidget } from "@/lib/bff-v1/agora/dashboard";
import type { WidgetSpecV2 } from "@/lib/bff-v1/agora/types";
import { cn } from "@/lib/utils";

import type { ChartDataRow } from "./ChartSpecRenderer";
import { WidgetRenderer } from "./WidgetRenderer";
import type { WidgetSensitivity } from "./registry";

export interface WidgetRevisionRequest {
  widget: WidgetSpecV2;
  instruction: string;
}

export interface WidgetRevisionProposal {
  widget: WidgetSpecV2;
  assistantMessage?: string;
  changeReason?: string;
}

export interface WidgetRevisionDecision {
  baseWidget: WidgetSpecV2;
  proposedWidget: WidgetSpecV2;
  instruction: string;
  validation: AgoraWidgetValidationResult;
}

export interface WidgetRevisionDrawerProps {
  open: boolean;
  widget?: WidgetSpecV2 | null;
  data?: ChartDataRow[];
  proposedData?: ChartDataRow[];
  allowedSensitivities?: WidgetSensitivity[];
  onOpenChange: (open: boolean) => void;
  onRequestRevision: (request: WidgetRevisionRequest) => Promise<WidgetRevisionProposal | WidgetSpecV2>;
  onAccept?: (decision: WidgetRevisionDecision) => void | Promise<void>;
  onKeepBoth?: (decision: WidgetRevisionDecision) => void | Promise<void>;
  onReject?: (decision: Omit<WidgetRevisionDecision, "validation"> & { validation?: AgoraWidgetValidationResult }) => void | Promise<void>;
  validateWidget?: (widget: WidgetSpecV2) => Promise<AgoraWidgetValidationResult>;
  triggerRef?: RefObject<HTMLElement>;
}

type RevisionPhase = "idle" | "requesting" | "ready" | "invalid" | "error";

interface DiffRow {
  label: string;
  before: string;
  after: string;
}

function resultWidget(result: WidgetRevisionProposal | WidgetSpecV2): WidgetSpecV2 {
  return "widget" in result ? result.widget : result;
}

function resultAssistantMessage(result: WidgetRevisionProposal | WidgetSpecV2): string | undefined {
  return "widget" in result ? result.assistantMessage : undefined;
}

function resultChangeReason(result: WidgetRevisionProposal | WidgetSpecV2): string | undefined {
  return "widget" in result ? result.changeReason : undefined;
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

function interactionSummary(widget: WidgetSpecV2): string {
  const kinds = [
    ...(widget.interactions ?? []).map((interaction) => interaction.kind),
    widget.chart_spec.click_action?.kind,
  ].filter(Boolean);
  return kinds.length ? kinds.join(", ") : "-";
}

function summarizeWidgetChanges(before: WidgetSpecV2, after: WidgetSpecV2): DiffRow[] {
  const rows: DiffRow[] = [
    { label: "Title", before: before.title, after: after.title },
    { label: "Widget type", before: before.widget_type, after: after.widget_type },
    { label: "Data source", before: before.data_source_id, after: after.data_source_id },
    { label: "Chart kind", before: before.chart_spec.kind, after: after.chart_spec.kind },
    { label: "Query filters", before: stableString(before.query.filters), after: stableString(after.query.filters) },
    { label: "Query window", before: stableString(before.query.window), after: stableString(after.query.window) },
    { label: "Query limit", before: stableString(before.query.limit), after: stableString(after.query.limit) },
    { label: "Transforms", before: stableString(before.chart_spec.transforms ?? []), after: stableString(after.chart_spec.transforms ?? []) },
    { label: "Interactions", before: interactionSummary(before), after: interactionSummary(after) },
    { label: "Sensitivity", before: before.sensitivity, after: after.sensitivity },
  ];
  return rows.filter((row) => row.before !== row.after);
}

function ValidationBadge({ validation }: { validation?: AgoraWidgetValidationResult }) {
  if (!validation) return null;
  return validation.valid ? (
    <Badge className="border-emerald-300 bg-emerald-50 text-emerald-800" variant="outline">
      <CheckCircle2 className="mr-1 h-3 w-3" />
      validated
    </Badge>
  ) : (
    <Badge className="border-red-300 bg-red-50 text-red-800" variant="outline">
      <XCircle className="mr-1 h-3 w-3" />
      invalid
    </Badge>
  );
}

function ValidationIssues({ validation }: { validation?: AgoraWidgetValidationResult }) {
  if (!validation || validation.valid) return null;
  return (
    <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-900" data-testid="widget-validation-errors">
      <div className="flex items-center gap-2 font-semibold">
        <AlertTriangle className="h-4 w-4" />
        Validation failed
      </div>
      <ul className="mt-2 space-y-1">
        {validation.errors.map((issue, index) => (
          <li key={`${issue.code}-${issue.path ?? index}`}>
            <span className="font-medium">{issue.code}</span>
            {issue.path ? <span> at {issue.path}</span> : null}
            <span>: {issue.message}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function DiffTable({ rows }: { rows: DiffRow[] }) {
  if (!rows.length) {
    return (
      <div className="rounded-md border border-slate-200 bg-white p-3 text-sm text-slate-600" data-testid="widget-diff-empty">
        No visible WidgetSpec field changes.
      </div>
    );
  }
  return (
    <div className="overflow-hidden rounded-md border border-slate-200" data-testid="widget-diff-table">
      <table className="w-full border-collapse text-left text-xs">
        <thead className="bg-slate-100 text-slate-600">
          <tr>
            <th className="w-36 px-3 py-2 font-semibold">Field</th>
            <th className="px-3 py-2 font-semibold">Before</th>
            <th className="px-3 py-2 font-semibold">After</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr className="border-t border-slate-200 align-top" key={row.label}>
              <td className="px-3 py-2 font-medium text-slate-700">{row.label}</td>
              <td className="break-words px-3 py-2 text-slate-600">{row.before}</td>
              <td className="break-words px-3 py-2 text-slate-950">{row.after}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function WidgetRevisionDrawer({
  allowedSensitivities,
  data,
  onAccept,
  onKeepBoth,
  onOpenChange,
  onReject,
  onRequestRevision,
  open,
  proposedData,
  triggerRef,
  validateWidget = validateAgoraWidget,
  widget,
}: WidgetRevisionDrawerProps) {
  const [instruction, setInstruction] = useState("");
  const [proposal, setProposal] = useState<WidgetSpecV2 | null>(null);
  const [assistantMessage, setAssistantMessage] = useState<string | undefined>();
  const [changeReason, setChangeReason] = useState<string | undefined>();
  const [validation, setValidation] = useState<AgoraWidgetValidationResult | undefined>();
  const [phase, setPhase] = useState<RevisionPhase>("idle");
  const [error, setError] = useState<string | undefined>();
  const [decisionBusy, setDecisionBusy] = useState<"accept" | "keepBoth" | "reject" | null>(null);

  useEffect(() => {
    if (!open) return;
    setInstruction("");
    setProposal(null);
    setAssistantMessage(undefined);
    setChangeReason(undefined);
    setValidation(undefined);
    setPhase("idle");
    setError(undefined);
    setDecisionBusy(null);
  }, [open, widget?.widget_id]);

  const diffRows = useMemo(
    () => (widget && proposal ? summarizeWidgetChanges(widget, proposal) : []),
    [proposal, widget],
  );

  const decision = useMemo<WidgetRevisionDecision | null>(() => {
    if (!widget || !proposal || !validation?.valid) return null;
    return {
      baseWidget: widget,
      proposedWidget: proposal,
      instruction: instruction.trim(),
      validation,
    };
  }, [instruction, proposal, validation, widget]);

  const submitRevision = useCallback(async (event: React.FormEvent) => {
    event.preventDefault();
    const text = instruction.trim();
    if (!widget || !text) return;

    setPhase("requesting");
    setError(undefined);
    setValidation(undefined);
    setProposal(null);
    setAssistantMessage(undefined);
    setChangeReason(undefined);

    try {
      const revisionResult = await onRequestRevision({ widget, instruction: text });
      const nextWidget = resultWidget(revisionResult);
      const validateResult = await validateWidget(nextWidget);
      setProposal(nextWidget);
      setAssistantMessage(resultAssistantMessage(revisionResult));
      setChangeReason(resultChangeReason(revisionResult));
      setValidation(validateResult);
      setPhase(validateResult.valid ? "ready" : "invalid");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Widget revision failed");
      setPhase("error");
    }
  }, [instruction, onRequestRevision, validateWidget, widget]);

  const runDecision = useCallback(async (
    kind: "accept" | "keepBoth" | "reject",
    handler?: (decision: WidgetRevisionDecision) => void | Promise<void>,
  ) => {
    if (!decision || !handler) return;
    setDecisionBusy(kind);
    try {
      await handler(decision);
    } finally {
      setDecisionBusy(null);
    }
  }, [decision]);

  const rejectProposal = useCallback(async () => {
    if (!widget || !proposal) return;
    setDecisionBusy("reject");
    try {
      await onReject?.({
        baseWidget: widget,
        proposedWidget: proposal,
        instruction: instruction.trim(),
        validation,
      });
    } finally {
      setDecisionBusy(null);
    }
  }, [instruction, onReject, proposal, validation, widget]);

  const canPreview = Boolean(proposal && validation?.valid);
  const canSubmit = Boolean(widget && instruction.trim()) && phase !== "requesting";
  const afterData = proposedData ?? data;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        aria-label="Widget revision drawer"
        className="w-full overflow-y-auto sm:max-w-5xl"
        onCloseAutoFocus={(event) => {
          const el = triggerRef?.current;
          if (el?.isConnected) {
            event.preventDefault();
            el.focus();
          }
        }}
        side="right"
      >
        <SheetHeader>
          <div className="flex items-start justify-between gap-3">
            <div>
              <SheetTitle className="flex items-center gap-2 text-base">
                <Columns3 className="h-4 w-4" />
                Widget Revision
              </SheetTitle>
              <SheetDescription>
                {widget ? `${widget.title} - ${widget.widget_type}` : "No widget selected"}
              </SheetDescription>
            </div>
            <button
              aria-label="Close widget revision drawer"
              className="rounded-md border border-slate-300 bg-white p-2 text-slate-600 hover:bg-slate-100"
              onClick={() => onOpenChange(false)}
              type="button"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </SheetHeader>

        {!widget ? (
          <EmptyState
            description="Select a registry-backed widget before requesting a revision."
            icon={<AlertTriangle className="h-8 w-8" />}
            title="No widget selected"
          />
        ) : (
          <div className="mt-5 space-y-5 text-sm" data-testid="widget-revision-drawer-body">
            <form className="space-y-3" onSubmit={submitRevision}>
              <label className="block text-xs font-semibold uppercase text-slate-500" htmlFor="widget-revision-instruction">
                Instruction
              </label>
              <textarea
                className="min-h-24 w-full rounded-md border border-slate-300 bg-white p-3 text-sm text-slate-950 outline-none focus:border-slate-700 focus:ring-2 focus:ring-slate-200"
                id="widget-revision-instruction"
                onChange={(event) => setInstruction(event.target.value)}
                placeholder="Example: show the candidate score as a table and put evidence links first"
                value={instruction}
              />
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="outline">{widget.registry_version}</Badge>
                  <Badge variant="outline">{widget.data_source_id}</Badge>
                  <ValidationBadge validation={validation} />
                </div>
                <button
                  className={cn(
                    "inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm font-semibold",
                    canSubmit
                      ? "border-slate-900 bg-slate-950 text-white hover:bg-slate-800"
                      : "border-slate-200 bg-slate-100 text-slate-400",
                  )}
                  disabled={!canSubmit}
                  type="submit"
                >
                  {phase === "requesting" ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                  Request
                </button>
              </div>
            </form>

            {error ? (
              <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-900" role="alert">
                {error}
              </div>
            ) : null}
            <ValidationIssues validation={validation} />

            {assistantMessage || changeReason ? (
              <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700">
                {assistantMessage ? <p>{assistantMessage}</p> : null}
                {changeReason ? <p className="mt-2 font-medium text-slate-950">{changeReason}</p> : null}
              </div>
            ) : null}

            <div className="grid gap-4 lg:grid-cols-2" data-testid="widget-before-after-preview">
              <section className="space-y-2">
                <h3 className="text-sm font-semibold text-slate-950">Before</h3>
                <WidgetRenderer
                  allowedSensitivities={allowedSensitivities}
                  data={data}
                  widget={widget}
                />
              </section>
              <section className="space-y-2">
                <h3 className="text-sm font-semibold text-slate-950">After</h3>
                {canPreview && proposal ? (
                  <WidgetRenderer
                    allowedSensitivities={allowedSensitivities}
                    data={afterData}
                    widget={proposal}
                  />
                ) : (
                  <div className="flex min-h-40 items-center justify-center rounded-md border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
                    {phase === "requesting" ? "Waiting for validated WidgetSpec." : "No validated revision yet."}
                  </div>
                )}
              </section>
            </div>

            {proposal ? <DiffTable rows={diffRows} /> : null}

            <div className="flex flex-wrap justify-end gap-2 border-t border-slate-200 pt-4">
              <button
                className="inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-100 disabled:text-slate-400"
                disabled={!proposal || !onReject || decisionBusy === "reject"}
                onClick={rejectProposal}
                type="button"
              >
                <XCircle className="h-4 w-4" />
                Reject
              </button>
              <button
                className="inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-100 disabled:text-slate-400"
                disabled={!decision || !onKeepBoth || decisionBusy === "keepBoth"}
                onClick={() => runDecision("keepBoth", onKeepBoth)}
                type="button"
              >
                <CopyPlus className="h-4 w-4" />
                Keep both
              </button>
              <button
                className="inline-flex items-center gap-2 rounded-md border border-emerald-700 bg-emerald-700 px-3 py-2 text-sm font-semibold text-white hover:bg-emerald-800 disabled:border-slate-200 disabled:bg-slate-100 disabled:text-slate-400"
                disabled={!decision || !onAccept || decisionBusy === "accept"}
                onClick={() => runDecision("accept", onAccept)}
                type="button"
              >
                <CheckCircle2 className="h-4 w-4" />
                Accept
              </button>
            </div>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

export default WidgetRevisionDrawer;
