// BFF-LUV-FE-002 — Management Console live read adapters.
//
// Coverage:
//   * every required Management Console family has a real adapter (list +
//     get for entity registries; list-only feeds are also asserted).
//   * hybrid mode (default fallback=auto): live transport failure falls back
//     to mock and reports the fallback through `liveStatus`.
//   * real / strict mode (VITE_BFF_FALLBACK=strict): live transport failure
//     surfaces as a typed BffError; mock seed is never substituted.
//   * mock mode: never touches `fetch`.
//   * minimal DTO normalization: `ListEnvelope` shape is preserved through
//     the adapter and items are typed correctly for representative families.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { liveStatus, BffError, makeBffError } from "@/lib/bff-v1";
import {
  managementClient,
  MANAGEMENT_FAMILIES,
  detectManagementMode,
  isHybridFallbackEnabled,
  isStrictRealMode,
  getLiveStatusSnapshot,
} from "@/lib/bff/client";

describe("managementClient — coverage", () => {
  it("exposes all required Management Console route families", () => {
    const required = [
      "strategies", "personas", "capitalPools", "rankingFormulas",
      "rebalances", "deployments", "evolution", "research", "artifacts",
      "tools", "mcpServers", "mcpTools", "skills", "channels",
      "jobs", "runtimes", "alerts", "incidents", "approvals", "audit",
      "oodaPackets",
    ] as const;
    for (const family of required) {
      expect(managementClient).toHaveProperty(family);
      expect(typeof managementClient[family].list).toBe("function");
    }
    expect(MANAGEMENT_FAMILIES.length).toBe(required.length);
  });

  it("entity-registry families expose a `get(id)` accessor", () => {
    const entityRegistries = [
      "strategies", "personas", "capitalPools", "rankingFormulas",
      "rebalances", "deployments", "evolution", "research", "artifacts",
      "tools", "mcpServers", "mcpTools", "skills", "channels",
      "jobs", "runtimes", "alerts", "incidents", "approvals",
      "oodaPackets",
    ] as const;
    for (const family of entityRegistries) {
      const adapter = managementClient[family] as { get?: unknown };
      expect(typeof adapter.get).toBe("function");
    }
  });
});

describe("managementClient — DTO normalization (mock mode)", () => {
  it("strategies.list returns a ListEnvelope with items + metadata", async () => {
    const env = await managementClient.strategies.list();
    expect(env.totalCountExact).toBe(true);
    expect(env.cursor).toEqual({});
    expect(env.pageSize).toBe(env.items.length);
    expect(env.items.length).toBeGreaterThan(0);
    const first = env.items[0];
    expect(first).toHaveProperty("id");
    expect(first).toHaveProperty("alpha");
  });

  it("alerts.list reports an estimated feed (totalCountExact=false)", async () => {
    const env = await managementClient.alerts.list();
    expect(env.totalCountExact).toBe(false);
    // realtimeFeed: estimatedTotal omitted.
    expect(env.estimatedTotal).toBeUndefined();
  });

  it("audit.list reports an audit feed (totalCountExact=false, estimatedTotal present)", async () => {
    const env = await managementClient.audit.list();
    expect(env.totalCountExact).toBe(false);
    expect(typeof env.estimatedTotal).toBe("number");
  });

  it("approvals.list classifies as governance queue (exact count)", async () => {
    const env = await managementClient.approvals.list();
    expect(env.totalCountExact).toBe(true);
    expect(env.cursor).toEqual({});
  });

  it("oodaPackets.list does not invent seed packets in mock mode", async () => {
    const env = await managementClient.oodaPackets.list();
    expect(env.items).toEqual([]);
    expect(env.totalCountExact).toBe(true);
    expect(env.meta?.surfaces).toHaveProperty("ooda_packets");
  });

  it("strategies.get returns a domain object or undefined", async () => {
    const list = await managementClient.strategies.list();
    if (list.items.length === 0) return; // empty seed in some modes
    const id = list.items[0].id;
    const detail = await managementClient.strategies.get(id);
    expect(detail).toBeDefined();
    expect(detail?.id).toBe(id);
    const missing = await managementClient.strategies.get("strat_does_not_exist_xyz");
    expect(missing).toBeUndefined();
  });
});

describe("managementClient — OODA packet live adapter", () => {
  const realFetch = globalThis.fetch;

  beforeEach(() => {
    vi.stubEnv("VITE_BFF_BASE_URL", "https://example.test");
    liveStatus._reset({ mode: "live", effective: "live", baseUrl: "https://example.test" });
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
    vi.unstubAllEnvs();
    liveStatus._reset();
  });

  it("reads packet detail from /bff/ooda/packets/{id} and preserves source meta", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        data: {
          packet_id: "ooda-live-001",
          loop_type: "paper_strategy",
          status: "closed",
          environment: "paper",
          observe: { source_refs: ["source://rs-003"] },
          orient: { evidence_bundle_refs: ["evidence://orientation"] },
          decide: { approval_decision_id: "approval-paper-001" },
          act: { runtime_binding_id: "runtime-binding-paper-001", live_capital_side_effects: false },
          learn: { telemetry_refs: ["telemetry://post-action"] },
        },
        meta: {
          snapshot_at: "2026-05-15T16:00:00Z",
          surfaces: {
            ooda_packet_detail: { status: "ok", source: "service_store" },
          },
        },
      }), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    globalThis.fetch = fetchMock;

    const detail = await managementClient.oodaPackets.get("ooda-live-001");

    expect(fetchMock.mock.calls[0][0]).toBe("https://example.test/bff/ooda/packets/ooda-live-001");
    expect(detail?.packet.packet_id).toBe("ooda-live-001");
    expect(detail?.meta?.surfaces?.ooda_packet_detail?.source).toBe("service_store");
  });

  it("reads OODA packet list and related packet routes without falling back to seed data", async () => {
    const responseBody = {
      items: [
        {
          packet_id: "ooda-live-002",
          loop_type: "paper_strategy",
          status: "acted",
          environment: "paper",
        },
      ],
      page_info: { total: 1 },
      meta: {
        surfaces: {
          ooda_packets: { status: "ok", source: "service_store" },
        },
      },
    };
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify(responseBody), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    globalThis.fetch = fetchMock;

    const listed = await managementClient.oodaPackets.list({ status: "acted", page_size: 5 });
    const strategy = await managementClient.oodaPackets.forStrategy("strat-rs-003");
    const runtime = await managementClient.oodaPackets.forRuntime("runtime-paper-001");
    const evolution = await managementClient.oodaPackets.forEvolutionProgram("evo-program-001");

    expect(listed.items).toHaveLength(1);
    expect(listed.items[0].packet_id).toBe("ooda-live-002");
    expect(listed.estimatedTotal).toBe(1);
    expect(strategy.items[0].packet_id).toBe("ooda-live-002");
    expect(runtime.items[0].packet_id).toBe("ooda-live-002");
    expect(evolution.items[0].packet_id).toBe("ooda-live-002");

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "https://example.test/bff/ooda/packets?status=acted&page_size=5",
      "https://example.test/bff/strategies/strat-rs-003/ooda",
      "https://example.test/bff/runtimes/runtime-paper-001/ooda",
      "https://example.test/bff/evolution-programs/evo-program-001/ooda",
    ]);
  });
});

describe("managementClient — mode detection", () => {
  it("defaults to mock in test runs", () => {
    expect(detectManagementMode()).toBe("mock");
    expect(isHybridFallbackEnabled()).toBe(false);
    expect(isStrictRealMode()).toBe(false);
  });

  it("getLiveStatusSnapshot returns the current effective mode", () => {
    const snap = getLiveStatusSnapshot();
    expect(snap.managementMode).toBe("mock");
    expect(snap.effective).toBe("mock");
  });
});

describe("managementClient — hybrid fallback (live transport failure)", () => {
  const realFetch = globalThis.fetch;

  beforeEach(() => {
    liveStatus._reset({ mode: "live", effective: "live", baseUrl: "https://example.test" });
  });
  afterEach(() => {
    globalThis.fetch = realFetch;
    liveStatus._reset();
  });

  it("network failure → falls back to mock + records lastError", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("ECONNREFUSED"));
    const env = await managementClient.runtimes.list();
    expect(env.items.length).toBeGreaterThan(0);
    expect(liveStatus.get().effective).toBe("mock");
    expect(liveStatus.get().lastError).toMatch(/ECONNREFUSED/);
  });

  it("5xx → falls back to mock (transport-class failure)", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response("oops", { status: 503 }));
    const env = await managementClient.incidents.list();
    expect(env.totalCountExact).toBe(true);
    expect(liveStatus.get().effective).toBe("mock");
  });

  it("4xx with BffError envelope is propagated, NOT silently mocked", async () => {
    const err = makeBffError({ code: "VALIDATION_FAILED", message: "bad request" });
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(err.envelope), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await expect(managementClient.incidents.list()).rejects.toBeInstanceOf(BffError);
    expect(liveStatus.get().effective).toBe("live");
  });
});

describe("managementClient — real (strict) mode", () => {
  const realFetch = globalThis.fetch;
  const originalFallback = process.env.VITE_BFF_FALLBACK;

  beforeEach(() => {
    process.env.VITE_BFF_FALLBACK = "strict";
    liveStatus._reset({ mode: "live", effective: "live", baseUrl: "https://example.test" });
  });
  afterEach(() => {
    if (originalFallback === undefined) delete process.env.VITE_BFF_FALLBACK;
    else process.env.VITE_BFF_FALLBACK = originalFallback;
    globalThis.fetch = realFetch;
    liveStatus._reset();
  });

  it("network failure surfaces as BffError; mock is NOT substituted", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("ECONNREFUSED"));
    await expect(managementClient.alerts.list()).rejects.toBeInstanceOf(BffError);
  });

  it("5xx surfaces as BffError; mock is NOT substituted", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response("boom", { status: 502 }));
    await expect(managementClient.alerts.list()).rejects.toBeInstanceOf(BffError);
  });
});

describe("managementClient — rankingFormulas live detail URL", () => {
  const realFetch = globalThis.fetch;

  beforeEach(() => {
    liveStatus._reset({ mode: "live", effective: "live", baseUrl: "https://example.test" });
  });
  afterEach(() => {
    globalThis.fetch = realFetch;
    liveStatus._reset();
  });

  it("rankingFormulas.get calls /bff/ranking-formulas/rank_1 in live mode", async () => {
    const mockFormula = { id: "rank_1", name: "Test Formula" };
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(mockFormula), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    globalThis.fetch = fetchSpy;
    await managementClient.rankingFormulas.get("rank_1");
    expect(fetchSpy).toHaveBeenCalled();
    const calledUrl = String(fetchSpy.mock.calls[0][0]);
    expect(calledUrl).toContain("/bff/ranking-formulas/rank_1");
  });
});

describe("managementClient — pure mock mode never hits fetch", () => {
  const realFetch = globalThis.fetch;
  beforeEach(() => {
    liveStatus._reset({ mode: "mock", effective: "mock" });
  });
  afterEach(() => {
    globalThis.fetch = realFetch;
    liveStatus._reset();
  });

  it("list adapters do not touch fetch when mode=mock", async () => {
    const fetchSpy = vi.fn();
    globalThis.fetch = fetchSpy;
    await managementClient.jobs.list();
    await managementClient.tools.list();
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
