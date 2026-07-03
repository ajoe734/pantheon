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

  it("classifies historical management URLs as planned workflows instead of pretending they are full pages", () => {
    expect(describeManagementRoute("/management/control-room")).toMatchObject({
      label: "Management Registry",
      status: "planned-workflow",
      panel: "planned",
    });
    expect(describeManagementRoute("/management/persona-league")).toMatchObject({
      label: "Performance Review",
      status: "planned-workflow",
      panel: "planned",
    });
    expect(describeManagementRoute("/management/nl/ask")).toMatchObject({
      label: "Management AI Ops",
      status: "planned-workflow",
      panel: "planned",
    });
  });
});
