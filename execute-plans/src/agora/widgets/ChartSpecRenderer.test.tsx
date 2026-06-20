import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChartSpecV1 } from "@/lib/bff-v1/agora/types";

import { ChartSpecRenderer, validateChartSpecForRendering } from "./ChartSpecRenderer";

vi.mock("echarts-for-react", async () => {
  const ReactModule = await import("react");
  return {
    default: ({ option }: { option: unknown }) =>
      ReactModule.createElement("div", { "data-option": JSON.stringify(option), "data-testid": "mock-echarts" }),
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

function metricSpec(overrides: Partial<ChartSpecV1> = {}): ChartSpecV1 {
  return {
    spec_version: "1.0",
    kind: "metric",
    encodings: {
      y: { field: "value", type: "quantitative" },
      label: { field: "label", type: "nominal" },
    },
    ...overrides,
  };
}

describe("ChartSpecRenderer", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders simple chart kinds through the Recharts dispatch", () => {
    render(<ChartSpecRenderer data={[{ label: "Sharpe", value: 1.42 }]} spec={metricSpec()} />);

    expect(screen.getByTestId("chart-renderer-recharts")).toBeTruthy();
    expect(screen.getByText("1.42")).toBeTruthy();
  });

  it("renders complex chart kinds through the ECharts dispatch", () => {
    render(
      <ChartSpecRenderer
        data={[{ source: "A", target: "B", value: 10 }]}
        spec={{
          spec_version: "1.0",
          kind: "network",
          encodings: {
            source: { field: "source", type: "nominal" },
            target: { field: "target", type: "nominal" },
            value: { field: "value", type: "quantitative" },
          },
        }}
      />,
    );

    expect(screen.getByTestId("chart-renderer-echarts")).toBeTruthy();
    expect(screen.getByTestId("mock-echarts").getAttribute("data-option")).toContain("graph");
  });

  it("renders table, timeline, and stacked_bar without external chart execution", () => {
    render(
      <ChartSpecRenderer
        data={[{ label: "Candidate A", value: 12 }]}
        spec={{
          spec_version: "1.0",
          kind: "table",
          encodings: {
            label: { field: "label", type: "nominal" },
            value: { field: "value", type: "quantitative" },
          },
        }}
      />,
    );

    expect(screen.getByTestId("chart-renderer-builtin")).toBeTruthy();
    expect(screen.getByText("Candidate A")).toBeTruthy();
  });

  it("rejects unsafe callbacks, html, scripts, and arbitrary interaction kinds", () => {
    const unsafeSpec = metricSpec({
      options: { formatter: "function () { return window.location.href }" },
    });
    expect(validateChartSpecForRendering(unsafeSpec)).toContain("renderer-unsafe");

    render(<ChartSpecRenderer data={[{ label: "Risk", value: 3 }]} spec={unsafeSpec} />);
    expect(screen.getByTestId("chart-render-notice").textContent).toContain("renderer-unsafe");

    expect(
      validateChartSpecForRendering(
        metricSpec({
          click_action: { kind: "place_order", params: { symbol: "AAPL" } } as unknown as ChartSpecV1["click_action"],
        }),
      ),
    ).toContain("Interaction is not");
  });

  it("fires only allowlisted declarative interactions", () => {
    const onInteraction = vi.fn();
    render(
      <ChartSpecRenderer
        data={[{ label: "Evidence", value: 1 }]}
        onInteraction={onInteraction}
        spec={metricSpec({ click_action: { kind: "open_evidence", params: { evidence_id: "ev-1" } } })}
      />,
    );

    fireEvent.click(screen.getByTestId("chart-spec-renderer"));
    expect(onInteraction).toHaveBeenCalledWith({ kind: "open_evidence", params: { evidence_id: "ev-1" } });
  });
});
