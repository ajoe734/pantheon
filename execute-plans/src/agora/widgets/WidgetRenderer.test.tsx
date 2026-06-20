import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WidgetSpecV2 } from "@/lib/bff-v1/agora/types";

import { WidgetRenderer } from "./WidgetRenderer";

vi.mock("echarts-for-react", async () => {
  const ReactModule = await import("react");
  return {
    default: () => ReactModule.createElement("div", { "data-testid": "mock-echarts" }),
  };
});

vi.mock("recharts", async () => {
  const ReactModule = await import("react");
  const Box = ({ children }: { children?: React.ReactNode }) => ReactModule.createElement("div", null, children);
  const Leaf = () => ReactModule.createElement("span");
  return {
    Area: Leaf,
    AreaChart: Box,
    Bar: Leaf,
    BarChart: Box,
    CartesianGrid: Leaf,
    Line: Leaf,
    LineChart: Box,
    ResponsiveContainer: Box,
    Tooltip: Leaf,
    XAxis: Leaf,
    YAxis: Leaf,
  };
});

function strategyStatusWidget(overrides: Partial<WidgetSpecV2> = {}): WidgetSpecV2 {
  return {
    spec_version: "2.0",
    widget_id: "widget-strategy-status",
    widget_type: "strategy_status_summary",
    title: "Strategy Status",
    data_source_id: "agora.strategy.summary",
    query: { filters: { strategy_id: "strat-001" } },
    chart_spec: {
      spec_version: "1.0",
      kind: "metric",
      encodings: {
        y: { field: "status", type: "nominal" },
        label: { field: "strategy_id", type: "nominal" },
      },
    },
    interactions: [{ kind: "open_strategy", params: { strategy_id: "strat-001" } }],
    sensitivity: "user_private",
    can_export: false,
    registry_version: "widget_registry.v1",
    version: 1,
    created_at: "2026-06-20T00:00:00Z",
    ...overrides,
  };
}

function servantWidget(): WidgetSpecV2 {
  return {
    ...strategyStatusWidget(),
    widget_id: "widget-servant",
    widget_type: "servant_assessment",
    title: "Servant Assessment",
    data_source_id: "agora.strategy.summary",
    chart_spec: {
      spec_version: "1.0",
      kind: "table",
      encodings: {
        label: { field: "assessment", type: "nominal" },
        value: { field: "confidence", type: "quantitative" },
      },
    },
    interactions: [{ kind: "request_more_research", params: { strategy_id: "strat-001" } }],
  };
}

describe("WidgetRenderer", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders active chart_spec widgets without fetching from data_source_id", () => {
    render(<WidgetRenderer data={[{ strategy_id: "strat-001", status: "monitoring" }]} widget={strategyStatusWidget()} />);

    expect(screen.getByTestId("widget-renderer")).toBeTruthy();
    expect(screen.getByTestId("chart-renderer-recharts")).toBeTruthy();
    expect(screen.getByText("monitoring")).toBeTruthy();
  });

  it("renders builtin registry entries through the builtin path", () => {
    const onInteraction = vi.fn();
    render(
      <WidgetRenderer
        data={[{ assessment: "needs more evidence", confidence: 0.74 }]}
        onInteraction={onInteraction}
        widget={servantWidget()}
      />,
    );

    expect(screen.getByTestId("widget-renderer-builtin")).toBeTruthy();
    fireEvent.click(screen.getByText("request_more_research"));
    expect(onInteraction).toHaveBeenCalledWith(
      { kind: "request_more_research", params: { strategy_id: "strat-001" } },
      expect.objectContaining({ widget: expect.objectContaining({ widget_type: "servant_assessment" }) }),
    );
  });

  it("rejects unknown widget types and unapproved chart kinds", () => {
    render(<WidgetRenderer widget={strategyStatusWidget({ widget_type: "unknown_widget" })} />);
    expect(screen.getByTestId("widget-renderer-error").textContent).toContain("UNKNOWN_WIDGET_TYPE");

    cleanup();
    render(
      <WidgetRenderer
        widget={strategyStatusWidget({
          chart_spec: {
            spec_version: "1.0",
            kind: "network",
            encodings: { source: { field: "source", type: "nominal" } },
          },
        })}
      />,
    );
    expect(screen.getByTestId("widget-renderer-error").textContent).toContain("UNAPPROVED_CHART_KIND");
  });

  it("fails closed for broker-sensitive or restricted widgets outside user scope", () => {
    render(
      <WidgetRenderer
        widget={{
          ...strategyStatusWidget(),
          widget_type: "related_branch_network",
          data_source_id: "winner_branch.related_branch_flow",
          chart_spec: {
            spec_version: "1.0",
            kind: "network",
            encodings: {
              source: { field: "source_branch", type: "nominal" },
              target: { field: "target_branch", type: "nominal" },
              value: { field: "net_value", type: "quantitative" },
            },
          },
          interactions: [{ kind: "open_evidence", params: { evidence_id: "ev-1" } }],
          sensitivity: "restricted",
        }}
      />,
    );

    expect(screen.getByTestId("widget-renderer-error").textContent).toContain("SENSITIVITY_SCOPE_DENIED");
  });
});
