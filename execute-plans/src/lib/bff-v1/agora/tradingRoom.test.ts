// AG-FE-TR-002 — focused tests for trading room BFF client header contract.
//
// Coverage:
//   * decideOnEvent forwards If-Match, Idempotency-Key, and X-Request-Id when provided
//   * decideOnEvent does NOT send headers when options are omitted
//   * decideOnEvent POSTs to the correct URL
//   * listDecisionEvents returns items + etag from response header
//   * Read-only methods (getTradingRoom, listDecisionEvents, getDecisionEvent) do NOT send mutation headers
//   * Error handling: non-2xx throws with message from error.message

import { describe, it, expect, vi, afterEach } from "vitest";
import {
  decideOnEvent,
  listDecisionEvents,
  getTradingRoom,
  getDecisionEvent,
} from "./tradingRoom";

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

// ── decideOnEvent — header forwarding ────────────────────────────────────────

describe("decideOnEvent — If-Match, Idempotency-Key, X-Request-Id", () => {
  it("forwards all three headers when provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ data: { decision_state: "approved_by_trader" } }));
    globalThis.fetch = fetchMock;

    await decideOnEvent(
      "evt-001",
      { decision: "approve" },
      {
        ifMatch: '"evt-etag-v1"',
        idempotencyKey: "idem-decide-1",
        requestId: "req-decide-1",
      },
      BASE,
    );

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(headers["If-Match"]).toBe('"evt-etag-v1"');
    expect(headers["Idempotency-Key"]).toBe("idem-decide-1");
    expect(headers["X-Request-Id"]).toBe("req-decide-1");
  });

  it("sends X-Request-Id without If-Match when only requestId provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ data: {} }));
    globalThis.fetch = fetchMock;

    await decideOnEvent(
      "evt-002",
      { decision: "reject" },
      { idempotencyKey: "idem-2", requestId: "req-2" },
      BASE,
    );

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(headers["X-Request-Id"]).toBe("req-2");
    expect(headers["Idempotency-Key"]).toBe("idem-2");
    expect(headers["If-Match"]).toBeUndefined();
  });

  it("does NOT send mutation headers when options are omitted", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ data: {} }));
    globalThis.fetch = fetchMock;

    await decideOnEvent("evt-003", { decision: "defer" }, undefined, BASE);

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(headers["If-Match"]).toBeUndefined();
    expect(headers["Idempotency-Key"]).toBeUndefined();
    expect(headers["X-Request-Id"]).toBeUndefined();
  });

  it("POSTs to the correct decisions URL with encoded event ID", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ data: {} }));
    globalThis.fetch = fetchMock;

    await decideOnEvent(
      "evt/abc-001",
      { decision: "modify" },
      { requestId: "req-1" },
      BASE,
    );

    expect(fetchMock.mock.calls[0][0]).toBe(
      `${BASE}/bff/agora/trading-room/decision-events/evt%2Fabc-001/decisions`,
    );
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
  });

  it("sends Content-Type application/json", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ data: {} }));
    globalThis.fetch = fetchMock;

    await decideOnEvent("evt-004", { decision: "approve" }, { requestId: "req-4" }, BASE);

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(headers["Content-Type"]).toBe("application/json");
  });
});

// ── listDecisionEvents — ETag capture ────────────────────────────────────────

describe("listDecisionEvents — ETag in response", () => {
  const mockEvent = {
    spec_version: "1.0",
    decision_event_id: "evt-list-001",
    event_kind: "entry",
    origin: "strategy_signal",
    strategy_id: "strat-001",
    strategy_spec_registry_id: "reg-001",
    subject: { symbol: "TSLA" },
    state: "pending_review",
    triggered_at: "2026-06-22T10:00:00Z",
    confidence: { value: 0.7, basis: "model", calibration_state: "calibrated" },
    probability: { value: 0.6, target_outcome: "price_up", horizon: "5d" },
    expected_value: { horizon: "5d", unit: "pct_return", gross: 0.05, cost: 0.002, net: 0.048, downside: -0.01 },
    rationale: [],
    risk_notes: [],
    evidence_refs: [],
    invalidation: { conditions: [], current_state: "valid" },
    suggested_action: "enter",
    no_order_route_proof: "agora_decision_support_only",
  };

  it("returns items and etag from response header", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      ok({ items: [mockEvent] }, 200, { ETag: '"events-etag-v1"' }),
    );
    globalThis.fetch = fetchMock;

    const result = await listDecisionEvents(undefined, BASE);

    expect(result.items).toHaveLength(1);
    expect(result.items[0].decision_event_id).toBe("evt-list-001");
    expect(result.etag).toBe('"events-etag-v1"');
  });

  it("returns null etag when server omits ETag header", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ items: [] }));
    globalThis.fetch = fetchMock;

    const result = await listDecisionEvents(undefined, BASE);

    expect(result.items).toHaveLength(0);
    expect(result.etag).toBeNull();
  });

  it("uses GET and sends no mutation headers", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ items: [] }));
    globalThis.fetch = fetchMock;

    await listDecisionEvents(undefined, BASE);

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("GET");
    expect((init.headers as Record<string, string>)["If-Match"]).toBeUndefined();
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBeUndefined();
    expect((init.headers as Record<string, string>)["X-Request-Id"]).toBeUndefined();
  });

  it("appends event_kind filter to query string", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ items: [] }));
    globalThis.fetch = fetchMock;

    await listDecisionEvents({ event_kind: "entry" }, BASE);

    expect(fetchMock.mock.calls[0][0]).toBe(
      `${BASE}/bff/agora/trading-room/decision-events?event_kind=entry`,
    );
  });
});

// ── Read-only methods — no mutation headers ──────────────────────────────────

describe("getTradingRoom — read-only, no mutation headers", () => {
  it("uses GET and sends no X-Request-Id, If-Match, or Idempotency-Key", async () => {
    const aggregate = {
      spec_version: "1.0",
      user_scope_ref: "scope-1",
      strategies: [],
      queue_summary: { entry: 0, add: 0, reduce: 0, exit: 0, review: 0 },
      risk_summary: { state: "normal" },
      snapshot_at: "2026-06-22T10:00:00Z",
      data_cutoff: "2026-06-22T09:55:00Z",
    };
    const fetchMock = vi.fn().mockResolvedValue(ok({ data: aggregate }));
    globalThis.fetch = fetchMock;

    await getTradingRoom(BASE);

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("GET");
    expect((init.headers as Record<string, string>)["X-Request-Id"]).toBeUndefined();
    expect((init.headers as Record<string, string>)["If-Match"]).toBeUndefined();
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBeUndefined();
  });
});

describe("getDecisionEvent — returns null on 404", () => {
  it("returns null for 404", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("", { status: 404 }));
    globalThis.fetch = fetchMock;

    const result = await getDecisionEvent("missing-evt", BASE);

    expect(result).toBeNull();
  });
});

// ── Error handling ────────────────────────────────────────────────────────────

describe("error handling", () => {
  it("decideOnEvent throws on 412 with message from error envelope", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ error: { message: "etag mismatch" } }),
        { status: 412, headers: { "Content-Type": "application/json" } },
      ),
    );
    globalThis.fetch = fetchMock;

    await expect(
      decideOnEvent(
        "evt-001",
        { decision: "approve" },
        { ifMatch: '"stale-etag"', idempotencyKey: "idem-1", requestId: "req-1" },
        BASE,
      ),
    ).rejects.toThrow("etag mismatch");
  });

  it("decideOnEvent throws on 409 (idempotency replay conflict)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ error: { message: "idempotency key conflict" } }),
        { status: 409, headers: { "Content-Type": "application/json" } },
      ),
    );
    globalThis.fetch = fetchMock;

    await expect(
      decideOnEvent(
        "evt-002",
        { decision: "approve" },
        { idempotencyKey: "idem-dupe", requestId: "req-dupe" },
        BASE,
      ),
    ).rejects.toThrow("idempotency key conflict");
  });

  it("listDecisionEvents throws on non-2xx", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ error: { message: "unauthorized" } }),
        { status: 401, headers: { "Content-Type": "application/json" } },
      ),
    );
    globalThis.fetch = fetchMock;

    await expect(listDecisionEvents(undefined, BASE)).rejects.toThrow("unauthorized");
  });
});
