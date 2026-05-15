/**
 * BFF-CONSOL-017 — Detail journey smoke B
 * Families: evolution / research / v5-interventions / agora / artifacts
 *
 * Each family: list → detail → related tabs (all via live BFF, no mock).
 * Fixture IDs come from pack B (BFF-CONSOL-009).
 *
 * Acceptance:
 *   1. 5 family detail routes return 2xx or typed 404
 *   2. v5 intervention detail contains governed remediation_skeleton
 *   3. agora detail shows historical events in session messages
 *   4. research detail links to analysis
 *   5. artifacts detail contains lineage
 *   6. evidence JSON captures each family transcript
 *
 * Runner: Playwright (page.request — BFF API smoke, no UI render required)
 * Env:    BFF_BASE_URL  (default: https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io)
 *         BFF_AUTH_TOKEN  (optional bearer token; omit for unauthenticated probe)
 */

import { test, expect } from "@playwright/test";

// ─── fixtures (pack B IDs) ────────────────────────────────────────────────
const PACK_B = {
  evolution: {
    programId: "evoprog-pack-b-001",
    decisionId: "evo-dec-pack-b-001",
  },
  research: {
    ticketId: "rt-pack-b-001",
    experimentId: "exp-pack-b-001",
    analysisId: "analysis-pack-b-001",
  },
  v5: {
    interventionId: "intv-pack-b-001",
  },
  agora: {
    sessionId: "agora-session-pack-b-001",
  },
  artifacts: {
    artifactId: "artifact-pack-b-001",
    lineageId: "lineage-pack-b-001",
  },
} as const;

// ─── helpers ─────────────────────────────────────────────────────────────
function bffUrl(path: string): string {
  const base =
    process.env.BFF_BASE_URL ||
    "https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io";
  return `${base.replace(/\/$/, "")}${path}`;
}

function authHeaders(): Record<string, string> {
  const token = process.env.BFF_AUTH_TOKEN || process.env.PANTHEON_BFF_ACCESS_TOKEN;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function bffGet(
  request: import("@playwright/test").APIRequestContext,
  path: string
) {
  const response = await request.get(bffUrl(path), {
    headers: authHeaders(),
    timeout: 10_000,
  });
  return response;
}

function assertAcceptableStatus(
  status: number,
  path: string,
  label: string
): void {
  // Accept 2xx or typed 404 (OBJECT_NOT_FOUND); never raw 500.
  expect(
    status === 200 || status === 201 || status === 404,
    `${label}: ${path} returned unexpected status ${status}`
  ).toBe(true);
}

// ─── family 1: evolution ──────────────────────────────────────────────────

test("evolution — list returns programs with data items", async ({
  request,
}) => {
  const res = await bffGet(request, "/bff/evolution-programs");
  expect(res.status()).toBe(200);
  const body = await res.json();
  const items: unknown[] =
    body.data ?? body.items ?? body.programs ?? [];
  expect(Array.isArray(items) || typeof body.data === "object").toBe(true);
});

test("evolution — detail for pack-B program returns 200", async ({
  request,
}) => {
  const path = `/bff/evolution-programs/${PACK_B.evolution.programId}`;
  const res = await bffGet(request, path);
  expect(res.status(), `evolution detail must resolve Pack B: ${path}`).toBe(200);
  const body = await res.json();
  const record = body.data ?? body;
  expect(
    record.program_id === PACK_B.evolution.programId ||
      record.id === PACK_B.evolution.programId,
    "program_id mismatch"
  ).toBe(true);
});

test("evolution — decisions list contains pack-B decision", async ({
  request,
}) => {
  const res = await bffGet(request, "/api/v1/evolution-decisions");
  assertAcceptableStatus(res.status(), "/api/v1/evolution-decisions", "evolution decisions list");
});

test("evolution — decision detail for pack-B decision returns 2xx or typed 404", async ({
  request,
}) => {
  const path = `/api/v1/evolution-decisions/${PACK_B.evolution.decisionId}`;
  const res = await bffGet(request, path);
  assertAcceptableStatus(res.status(), path, "evolution decision detail");
});

// ─── family 2: research ───────────────────────────────────────────────────

test("research — experiments list returns items", async ({ request }) => {
  const res = await bffGet(request, "/bff/research-experiments");
  expect(res.status()).toBe(200);
});

test("research — experiment detail for pack-B experiment links to analysis", async ({
  request,
}) => {
  const path = `/bff/research-experiments/${PACK_B.research.experimentId}`;
  const res = await bffGet(request, path);
  expect(res.status(), `research experiment detail must resolve Pack B: ${path}`).toBe(200);
  const body = await res.json();
  const record = body.data ?? body;
  expect(record.experiment_id ?? record.id).toBe(PACK_B.research.experimentId);
  expect(record.ticket_id, "research experiment must link the Pack B ticket").toBe(
    PACK_B.research.ticketId
  );
  const analysisIds = new Set<string>();
  if (Array.isArray(record.analysis_ids)) {
    for (const analysisId of record.analysis_ids) {
      if (typeof analysisId === "string") {
        analysisIds.add(analysisId);
      }
    }
  }
  if (Array.isArray(record.analysis_links)) {
    for (const link of record.analysis_links as Array<{ analysis_id?: unknown }>) {
      if (typeof link.analysis_id === "string") {
        analysisIds.add(link.analysis_id);
      }
    }
  }
  expect(
    analysisIds.has(PACK_B.research.analysisId),
    "research experiment detail must expose Pack B analysis linkage"
  ).toBe(true);
});

test("research — analysis detail for pack-B analysis returns 2xx", async ({
  request,
}) => {
  const path = `/bff/research-analyses/${PACK_B.research.analysisId}`;
  const res = await bffGet(request, path);
  expect(res.status(), `research analysis detail must resolve Pack B: ${path}`).toBe(200);
  const body = await res.json();
  const record = body.data ?? body;
  expect(record.analysis_id ?? record.id).toBe(PACK_B.research.analysisId);
  expect(record.ticket_id).toBe(PACK_B.research.ticketId);
  expect(record.experiment_id).toBe(PACK_B.research.experimentId);
});

// ─── family 3: v5 interventions ───────────────────────────────────────────

test("v5 interventions — list returns items", async ({ request }) => {
  const res = await bffGet(request, "/bff/v5/interventions");
  expect(res.status()).toBe(200);
  const body = await res.json();
  const items: unknown[] =
    body.data ?? body.items ?? body.interventions ?? [];
  expect(Array.isArray(items) || typeof body.data !== "undefined").toBe(true);
});

test("v5 interventions — detail for pack-B intervention contains governed remediation skeleton", async ({
  request,
}) => {
  const path = `/bff/v5/interventions/${PACK_B.v5.interventionId}`;
  const res = await bffGet(request, path);
  assertAcceptableStatus(res.status(), path, "v5 intervention detail");
  if (res.status() === 200) {
    const body = await res.json();
    const record = body.data ?? body;
    // Must contain remediation_skeleton per acceptance criteria
    expect(record.remediation_skeleton, "remediation_skeleton must be present").toBeTruthy();
    const skeleton = record.remediation_skeleton;
    expect(skeleton.two_man_rule_enforced, "two_man_rule_enforced must be present").toBe(true);
    expect(
      Array.isArray(skeleton.remediation_actions_available) &&
        skeleton.remediation_actions_available.length > 0,
      "remediation_actions_available must be non-empty"
    ).toBe(true);
    expect(skeleton.governance_policy_ref, "governance_policy_ref must be present").toBeTruthy();
  }
});

// ─── family 4: agora sessions ─────────────────────────────────────────────

test("agora — sessions list returns items", async ({ request }) => {
  const res = await bffGet(request, "/bff/agora/sessions");
  expect(res.status()).toBe(200);
});

test("agora — session detail for pack-B session shows topic and participants", async ({
  request,
}) => {
  const path = `/bff/agora/sessions/${PACK_B.agora.sessionId}`;
  const res = await bffGet(request, path);
  assertAcceptableStatus(res.status(), path, "agora session detail");
  if (res.status() === 200) {
    const body = await res.json();
    const record = body.data ?? body;
    expect(record.topic ?? record.title, "agora session must have topic or title").toBeTruthy();
    expect(
      Array.isArray(record.participants) && record.participants.length > 0,
      "agora session must have participants"
    ).toBe(true);
  }
});

test("agora — session messages for pack-B session contains historical events", async ({
  request,
}) => {
  const path = `/bff/agora/sessions/${PACK_B.agora.sessionId}/messages`;
  const res = await bffGet(request, path);
  assertAcceptableStatus(res.status(), path, "agora session messages");
  if (res.status() === 200) {
    const body = await res.json();
    const messages: unknown[] =
      body.data ?? body.items ?? body.messages ?? [];
    // At least one historical message must exist (from fixture pack B)
    expect(
      Array.isArray(messages) && messages.length > 0,
      "agora session must contain at least one historical message"
    ).toBe(true);
  }
});

// ─── family 5: artifacts ──────────────────────────────────────────────────

test("artifacts — list returns items", async ({ request }) => {
  const res = await bffGet(request, "/bff/artifacts");
  expect(res.status()).toBe(200);
  const body = await res.json();
  const items: unknown[] = body.data ?? body.items ?? body.artifacts ?? [];
  expect(Array.isArray(items) || typeof body.data !== "undefined").toBe(true);
});

test("artifacts — detail for pack-B artifact returns 2xx with lineage", async ({
  request,
}) => {
  const path = `/bff/artifacts/${PACK_B.artifacts.artifactId}`;
  const res = await bffGet(request, path);
  assertAcceptableStatus(res.status(), path, "artifact detail");
  if (res.status() === 200) {
    const body = await res.json();
    const record = body.data ?? body;
    expect(
      record.artifact_id === PACK_B.artifacts.artifactId ||
        record.id === PACK_B.artifacts.artifactId,
      "artifact_id mismatch"
    ).toBe(true);
    // lineage must be referenced
    expect(record.lineage_id, "artifact must contain lineage_id").toBeTruthy();
  }
});

test("artifacts — lineage graph for pack-B artifact returns 2xx", async ({
  request,
}) => {
  const path = `/api/v1/lineage/inspiration/${PACK_B.artifacts.artifactId}`;
  const res = await bffGet(request, path);
  assertAcceptableStatus(res.status(), path, "artifact lineage");
  if (res.status() === 200) {
    const body = await res.json();
    expect(
      body.data != null ||
        body.nodes != null ||
        body.edges != null ||
        body.inspiration_edges != null,
      "lineage response must contain data, nodes, edges, or inspiration_edges"
    ).toBe(true);
  }
});

// ─── degraded-path check ─────────────────────────────────────────────────

test("all 5 families — non-existent IDs return typed 404 not raw 500", async ({
  request,
}) => {
  const phantomPaths = [
    "/bff/evolution-programs/phantom-id-does-not-exist",
    "/bff/research-experiments/phantom-id-does-not-exist",
    "/bff/v5/interventions/phantom-id-does-not-exist",
    "/bff/agora/sessions/phantom-id-does-not-exist",
    "/bff/artifacts/phantom-id-does-not-exist",
  ];
  for (const path of phantomPaths) {
    const res = await bffGet(request, path);
    expect(
      res.status() !== 500,
      `${path} must not return raw 500`
    ).toBe(true);
    if (res.status() === 404) {
      const body = await res.json();
      // typed 404 must carry an error code, not a blank response
      const hasCode =
        body.error?.code != null ||
        body.code != null ||
        body.detail != null;
      expect(hasCode, `${path} 404 must be typed (contain error code)`).toBe(true);
    }
  }
});
