import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DashboardChangeLog, type DashboardRecipeVersionSummary } from "./DashboardChangeLog";

const versions: DashboardRecipeVersionSummary[] = [
  {
    version: 1,
    previous_version: null,
    status: "proposal",
    content_sha256: "a".repeat(64),
    generated_by: "servant",
    change_reason: "Initial proposal",
    created_at: "2026-06-20T00:00:00Z",
  },
  {
    version: 2,
    previous_version: 1,
    status: "active",
    content_sha256: "b".repeat(64),
    generated_by: "user",
    change_reason: "Accepted proposal",
    created_at: "2026-06-20T01:00:00Z",
  },
  {
    version: 3,
    previous_version: 2,
    status: "rolled_back",
    content_sha256: "c".repeat(64),
    generated_by: "user",
    change_reason: "Rollback to version 1",
    created_at: "2026-06-20T02:00:00Z",
  },
];

describe("DashboardChangeLog", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("renders immutable version history and emits the OpenAPI rollback body for a selected historical version", async () => {
    const onRollback = vi.fn();
    const etag = "\"recipe:rec-001:v3:cccccccc\"";
    render(
      <DashboardChangeLog
        activeVersion={3}
        etag={etag}
        idempotencyKey="22222222-2222-4222-8222-222222222222"
        onRollback={onRollback}
        recipeId="rec-001"
        versions={versions}
      />,
    );

    expect(screen.getByTestId("dashboard-version-table").textContent).toContain("Rollback to version 1");
    expect(screen.getByText("v3")).toBeTruthy();

    const selectButtons = screen.getAllByRole("button", { name: /Select/i });
    expect((selectButtons[0] as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(selectButtons[2]);
    expect(screen.getByTestId("dashboard-rollback-selection").textContent).toContain("v1");

    fireEvent.change(screen.getByLabelText("Rollback reason"), { target: { value: "restore stable recipe" } });
    fireEvent.click(screen.getByRole("button", { name: /Rollback/i }));

    await waitFor(() => {
      expect(onRollback).toHaveBeenCalledWith({
        recipe_id: "rec-001",
        headers: {
          "If-Match": etag,
          "Idempotency-Key": "22222222-2222-4222-8222-222222222222",
        },
        body: {
          expected_version: 3,
          target_version: 1,
          reason: "restore stable recipe",
        },
      });
    });
  });

  it("requires a selected historical version before rollback", () => {
    render(
      <DashboardChangeLog
        activeVersion={3}
        etag="etag"
        onRollback={vi.fn()}
        recipeId="rec-001"
        versions={versions}
      />,
    );

    expect((screen.getByRole("button", { name: /Rollback/i }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("surfaces concurrent modification details for rollback failures", () => {
    render(
      <DashboardChangeLog
        activeVersion={3}
        concurrencyError={{
          code: "CONCURRENT_MODIFICATION",
          message: "Dashboard recipe changed after the client snapshot.",
          details: {
            expected_version: 3,
            current_version: 4,
            current_etag: "\"recipe:rec-001:v4:dddddddd\"",
            latest_href: "/bff/agora/dashboard-recipes/rec-001",
          },
        }}
        recipeId="rec-001"
        versions={versions}
      />,
    );

    const text = screen.getByTestId("dashboard-changelog-concurrency-error").textContent;
    expect(text).toContain("CONCURRENT_MODIFICATION");
    expect(text).toContain("dddddddd");
    expect(text).toContain("/bff/agora/dashboard-recipes/rec-001");
  });
});
