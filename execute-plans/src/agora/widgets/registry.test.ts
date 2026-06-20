import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import type { WidgetSpecV2 } from "@/lib/bff-v1/agora/types";

import {
  AGORA_WIDGET_CONTRACT_HASHES,
  CHART_ENCODING_CHANNELS,
  CHART_SPEC_KINDS,
  CHART_TRANSFORM_TYPES,
  WIDGET_INTERACTION_KINDS,
  WIDGET_REGISTRY,
  WIDGET_REGISTRY_ENTRY_COUNT,
  chartRendererForKind,
  getActiveWidgetTypes,
  validateWidgetSpecAgainstRegistry,
} from "./registry";

const registryPath = path.resolve(
  process.cwd(),
  "..",
  "docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/widget_registry.v1.json",
);

function sha256(filePath: string): string {
  return createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
}

function baseWidget(overrides: Partial<WidgetSpecV2> = {}): WidgetSpecV2 {
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

describe("Agora widget registry", () => {
  it("matches the frozen A3 registry exactly", () => {
    const source = JSON.parse(fs.readFileSync(registryPath, "utf8")) as typeof WIDGET_REGISTRY;
    const sourceTypes = source.entries.map((entry) => entry.widget_type).sort();
    const frontendTypes = WIDGET_REGISTRY.entries.map((entry) => entry.widget_type).sort();

    expect(WIDGET_REGISTRY.entries).toHaveLength(WIDGET_REGISTRY_ENTRY_COUNT);
    expect(frontendTypes).toEqual(sourceTypes);
    expect(getActiveWidgetTypes().sort()).toEqual(sourceTypes);
    expect(new Set(frontendTypes).size).toBe(WIDGET_REGISTRY_ENTRY_COUNT);
  });

  it("records the contract hashes used by renderer tests", () => {
    expect(sha256(registryPath)).toBe(AGORA_WIDGET_CONTRACT_HASHES.widgetRegistryV1);
    expect(AGORA_WIDGET_CONTRACT_HASHES.widgetSpecV2).toBe(
      "d360a17a9762d69e6a5e2c87921117bb85ee34d972fd8034f8904df6facb993f",
    );
    expect(AGORA_WIDGET_CONTRACT_HASHES.chartSpecV1).toBe(
      "0bcd0fa5fc21d7c021d54803780e310cfd9234b3ea15c044fa0b5cdfffed0967",
    );
  });

  it("exports the ChartSpec v1 allowlists without omissions", () => {
    expect(CHART_SPEC_KINDS).toHaveLength(13);
    expect(CHART_ENCODING_CHANNELS).toHaveLength(18);
    expect(CHART_TRANSFORM_TYPES).toHaveLength(16);
    expect(WIDGET_INTERACTION_KINDS).toHaveLength(15);
    expect(chartRendererForKind("metric")).toBe("recharts");
    expect(chartRendererForKind("network")).toBe("echarts");
    expect(chartRendererForKind("timeline")).toBe("builtin");
  });

  it("accepts only active registry-backed WidgetSpec v2 entries", () => {
    expect(validateWidgetSpecAgainstRegistry(baseWidget())).toMatchObject({ ok: true });
    expect(validateWidgetSpecAgainstRegistry(baseWidget({ widget_type: "not_registered" }))).toMatchObject({
      ok: false,
      code: "UNKNOWN_WIDGET_TYPE",
    });
    expect(validateWidgetSpecAgainstRegistry(baseWidget({ data_source_id: "/bff/agora/custom" }))).toMatchObject({
      ok: false,
      code: "UNAPPROVED_DATA_SOURCE",
    });
  });

  it("blocks chart, transform, interaction, and sensitivity deviations", () => {
    expect(
      validateWidgetSpecAgainstRegistry(
        baseWidget({
          chart_spec: { spec_version: "1.0", kind: "network", encodings: { source: { field: "a", type: "nominal" } } },
        }),
      ),
    ).toMatchObject({ ok: false, code: "UNAPPROVED_CHART_KIND" });

    expect(
      validateWidgetSpecAgainstRegistry(
        baseWidget({
          chart_spec: {
            spec_version: "1.0",
            kind: "metric",
            encodings: { y: { field: "status", type: "nominal" } },
            transforms: [{ type: "rolling_sum", params: { field: "status" } }],
          },
        }),
      ),
    ).toMatchObject({ ok: false, code: "UNAPPROVED_TRANSFORM" });

    expect(
      validateWidgetSpecAgainstRegistry(
        baseWidget({
          interactions: [{ kind: "place_order", params: { symbol: "AAPL" } }] as unknown as WidgetSpecV2["interactions"],
        }),
      ),
    ).toMatchObject({ ok: false, code: "UNAPPROVED_INTERACTION" });

    expect(validateWidgetSpecAgainstRegistry(baseWidget({ sensitivity: "public_market" }))).toMatchObject({
      ok: false,
      code: "SENSITIVITY_DOWNGRADE",
    });
  });
});
