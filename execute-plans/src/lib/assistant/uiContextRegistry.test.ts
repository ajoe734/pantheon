import { describe, expect, it } from "vitest";

import { assistantUiContextMetadata, buildAssistantUiContext } from "./uiContextRegistry";

describe("buildAssistantUiContext", () => {
  it("serializes route, form, table, visible errors, and redacts sensitive fields", () => {
    const context = buildAssistantUiContext({
      now: () => new Date("2026-06-07T07:30:00Z"),
      route: { path: "/agora/ask", name: "Ask Personas", query: { tab: "debug" } },
      visibleSurface: {
        workbench: "Platform Admin / Agora",
        screenId: "screen-agora-ask-personas",
        componentId: "surface-management-assistant-panel",
      },
      formRegistry: {
        formId: "ask-personas-prompt",
        action: {
          kind: "bff_route",
          method: "POST",
          href: "/bff/management/nl/ask",
          idempotencyRequired: true,
          submitAuthority: "bff",
        },
        dirty: true,
        errors: [],
        fields: [
          {
            name: "prompt",
            label: "Prompt",
            value: "Why is the worker stale?",
            valueState: "present",
            validatorRefs: [{ type: "required" }],
          },
          {
            name: "providerToken",
            label: "Provider Token",
            value: "secret-token-value",
            valueState: "present",
            validatorRefs: [],
          },
        ],
      },
      tableContext: {
        tableId: "worker-status",
        filters: [{ key: "status", operator: "eq", value: "running" }],
        selectedRows: [{ id: "run-001", entityType: "worker" }],
        visibleColumns: ["run_id", "status"],
      },
      visibleErrors: [{ message: "Provider degraded", source: "assistant" }],
      contextRefs: [{ sourceId: "orchestrator", href: "/bff/assistant/orchestrator/status" }],
    });

    expect(context).toMatchObject({
      version: "assistant_ui_context.v1",
      capturedAt: "2026-06-07T07:30:00.000Z",
      route: { path: "/agora/ask", query: { tab: "debug" } },
      formRegistry: {
        action: {
          href: "/bff/management/nl/ask",
          submitAuthority: "bff",
        },
      },
      tableContext: {
        tableId: "worker-status",
      },
    });
    expect(context.formRegistry?.fields[0].value).toBe("Why is the worker stale?");
    expect(context.formRegistry?.fields[1]).toMatchObject({
      name: "providerToken",
      valueState: "redacted",
    });
    expect("value" in (context.formRegistry?.fields[1] ?? {})).toBe(false);
    expect(JSON.stringify(context)).not.toContain("secret-token-value");
  });

  it("adds assistant metadata without making frontend hints authoritative", () => {
    const context = buildAssistantUiContext({
      now: () => new Date("2026-06-07T07:30:00Z"),
      route: { path: "/agora/ask" },
    });

    expect(assistantUiContextMetadata(context, { source: "ask-personas-ui" })).toMatchObject({
      source: "ask-personas-ui",
      assistant_context_version: "assistant_ui_context.v1",
      assistant_ui_context: context,
    });
  });
});
