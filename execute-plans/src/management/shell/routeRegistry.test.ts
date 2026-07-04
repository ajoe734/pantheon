import { describe, expect, it } from "vitest";

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
