import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DashboardRecipeV2, WidgetSpecV2 } from "@/lib/bff-v1/agora/types";

import { DashboardProposalPreview } from "./DashboardProposalPreview";

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

function widget(overrides: Partial<WidgetSpecV2> = {}): WidgetSpecV2 {
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

function recipe(overrides: Partial<DashboardRecipeV2> = {}, widgets: WidgetSpecV2[] = [widget()]): DashboardRecipeV2 {
  return {
    spec_version: "2.0",
    recipe_id: "rec-001",
    tenant_id: "tenant-001",
    user_id: "user-001",
    strategy_id: "strat-001",
    strategy_version_id: "sv-001",
    workspace: "trading_room",
    phase: "monitoring",
    views: [
      {
        view_id: "view-main",
        title: "Main",
        purpose: "Monitor strategy",
        layout_template_id: "time_series_execution_workspace",
        breakpoints: { lg: 12 },
        placements: widgets.map((item, index) => ({
          widget_id: item.widget_id,
          x: 0,
          y: index * 4,
          w: 6,
          h: 4,
          min_w: 2,
          min_h: 2,
        })),
        widgets,
      },
    ],
    generated_by: "servant",
    change_reason: "Initial proposal",
    version: 1,
    previous_version: null,
    status: "proposal",
    content_sha256: "a".repeat(64),
    created_at: "2026-06-20T00:00:00Z",
    updated_at: "2026-06-20T00:00:00Z",
    ...overrides,
  };
}

describe("DashboardProposalPreview", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("renders before/after DashboardRecipe v2 deltas and accepts with the OpenAPI accept body", async () => {
    const activeRecipe = recipe({
      status: "active",
      version: 2,
      change_reason: "Active dashboard",
    });
    const proposal = recipe({
      recipe_id: "rec-001",
      status: "proposal",
      version: 3,
      previous_version: 2,
      change_reason: "Add evidence table",
    }, [
      widget(),
      widget({
        widget_id: "widget-evidence",
        title: "Evidence Table",
        chart_spec: {
          spec_version: "1.0",
          kind: "table",
          encodings: {
            label: { field: "evidence_ref", type: "nominal" },
            value: { field: "status", type: "nominal" },
          },
        },
      }),
    ]);
    const onAccept = vi.fn();
    const etag = "\"recipe:rec-001:v3:aaaaaaaa\"";

    render(
      <DashboardProposalPreview
        activeRecipe={activeRecipe}
        etag={etag}
        idempotencyKey="11111111-1111-4111-8111-111111111111"
        onAccept={onAccept}
        proposal={proposal}
        widgetData={{
          "widget-strategy-status": [{ strategy_id: "strat-001", status: "monitoring" }],
          "widget-evidence": [{ evidence_ref: "ev-1", status: "ready" }],
        }}
      />,
    );

    expect(screen.getByTestId("dashboard-before-after-preview").textContent).toContain("Before");
    expect(screen.getByTestId("proposal-delta-list").textContent).toContain("Added");
    expect(screen.getByTestId("proposal-delta-list").textContent).toContain("Evidence Table");

    fireEvent.change(screen.getByLabelText("Note"), { target: { value: "approved by trader" } });
    fireEvent.click(screen.getByRole("button", { name: /Accept/i }));

    await waitFor(() => {
      expect(onAccept).toHaveBeenCalledWith({
        recipe_id: "rec-001",
        headers: {
          "If-Match": etag,
          "Idempotency-Key": "11111111-1111-4111-8111-111111111111",
        },
        body: {
          expected_version: 3,
          note: "approved by trader",
        },
      });
    });
  });

  it("disables accept when the proposal scope does not match the active recipe", () => {
    render(
      <DashboardProposalPreview
        activeRecipe={recipe({ workspace: "trading_room" })}
        etag="etag"
        onAccept={vi.fn()}
        proposal={recipe({ workspace: "strategy_workshop" })}
      />,
    );

    expect(screen.getByTestId("proposal-scope-mismatch").textContent).toContain("Proposal scope mismatch");
    expect((screen.getByRole("button", { name: /Accept/i }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("surfaces concurrent modification details from the dashboard recipe error envelope", () => {
    render(
      <DashboardProposalPreview
        concurrencyError={{
          code: "CONCURRENT_MODIFICATION",
          message: "Dashboard recipe changed after the client snapshot.",
          details: {
            expected_version: 3,
            current_version: 4,
            current_etag: "\"recipe:rec-001:v4:bbbbbbbb\"",
            latest_href: "/bff/agora/dashboard-recipes/rec-001",
          },
        }}
        etag="etag"
        proposal={recipe()}
      />,
    );

    const text = screen.getByTestId("dashboard-concurrency-error").textContent;
    expect(text).toContain("CONCURRENT_MODIFICATION");
    expect(text).toContain("4");
    expect(text).toContain("/bff/agora/dashboard-recipes/rec-001");
  });
});
