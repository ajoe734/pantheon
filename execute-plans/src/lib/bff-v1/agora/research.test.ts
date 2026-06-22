// AG-FE-RS-001 — focused tests for research BFF client.
//
// Coverage:
//   * CommandResponse type shape: status/data/meta (NOT ok/request_id/idempotency_key)
//   * X-Request-Id header forwarded by all 5 mutating methods
//   * Idempotency-Key and If-Match headers forwarded when provided
//   * commandResponseFrom normalises server payload into CommandResponse
//   * Read-only methods do NOT forward X-Request-Id
//   * Error handling: non-2xx throws with message from error.message

import { describe, it, expect, vi, afterEach } from "vitest";
import {
  createWorkshopResearchPlan,
  approveResearchPlan,
  cancelResearchPlan,
  dispatchResearchPlan,
  cancelResearchRun,
  listWorkshopResearchPlans,
  getResearchPlan,
  listResearchPlanRuns,
  getResearchRun,
  listResearchRunArtifacts,
} from "./research";
import type { CommandResponse } from "./research";

const BASE = "https://test.example";

function ok(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

// ── CommandResponse type / shape ────────────────────────────────────────────

describe("CommandResponse — shape matches agora_v1_3 schema", () => {
  it("CommandResponse requires status/data/meta, not ok/request_id/idempotency_key", () => {
    const r: CommandResponse = { status: "accepted", data: null, meta: {} };
    expect(r.status).toBe("accepted");
    expect("ok" in r).toBe(false);
    expect("request_id" in r).toBe(false);
    expect("idempotency_key" in r).toBe(false);
  });

  it("accepts all valid status enum values", () => {
    const statuses: CommandResponse["status"][] = ["accepted", "queued", "completed"];
    for (const s of statuses) {
      const r: CommandResponse = { status: s, data: null, meta: {} };
      expect(r.status).toBe(s);
    }
  });
});

// ── commandResponseFrom normalization ────────────────────────────────────────

describe("approveResearchPlan — CommandResponse normalization", () => {
  it("extracts status/data/meta from server response body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      ok({ status: "accepted", data: { plan_id: "plan-001" }, meta: { trace: "t1" } }),
    );
    globalThis.fetch = fetchMock;

    const result = await approveResearchPlan("plan-001", {}, BASE);

    expect(result.status).toBe("accepted");
    expect((result.data as Record<string, unknown>)?.plan_id).toBe("plan-001");
    expect(result.meta.trace).toBe("t1");
  });

  it("falls back to 'accepted' when server omits status field", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ data: null, meta: {} }));
    globalThis.fetch = fetchMock;

    const result = await approveResearchPlan("plan-002", {}, BASE);

    expect(result.status).toBe("accepted");
    expect(result.meta).toEqual({});
  });
});

// ── X-Request-Id forwarding ──────────────────────────────────────────────────

describe("createWorkshopResearchPlan — X-Request-Id header", () => {
  it("sends X-Request-Id when requestId option is provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ data: {}, meta: {} }, 201));
    globalThis.fetch = fetchMock;

    await createWorkshopResearchPlan("ws-001", {}, { requestId: "req-abc" }, BASE);

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["X-Request-Id"]).toBe("req-abc");
  });

  it("does NOT send X-Request-Id when requestId is omitted", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ data: {}, meta: {} }, 201));
    globalThis.fetch = fetchMock;

    await createWorkshopResearchPlan("ws-001", {}, {}, BASE);

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["X-Request-Id"]).toBeUndefined();
  });
});

describe("approveResearchPlan — X-Request-Id header", () => {
  it("sends X-Request-Id when requestId is provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ status: "accepted", data: null, meta: {} }));
    globalThis.fetch = fetchMock;

    await approveResearchPlan("plan-001", { requestId: "req-approve-1" }, BASE);

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["X-Request-Id"]).toBe("req-approve-1");
  });
});

describe("cancelResearchPlan — X-Request-Id header", () => {
  it("sends X-Request-Id when requestId is provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ status: "accepted", data: null, meta: {} }));
    globalThis.fetch = fetchMock;

    await cancelResearchPlan("plan-001", { requestId: "req-cancel-plan-1" }, BASE);

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["X-Request-Id"]).toBe("req-cancel-plan-1");
  });
});

describe("dispatchResearchPlan — X-Request-Id header", () => {
  it("sends X-Request-Id when requestId is provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ status: "queued", data: null, meta: {} }));
    globalThis.fetch = fetchMock;

    await dispatchResearchPlan("plan-001", { requestId: "req-dispatch-1" }, BASE);

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["X-Request-Id"]).toBe("req-dispatch-1");
  });

  it("POSTs to /bff/agora/research-plans/{plan_id}/runs", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ status: "queued", data: null, meta: {} }));
    globalThis.fetch = fetchMock;

    await dispatchResearchPlan("plan-xyz", { requestId: "req-1" }, BASE);

    expect(fetchMock.mock.calls[0][0]).toBe(
      `${BASE}/bff/agora/research-plans/plan-xyz/runs`,
    );
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("POST");
  });
});

describe("cancelResearchRun — X-Request-Id header", () => {
  it("sends X-Request-Id when requestId is provided", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ status: "accepted", data: null, meta: {} }));
    globalThis.fetch = fetchMock;

    await cancelResearchRun("run-001", { requestId: "req-cancel-run-1" }, BASE);

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["X-Request-Id"]).toBe("req-cancel-run-1");
  });

  it("POSTs to /bff/agora/research-runs/{run_id}/cancel", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ status: "accepted", data: null, meta: {} }));
    globalThis.fetch = fetchMock;

    await cancelResearchRun("run-xyz", { requestId: "req-1" }, BASE);

    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/bff/agora/research-runs/run-xyz/cancel`);
  });
});

// ── Idempotency-Key / If-Match forwarding ────────────────────────────────────

describe("mutating methods — Idempotency-Key and If-Match", () => {
  it("approveResearchPlan forwards Idempotency-Key and If-Match", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ status: "accepted", data: null, meta: {} }));
    globalThis.fetch = fetchMock;

    await approveResearchPlan(
      "plan-001",
      { idempotencyKey: "idem-key-1", ifMatch: "etag-v2", requestId: "req-1" },
      BASE,
    );

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(headers["Idempotency-Key"]).toBe("idem-key-1");
    expect(headers["If-Match"]).toBe("etag-v2");
    expect(headers["X-Request-Id"]).toBe("req-1");
  });

  it("cancelResearchRun has no If-Match (not in spec)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ status: "accepted", data: null, meta: {} }));
    globalThis.fetch = fetchMock;

    await cancelResearchRun("run-001", { requestId: "req-1", idempotencyKey: "idem-1" }, BASE);

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(headers["Idempotency-Key"]).toBe("idem-1");
    expect(headers["X-Request-Id"]).toBe("req-1");
    expect(headers["If-Match"]).toBeUndefined();
  });
});

// ── Read-only methods do NOT forward X-Request-Id ───────────────────────────

describe("read-only methods — no X-Request-Id", () => {
  it("listWorkshopResearchPlans uses GET without mutation headers", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ items: [], page_info: {}, meta: {} }));
    globalThis.fetch = fetchMock;

    await listWorkshopResearchPlans("ws-001", BASE);

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("GET");
    expect((init.headers as Record<string, string>)["X-Request-Id"]).toBeUndefined();
  });

  it("getResearchPlan returns null for 404", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("", { status: 404 }));
    globalThis.fetch = fetchMock;

    const result = await getResearchPlan("plan-missing", BASE);

    expect(result).toBeNull();
  });

  it("getResearchRun returns null for 404", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("", { status: 404 }));
    globalThis.fetch = fetchMock;

    const result = await getResearchRun("run-missing", BASE);

    expect(result).toBeNull();
  });
});

// ── Error handling ───────────────────────────────────────────────────────────

describe("error handling", () => {
  it("throws on non-2xx with error.message from response body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ error: { message: "plan not found" } }),
        { status: 404, headers: { "Content-Type": "application/json" } },
      ),
    );
    globalThis.fetch = fetchMock;

    await expect(listResearchPlanRuns("plan-404", BASE)).rejects.toThrow("plan not found");
  });

  it("throws on 500 with status text when body has no error envelope", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("", { status: 500, statusText: "Internal Server Error" }),
    );
    globalThis.fetch = fetchMock;

    await expect(listResearchRunArtifacts("run-500", BASE)).rejects.toThrow(/500/);
  });
});

// ── URL construction ─────────────────────────────────────────────────────────

describe("URL construction", () => {
  it("listWorkshopResearchPlans encodes workshopId", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ items: [], page_info: {}, meta: {} }));
    globalThis.fetch = fetchMock;

    await listWorkshopResearchPlans("ws/001", BASE);

    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/bff/agora/workshops/ws%2F001/research-plans`);
  });

  it("approveResearchPlan hits /bff/agora/research-plans/{id}/approve", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ status: "accepted", data: null, meta: {} }));
    globalThis.fetch = fetchMock;

    await approveResearchPlan("plan-approve-001", {}, BASE);

    expect(fetchMock.mock.calls[0][0]).toBe(
      `${BASE}/bff/agora/research-plans/plan-approve-001/approve`,
    );
  });

  it("cancelResearchPlan hits /bff/agora/research-plans/{id}/cancel", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ status: "accepted", data: null, meta: {} }));
    globalThis.fetch = fetchMock;

    await cancelResearchPlan("plan-cancel-001", {}, BASE);

    expect(fetchMock.mock.calls[0][0]).toBe(
      `${BASE}/bff/agora/research-plans/plan-cancel-001/cancel`,
    );
  });
});
