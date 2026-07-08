import { afterEach, describe, expect, it, vi } from "vitest";
import {
  getWorkshop,
  getWorkshopReadiness,
  listWorkshopCards,
  listWorkshopEvents,
  listWorkshops,
  postWorkshopMessage,
} from "./workshops";
import { setAuthProvider } from "../headers";

const BASE = "https://bff.example.test";

function ok(body: unknown, status = 200, headers?: HeadersInit): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
      ...(headers ?? {}),
    },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  setAuthProvider(undefined);
});

describe("workshop read helpers", () => {
  it("sends shared BFF auth headers on list/detail/cards/readiness/events reads", async () => {
    setAuthProvider(() => ({ bearerToken: "workshop-token", tenantId: "tenant-live" }));
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(ok({ data: [{ workshop_id: "ws-001" }] }))
      .mockResolvedValueOnce(ok({ data: { workshop_id: "ws-001" } }))
      .mockResolvedValueOnce(ok({ data: { highest_ready_gate: "trading_room" } }))
      .mockResolvedValueOnce(ok({ data: [{ card_id: "card-001" }] }))
      .mockResolvedValueOnce(ok({ data: [{ event_id: "event-001" }] }));
    globalThis.fetch = fetchMock;

    await listWorkshops(BASE);
    await getWorkshop("ws-001", BASE);
    await getWorkshopReadiness("ws-001", BASE);
    await listWorkshopCards("ws-001", BASE);
    await listWorkshopEvents("ws-001", BASE);

    expect(fetchMock).toHaveBeenCalledTimes(5);
    for (const call of fetchMock.mock.calls) {
      const init = call[1] as RequestInit;
      const headers = init.headers as Record<string, string>;
      expect(init.credentials).toBe("include");
      expect(headers.Accept).toBe("application/json");
      expect(headers.Authorization).toBe("Bearer workshop-token");
      expect(headers["X-Tenant-Id"]).toBe("tenant-live");
    }
  });
});

describe("postWorkshopMessage", () => {
  it("fetches current ETag before posting with If-Match and Idempotency-Key", async () => {
    setAuthProvider(() => ({ bearerToken: "workshop-token", tenantId: "tenant-live" }));
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(ok({ data: { workshop_id: "ws/001" } }, 200, { ETag: 'W/"workshop:ws/001:v3"' }))
      .mockResolvedValueOnce(ok({ data: { event_id: "ev-001" } }, 202));
    globalThis.fetch = fetchMock;

    const result = await postWorkshopMessage("ws/001", { content: "要求研究" }, BASE);

    expect(result.event_id).toBe("ev-001");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/bff/agora/workshops/ws%2F001`);
    expect(fetchMock.mock.calls[1][0]).toBe(`${BASE}/bff/agora/workshops/ws%2F001/messages`);

    const init = fetchMock.mock.calls[1][1] as RequestInit;
    const headers = init.headers as Record<string, string>;
    expect(init.method).toBe("POST");
    expect(headers.Authorization).toBe("Bearer workshop-token");
    expect(headers["X-Tenant-Id"]).toBe("tenant-live");
    expect(headers["If-Match"]).toBe('W/"workshop:ws/001:v3"');
    expect(headers["Idempotency-Key"]).toBeTruthy();
    expect(JSON.parse(String(init.body))).toEqual({
      content: "要求研究",
      attachment_refs: [],
    });
  });

  it("fails before POST when the workshop ETag is unavailable", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(ok({ data: { workshop_id: "ws-001" } }));
    globalThis.fetch = fetchMock;

    await expect(postWorkshopMessage("ws-001", { content: "要求研究" }, BASE)).rejects.toThrow(
      "Workshop ETag is required",
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
