import React, { useMemo } from "react";
import type { EChartsOption } from "echarts";
import ReactECharts from "echarts-for-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { ChartSpecV1 } from "@/lib/bff-v1/agora/types";

import {
  chartRendererForKind,
  isWidgetInteractionKind,
  type ChartSpecKind,
  type WidgetInteractionKind,
  validateChartSpecGrammar,
} from "./registry";

export type ChartDataRow = Record<string, unknown>;
export type ChartInteraction = NonNullable<ChartSpecV1["click_action"]>;

export interface ChartSpecRendererProps {
  spec: ChartSpecV1;
  data?: ChartDataRow[];
  height?: number;
  onInteraction?: (interaction: ChartInteraction) => void;
}

const UNSAFE_KEY_PATTERN = /(callback|dangerouslySetInnerHTML|eval|formatter|function|html|iframe|on[A-Z]|script)/u;
const UNSAFE_STRING_PATTERN = /(<\s*script|<\s*iframe|<\s*object|<\s*embed|javascript:|data:text\/html|eval\s*\(|new\s+Function|function\s*\(|=>)/iu;

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function hasUnsafeRenderableValue(value: unknown): boolean {
  if (typeof value === "string") {
    return UNSAFE_STRING_PATTERN.test(value);
  }
  if (Array.isArray(value)) {
    return value.some((entry) => hasUnsafeRenderableValue(entry));
  }
  if (isPlainRecord(value)) {
    return Object.entries(value).some(([key, entry]) => UNSAFE_KEY_PATTERN.test(key) || hasUnsafeRenderableValue(entry));
  }
  return false;
}

export function validateChartSpecForRendering(spec: ChartSpecV1): string | null {
  const grammarFailure = validateChartSpecGrammar(spec);
  if (grammarFailure) {
    return grammarFailure.message;
  }
  if (hasUnsafeRenderableValue(spec.options) || hasUnsafeRenderableValue(spec.transforms) || hasUnsafeRenderableValue(spec.click_action)) {
    return "ChartSpec contains renderer-unsafe callback, HTML, script, or executable code markers.";
  }
  return null;
}

function asRows(data: ChartDataRow[] | undefined): ChartDataRow[] {
  return Array.isArray(data) ? data.filter(isPlainRecord) : [];
}

function fieldFor(spec: ChartSpecV1, channel: string, fallback?: string): string | undefined {
  const encoding = spec.encodings[channel];
  return encoding?.field || fallback;
}

function firstField(spec: ChartSpecV1, channels: string[], fallback?: string): string | undefined {
  for (const channel of channels) {
    const field = fieldFor(spec, channel);
    if (field) return field;
  }
  return fallback;
}

function valueFrom(row: ChartDataRow | undefined, field: string | undefined): unknown {
  if (!row || !field) return undefined;
  return row[field];
}

function numberFrom(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return 0;
}

function textFrom(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function formatValue(value: unknown): string {
  if (typeof value === "number") {
    return Number.isInteger(value) ? value.toLocaleString() : value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  }
  return textFrom(value) || "-";
}

function chartTitle(spec: ChartSpecV1): string {
  const label = firstField(spec, ["label", "y", "value", "x"]);
  return label || spec.kind;
}

function ChartFrame({
  children,
  interaction,
  onInteraction,
}: {
  children: React.ReactNode;
  interaction?: ChartInteraction;
  onInteraction?: (interaction: ChartInteraction) => void;
}) {
  const clickable = Boolean(interaction && onInteraction && isWidgetInteractionKind(interaction.kind));
  return (
    <div
      className="h-full min-h-[180px] rounded-md border border-slate-200 bg-white p-3 text-slate-950"
      data-testid="chart-spec-renderer"
      onClick={clickable ? () => onInteraction?.(interaction as ChartInteraction) : undefined}
      onKeyDown={
        clickable
          ? (event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onInteraction?.(interaction as ChartInteraction);
              }
            }
          : undefined
      }
      role={clickable ? "button" : undefined}
      tabIndex={clickable ? 0 : undefined}
    >
      {children}
    </div>
  );
}

function ChartNotice({ message }: { message: string }) {
  return (
    <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900" data-testid="chart-render-notice">
      {message}
    </div>
  );
}

function MetricRenderer({ rows, spec }: { rows: ChartDataRow[]; spec: ChartSpecV1 }) {
  const valueField = firstField(spec, ["value", "y", "x"]);
  const labelField = firstField(spec, ["label", "x"]);
  const firstRow = rows[0];
  return (
    <div className="flex h-full min-h-[140px] flex-col justify-center gap-2" data-testid="chart-renderer-recharts">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{formatValue(valueFrom(firstRow, labelField) || chartTitle(spec))}</div>
      <div className="text-4xl font-semibold text-slate-950">{formatValue(valueFrom(firstRow, valueField))}</div>
    </div>
  );
}

function RechartsRenderer({ rows, spec, height }: { rows: ChartDataRow[]; spec: ChartSpecV1; height: number }) {
  if (spec.kind === "metric") {
    return <MetricRenderer rows={rows} spec={spec} />;
  }

  const xField = firstField(spec, ["x", "time", "label"], "label") ?? "label";
  const yField = firstField(spec, ["y", "value"], "value") ?? "value";
  const color = "#2563eb";
  const common = (
    <>
      <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" />
      <XAxis dataKey={xField} tick={{ fill: "#475569", fontSize: 11 }} />
      <YAxis tick={{ fill: "#475569", fontSize: 11 }} />
      <Tooltip />
    </>
  );

  return (
    <div data-testid="chart-renderer-recharts" style={{ height }}>
      <ResponsiveContainer height="100%" width="100%">
        {spec.kind === "area" ? (
          <AreaChart data={rows}>
            {common}
            <Area dataKey={yField} fill="#bfdbfe" stroke={color} type="monotone" />
          </AreaChart>
        ) : spec.kind === "bar" ? (
          <BarChart data={rows}>
            {common}
            <Bar dataKey={yField} fill={color} />
          </BarChart>
        ) : (
          <LineChart data={rows}>
            {common}
            <Line dataKey={yField} dot={false} stroke={color} strokeWidth={2} type="monotone" />
          </LineChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}

function echartOptionFor(kind: ChartSpecKind, spec: ChartSpecV1, rows: ChartDataRow[]): EChartsOption {
  const xField = firstField(spec, ["x", "time", "label"], "x");
  const yField = firstField(spec, ["y", "value"], "y");
  const valueField = firstField(spec, ["value", "size", "y"], "value");
  const sourceField = firstField(spec, ["source"], "source");
  const targetField = firstField(spec, ["target"], "target");

  if (kind === "network" || kind === "sankey") {
    const nodes = new Map<string, { name: string }>();
    const links = rows.map((row) => {
      const source = textFrom(valueFrom(row, sourceField));
      const target = textFrom(valueFrom(row, targetField));
      if (source) nodes.set(source, { name: source });
      if (target) nodes.set(target, { name: target });
      return { source, target, value: numberFrom(valueFrom(row, valueField)) || 1 };
    }).filter((link) => link.source && link.target);
    return {
      tooltip: {},
      series: [
        kind === "sankey"
          ? { type: "sankey", data: [...nodes.values()], links }
          : { type: "graph", layout: "force", roam: false, data: [...nodes.values()], links },
      ],
    };
  }

  if (kind === "candlestick") {
    const open = firstField(spec, ["open"], "open");
    const high = firstField(spec, ["high"], "high");
    const low = firstField(spec, ["low"], "low");
    const close = firstField(spec, ["close"], "close");
    return {
      xAxis: { type: "category", data: rows.map((row) => textFrom(valueFrom(row, xField))) },
      yAxis: { scale: true },
      tooltip: { trigger: "axis" },
      series: [
        {
          type: "candlestick",
          data: rows.map((row) => [
            numberFrom(valueFrom(row, open)),
            numberFrom(valueFrom(row, close)),
            numberFrom(valueFrom(row, low)),
            numberFrom(valueFrom(row, high)),
          ]),
        },
      ],
    };
  }

  if (kind === "gauge") {
    return {
      series: [
        {
          type: "gauge",
          progress: { show: true },
          data: [{ value: numberFrom(valueFrom(rows[0], valueField)), name: chartTitle(spec) }],
        },
      ],
    };
  }

  if (kind === "heatmap") {
    return {
      xAxis: { type: "category" },
      yAxis: { type: "category" },
      visualMap: { min: 0, max: Math.max(...rows.map((row) => numberFrom(valueFrom(row, valueField))), 1) },
      tooltip: {},
      series: [
        {
          type: "heatmap",
          data: rows.map((row) => [
            textFrom(valueFrom(row, xField)),
            textFrom(valueFrom(row, yField)),
            numberFrom(valueFrom(row, valueField)),
          ]),
        },
      ],
    };
  }

  // scatter — supports the A3 grammar `size` encoding channel for bubble charts
  const sizeField = firstField(spec, ["size"], undefined);
  const maxSizeValue = sizeField
    ? Math.max(...rows.map((row) => numberFrom(valueFrom(row, sizeField))), 1)
    : 1;
  return {
    xAxis: { type: "value" },
    yAxis: { type: "value" },
    tooltip: { trigger: "item" },
    series: [
      {
        type: "scatter",
        symbolSize: sizeField
          ? (d: number[]) => Math.max(8, (d[2] / maxSizeValue) * 40)
          : 8,
        data: rows.map((row) => {
          const base: number[] = [numberFrom(valueFrom(row, xField)), numberFrom(valueFrom(row, yField))];
          if (sizeField) base.push(numberFrom(valueFrom(row, sizeField)));
          return base;
        }),
      },
    ],
  } as EChartsOption;
}

function EChartsRenderer({ rows, spec, height }: { rows: ChartDataRow[]; spec: ChartSpecV1; height: number }) {
  const option = useMemo(() => echartOptionFor(spec.kind, spec, rows), [rows, spec]);
  return (
    <div data-testid="chart-renderer-echarts">
      <ReactECharts lazyUpdate notMerge option={option} style={{ height }} />
    </div>
  );
}

function TableRenderer({ rows, spec }: { rows: ChartDataRow[]; spec: ChartSpecV1 }) {
  const fields = Object.values(spec.encodings)
    .map((encoding) => encoding.field)
    .filter((field, index, all): field is string => Boolean(field) && all.indexOf(field) === index);
  const columns = fields.length ? fields : Object.keys(rows[0] ?? {}).slice(0, 8);
  return (
    <div className="overflow-auto" data-testid="chart-renderer-builtin">
      <table className="w-full border-collapse text-left text-sm">
        <thead>
          <tr>
            {columns.map((column) => (
              <th className="border-b border-slate-200 px-2 py-1 font-medium text-slate-600" key={column}>
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 25).map((row, rowIndex) => (
            <tr key={rowIndex}>
              {columns.map((column) => (
                <td className="border-b border-slate-100 px-2 py-1 text-slate-900" key={column}>
                  {formatValue(row[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TimelineRenderer({ rows, spec }: { rows: ChartDataRow[]; spec: ChartSpecV1 }) {
  const timeField = firstField(spec, ["time", "x"], "time");
  const labelField = firstField(spec, ["label", "y"], "label");
  return (
    <ol className="space-y-2" data-testid="chart-renderer-builtin">
      {rows.slice(0, 25).map((row, index) => (
        <li className="rounded border border-slate-200 px-3 py-2" key={index}>
          <div className="text-xs text-slate-500">{formatValue(valueFrom(row, timeField))}</div>
          <div className="text-sm font-medium text-slate-950">{formatValue(valueFrom(row, labelField))}</div>
        </li>
      ))}
    </ol>
  );
}

function StackedBarFallback({ rows, spec }: { rows: ChartDataRow[]; spec: ChartSpecV1 }) {
  const labelField = firstField(spec, ["x", "label"], "label");
  const valueField = firstField(spec, ["value", "y"], "value");
  const max = Math.max(...rows.map((row) => numberFrom(valueFrom(row, valueField))), 1);
  return (
    <div className="space-y-2" data-testid="chart-renderer-builtin">
      {rows.slice(0, 20).map((row, index) => {
        const value = numberFrom(valueFrom(row, valueField));
        return (
          <div className="grid grid-cols-[minmax(96px,1fr)_3fr_auto] items-center gap-2 text-sm" key={index}>
            <span className="truncate text-slate-600">{formatValue(valueFrom(row, labelField))}</span>
            <span className="h-2 rounded bg-slate-100">
              <span className="block h-2 rounded bg-blue-600" style={{ width: `${Math.max((value / max) * 100, 2)}%` }} />
            </span>
            <span className="tabular-nums text-slate-900">{formatValue(value)}</span>
          </div>
        );
      })}
    </div>
  );
}

function BuiltinChartRenderer({ rows, spec }: { rows: ChartDataRow[]; spec: ChartSpecV1 }) {
  if (spec.kind === "timeline") {
    return <TimelineRenderer rows={rows} spec={spec} />;
  }
  if (spec.kind === "stacked_bar") {
    return <StackedBarFallback rows={rows} spec={spec} />;
  }
  return <TableRenderer rows={rows} spec={spec} />;
}

export function ChartSpecRenderer({ spec, data, height = 260, onInteraction }: ChartSpecRendererProps) {
  const validationMessage = validateChartSpecForRendering(spec);
  if (validationMessage) {
    return <ChartNotice message={validationMessage} />;
  }

  const rows = asRows(data);
  const renderer = chartRendererForKind(spec.kind);

  return (
    <ChartFrame interaction={spec.click_action} onInteraction={onInteraction}>
      {rows.length === 0 && spec.kind !== "metric" ? (
        <ChartNotice message="No chart data is available for this WidgetSpec." />
      ) : renderer === "recharts" ? (
        <RechartsRenderer height={height} rows={rows} spec={spec} />
      ) : renderer === "echarts" ? (
        <EChartsRenderer height={height} rows={rows} spec={spec} />
      ) : (
        <BuiltinChartRenderer rows={rows} spec={spec} />
      )}
    </ChartFrame>
  );
}

export default ChartSpecRenderer;
