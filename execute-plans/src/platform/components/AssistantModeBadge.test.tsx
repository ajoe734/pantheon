import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AssistantModeBadge } from "./AssistantModeBadge";

describe("AssistantModeBadge", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders kernel session mode, TTL, provider, command, snapshot, and audit refs", () => {
    render(
      <AssistantModeBadge
        signals={{
          mode: "kernel_debug",
          sessionId: "asst-kernel-001",
          ttlSeconds: 3600,
          provider: {
            name: "codex_cli",
            status: "ready",
            runId: "llmrun-001",
            runtime: "openclaw_gateway_cli_mount",
          },
          commandsEnabled: true,
          contextSnapshotAt: "2026-06-02T13:00:00Z",
          contextPackId: "ctx-001",
          auditRefs: ["audit-001"],
          canViewKernelControls: true,
        }}
      />,
    );

    expect(screen.getByTestId("assistant-kernel-signals")).toBeInTheDocument();
    expect(screen.getByTestId("assistant-mode")).toHaveTextContent("Kernel Debug");
    expect(screen.getByTestId("assistant-provider-status")).toHaveTextContent("codex_cli: ready");
    expect(screen.getByTestId("assistant-command-state")).toHaveTextContent("commands enabled");
    expect(screen.getByText("1h 0m TTL")).toBeInTheDocument();
    expect(screen.getByText("asst-kernel-001")).toBeInTheDocument();
    expect(screen.getByText("ctx-001")).toBeInTheDocument();
    expect(screen.getByText("audit-001")).toBeInTheDocument();
    expect(screen.getByText("llmrun-001")).toBeInTheDocument();
    expect(screen.getByText("openclaw_gateway_cli_mount")).toBeInTheDocument();
  });

  it("does not render kernel controls for user mode", () => {
    const { container } = render(
      <AssistantModeBadge
        signals={{
          mode: "user",
          provider: { name: "codex_cli", status: "ready" },
          canViewKernelControls: true,
        }}
      />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("does not render kernel controls when the operator is not authorized", () => {
    const { container } = render(
      <AssistantModeBadge
        signals={{
          mode: "kernel_observe",
          provider: { name: "codex_cli", status: "ready" },
          commandsEnabled: false,
          canViewKernelControls: false,
        }}
      />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("shows degraded provider state and deterministic fallback without hiding the panel", () => {
    render(
      <AssistantModeBadge
        signals={{
          mode: "kernel_debug",
          sessionId: "asst-degraded-001",
          ttlSeconds: 900,
          provider: {
            name: "codex_cli",
            status: "degraded",
            reason: "OPENCLAW_ADAPTER_UNREACHABLE",
            fallback: "deterministic_synthesis",
          },
          commandsEnabled: false,
          contextSnapshotAt: "2026-06-02T13:05:00Z",
          auditRefs: ["audit-degraded-001"],
          canViewKernelControls: true,
        }}
      />,
    );

    expect(screen.getByTestId("assistant-provider-status")).toHaveTextContent("codex_cli: degraded");
    expect(screen.getByTestId("assistant-command-state")).toHaveTextContent("commands disabled");
    expect(screen.getByText("deterministic_synthesis")).toBeInTheDocument();
    expect(screen.getByText("OPENCLAW_ADAPTER_UNREACHABLE")).toBeInTheDocument();
    expect(screen.getByText("audit-degraded-001")).toBeInTheDocument();
  });
});
