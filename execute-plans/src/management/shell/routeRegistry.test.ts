import { describe, expect, it } from "vitest";

import {
  buildPerformanceAttributionHref,
  dataConfidenceFromSurface,
  displayMoney,
  displayPercent,
  displayText,
  managementField,
} from "@/lib/utils";

import { describeManagementRoute } from "./routeRegistry";

describe("describeManagementRoute", () => {
  it("normalizes the html entrypoint to the management shell", () => {
    expect(describeManagementRoute("/management.html")).toMatchObject({
      path: "/management",
      label: "Management Shell",
      status: "shell",
    });
  });

  it("marks evidence and loop URLs as active shell panels", () => {
    expect(describeManagementRoute("/management/evidence")).toMatchObject({
      label: "Evidence Explorer",
      status: "active-panel",
      panel: "evidence",
    });
    expect(describeManagementRoute("/management/loops/execution")).toMatchObject({
      label: "Loop Truth",
      status: "active-panel",
      panel: "loop-truth",
    });
    expect(describeManagementRoute("/management/ooda?packet=ooda-paper-001")).toMatchObject({
      label: "OODA Packets",
      status: "active-panel",
      panel: "ooda",
    });
  });

  it("maps newly mounted workflow suites to active panels", () => {
    expect(describeManagementRoute("/management/persona-league")).toMatchObject({
      label: "Promotion & Allocation",
      status: "active-panel",
      panel: "promotion-allocation",
    });
    expect(describeManagementRoute("/management/quarterly-ranking")).toMatchObject({
      label: "Promotion & Allocation",
      status: "active-panel",
      panel: "promotion-allocation",
    });
    expect(describeManagementRoute("/management/rebalances/rb-q3")).toMatchObject({
      label: "Promotion & Allocation",
      status: "active-panel",
      panel: "promotion-allocation",
    });
    expect(describeManagementRoute("/management/capital")).toMatchObject({
      label: "Promotion & Allocation",
      status: "active-panel",
      panel: "promotion-allocation",
    });
    expect(describeManagementRoute("/management/readiness/capital-binding-live")).toMatchObject({
      label: "Promotion & Allocation",
      status: "active-panel",
      panel: "promotion-allocation",
    });
    expect(describeManagementRoute("/management/portfolio-book")).toMatchObject({
      label: "Performance Review",
      status: "active-panel",
      panel: "performance-review",
    });
    expect(describeManagementRoute("/management/persona-fleet")).toMatchObject({
      label: "Performance Review",
      status: "active-panel",
      panel: "performance-review",
    });
    expect(describeManagementRoute("/management/nl/ask")).toMatchObject({
      label: "Management AI Ops",
      status: "active-panel",
      panel: "ai-ops",
    });
    expect(describeManagementRoute("/management/readiness/broker-live")).toMatchObject({
      label: "Readiness",
      status: "active-panel",
      panel: "readiness-suite",
    });
    expect(describeManagementRoute("/management/human-inbox")).toMatchObject({
      label: "Decision Workbench",
      status: "active-panel",
      panel: "decision-workbench",
    });
  });

  it("classifies historical registry URLs as planned workflows instead of pretending they are full pages", () => {
    expect(describeManagementRoute("/management/control-room")).toMatchObject({
      label: "Management Registry",
      status: "planned-workflow",
      panel: "planned",
    });
  });
});

describe("management display helpers", () => {
  it("normalizes snake_case and camelCase field reads at the adapter boundary", () => {
    const camel = { runtimeId: "runtime-1", totalPnl: 42 };
    const snake = { runtime_id: "runtime-2", total_pnl: 84 };

    expect(managementField(camel, "runtime_id")).toBe("runtime-1");
    expect(managementField(camel, "total_pnl")).toBe(42);
    expect(managementField(snake, "runtimeId")).toBe("runtime-2");
    expect(managementField(snake, "totalPnl")).toBe(84);
  });

  it("never renders nan, undefined, or missing numeric values as operator metrics", () => {
    expect(displayText("nan")).toBe("-");
    expect(displayText("undefined")).toBe("-");
    expect(displayMoney(Number.NaN)).toBe("-");
    expect(displayPercent("NaN")).toBe("-");
  });

  it("maps surface metadata to the shared confidence states", () => {
    expect(dataConfidenceFromSurface({ status: "ok", source: "bff_composed" })).toBe("formal");
    expect(dataConfidenceFromSurface({ status: "ok", source: "snapshot_fallback" })).toBe("fallback");
    expect(dataConfidenceFromSurface({ status: "degraded", source: "bff_composed" })).toBe("degraded");
    expect(dataConfidenceFromSurface({ status: "unavailable", source: "missing" })).toBe("unavailable");
  });

  it("builds attribution links that preserve persona, runtime, period, and source confidence", () => {
    const href = buildPerformanceAttributionHref({
      personaId: "persona-alpha",
      runtimeId: "runtime-alpha",
      period: "quarter",
      sourceHint: "bff_composed_slim_list",
      sourceConfidence: "fallback",
      diagnostic: true,
    });

    expect(href).toBe(
      "/management/performance-attribution?dimension=persona&period=quarter&persona_id=persona-alpha&runtime_id=runtime-alpha&source_hint=bff_composed_slim_list&source_confidence=fallback&mode=fallback-diagnostic",
    );
  });
});
