import React from "react";

import type { WidgetSpecV2 } from "@/lib/bff-v1/agora/types";

import ChartSpecRenderer, { type ChartDataRow, type ChartInteraction } from "./ChartSpecRenderer";
import {
  type WidgetRegistryEntry,
  type WidgetSensitivity,
  validateWidgetSpecAgainstRegistry,
} from "./registry";

export type WidgetInteraction = ChartInteraction | NonNullable<WidgetSpecV2["interactions"]>[number];

export interface WidgetRendererProps {
  widget: WidgetSpecV2;
  data?: ChartDataRow[];
  allowedSensitivities?: WidgetSensitivity[];
  className?: string;
  onInteraction?: (interaction: WidgetInteraction, context: { widget: WidgetSpecV2; entry: WidgetRegistryEntry }) => void;
}

const DEFAULT_ALLOWED_SENSITIVITIES: WidgetSensitivity[] = ["public_market", "user_private"];

function canReadSensitivity(required: WidgetSensitivity, allowed: WidgetSensitivity[]): boolean {
  return allowed.includes(required);
}

function WidgetError({ title, message }: { title: string; message: string }) {
  return (
    <div className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-900" data-testid="widget-renderer-error">
      <div className="font-semibold">{title}</div>
      <div className="mt-1">{message}</div>
    </div>
  );
}

function WidgetShell({
  children,
  className = "",
  entry,
  widget,
}: {
  children: React.ReactNode;
  className?: string;
  entry: WidgetRegistryEntry;
  widget: WidgetSpecV2;
}) {
  return (
    <section className={`rounded-md border border-slate-200 bg-slate-50 p-3 ${className}`} data-testid="widget-renderer">
      <header className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-950">{widget.title}</h3>
          {widget.description ? <p className="mt-1 text-xs text-slate-600">{widget.description}</p> : null}
        </div>
        <div className="shrink-0 rounded border border-slate-200 bg-white px-2 py-1 text-[11px] font-medium text-slate-600">
          {entry.display_name}
        </div>
      </header>
      {children}
    </section>
  );
}

function BuiltinWidgetRenderer({
  data = [],
  entry,
  onInteraction,
  widget,
}: {
  data?: ChartDataRow[];
  entry: WidgetRegistryEntry;
  onInteraction?: (interaction: WidgetInteraction, context: { widget: WidgetSpecV2; entry: WidgetRegistryEntry }) => void;
  widget: WidgetSpecV2;
}) {
  const rows = data.slice(0, 6);
  return (
    <div className="space-y-3" data-testid="widget-renderer-builtin">
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="rounded border border-slate-200 bg-white p-3">
          <div className="text-xs font-medium uppercase text-slate-500">Data Source</div>
          <div className="mt-1 text-sm text-slate-950">{widget.data_source_id}</div>
        </div>
        <div className="rounded border border-slate-200 bg-white p-3">
          <div className="text-xs font-medium uppercase text-slate-500">Sensitivity</div>
          <div className="mt-1 text-sm text-slate-950">{widget.sensitivity}</div>
        </div>
      </div>
      {rows.length ? (
        <ul className="space-y-2">
          {rows.map((row, index) => (
            <li className="rounded border border-slate-200 bg-white p-2 text-xs text-slate-700" key={index}>
              {Object.entries(row)
                .slice(0, 4)
                .map(([key, value]) => `${key}: ${String(value ?? "-")}`)
                .join(" | ")}
            </li>
          ))}
        </ul>
      ) : null}
      {widget.interactions.length ? (
        <div className="flex flex-wrap gap-2">
          {widget.interactions.map((interaction) => (
            <button
              className="rounded border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100"
              key={interaction.kind}
              onClick={() => onInteraction?.(interaction, { widget, entry })}
              type="button"
            >
              {interaction.kind}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function WidgetRenderer({
  allowedSensitivities = DEFAULT_ALLOWED_SENSITIVITIES,
  className,
  data,
  onInteraction,
  widget,
}: WidgetRendererProps) {
  const validation = validateWidgetSpecAgainstRegistry(widget);
  if (!validation.ok) {
    return <WidgetError message={validation.message} title={validation.code} />;
  }

  const { entry } = validation;
  if (!canReadSensitivity(entry.sensitivity, allowedSensitivities) || !canReadSensitivity(widget.sensitivity, allowedSensitivities)) {
    return (
      <WidgetError
        message={`Current user scope cannot read ${widget.sensitivity} widget data.`}
        title="SENSITIVITY_SCOPE_DENIED"
      />
    );
  }

  return (
    <WidgetShell className={className} entry={entry} widget={widget}>
      {entry.renderer === "builtin" ? (
        <BuiltinWidgetRenderer data={data} entry={entry} onInteraction={onInteraction} widget={widget} />
      ) : (
        <ChartSpecRenderer
          data={data}
          onInteraction={(interaction) => onInteraction?.(interaction, { widget, entry })}
          spec={widget.chart_spec}
        />
      )}
    </WidgetShell>
  );
}

export default WidgetRenderer;
