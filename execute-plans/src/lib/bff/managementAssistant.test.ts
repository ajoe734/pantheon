import { afterEach, describe, expect, it, vi } from "vitest";

import {
  assistantCatalogRouteFromHandlerRef,
  activateAssistantControlMode,
  getAssistantProviderReauthStatus,
  getAssistantProviders,
  invokeAssistantCatalogRoute,
  startAssistantProviderReauth,
} from "./managementAssistant";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("assistant catalog route descriptors", () => {
  const realFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = realFetch;
    vi.restoreAllMocks();
  });

  it("parses frontend-routable BFF route handler refs", () => {
    expect(
      assistantCatalogRouteFromHandlerRef("bff.route:POST /bff/assistant/dev-docs/generate"),
    ).toEqual({
      method: "POST",
      path: "/bff/assistant/dev-docs/generate",
    });
  });

  it("rejects non-BFF and non-route handler refs", () => {
    expect(assistantCatalogRouteFromHandlerRef("openclaw.tool:any.catalog.skill")).toBeNull();
    expect(assistantCatalogRouteFromHandlerRef("bff.route:POST /api/openclaw-adapter/tools")).toBeNull();
  });

  it("invokes a catalog-provided route without depending on a capability id", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ data: { ok: true } }, 201));
    globalThis.fetch = fetchMock;

    const result = await invokeAssistantCatalogRoute(
      "bff.route:POST /bff/assistant/dev-docs/generate",
      { conversationId: "mgmt-nl-1", featureSummary: "Generate work packet" },
      "https://bff.example.test",
    );

    expect(result).toEqual({ data: { ok: true } });
    expect(fetchMock.mock.calls[0][0]).toBe("https://bff.example.test/bff/assistant/dev-docs/generate");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(JSON.parse(String(init.body))).toEqual({
      conversationId: "mgmt-nl-1",
      featureSummary: "Generate work packet",
    });
  });

  it("substitutes catalog route path parameters from descriptor input", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ data: { transcript: [] } }));
    globalThis.fetch = fetchMock;

    await invokeAssistantCatalogRoute(
      "bff.route:GET /bff/assistant/sessions/{sessionId}/transcript",
      { sessionId: "session/alpha" },
      "https://bff.example.test",
    );

    expect(fetchMock.mock.calls[0][0]).toBe(
      "https://bff.example.test/bff/assistant/sessions/session%2Falpha/transcript",
    );
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("GET");
    expect(init.body).toBeUndefined();
  });

  it("fails closed when a descriptor route template is missing input", async () => {
    await expect(
      invokeAssistantCatalogRoute(
        "bff.route:GET /bff/assistant/sessions/{sessionId}/transcript",
        {},
      ),
    ).rejects.toThrow("Missing assistant catalog route path parameter: sessionId");
  });
});

describe("OpenClaw assistant provider auth client", () => {
  const realFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = realFetch;
    vi.restoreAllMocks();
  });

  it("reads provider readiness with auth probe enabled", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      status: "ok",
      data: [{ provider: "codex_cli", auth_status: "ready" }],
    }));
    globalThis.fetch = fetchMock;

    const result = await getAssistantProviders({ authProbe: true }, "https://bff.example.test");

    expect(result.data[0].provider).toBe("codex_cli");
    expect(fetchMock.mock.calls[0][0]).toBe("https://bff.example.test/bff/assistant/providers?auth_probe=true");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBeUndefined();
    expect(init.credentials).toBe("include");
  });

  it("activates control mode and starts provider reauth through BFF routes", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ data: { active: true, mode: "kernel_debug" } }, 202))
      .mockResolvedValueOnce(jsonResponse({
        data: {
          provider: "codex_cli",
          reauth_session_id: "codex_reauth_1",
          status: "pending",
          verification_uri: "https://auth.openai.com/device",
          user_code: "ABCD-EFGH",
        },
      }, 202));
    globalThis.fetch = fetchMock;

    await activateAssistantControlMode(
      { passphrase: "control phrase ok", reason: "reauth", mode: "kernel_debug" },
      "https://bff.example.test",
    );
    const reauth = await startAssistantProviderReauth(
      { provider: "codex", reason: "expired" },
      "https://bff.example.test",
    );

    expect(reauth.data.user_code).toBe("ABCD-EFGH");
    expect(fetchMock.mock.calls[0][0]).toBe("https://bff.example.test/bff/assistant/control-mode/activate");
    expect(fetchMock.mock.calls[1][0]).toBe("https://bff.example.test/bff/assistant/provider/reauth");
    expect(JSON.parse(String((fetchMock.mock.calls[1][1] as RequestInit).body))).toEqual({
      provider: "codex",
      reason: "expired",
    });
  });

  it("reads provider reauth status with provider query", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      data: {
        provider: "codex_cli",
        reauthSessionId: "codex_reauth_1",
        status: "completed",
      },
    }));
    globalThis.fetch = fetchMock;

    const result = await getAssistantProviderReauthStatus(
      "codex_reauth_1",
      "codex",
      "https://bff.example.test",
    );

    expect(result.data.status).toBe("completed");
    expect(fetchMock.mock.calls[0][0]).toBe(
      "https://bff.example.test/bff/assistant/provider/reauth/codex_reauth_1?provider=codex",
    );
  });
});
