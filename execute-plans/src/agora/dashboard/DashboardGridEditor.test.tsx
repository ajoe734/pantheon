import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PersonalizationEvent, WidgetSpecV2 } from "@/lib/bff-v1/agora/types";
import type { ChartSpecKind } from "@/agora/widgets/registry";
import type { Layout } from "react-grid-layout";

import {
  DashboardGridEditor,
  layoutToPlacements,
  placementsToLayout,
  type WidgetPlacement,
} from "./DashboardGridEditor";

const gridCallbacks = vi.hoisted(() => ({
  onLayoutChange: undefined as ((layout: Layout[]) => void) | undefined,
}));

vi.mock("react-grid-layout", async () => {
  const ReactModule = await import("react");

  const MockGridLayout = ({
    children,
    onLayoutChange,
    layout,
    draggableHandle: _draggableHandle,
    rowHeight: _rowHeight,
    ...rest
  }: {
    children: React.ReactNode;
    onLayoutChange?: (layout: Layout[]) => void;
    layout?: Layout[];
    draggableHandle?: string;
    rowHeight?: number;
    [key: string]: unknown;
  }) => {
    gridCallbacks.onLayoutChange = onLayoutChange;
    return ReactModule.createElement(
      "div",
      {
        "data-layout": JSON.stringify(layout ?? []),
        "data-testid": "mock-grid-layout",
        ...rest,
      },
      children,
    );
  };

  MockGridLayout.displayName = "MockGridLayout";
  return { default: MockGridLayout };
});

vi.mock("react-grid-layout/css/styles.css", () => ({}));
vi.mock("react-resizable/css/styles.css", () => ({}));

vi.mock("@/agora/widgets/WidgetRenderer", () => {
  const ReactModule = { createElement: React.createElement };
  return {
    default: ({ widget }: { widget: WidgetSpecV2 }) =>
      ReactModule.createElement("div", {
        "data-testid": `widget-renderer-${widget.widget_id}`,
        "data-widget-type": widget.widget_type,
      }),
  };
});

vi.mock("@/agora/widgets/registry", async () => {
  const actual = await import("@/agora/widgets/registry");
  return {
    ...actual,
    getActiveWidgetTypes: () => ["strategy_status_summary", "candidate_ranking_table"],
    getWidgetRegistryEntry: (widgetType: string) => {
      type MockEntry = {
        display_name: string;
        description: string;
        allowed_chart_kinds: ChartSpecKind[];
        status: "active" | "inactive" | "deprecated";
        category: string;
        renderer: "builtin" | "chart_spec";
        allowed_data_sources: string[];
        required_fields: string[];
        optional_fields: string[];
        allowed_transforms: string[];
        allowed_interactions: string[];
        sensitivity: "public_market" | "user_private" | "broker_sensitive" | "restricted";
        phase: string;
        widget_type: string;
      };
      const entries: Record<string, MockEntry> = {
        strategy_status_summary: {
          widget_type: "strategy_status_summary",
          display_name: "Strategy Status Summary",
          description: "Strategy version, status, completeness",
          category: "generic",
          renderer: "chart_spec",
          allowed_chart_kinds: ["metric", "table"],
          allowed_data_sources: ["agora.strategy.summary"],
          required_fields: ["strategy_id"],
          optional_fields: [],
          allowed_transforms: ["filter"],
          allowed_interactions: ["open_strategy"],
          sensitivity: "public_market",
          phase: "any",
          status: "active",
        },
        candidate_ranking_table: {
          widget_type: "candidate_ranking_table",
          display_name: "Candidate Ranking",
          description: "Candidate ranking with score decomposition",
          category: "generic",
          renderer: "chart_spec",
          allowed_chart_kinds: ["table"],
          allowed_data_sources: ["agora.candidate.members"],
          required_fields: [],
          optional_fields: [],
          allowed_transforms: ["sort", "top_k"],
          allowed_interactions: ["open_candidate"],
          sensitivity: "user_private",
          phase: "candidate_review",
          status: "active",
        },
      };
      return entries[widgetType];
    },
    isActiveWidgetType: (wt: string) =>
      ["strategy_status_summary", "candidate_ranking_table"].includes(wt),
  };
});

function makeWidget(id: string, type = "strategy_status_summary"): WidgetSpecV2 {
  return {
    spec_version: "2.0",
    widget_id: id,
    widget_type: type,
    title: `Widget ${id}`,
    data_source_id: "agora.strategy.summary",
    query: { filters: {} },
    chart_spec: {
      spec_version: "1.0",
      kind: "metric",
      encodings: { value: { field: "status", type: "nominal" } },
    },
    interactions: [],
    sensitivity: "public_market",
    can_export: false,
    registry_version: "widget_registry.v1",
    version: 1,
    created_at: "2026-06-22T00:00:00Z",
  };
}

function makePlacement(
  widgetId: string,
  overrides: Partial<WidgetPlacement> = {},
): WidgetPlacement {
  return {
    widget_id: widgetId,
    x: 0,
    y: 0,
    w: 4,
    h: 4,
    min_w: 2,
    min_h: 2,
    ...overrides,
  };
}

function getLayoutChangeTrigger(): (layout: Layout[]) => void {
  if (!gridCallbacks.onLayoutChange) {
    throw new Error("onLayoutChange callback not captured — was the grid rendered?");
  }
  return gridCallbacks.onLayoutChange;
}

describe("placementsToLayout", () => {
  it("maps all required fields", () => {
    const p: WidgetPlacement = {
      widget_id: "w1",
      x: 1,
      y: 2,
      w: 3,
      h: 4,
      min_w: 2,
      min_h: 2,
    };
    const [item] = placementsToLayout([p]);
    expect(item.i).toBe("w1");
    expect(item.x).toBe(1);
    expect(item.y).toBe(2);
    expect(item.w).toBe(3);
    expect(item.h).toBe(4);
    expect(item.minW).toBe(2);
    expect(item.minH).toBe(2);
    expect(item.static).toBe(false);
  });

  it("sets static=true when pinned", () => {
    const p = makePlacement("w1", { pinned: true });
    const [item] = placementsToLayout([p]);
    expect(item.static).toBe(true);
  });

  it("propagates max_w and max_h", () => {
    const p = makePlacement("w1", { max_w: 6, max_h: 8 });
    const [item] = placementsToLayout([p]);
    expect(item.maxW).toBe(6);
    expect(item.maxH).toBe(8);
  });
});

describe("layoutToPlacements", () => {
  it("maps all required fields back", () => {
    const orig = makePlacement("w1", { min_w: 2, min_h: 3, max_w: 10, max_h: 10 });
    const layout: Layout[] = [{ i: "w1", x: 2, y: 3, w: 5, h: 6 }];
    const [out] = layoutToPlacements(layout, [orig]);
    expect(out.widget_id).toBe("w1");
    expect(out.x).toBe(2);
    expect(out.y).toBe(3);
    expect(out.w).toBe(5);
    expect(out.h).toBe(6);
    expect(out.min_w).toBe(2);
    expect(out.min_h).toBe(3);
    expect(out.max_w).toBe(10);
    expect(out.max_h).toBe(10);
  });

  it("preserves pinned flag from original placement", () => {
    const orig = makePlacement("w1", { pinned: true });
    const layout: Layout[] = [{ i: "w1", x: 0, y: 0, w: 4, h: 4 }];
    const [out] = layoutToPlacements(layout, [orig]);
    expect(out.pinned).toBe(true);
  });

  it("uses fallback min_w=1, min_h=1 when missing", () => {
    const layout: Layout[] = [{ i: "wx", x: 0, y: 0, w: 3, h: 3 }];
    const [out] = layoutToPlacements(layout, []);
    expect(out.min_w).toBe(1);
    expect(out.min_h).toBe(1);
  });
});

describe("DashboardGridEditor", () => {
  const baseProps = {
    viewId: "view-1",
    recipeId: "recipe-1",
    operatorId: "op-1",
    sessionId: "session-1",
    onPlacementsChange: vi.fn(),
    onWidgetRemove: vi.fn(),
    onWidgetAdd: vi.fn(),
    onWidgetChartChange: vi.fn(),
    onPersonalizationEvent: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    gridCallbacks.onLayoutChange = undefined;
  });

  afterEach(() => {
    cleanup();
  });

  it("renders widgets in grid cells", () => {
    const w1 = makeWidget("w1");
    const w2 = makeWidget("w2");
    render(
      <DashboardGridEditor
        {...baseProps}
        placements={[makePlacement("w1"), makePlacement("w2", { y: 4 })]}
        widgets={[w1, w2]}
      />,
    );
    expect(screen.getByTestId("grid-cell-w1")).not.toBeNull();
    expect(screen.getByTestId("grid-cell-w2")).not.toBeNull();
  });

  it("emits widget_reordered PersonalizationEvent on drag", () => {
    const onEvent = vi.fn();
    const onChangePlacements = vi.fn();
    const w1 = makeWidget("w1");
    const placement = makePlacement("w1");

    render(
      <DashboardGridEditor
        {...baseProps}
        placements={[placement]}
        widgets={[w1]}
        onPersonalizationEvent={onEvent}
        onPlacementsChange={onChangePlacements}
      />,
    );

    const trigger = getLayoutChangeTrigger();
    const newLayout: Layout[] = [{ i: "w1", x: 2, y: 1, w: 4, h: 4 }];
    trigger(newLayout);

    expect(onEvent).toHaveBeenCalledOnce();
    const evt: PersonalizationEvent = onEvent.mock.calls[0][0];
    expect(evt.spec_version).toBe("1.0");
    expect(evt.event_type).toBe("widget_reordered");
    expect(evt.operator_id).toBe("op-1");
    expect(evt.session_id).toBe("session-1");
    expect(evt.source).toBe("operator_action");
    expect(evt.target?.target_type).toBe("dashboard_recipe");
    expect(evt.target?.target_id).toBe("recipe-1");
    expect(evt.event_id).toBeTruthy();
    expect(evt.occurred_at).toBeTruthy();
    expect(evt.before_state?.placements).toEqual([placement]);
    expect((evt.after_state?.placements as WidgetPlacement[])?.[0].x).toBe(2);
    expect((evt.after_state?.placements as WidgetPlacement[])?.[0].y).toBe(1);

    expect(onChangePlacements).toHaveBeenCalledOnce();
    const updatedPlacements: WidgetPlacement[] = onChangePlacements.mock.calls[0][0];
    expect(updatedPlacements[0].widget_id).toBe("w1");
    expect(updatedPlacements[0].x).toBe(2);
    expect(updatedPlacements[0].y).toBe(1);
  });

  it("emits widget_reordered with correct WidgetPlacement fields on resize", () => {
    const onEvent = vi.fn();
    const w1 = makeWidget("w1");
    const placement = makePlacement("w1", { min_w: 3, min_h: 3, max_w: 8, max_h: 8 });

    render(
      <DashboardGridEditor
        {...baseProps}
        placements={[placement]}
        widgets={[w1]}
        onPersonalizationEvent={onEvent}
        onPlacementsChange={vi.fn()}
      />,
    );

    const trigger = getLayoutChangeTrigger();
    trigger([{ i: "w1", x: 0, y: 0, w: 6, h: 6 }]);

    const evt: PersonalizationEvent = onEvent.mock.calls[0][0];
    expect(evt.event_type).toBe("widget_reordered");
    const after = (evt.after_state?.placements as WidgetPlacement[])?.[0];
    expect(after.w).toBe(6);
    expect(after.h).toBe(6);
    expect(after.min_w).toBe(3);
    expect(after.min_h).toBe(3);
    expect(after.max_w).toBe(8);
    expect(after.max_h).toBe(8);
  });

  it("emits widget_removed PersonalizationEvent and calls onWidgetRemove", () => {
    const onEvent = vi.fn();
    const onRemove = vi.fn();
    const w1 = makeWidget("w1");
    const w2 = makeWidget("w2");
    render(
      <DashboardGridEditor
        {...baseProps}
        placements={[makePlacement("w1"), makePlacement("w2", { y: 4 })]}
        widgets={[w1, w2]}
        onPersonalizationEvent={onEvent}
        onWidgetRemove={onRemove}
      />,
    );

    fireEvent.click(screen.getByTestId("remove-widget-button-w1"));

    expect(onRemove).toHaveBeenCalledWith("w1");
    expect(onEvent).toHaveBeenCalledOnce();
    const evt: PersonalizationEvent = onEvent.mock.calls[0][0];
    expect(evt.event_type).toBe("widget_removed");
    expect(evt.target?.target_type).toBe("widget");
    expect(evt.target?.target_id).toBe("w1");
    expect(evt.memory_writeback_eligible).toBe(true);
    expect((evt.before_state?.widget_ids as string[]).includes("w1")).toBe(true);
    expect((evt.after_state?.widget_ids as string[]).includes("w1")).toBe(false);
  });

  it("does not show remove button for pinned widgets", () => {
    const w1 = makeWidget("w1");
    render(
      <DashboardGridEditor
        {...baseProps}
        placements={[makePlacement("w1", { pinned: true })]}
        widgets={[w1]}
      />,
    );
    expect(screen.queryByTestId("remove-widget-button-w1")).toBeNull();
  });

  it("emits widget_added PersonalizationEvent and calls onWidgetAdd", () => {
    const onEvent = vi.fn();
    const onAdd = vi.fn();
    render(
      <DashboardGridEditor
        {...baseProps}
        placements={[makePlacement("w1")]}
        widgets={[makeWidget("w1")]}
        onPersonalizationEvent={onEvent}
        onWidgetAdd={onAdd}
      />,
    );

    fireEvent.click(screen.getByTestId("add-widget-button"));
    expect(screen.getByTestId("add-widget-panel")).not.toBeNull();

    fireEvent.click(screen.getByTestId("widget-type-option-strategy_status_summary"));
    fireEvent.click(screen.getByTestId("chart-kind-option-metric"));

    expect(onAdd).toHaveBeenCalledWith("strategy_status_summary", "metric");
    expect(onEvent).toHaveBeenCalledOnce();
    const evt: PersonalizationEvent = onEvent.mock.calls[0][0];
    expect(evt.event_type).toBe("widget_added");
    expect(evt.target?.target_type).toBe("dashboard_recipe");
    expect(evt.target?.target_id).toBe("recipe-1");
    expect(evt.after_state?.widget_type).toBe("strategy_status_summary");
    expect(evt.after_state?.chart_kind).toBe("metric");
    expect(evt.memory_writeback_eligible).toBe(true);
    expect(screen.queryByTestId("add-widget-panel")).toBeNull();
  });

  it("emits dashboard_recipe_changed PersonalizationEvent and calls onWidgetChartChange", () => {
    const onEvent = vi.fn();
    const onChartChange = vi.fn();
    const w1 = makeWidget("w1");
    render(
      <DashboardGridEditor
        {...baseProps}
        placements={[makePlacement("w1")]}
        widgets={[w1]}
        onPersonalizationEvent={onEvent}
        onWidgetChartChange={onChartChange}
      />,
    );

    fireEvent.click(screen.getByTestId("change-chart-button-w1"));
    expect(screen.getByTestId("change-chart-panel-w1")).not.toBeNull();

    fireEvent.click(screen.getByTestId("chart-kind-change-table"));

    expect(onChartChange).toHaveBeenCalledWith("w1", "table");
    expect(onEvent).toHaveBeenCalledOnce();
    const evt: PersonalizationEvent = onEvent.mock.calls[0][0];
    expect(evt.event_type).toBe("dashboard_recipe_changed");
    expect(evt.target?.target_type).toBe("widget");
    expect(evt.target?.target_id).toBe("w1");
    expect(evt.before_state?.chart_kind).toBe("metric");
    expect(evt.after_state?.chart_kind).toBe("table");
    expect(evt.memory_writeback_eligible).toBe(true);
    expect(screen.queryByTestId("change-chart-panel-w1")).toBeNull();
  });

  it("PersonalizationEvent has required fields: spec_version, event_id, operator_id, occurred_at", () => {
    const onEvent = vi.fn();
    const w1 = makeWidget("w1");
    render(
      <DashboardGridEditor
        {...baseProps}
        placements={[makePlacement("w1")]}
        widgets={[w1]}
        onPersonalizationEvent={onEvent}
      />,
    );

    fireEvent.click(screen.getByTestId("remove-widget-button-w1"));

    const evt: PersonalizationEvent = onEvent.mock.calls[0][0];
    expect(evt.spec_version).toBe("1.0");
    expect(typeof evt.event_id).toBe("string");
    expect(evt.event_id.length).toBeGreaterThan(0);
    expect(evt.operator_id).toBe("op-1");
    expect(typeof evt.occurred_at).toBe("string");
    expect(new Date(evt.occurred_at).getTime()).not.toBeNaN();
  });

  it("renders add widget button", () => {
    render(
      <DashboardGridEditor
        {...baseProps}
        placements={[]}
        widgets={[]}
      />,
    );
    expect(screen.getByTestId("add-widget-button")).not.toBeNull();
  });

  it("closes add widget panel when close button is clicked", () => {
    render(
      <DashboardGridEditor
        {...baseProps}
        placements={[]}
        widgets={[]}
      />,
    );
    fireEvent.click(screen.getByTestId("add-widget-button"));
    expect(screen.getByTestId("add-widget-panel")).not.toBeNull();
    fireEvent.click(screen.getByLabelText("Close add widget panel"));
    expect(screen.queryByTestId("add-widget-panel")).toBeNull();
  });
});
