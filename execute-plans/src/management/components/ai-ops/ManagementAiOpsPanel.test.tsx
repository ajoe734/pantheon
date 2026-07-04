import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getManagementAssistantConversation,
  postManagementAssistantAsk,
} from "@/lib/bff/managementAssistant";

import { ManagementAiOpsPanel } from "./ManagementAiOpsPanel";

vi.mock("@/lib/bff/managementAssistant", () => ({
  postManagementAssistantAsk: vi.fn(),
  getManagementAssistantConversation: vi.fn(),
}));

const postAskMock = vi.mocked(postManagementAssistantAsk);
const getConversationMock = vi.mocked(getManagementAssistantConversation);

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, resolve, reject };
}

describe("ManagementAiOpsPanel", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    window.history.pushState({}, "", "/");
  });

  it("renders an empty conversation state without a selected session", () => {
    render(<ManagementAiOpsPanel mode="conversations" />);

    expect(screen.getByTestId("management-ai-ops-panel").getAttribute("data-active-mode")).toBe("conversations");
    expect(screen.getByText("No conversation selected")).toBeTruthy();
    expect(getConversationMock).not.toHaveBeenCalled();
  });

  it("shows conversation loading and then an empty readback", async () => {
    const pending = deferred<Record<string, unknown>>();
    getConversationMock.mockReturnValue(pending.promise);

    render(<ManagementAiOpsPanel mode="conversations" initialSessionId="mgmt-loading-session" />);

    expect((await screen.findByRole("status")).textContent).toContain("Loading conversation");
    expect(getConversationMock).toHaveBeenCalledWith("mgmt-loading-session", undefined);

    pending.resolve({
      data: {
        session_id: "mgmt-loading-session",
        turns: [],
      },
    });

    await screen.findByText("No turns returned");
  });

  it("submits an ask through the management assistant helper and reads back the server conversation", async () => {
    postAskMock.mockResolvedValue({
      data: {
        answer: "Provider grounded management answer.",
        session_id: "mgmt-success-session",
        trace_id: "trace-success",
        confidence: "high",
        sources: ["portfolio"],
        provider_status: {
          provider: "codex_cli",
          status: "completed",
          used: true,
        },
        conversation: {
          href: "/bff/management/ai/conversations/mgmt-success-session",
        },
      },
    });
    getConversationMock.mockResolvedValue({
      data: {
        session_id: "mgmt-success-session",
        turn_count: 2,
        turns: [
          {
            id: "turn-user",
            role: "user",
            text: "What is unhealthy?",
            created_at: "2026-07-03T00:00:00Z",
          },
          {
            id: "turn-assistant",
            role: "assistant",
            text: "Provider grounded management answer.",
            created_at: "2026-07-03T00:00:01Z",
            provider_status: {
              status: "completed",
              provider: "codex_cli",
            },
          },
        ],
      },
    });

    render(<ManagementAiOpsPanel mode="ask" />);

    fireEvent.change(screen.getByLabelText("Question"), {
      target: { value: "What is unhealthy?" },
    });
    fireEvent.submit(screen.getByTestId("management-ai-ask-form"));

    await waitFor(() => expect(postAskMock).toHaveBeenCalledTimes(1));
    const askBody = postAskMock.mock.calls[0][0];
    expect(askBody).toMatchObject({
      question: "What is unhealthy?",
      focus: "operations",
      metadata: {
        sourcePanel: "ManagementAiOpsPanel",
        panelMode: "ask",
      },
    });
    expect(askBody.ui?.route.path).toBe("/management/nl/ask");
    expect(askBody).not.toHaveProperty("sessionId");

    const askResult = await screen.findByTestId("management-ai-ask-result");
    expect(within(askResult).getByText("Provider grounded management answer.")).toBeTruthy();
    expect(screen.getByTestId("management-ai-ask-result-state").textContent).toContain("Accepted by BFF");
    expect(getConversationMock).toHaveBeenCalledWith("mgmt-success-session", undefined);

    const assistantTurn = await screen.findByTestId("management-ai-turn-turn-assistant");
    expect(within(assistantTurn).getByText("Provider grounded management answer.")).toBeTruthy();
  });

  it("renders provider degraded state without treating fallback as local success", async () => {
    postAskMock.mockResolvedValue({
      data: {
        answer: "Management summary for question: fallback.",
        session_id: "mgmt-degraded-session",
        provider_status: {
          status: "degraded",
          reason: "CODEX_AUTH_UNAVAILABLE",
          display_message: "Codex service-user session expired.",
          fallback: "deterministic_synthesis",
          used: false,
        },
        conversation: {
          href: "/bff/management/ai/conversations/mgmt-degraded-session",
        },
      },
    });
    getConversationMock.mockResolvedValue({
      data: {
        session_id: "mgmt-degraded-session",
        local_only: true,
        missing_in_store: true,
        degraded_reason: "Conversation is missing in the durable store.",
        turns: [],
      },
    });

    render(<ManagementAiOpsPanel mode="ask" />);

    fireEvent.change(screen.getByLabelText("Question"), {
      target: { value: "Check provider health" },
    });
    fireEvent.submit(screen.getByTestId("management-ai-ask-form"));

    await screen.findByText("Management summary for question: fallback.");
    expect(screen.getByTestId("management-ai-ask-result-state").textContent).toContain("Degraded");
    expect(screen.getByTestId("management-ai-degraded-banner").textContent).toContain(
      "Codex service-user session expired.",
    );
    await screen.findByText("Conversation not in durable store");
  });

  it("labels authorization failures from conversation readback", async () => {
    getConversationMock.mockRejectedValue(
      new Error("GET /bff/management/ai/conversations/mgmt-owned-session failed 403: forbidden"),
    );

    render(<ManagementAiOpsPanel mode="conversations" initialSessionId="mgmt-owned-session" />);

    expect(await screen.findByText("Authorization required")).toBeTruthy();
    expect(screen.getByText(/failed 403/)).toBeTruthy();
  });
});
