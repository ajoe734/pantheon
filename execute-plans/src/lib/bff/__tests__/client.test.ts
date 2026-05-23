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

describe("managementClient — Persona Fleet aggregate live adapter", () => {
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

  it("reads /bff/management/persona-fleet without falling back to list seeds", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        items: [
          {
            id: "persona-alpha",
            persona_id: "persona-alpha",
            persona: { id: "persona-alpha", name: "Alpha Persona", state: "active" },
            health: {
              status: "critical",
              severity: "high",
              score: 65,
              reasons: ["drawdown_threshold"],
              latest_telemetry_at: "2026-04-10T15:00:00Z",
              active_incident_count: 1,
            },
            bindings: [{ id: "binding-042", capital_pool_id: "pool-main" }],
            capitalPools: [{ id: "pool-main" }],
            capital_pools: [{ id: "pool-main" }],
            runtimeBindings: [{ runtime_id: "runtime-042" }],
            runtime_bindings: [{ runtime_id: "runtime-042" }],
            telemetrySummary: { latest: { runtime_id: "runtime-042" } },
            telemetry_summary: { latest: { runtime_id: "runtime-042" } },
            training: { session_count: 2 },
            evolution: { decision_count: 1 },
            allowedActions: { canRetire: true },
          },
        ],
        summary: {
          total_personas: 1,
          returned_personas: 1,
          critical_personas: 1,
          degraded_personas: 0,
          healthy_personas: 0,
          bound_personas: 1,
          runtime_bound_personas: 1,
        },
        page_info: { total: 1, page_size: 1, next_page_token: null },
        meta: {
          surfaces: {
            persona_fleet: { status: "ok", source: "bff_composed" },
          },
        },
      }), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    globalThis.fetch = fetchMock;

    const aggregate = await managementClient.personaFleet.list({ health: "critical", page_size: 1 });

    expect(fetchMock.mock.calls[0][0]).toBe("https://example.test/bff/management/persona-fleet?health=critical&page_size=1");
    expect(aggregate.items[0].id).toBe("persona-alpha");
    expect(aggregate.items[0].telemetrySummary).toHaveProperty("latest");
    expect(aggregate.summary.critical_personas).toBe(1);
    expect(aggregate.meta.surfaces?.persona_fleet).toEqual({ status: "ok", source: "bff_composed" });
  });
});

describe("managementClient — Evidence Explorer aggregate live adapter", () => {
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

  it("reads /bff/management/evidence with explorer filters and preserves redaction meta", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        items: [
          {
            id: "evref-b3-metric-001",
            refId: "evref-b3-metric-001",
            ref_id: "evref-b3-metric-001",
            title: "Runtime performance window",
            sourceType: "metric",
            source_type: "metric",
            linkType: "supporting_evidence",
            link_type: "supporting_evidence",
            credibility: { tier: "primary", verified: true },
            linkedObjectSummary: { entity_type: "experiment", entity_ref: "exp-b3-alpha" },
            linked_object_summary: { entity_type: "experiment", entity_ref: "exp-b3-alpha" },
            routeHref: "/knowledge/evidence/evref-b3-metric-001",
            redacted: false,
          },
        ],
        summary: {
          totalEvidence: 1,
          total_evidence: 1,
          returnedEvidence: 1,
          returned_evidence: 1,
          visibleEvidence: 1,
          visible_evidence: 1,
          redactedEvidence: 0,
          redacted_evidence: 0,
          verifiedEvidence: 1,
          verified_evidence: 1,
          bySourceType: { metric: 1 },
          by_source_type: { metric: 1 },
          byLinkType: { supporting_evidence: 1 },
          by_link_type: { supporting_evidence: 1 },
          byCredibilityTier: { primary: 1 },
          by_credibility_tier: { primary: 1 },
        },
        facets: {
          sourceTypes: { metric: 1 },
          source_types: { metric: 1 },
          linkTypes: { supporting_evidence: 1 },
          link_types: { supporting_evidence: 1 },
          credibilityTiers: { primary: 1 },
          credibility_tiers: { primary: 1 },
        },
        page_info: { total: 1, page_size: 2, next_page_token: null },
        meta: {
          redacted_evidence_count: 0,
          surfaces: {
            management_evidence: { status: "ok", source: "bff_composed" },
            evidence_refs: { status: "ok", source: "service_store" },
          },
        },
      }), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    globalThis.fetch = fetchMock;

    const aggregate = await managementClient.evidenceExplorer.list({
      linked_entity_type: "experiment",
      verified: true,
      page_size: 2,
    });

    expect(fetchMock.mock.calls[0][0]).toBe(
      "https://example.test/bff/management/evidence?linked_entity_type=experiment&verified=true&page_size=2",
    );
    expect(aggregate.items[0].refId).toBe("evref-b3-metric-001");
    expect(aggregate.summary.totalEvidence).toBe(1);
    expect(aggregate.facets.sourceTypes).toEqual({ metric: 1 });
    expect(aggregate.meta.redacted_evidence_count).toBe(0);
    expect(aggregate.meta.surfaces.management_evidence.source).toBe("bff_composed");
  });
});

describe("managementClient — evolution review / approval linkage", () => {
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

  it("reads mutation review authority and linked approval identity from the live route", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        decision_id: "evo-dec-88f3a2c1",
        target_type: "candidate_artifact",
        target_id: "artifact-44d7e9b0",
        target_version: "v3.1.2",
        action_type: "freeze_canary",
        decision_state: "reviewed",
        risk_level: "medium",
        created_at: "2026-04-18T09:32:00Z",
        approval_decision_id: "appr-dec-c5a9f11e",
        proposed_changes: {
          summary: "Freeze candidate artifact at canary stage.",
          target_stage: "canary",
          downstream_plane: "runtime",
          change_details: [],
        },
        risk_assessment: {
          risk_summary: "Execution drift threshold breached.",
          severity: null,
          threshold_triggers: [],
        },
        required_approvals: [
          { role: "reviewer", approved_by: "reviewer-01", approved_at: "2026-04-18T10:00:00Z", status: "approved" },
          { role: "risk_owner", approved_by: null, approved_at: null, status: "pending" },
        ],
        review_chain: [
          {
            action: "reviewed",
            actor_role: "reviewer",
            actor_id: "reviewer-01",
            acted_at: "2026-04-18T10:00:00Z",
            note: "Ready for risk-owner decision.",
          },
        ],
        linked_incident_id: null,
        linked_postmortem_id: null,
        evidence_refs: [],
        rollback_followthrough: null,
        allowedActions: {
          canApproveMutation: true,
          canRejectMutation: true,
        },
        meta: {
          snapshot_at: "2026-04-18T11:05:00Z",
          surfaces: {
            mutation_review: "fresh",
          },
        },
      }), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    globalThis.fetch = fetchMock;

    const detail = await managementClient.evolutionReviews.get("evo-dec-88f3a2c1");

    expect(fetchMock.mock.calls[0][0]).toBe("https://example.test/api/v1/operator/mutation-review/evo-dec-88f3a2c1");
    expect(detail?.decision_id).toBe("evo-dec-88f3a2c1");
    expect(detail?.approval_decision_id).toBe("appr-dec-c5a9f11e");
    expect(detail?.allowedActions.canApproveMutation).toBe(true);
    expect(detail?.meta.surfaces?.mutation_review).toBe("fresh");
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
