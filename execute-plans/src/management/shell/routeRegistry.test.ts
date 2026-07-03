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
    });
    expect(describeManagementRoute("/management/loops/execution")).toMatchObject({
      label: "Loop Truth",
      status: "active-panel",
    });
  });

  it("classifies historical management URLs as planned workflows instead of pretending they are full pages", () => {
    expect(describeManagementRoute("/management/control-room")).toMatchObject({
      label: "Management Registry",
      status: "planned-workflow",
    });
    expect(describeManagementRoute("/management/persona-league")).toMatchObject({
      label: "Performance Review",
      status: "planned-workflow",
    });
    expect(describeManagementRoute("/management/nl/ask")).toMatchObject({
      label: "Management AI Ops",
      status: "planned-workflow",
    });
  });
});
