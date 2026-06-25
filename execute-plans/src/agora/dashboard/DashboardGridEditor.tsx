import React, { useCallback, useId, useState } from "react";
import GridLayout, { type Layout } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";

import type { PersonalizationEvent, WidgetSpecV2 } from "@/lib/bff-v1/agora/types";

import {
  type ChartSpecKind,
  type WidgetSensitivity,
  CHART_SPEC_KINDS,
  getActiveWidgetTypes,
  getWidgetRegistryEntry,
} from "@/agora/widgets/registry";
import WidgetRenderer, { type WidgetInteraction } from "@/agora/widgets/WidgetRenderer";
import type { ChartDataRow } from "@/agora/widgets/ChartSpecRenderer";

export interface WidgetPlacement {
  widget_id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  min_w: number;
  min_h: number;
  max_w?: number;
  max_h?: number;
  pinned?: boolean;
}

export interface DashboardGridEditorProps {
  viewId: string;
  recipeId: string;
  placements: WidgetPlacement[];
  widgets: WidgetSpecV2[];
  operatorId: string;
  sessionId?: string;
  cols?: number;
  rowHeight?: number;
  onPlacementsChange: (placements: WidgetPlacement[]) => void;
  onWidgetRemove: (widgetId: string) => void;
  onWidgetAdd: (widgetType: string, chartKind: ChartSpecKind) => void;
  onWidgetChartChange: (widgetId: string, newChartKind: ChartSpecKind) => void;
  onPersonalizationEvent: (event: PersonalizationEvent) => void;
  onWidgetInteraction?: (interaction: WidgetInteraction, context: { widget: WidgetSpecV2 }) => void;
  data?: Record<string, ChartDataRow[]>;
  allowedSensitivities?: WidgetSensitivity[];
}

export function placementsToLayout(placements: WidgetPlacement[]): Layout[] {
  return placements.map((p) => ({
    i: p.widget_id,
    x: p.x,
    y: p.y,
    w: p.w,
    h: p.h,
    minW: p.min_w,
    minH: p.min_h,
    maxW: p.max_w,
    maxH: p.max_h,
    static: p.pinned ?? false,
  }));
}

export function layoutToPlacements(
  layout: Layout[],
  originalPlacements: WidgetPlacement[],
): WidgetPlacement[] {
  const origMap = new Map(originalPlacements.map((p) => [p.widget_id, p]));
  return layout.map((item) => {
    const orig = origMap.get(item.i);
    return {
      widget_id: item.i,
      x: item.x,
      y: item.y,
      w: item.w,
      h: item.h,
      min_w: item.minW ?? orig?.min_w ?? 1,
      min_h: item.minH ?? orig?.min_h ?? 1,
      max_w: item.maxW ?? orig?.max_w,
      max_h: item.maxH ?? orig?.max_h,
      pinned: orig?.pinned,
    };
  });
}

function makeEventId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `evt-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

function computeNextPlacement(
  placements: WidgetPlacement[],
  widget: WidgetSpecV2,
  cols: number,
): WidgetPlacement {
  const constraints = widget.layout_constraints ?? {};
  const w = constraints.min_w ?? 4;
  const h = constraints.min_h ?? 4;
  const minW = constraints.min_w ?? 2;
  const minH = constraints.min_h ?? 2;
  const maxY = placements.reduce((m, p) => Math.max(m, p.y + p.h), 0);
  return {
    widget_id: widget.widget_id,
    x: 0,
    y: maxY,
    w: Math.min(w, cols),
    h,
    min_w: minW,
    min_h: minH,
    max_w: constraints.max_w,
    max_h: constraints.max_h,
  };
}

interface AddWidgetPanelProps {
  onSelect: (widgetType: string, chartKind: ChartSpecKind) => void;
  onClose: () => void;
}

function AddWidgetPanel({ onSelect, onClose }: AddWidgetPanelProps) {
  const activeTypes = getActiveWidgetTypes();
  const [selectedType, setSelectedType] = useState<string | null>(null);

  const entry = selectedType ? getWidgetRegistryEntry(selectedType) : null;
  const allowedKinds: ChartSpecKind[] = entry
    ? (entry.allowed_chart_kinds as ChartSpecKind[])
    : [];

  return (
    <div
      className="absolute right-0 top-10 z-50 w-72 rounded-lg border border-slate-200 bg-white shadow-lg"
      data-testid="add-widget-panel"
    >
      <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2">
        <span className="text-sm font-semibold text-slate-900">Add Widget</span>
        <button
          aria-label="Close add widget panel"
          className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
          onClick={onClose}
          type="button"
        >
          ×
        </button>
      </div>

      {!selectedType ? (
        <ul
          className="max-h-60 overflow-y-auto p-1"
          data-testid="widget-type-list"
        >
          {activeTypes.map((wt) => {
            const e = getWidgetRegistryEntry(wt);
            return (
              <li key={wt}>
                <button
                  className="w-full rounded px-3 py-2 text-left text-xs text-slate-700 hover:bg-slate-50"
                  data-testid={`widget-type-option-${wt}`}
                  onClick={() => setSelectedType(wt)}
                  type="button"
                >
                  <span className="font-medium">{e?.display_name ?? wt}</span>
                  {e?.description ? (
                    <span className="ml-2 text-slate-400">{e.description}</span>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ul>
      ) : (
        <div className="p-3">
          <button
            className="mb-2 text-xs text-slate-400 hover:text-slate-700"
            onClick={() => setSelectedType(null)}
            type="button"
          >
            ← Back
          </button>
          <p className="mb-2 text-xs font-semibold text-slate-700">
            Chart kind for{" "}
            {getWidgetRegistryEntry(selectedType)?.display_name ?? selectedType}:
          </p>
          <ul className="grid grid-cols-2 gap-1">
            {allowedKinds.map((kind) => (
              <li key={kind}>
                <button
                  className="w-full rounded border border-slate-200 px-2 py-1 text-xs text-slate-700 hover:bg-slate-50"
                  data-testid={`chart-kind-option-${kind}`}
                  onClick={() => onSelect(selectedType, kind)}
                  type="button"
                >
                  {kind}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

interface ChangeChartPanelProps {
  widget: WidgetSpecV2;
  onSelect: (kind: ChartSpecKind) => void;
  onClose: () => void;
}

function ChangeChartPanel({ widget, onSelect, onClose }: ChangeChartPanelProps) {
  const entry = getWidgetRegistryEntry(widget.widget_type);
  const allowedKinds: ChartSpecKind[] = entry
    ? (entry.allowed_chart_kinds as ChartSpecKind[])
    : (CHART_SPEC_KINDS as readonly ChartSpecKind[]).slice();

  return (
    <div
      className="absolute right-0 top-8 z-50 w-48 rounded-lg border border-slate-200 bg-white shadow-lg"
      data-testid={`change-chart-panel-${widget.widget_id}`}
    >
      <div className="flex items-center justify-between border-b border-slate-100 px-3 py-1.5">
        <span className="text-xs font-semibold text-slate-700">Change chart</span>
        <button
          aria-label="Close change chart panel"
          className="rounded p-0.5 text-slate-400 hover:bg-slate-100"
          onClick={onClose}
          type="button"
        >
          ×
        </button>
      </div>
      <ul className="p-1">
        {allowedKinds.map((kind) => (
          <li key={kind}>
            <button
              className={`w-full rounded px-3 py-1 text-left text-xs hover:bg-slate-50 ${
                widget.chart_spec.kind === kind
                  ? "font-semibold text-slate-900"
                  : "text-slate-600"
              }`}
              data-testid={`chart-kind-change-${kind}`}
              onClick={() => onSelect(kind)}
              type="button"
            >
              {kind}
              {widget.chart_spec.kind === kind ? " ✓" : ""}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

const DEFAULT_COLS = 12;
const DEFAULT_ROW_HEIGHT = 80;

export function DashboardGridEditor({
  viewId,
  recipeId,
  placements,
  widgets,
  operatorId,
  sessionId,
  cols = DEFAULT_COLS,
  rowHeight = DEFAULT_ROW_HEIGHT,
  onPlacementsChange,
  onWidgetRemove,
  onWidgetAdd,
  onWidgetChartChange,
  onPersonalizationEvent,
  onWidgetInteraction,
  data,
  allowedSensitivities,
}: DashboardGridEditorProps) {
  const labelId = useId();
  const [showAddPanel, setShowAddPanel] = useState(false);
  const [changeChartWidgetId, setChangeChartWidgetId] = useState<string | null>(null);

  const widgetMap = new Map(widgets.map((w) => [w.widget_id, w]));
  const layout = placementsToLayout(placements);

  const emitEvent = useCallback(
    (partial: Omit<PersonalizationEvent, "spec_version" | "event_id" | "operator_id" | "occurred_at">) => {
      onPersonalizationEvent({
        spec_version: "1.0",
        event_id: makeEventId(),
        operator_id: operatorId,
        session_id: sessionId,
        occurred_at: new Date().toISOString(),
        source: "operator_action",
        ...partial,
      });
    },
    [operatorId, sessionId, onPersonalizationEvent],
  );

  const handleLayoutChange = useCallback(
    (newLayout: Layout[]) => {
      const newPlacements = layoutToPlacements(newLayout, placements);
      emitEvent({
        event_type: "widget_reordered",
        target: { target_type: "dashboard_recipe", target_id: recipeId },
        before_state: { view_id: viewId, placements },
        after_state: { view_id: viewId, placements: newPlacements },
        memory_writeback_eligible: false,
      });
      onPlacementsChange(newPlacements);
    },
    [placements, recipeId, viewId, emitEvent, onPlacementsChange],
  );

  const handleRemoveWidget = useCallback(
    (widgetId: string) => {
      const beforeWidgetIds = placements.map((p) => p.widget_id);
      const afterWidgetIds = beforeWidgetIds.filter((id) => id !== widgetId);
      emitEvent({
        event_type: "widget_removed",
        target: { target_type: "widget", target_id: widgetId },
        before_state: { view_id: viewId, widget_ids: beforeWidgetIds },
        after_state: { view_id: viewId, widget_ids: afterWidgetIds },
        memory_writeback_eligible: true,
      });
      onWidgetRemove(widgetId);
    },
    [placements, viewId, emitEvent, onWidgetRemove],
  );

  const handleAddWidget = useCallback(
    (widgetType: string, chartKind: ChartSpecKind) => {
      setShowAddPanel(false);
      emitEvent({
        event_type: "widget_added",
        target: { target_type: "dashboard_recipe", target_id: recipeId },
        before_state: { view_id: viewId, widget_count: widgets.length },
        after_state: {
          view_id: viewId,
          widget_type: widgetType,
          chart_kind: chartKind,
        },
        memory_writeback_eligible: true,
      });
      onWidgetAdd(widgetType, chartKind);
    },
    [recipeId, viewId, widgets.length, emitEvent, onWidgetAdd],
  );

  const handleChangeChart = useCallback(
    (widgetId: string, newKind: ChartSpecKind) => {
      setChangeChartWidgetId(null);
      const widget = widgetMap.get(widgetId);
      const oldKind = widget?.chart_spec.kind;
      emitEvent({
        event_type: "dashboard_recipe_changed",
        target: { target_type: "widget", target_id: widgetId },
        before_state: { view_id: viewId, widget_id: widgetId, chart_kind: oldKind },
        after_state: { view_id: viewId, widget_id: widgetId, chart_kind: newKind },
        memory_writeback_eligible: true,
      });
      onWidgetChartChange(widgetId, newKind);
    },
    [widgetMap, viewId, emitEvent, onWidgetChartChange],
  );

  return (
    <div
      aria-labelledby={labelId}
      className="relative"
      data-recipe-id={recipeId}
      data-testid="dashboard-grid-editor"
      data-view-id={viewId}
    >
      <div className="mb-3 flex items-center justify-between">
        <h2 className="sr-only" id={labelId}>
          Dashboard grid editor
        </h2>
        <div className="relative ml-auto">
          <button
            className="rounded border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
            data-testid="add-widget-button"
            onClick={() => setShowAddPanel((v) => !v)}
            type="button"
          >
            + Add Widget
          </button>
          {showAddPanel ? (
            <AddWidgetPanel
              onClose={() => setShowAddPanel(false)}
              onSelect={handleAddWidget}
            />
          ) : null}
        </div>
      </div>

      <GridLayout
        className="layout"
        cols={cols}
        draggableHandle=".widget-drag-handle"
        layout={layout}
        rowHeight={rowHeight}
        width={cols * 120}
        onLayoutChange={handleLayoutChange}
      >
        {placements.map((placement) => {
          const widget = widgetMap.get(placement.widget_id);
          if (!widget) return null;
          const isChangingChart = changeChartWidgetId === placement.widget_id;

          return (
            <div
              key={placement.widget_id}
              className="flex flex-col overflow-hidden rounded-md border border-slate-200 bg-white"
              data-testid={`grid-cell-${placement.widget_id}`}
            >
              <div className="widget-drag-handle flex shrink-0 cursor-grab items-center justify-between border-b border-slate-100 px-3 py-1.5 active:cursor-grabbing">
                <span className="truncate text-xs font-medium text-slate-700">
                  {widget.title}
                </span>
                <div className="relative flex shrink-0 items-center gap-1">
                  <button
                    aria-label={`Change chart for ${widget.title}`}
                    className="rounded p-1 text-[11px] text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                    data-testid={`change-chart-button-${placement.widget_id}`}
                    onClick={() =>
                      setChangeChartWidgetId((id) =>
                        id === placement.widget_id ? null : placement.widget_id,
                      )
                    }
                    type="button"
                  >
                    ⚙
                  </button>
                  {!placement.pinned ? (
                    <button
                      aria-label={`Remove ${widget.title}`}
                      className="rounded p-1 text-[11px] text-slate-400 hover:bg-red-50 hover:text-red-600"
                      data-testid={`remove-widget-button-${placement.widget_id}`}
                      onClick={() => handleRemoveWidget(placement.widget_id)}
                      type="button"
                    >
                      ×
                    </button>
                  ) : null}
                  {isChangingChart ? (
                    <ChangeChartPanel
                      widget={widget}
                      onClose={() => setChangeChartWidgetId(null)}
                      onSelect={(kind) => handleChangeChart(placement.widget_id, kind)}
                    />
                  ) : null}
                </div>
              </div>

              <div className="min-h-0 flex-1 overflow-hidden p-2">
                <WidgetRenderer
                  allowedSensitivities={allowedSensitivities}
                  data={data?.[placement.widget_id]}
                  widget={widget}
                  onInteraction={
                    onWidgetInteraction
                      ? (interaction, ctx) =>
                          onWidgetInteraction(interaction, { widget: ctx.widget })
                      : undefined
                  }
                />
              </div>
            </div>
          );
        })}
      </GridLayout>
    </div>
  );
}

export function computeInitialPlacement(
  widget: WidgetSpecV2,
  existingPlacements: WidgetPlacement[],
  cols = DEFAULT_COLS,
): WidgetPlacement {
  return computeNextPlacement(existingPlacements, widget, cols);
}

export default DashboardGridEditor;
