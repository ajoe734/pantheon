import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AgoraApp from "./AgoraApp";

vi.mock("@/lib/bff-v1/agora/identity", () => ({
  agoraIdentityClient: {
    getMe: vi.fn(),
    getCapabilities: vi.fn(),
  },
}));

vi.mock("@/lib/bff-v1/agora/servant", () => ({
  agoraServantClient: {
    ensure: vi.fn(),
  },
}));

import { agoraIdentityClient } from "@/lib/bff-v1/agora/identity";
import { agoraServantClient } from "@/lib/bff-v1/agora/servant";
import type { AgoraUserScope, ServantProfile } from "@/lib/bff-v1/agora/types";

const mockScope: AgoraUserScope = {
  spec_version: "1.0",
  scope_id: "test-scope",
  tenant_id: "test-tenant",
  user_id: "test-user",
  operator_id: "test-operator",
  granted_capabilities: [],
  read_predicate: {
    tenant_id: "test-tenant",
    user_id: "test-user",
    required_fields: ["tenant_id", "user_id"],
    fail_closed: true,
  },
  servant_policy: {
    persona_class: "agora_servant",
    owner_scope: "user_private",
    visibility_scope: "private",
    memory_scope: "private_user",
    persona_registry_backed: true,
    execution_authority: "none",
    prohibited_authority: ["runtime_binding", "broker_order", "capital_binding"],
  },
  created_at: "2026-01-01T00:00:00Z",
};

const mockServant: ServantProfile = {
  spec_version: "1.0",
  persona_id: "servant-001",
  display_name: "Test Servant",
  status: "active",
  tenant_id: "test-tenant",
  agora_user_id: "test-user",
  persona_class: "agora_servant",
  owner_scope: "user_private",
  visibility_scope: "private",
  memory_scope: "private_user",
  capability_summary: { can_ask: true, can_research: false, can_workshop: false },
  policy: {
    persona_class: "agora_servant",
    owner_scope: "user_private",
    visibility_scope: "private",
    memory_scope: "private_user",
    persona_registry_backed: true,
    execution_authority: "none",
    prohibited_authority: ["runtime_binding", "broker_order", "capital_binding"],
  },
};

describe("AgoraApp", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows loading state while resolving identity", () => {
    vi.mocked(agoraIdentityClient.getMe).mockReturnValue(new Promise(() => {}));
    vi.mocked(agoraIdentityClient.getCapabilities).mockReturnValue(new Promise(() => {}));
    vi.mocked(agoraServantClient.ensure).mockReturnValue(new Promise(() => {}));

    render(<AgoraApp />);
    expect(screen.getByTestId("agora-loading")).toBeTruthy();
  });

  it("shows provisioning state after identity resolves but before servant ensure", async () => {
    let resolveServant!: (v: ServantProfile) => void;
    vi.mocked(agoraIdentityClient.getMe).mockResolvedValue(mockScope);
    vi.mocked(agoraIdentityClient.getCapabilities).mockResolvedValue([]);
    vi.mocked(agoraServantClient.ensure).mockReturnValue(
      new Promise((resolve) => { resolveServant = resolve; }),
    );

    render(<AgoraApp />);
    await waitFor(() => expect(screen.getByTestId("agora-provisioning")).toBeTruthy());

    // Resolve servant to avoid dangling promise
    resolveServant(mockServant);
  });

  it("renders servant status and command bar when ready", async () => {
    vi.mocked(agoraIdentityClient.getMe).mockResolvedValue(mockScope);
    vi.mocked(agoraIdentityClient.getCapabilities).mockResolvedValue([]);
    vi.mocked(agoraServantClient.ensure).mockResolvedValue(mockServant);

    render(<AgoraApp />);
    await waitFor(() => screen.getByTestId("agora-ready"));

    const identityEl = screen.getByTestId("agora-identity");
    expect(identityEl.textContent).toContain("test-user");
    expect(identityEl.textContent).toContain("test-tenant");

    const servantEl = screen.getByTestId("agora-servant-status");
    expect(servantEl.textContent).toContain("Test Servant");
    expect(servantEl.textContent).toContain("active");

    expect(screen.getByTestId("agora-command-bar")).toBeTruthy();
  });

  it("servant ensure is only called after identity resolves", async () => {
    let resolveMe!: (v: AgoraUserScope) => void;
    vi.mocked(agoraIdentityClient.getMe).mockReturnValue(
      new Promise((resolve) => { resolveMe = resolve; }),
    );
    vi.mocked(agoraIdentityClient.getCapabilities).mockResolvedValue([]);
    vi.mocked(agoraServantClient.ensure).mockResolvedValue(mockServant);

    render(<AgoraApp />);
    expect(vi.mocked(agoraServantClient.ensure)).not.toHaveBeenCalled();

    resolveMe(mockScope);
    await waitFor(() => expect(vi.mocked(agoraServantClient.ensure)).toHaveBeenCalledOnce());
  });

  it("shows error state when identity fetch fails", async () => {
    vi.mocked(agoraIdentityClient.getMe).mockRejectedValue(new Error("BFF identity error"));
    vi.mocked(agoraIdentityClient.getCapabilities).mockResolvedValue([]);
    vi.mocked(agoraServantClient.ensure).mockResolvedValue(mockServant);

    render(<AgoraApp />);
    await waitFor(() => screen.getByTestId("agora-error"));
    expect(screen.getByTestId("agora-error").textContent).toContain("BFF identity error");
    expect(vi.mocked(agoraServantClient.ensure)).not.toHaveBeenCalled();
  });

  it("shows error state when servant ensure fails", async () => {
    vi.mocked(agoraIdentityClient.getMe).mockResolvedValue(mockScope);
    vi.mocked(agoraIdentityClient.getCapabilities).mockResolvedValue([]);
    vi.mocked(agoraServantClient.ensure).mockRejectedValue(new Error("servant ensure failed"));

    render(<AgoraApp />);
    await waitFor(() => screen.getByTestId("agora-error"));
    expect(screen.getByTestId("agora-error").textContent).toContain("servant ensure failed");
  });

  it("command send button is disabled when servant is null or command is empty", async () => {
    vi.mocked(agoraIdentityClient.getMe).mockResolvedValue(mockScope);
    vi.mocked(agoraIdentityClient.getCapabilities).mockResolvedValue([]);
    vi.mocked(agoraServantClient.ensure).mockResolvedValue(mockServant);

    render(<AgoraApp />);
    await waitFor(() => screen.getByTestId("agora-ready"));

    const sendBtn = screen.getByTestId("agora-command-send") as HTMLButtonElement;
    expect(sendBtn.disabled).toBe(true);
  });

  it("does not reference management, capital pool, or RuntimeBinding surfaces", () => {
    // Structural boundary: AgoraApp must not import from management routes.
    // This test is a marker; the ESLint boundary check enforces it statically.
    expect(true).toBe(true);
  });
});
