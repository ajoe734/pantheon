import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { validateAgoraWidget } from "@/lib/bff-v1/agora/dashboard";
import type { WidgetSpecV2 } from "@/lib/bff-v1/agora/types";

import { WidgetRevisionDrawer } from "./WidgetRevisionDrawer";

vi.mock("@/lib/bff-v1/agora/dashboard", () => ({
  validateAgoraWidget: vi.fn(),
}));

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

const validateMock = vi.mocked(validateAgoraWidget);

function strategyStatusWidget(overrides: Partial<WidgetSpecV2> = {}): WidgetSpecV2 {
  return {
    spec_version: "2.0",
    widget_id: "widget-strategy-status",
    widget_type: "strategy_status_summary",
    title: "Strategy Status",
    data_source_id: "agora.strategy.summary",
    query: { filters: { strategy_id: "strat-001" }, limit: 20 },
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

function revisedWidget(): WidgetSpecV2 {
  return {
    ...strategyStatusWidget(),
    widget_id: "widget-strategy-status-revised",
    title: "Strategy Evidence Table",
    chart_spec: {
      spec_version: "1.0",
      kind: "table",
      encodings: {
        label: { field: "evidence_ref", type: "nominal" },
        value: { field: "status", type: "nominal" },
      },
    },
    interactions: [{ kind: "open_evidence", params: { evidence_id: "ev-1" } }],
    version: 2,
    updated_at: "2026-06-20T01:00:00Z",
  };
}

function renderDrawer(props: Partial<React.ComponentProps<typeof WidgetRevisionDrawer>> = {}) {
  const onRequestRevision = vi.fn().mockResolvedValue({
    widget: revisedWidget(),
    assistantMessage: "Revised widget spec is ready.",
    changeReason: "Evidence-first table.",
  });
  const onOpenChange = vi.fn();
  return {
    onRequestRevision,
    onOpenChange,
    ...render(
      <WidgetRevisionDrawer
        data={[{ strategy_id: "strat-001", status: "monitoring", evidence_ref: "ev-1" }]}
        onOpenChange={onOpenChange}
        onRequestRevision={onRequestRevision}
        open
        widget={strategyStatusWidget()}
        {...props}
      />,
    ),
  };
}

describe("WidgetRevisionDrawer", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("requests a servant revision, validates the returned WidgetSpec, and shows before/after", async () => {
    validateMock.mockResolvedValue({
      valid: true,
      errors: [],
      warnings: [],
      registry_version: "widget_registry.v1",
      schema_hash: "hash-1",
    });
    const onAccept = vi.fn();
    const { onRequestRevision } = renderDrawer({ onAccept });

    fireEvent.change(screen.getByLabelText("Instruction"), {
      target: { value: "show evidence refs first" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Request/i }));

    await screen.findByText("Revised widget spec is ready.");
    expect(onRequestRevision).toHaveBeenCalledWith({
      widget: expect.objectContaining({ widget_id: "widget-strategy-status" }),
      instruction: "show evidence refs first",
    });
    expect(validateMock).toHaveBeenCalledWith(expect.objectContaining({ widget_id: "widget-strategy-status-revised" }));
    expect(screen.getByTestId("widget-before-after-preview").textContent).toContain("Before");
    expect(screen.getByTestId("widget-before-after-preview").textContent).toContain("After");
    expect(screen.getByTestId("widget-diff-table").textContent).toContain("Chart kind");
    expect(screen.getByText("validated")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Accept/i }));
    await waitFor(() => {
      expect(onAccept).toHaveBeenCalledWith(
        expect.objectContaining({
          baseWidget: expect.objectContaining({ widget_id: "widget-strategy-status" }),
          proposedWidget: expect.objectContaining({ widget_id: "widget-strategy-status-revised" }),
          instruction: "show evidence refs first",
          validation: expect.objectContaining({ valid: true }),
        }),
      );
    });
  });

  it("blocks accept and keep-both when backend validation rejects the proposal", async () => {
    validateMock.mockResolvedValue({
      valid: false,
      errors: [
        {
          code: "DATA_SOURCE_NOT_ALLOWED",
          path: "data_source_id",
          message: "Data source is not allowed",
        },
      ],
      warnings: [],
      registry_version: "widget_registry.v1",
    });
    const onAccept = vi.fn();
    renderDrawer({ onAccept });

    fireEvent.change(screen.getByLabelText("Instruction"), {
      target: { value: "use an unapproved source" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Request/i }));

    await screen.findByTestId("widget-validation-errors");
    expect(screen.getByTestId("widget-validation-errors").textContent).toContain("DATA_SOURCE_NOT_ALLOWED");
    expect((screen.getByRole("button", { name: /Accept/i }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: /Keep both/i }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /Accept/i }));
    expect(onAccept).not.toHaveBeenCalled();
  });

  it("allows rejecting an invalid generated candidate without mutating the widget", async () => {
    validateMock.mockResolvedValue({
      valid: false,
      errors: [{ code: "WIDGET_SPEC_INVALID", message: "WidgetSpec v2 validation failed" }],
      warnings: [],
      registry_version: "widget_registry.v1",
    });
    const onReject = vi.fn();
    renderDrawer({ onReject });

    fireEvent.change(screen.getByLabelText("Instruction"), {
      target: { value: "make it unsafe" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Request/i }));

    await screen.findByText("invalid");
    fireEvent.click(screen.getByRole("button", { name: /Reject/i }));

    await waitFor(() => {
      expect(onReject).toHaveBeenCalledWith(
        expect.objectContaining({
          baseWidget: expect.objectContaining({ widget_id: "widget-strategy-status" }),
          proposedWidget: expect.objectContaining({ widget_id: "widget-strategy-status-revised" }),
          instruction: "make it unsafe",
          validation: expect.objectContaining({ valid: false }),
        }),
      );
    });
  });
});
