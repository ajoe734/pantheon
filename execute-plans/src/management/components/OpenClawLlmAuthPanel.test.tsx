import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OpenClawLlmAuthPanel, type OpenClawLlmAuthApi } from "./OpenClawLlmAuthPanel";

function api(overrides: Partial<OpenClawLlmAuthApi> = {}): OpenClawLlmAuthApi {
  return {
    getProviders: vi.fn().mockResolvedValue({
      status: "ok",
      data: [
        {
          provider: "codex_cli",
          provider_name: "codex",
          runtime: "openclaw_gateway_cli_mount",
          ready: false,
          status: "degraded",
          auth_status: "failed",
          degraded_reason: "codex_auth_unavailable",
          mount_mode: "rw",
        },
        {
          provider: "claude",
          provider_name: "claude",
          runtime: "openclaw_gateway_cli_mount",
          ready: false,
          status: "degraded",
          auth_status: "failed",
          degraded_reason: "claude_auth_failure",
          mount_mode: "rw",
        },
      ],
    }),
    getControlMode: vi.fn().mockResolvedValue({
      data: {
        active: false,
        state: "inactive",
        reason: "not_active",
      },
    }),
    activateControlMode: vi.fn().mockResolvedValue({
      data: {
        active: true,
        state: "active",
        mode: "kernel_debug",
      },
    }),
    startReauth: vi.fn().mockResolvedValue({
      data: {
        provider: "codex_cli",
        status: "pending",
        reauth_session_id: "codex_reauth_1",
        verification_uri: "https://auth.openai.com/device",
        user_code: "ABCD-EFGH",
      },
    }),
    getReauthStatus: vi.fn().mockResolvedValue({
      data: {
        provider: "codex_cli",
        status: "completed",
        reauth_session_id: "codex_reauth_1",
      },
    }),
    ...overrides,
  };
}

describe("OpenClawLlmAuthPanel", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows provider auth failures and disables reauth until control mode is active", async () => {
    render(<OpenClawLlmAuthPanel api={api()} />);

    expect(await screen.findByText("codex")).toBeTruthy();
    expect(screen.getByText("codex_auth_unavailable")).toBeTruthy();
    expect(screen.getByText("claude_auth_failure")).toBeTruthy();
    expect(screen.getByText("Activate kernel debug before reauth.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Reauth unsupported" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("activates control mode, starts Codex reauth, and shows device flow fields", async () => {
    const fakeApi = api();
    const user = userEvent.setup();
    render(<OpenClawLlmAuthPanel api={fakeApi} />);

    await screen.findByText("codex");
    await user.type(screen.getByLabelText("Control mode passphrase"), "control phrase ok");
    await user.click(screen.getByRole("button", { name: /Activate/i }));

    await waitFor(() => {
      expect(fakeApi.activateControlMode).toHaveBeenCalledWith({
        mode: "kernel_debug",
        passphrase: "control phrase ok",
        reason: "Refresh OpenClaw LLM provider auth",
        ttlSeconds: 900,
        idleTtlSeconds: 600,
      });
    });

    await user.click(screen.getAllByRole("button", { name: /Start reauth/i })[0]);

    await waitFor(() => {
      expect(fakeApi.startReauth).toHaveBeenCalledWith({
        provider: "codex_cli",
        reason: "Refresh OpenClaw LLM provider auth",
      });
    });
    expect(screen.getByText("ABCD-EFGH")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Open verification/i }).getAttribute("href")).toBe(
      "https://auth.openai.com/device",
    );
  });

  it("refreshes a reauth session status", async () => {
    const fakeApi = api({
      getControlMode: vi.fn().mockResolvedValue({
        data: { active: true, state: "active", mode: "kernel_debug" },
      }),
    });
    const user = userEvent.setup();
    render(<OpenClawLlmAuthPanel api={fakeApi} />);

    await screen.findByText("codex");
    await user.click(screen.getAllByRole("button", { name: /Start reauth/i })[0]);
    await screen.findByText("ABCD-EFGH");
    await user.click(screen.getByRole("button", { name: /Status/i }));

    await waitFor(() => {
      expect(fakeApi.getReauthStatus).toHaveBeenCalledWith("codex_reauth_1", "codex_cli");
    });
  });
});
