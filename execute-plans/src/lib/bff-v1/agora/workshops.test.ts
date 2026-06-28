import { afterEach, describe, expect, it, vi } from "vitest";
import { postWorkshopMessage } from "./workshops";

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
});

describe("postWorkshopMessage", () => {
  it("fetches current ETag before posting with If-Match and Idempotency-Key", async () => {
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
