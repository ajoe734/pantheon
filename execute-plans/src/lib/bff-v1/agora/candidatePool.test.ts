// AG-FE-TR-002 — focused tests for candidate pool BFF client header contract.
//
// Coverage:
//   * reviewCandidateMember forwards If-Match, Idempotency-Key, and X-Request-Id when provided
//   * reviewCandidateMember sends POST to the correct URL
//   * listCandidatePoolMembers returns items + etag from response header
//   * triggerCandidatePoolScore forwards If-Match, Idempotency-Key, and X-Request-Id
//   * Read-only methods do NOT send mutation headers
//   * Error handling: non-2xx throws with message from error.message

import { describe, it, expect, vi, afterEach } from "vitest";
import {
  getCandidatePoolScore,
  listCandidatePoolMembers,
  reviewCandidateMember,
  triggerCandidatePoolScore,
} from "./candidatePool";

const BASE = "https://test.example";

function ok(body: unknown, status = 200, extraHeaders?: Record<string, string>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...extraHeaders },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

// ── reviewCandidateMember — header forwarding ────────────────────────────────

describe("reviewCandidateMember — If-Match, Idempotency-Key, and X-Request-Id", () => {
  it("sends If-Match, Idempotency-Key, and X-Request-Id when all provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ data: { lifecycle_state: "approved" } }));
    globalThis.fetch = fetchMock;

    await reviewCandidateMember(
      "pool-001",
      "artifact-001",
      { decision: "approve_for_monitoring", reviewed_by: "operator" },
      { ifMatch: '"etag-v3"', idempotencyKey: "idem-key-review-1", requestId: "req-review-1" },
      BASE,
    );

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(headers["If-Match"]).toBe('"etag-v3"');
    expect(headers["Idempotency-Key"]).toBe("idem-key-review-1");
    expect(headers["X-Request-Id"]).toBe("req-review-1");
  });

  it("sends If-Match and Idempotency-Key when provided (without requestId)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ data: { lifecycle_state: "approved" } }));
    globalThis.fetch = fetchMock;

    await reviewCandidateMember(
      "pool-001",
      "artifact-001",
      { decision: "approve_for_monitoring", reviewed_by: "operator" },
      { ifMatch: '"etag-v3"', idempotencyKey: "idem-key-review-1" },
      BASE,
    );

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(headers["If-Match"]).toBe('"etag-v3"');
    expect(headers["Idempotency-Key"]).toBe("idem-key-review-1");
    expect(headers["X-Request-Id"]).toBeUndefined();
  });

  it("does NOT send If-Match when omitted", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ data: {} }));
    globalThis.fetch = fetchMock;

    await reviewCandidateMember(
      "pool-001",
      "artifact-001",
      { decision: "park", rationale: "low signal", reviewed_by: "operator" },
      { idempotencyKey: "idem-key-2" },
      BASE,
    );

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(headers["If-Match"]).toBeUndefined();
    expect(headers["Idempotency-Key"]).toBe("idem-key-2");
  });

  it("does NOT send Idempotency-Key when omitted", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ data: {} }));
    globalThis.fetch = fetchMock;

    await reviewCandidateMember(
      "pool-001",
      "artifact-001",
      { decision: "reject", rationale: "stale", reviewed_by: "operator" },
      { ifMatch: '"etag-v1"' },
      BASE,
    );

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(headers["If-Match"]).toBe('"etag-v1"');
    expect(headers["Idempotency-Key"]).toBeUndefined();
  });

  it("POSTs to the correct review URL with encoded IDs", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ data: {} }));
    globalThis.fetch = fetchMock;

    await reviewCandidateMember(
      "pool/alpha",
      "artifact/001",
      { decision: "send_to_shadow", reviewed_by: "operator" },
      { ifMatch: '"etag"', idempotencyKey: "idem-1" },
      BASE,
    );

    expect(fetchMock.mock.calls[0][0]).toBe(
      `${BASE}/bff/agora/candidate-pools/pool%2Falpha/members/artifact%2F001/review`,
    );
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
  });
});

// ── listCandidatePoolMembers — ETag capture ──────────────────────────────────

describe("listCandidatePoolMembers — ETag in response", () => {
  it("returns items and etag from response header", async () => {
    const member = {
      artifact_id: "art-001",
      strategy_ref: "strat-001",
      lifecycle_state: "candidate",
      created_at: "2026-06-20T00:00:00Z",
    };
    const fetchMock = vi.fn().mockResolvedValue(
      ok({ items: [member] }, 200, { ETag: '"members-etag-v2"' }),
    );
    globalThis.fetch = fetchMock;

    const result = await listCandidatePoolMembers("pool-001", BASE);

    expect(result.items).toHaveLength(1);
    expect(result.items[0].artifact_id).toBe("art-001");
    expect(result.etag).toBe('"members-etag-v2"');
  });

  it("returns null etag when server omits ETag header", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ items: [] }));
    globalThis.fetch = fetchMock;

    const result = await listCandidatePoolMembers("pool-no-etag", BASE);

    expect(result.items).toHaveLength(0);
    expect(result.etag).toBeNull();
  });

  it("uses GET and sends no mutation headers", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ items: [] }));
    globalThis.fetch = fetchMock;

    await listCandidatePoolMembers("pool-001", BASE);

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("GET");
    expect((init.headers as Record<string, string>)["If-Match"]).toBeUndefined();
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBeUndefined();
  });
});

// ── triggerCandidatePoolScore — header forwarding ────────────────────────────

describe("triggerCandidatePoolScore — If-Match, Idempotency-Key, and X-Request-Id", () => {
  it("forwards If-Match, Idempotency-Key, and X-Request-Id when all provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({}, 202));
    globalThis.fetch = fetchMock;

    await triggerCandidatePoolScore(
      "pool-001",
      { ifMatch: '"score-etag-v1"', idempotencyKey: "idem-score-1", requestId: "req-score-1" },
      BASE,
    );

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(headers["If-Match"]).toBe('"score-etag-v1"');
    expect(headers["Idempotency-Key"]).toBe("idem-score-1");
    expect(headers["X-Request-Id"]).toBe("req-score-1");
  });

  it("forwards If-Match and Idempotency-Key when provided (without requestId)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({}, 202));
    globalThis.fetch = fetchMock;

    await triggerCandidatePoolScore(
      "pool-001",
      { ifMatch: '"score-etag-v1"', idempotencyKey: "idem-score-1" },
      BASE,
    );

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(headers["If-Match"]).toBe('"score-etag-v1"');
    expect(headers["Idempotency-Key"]).toBe("idem-score-1");
    expect(headers["X-Request-Id"]).toBeUndefined();
  });
});

// ── getCandidatePoolScore — read-only, no mutation headers ───────────────────

describe("getCandidatePoolScore — read-only, no mutation headers", () => {
  it("uses GET and sends no If-Match or Idempotency-Key headers", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ items: [] }));
    globalThis.fetch = fetchMock;

    await getCandidatePoolScore("pool-001", BASE);

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("GET");
    expect((init.headers as Record<string, string>)["If-Match"]).toBeUndefined();
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBeUndefined();
  });

  it("returns empty array when BFF returns status:queued", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ status: "queued", data: {} }));
    globalThis.fetch = fetchMock;

    const result = await getCandidatePoolScore("pool-queued", BASE);

    expect(result).toEqual([]);
  });
});

// ── Error handling ────────────────────────────────────────────────────────────

describe("error handling", () => {
  it("reviewCandidateMember throws with message from error envelope", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ error: { message: "precondition failed" } }),
        { status: 412, headers: { "Content-Type": "application/json" } },
      ),
    );
    globalThis.fetch = fetchMock;

    await expect(
      reviewCandidateMember(
        "pool-001",
        "artifact-001",
        { decision: "approve_for_monitoring", reviewed_by: "operator" },
        { ifMatch: '"stale-etag"', idempotencyKey: "idem-1" },
        BASE,
      ),
    ).rejects.toThrow("precondition failed");
  });

  it("listCandidatePoolMembers throws on non-2xx", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ error: { message: "pool not found" } }),
        { status: 404, headers: { "Content-Type": "application/json" } },
      ),
    );
    globalThis.fetch = fetchMock;

    await expect(listCandidatePoolMembers("missing-pool", BASE)).rejects.toThrow("pool not found");
  });
});
