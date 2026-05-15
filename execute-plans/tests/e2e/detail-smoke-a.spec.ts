/**
 * BFF-CONSOL-016 - Detail journey smoke A
 * Families: strategy / persona / deployment / runtime
 *
 * Each family: list -> detail -> related tabs/routes via live BFF, no mock.
 * Fixture IDs come from pack A (BFF-CONSOL-008).
 *
 * Acceptance:
 *   1. 4 family detail routes return 2xx or typed 404
 *   2. related tabs/routes render at least one non-empty fixture-backed entry
 *   3. degraded path shows typed OBJECT_NOT_FOUND instead of raw 500
 *   4. strategy detail links specs / experiments / artifacts / lineage / audit
 *   5. persona detail shows route-policy and activity/evaluation surfaces
 *   6. evidence JSON captures each family route and status
 *
 * Runner: Playwright (page.request - BFF API smoke, no UI render required)
 * Env:    BFF_BASE_URL  (default: https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io)
 *         BFF_AUTH_TOKEN  (optional bearer token; required by strict-auth BFFs)
 */

import { test, expect } from "@playwright/test";

const PACK_A = {
  strategy: {
    strategyId: "strategy-pack-a-momentum",
    specVersionId: "spec-pack-a-momentum-v1",
    experimentId: "exp-pack-a-momentum-001",
    artifactId: "artifact-pack-a-momentum-v1",
    lineageEdgeId: "lineage-pack-a-strategy-artifact",
    auditEntryId: "audit-pack-a-strategy-approved",
  },
  persona: {
    personaId: "persona-pack-a-momentum",
    routePolicyId: "route-policy-pack-a-momentum",
    evaluationSessionId: "eval-pack-a-momentum-001",
  },
  deployment: {
    planId: "plan-pack-a-paper-001",
    approvalId: "approval-pack-a-deploy",
  },
  runtime: {
    runtimeId: "runtime-pack-a-paper-001",
    stage: "paper",
    poolId: "pool-pack-a-ops",
  },
} as const;

type JsonRecord = Record<string, any>;

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
  return request.get(bffUrl(path), {
    headers: authHeaders(),
    timeout: 10_000,
  });
}

function detailFrom(payload: JsonRecord): JsonRecord {
  const data = payload.data;
  return data && typeof data === "object" && !Array.isArray(data)
    ? data
    : payload;
}

function rowsFrom(payload: JsonRecord): JsonRecord[] {
  const rows =
    payload.data ??
    payload.items ??
    payload.events ??
    payload.sessions ??
    [];
  return Array.isArray(rows)
    ? rows.filter((row) => row && typeof row === "object")
    : [];
}

function errorCodeFrom(payload: JsonRecord): string | undefined {
  return (
    payload.detail?.error?.code ??
    payload.error?.code ??
    payload.code ??
    payload.detail?.code
  );
}

async function expect2xxOrTyped404(
  response: import("@playwright/test").APIResponse,
  path: string,
  label: string
): Promise<boolean> {
  const status = response.status();
  expect(status, `${label}: ${path} returned raw server error`).toBeLessThan(500);
  if (status === 404) {
    const text = await response.text();
    expect(text, `${label}: ${path} must not return undefined`).not.toContain(
      "undefined"
    );
    let payload: JsonRecord = {};
    try {
      payload = JSON.parse(text);
    } catch {
      payload = {};
    }
    expect(
      errorCodeFrom(payload),
      `${label}: ${path} 404 must carry a typed error code`
    ).toBeTruthy();
    return false;
  }
  expect(
    status >= 200 && status < 300,
    `${label}: ${path} returned unexpected status ${status}`
  ).toBe(true);
  return true;
}

function expectRowsContainId(
  rows: JsonRecord[],
  id: string,
  keys: string[],
  label: string
): JsonRecord {
  const row = rows.find((item) =>
    keys.some((key) => String(item[key] ?? "") === id)
  );
  expect(row, `${label} must contain ${id}`).toBeTruthy();
  return row as JsonRecord;
}

test("strategy - detail links specs, experiments, artifacts, lineage, and audit", async ({
  request,
}) => {
  const list = await bffGet(request, "/bff/strategies");
  expect(list.status()).toBe(200);
  const listRows = rowsFrom(await list.json());
  expectRowsContainId(
    listRows,
    PACK_A.strategy.strategyId,
    ["id", "strategy_id"],
    "strategy list"
  );

  const detailPath = `/bff/strategies/${PACK_A.strategy.strategyId}`;
  const detail = await bffGet(request, detailPath);
  expect(detail.status(), `strategy detail must resolve Pack A: ${detailPath}`).toBe(200);
  const strategy = detailFrom(await detail.json());
  expect(strategy.id ?? strategy.strategy_id).toBe(PACK_A.strategy.strategyId);
  expect(
    Array.isArray(strategy.personaIds) &&
      strategy.personaIds.includes(PACK_A.persona.personaId),
    "strategy detail must link the Pack A persona"
  ).toBe(true);

  const specs = await bffGet(
    request,
    `/bff/strategies/${PACK_A.strategy.strategyId}/specs`
  );
  expect(specs.status()).toBe(200);
  expectRowsContainId(
    rowsFrom(await specs.json()),
    PACK_A.strategy.specVersionId,
    ["spec_version_id", "id"],
    "strategy specs"
  );

  const experiments = await bffGet(
    request,
    `/bff/strategies/${PACK_A.strategy.strategyId}/experiments`
  );
  expect(experiments.status()).toBe(200);
  const experiment = expectRowsContainId(
    rowsFrom(await experiments.json()),
    PACK_A.strategy.experimentId,
    ["experiment_id", "id"],
    "strategy experiments"
  );
  expect(experiment.artifact_ids).toContain(PACK_A.strategy.artifactId);

  const artifacts = await bffGet(
    request,
    `/bff/strategies/${PACK_A.strategy.strategyId}/artifacts`
  );
  expect(artifacts.status()).toBe(200);
  const artifact = expectRowsContainId(
    rowsFrom(await artifacts.json()),
    PACK_A.strategy.artifactId,
    ["artifact_id", "id"],
    "strategy artifacts"
  );
  expect(artifact.lineage_id).toBeTruthy();

  const lineage = await bffGet(
    request,
    `/bff/strategies/${PACK_A.strategy.strategyId}/lineage`
  );
  expect(lineage.status()).toBe(200);
  const lineageRecord = detailFrom(await lineage.json());
  expect(
    Array.isArray(lineageRecord.edges) && lineageRecord.edges.length > 0,
    "strategy lineage must include at least one edge"
  ).toBe(true);
  expectRowsContainId(
    lineageRecord.edges,
    PACK_A.strategy.lineageEdgeId,
    ["id", "lineage_id"],
    "strategy lineage"
  );

  const audit = await bffGet(
    request,
    `/bff/strategies/${PACK_A.strategy.strategyId}/audit`
  );
  expect(audit.status()).toBe(200);
  expectRowsContainId(
    rowsFrom(await audit.json()),
    PACK_A.strategy.auditEntryId,
    ["entry_id", "id"],
    "strategy audit"
  );
});

test("persona - detail shows route policy plus activity and evaluation surfaces", async ({
  request,
}) => {
  const list = await bffGet(request, "/bff/personas");
  expect(list.status()).toBe(200);
  expectRowsContainId(
    rowsFrom(await list.json()),
    PACK_A.persona.personaId,
    ["id", "persona_id"],
    "persona list"
  );

  const detailPath = `/bff/personas/${PACK_A.persona.personaId}`;
  const detail = await bffGet(request, detailPath);
  expect(detail.status(), `persona detail must resolve Pack A: ${detailPath}`).toBe(200);
  const persona = detailFrom(await detail.json());
  expect(persona.id ?? persona.persona_id).toBe(PACK_A.persona.personaId);
  expect(persona.routedStrategies, "persona must expose routed strategy count").toBeGreaterThan(0);

  const routePolicy = await bffGet(
    request,
    `/bff/personas/${PACK_A.persona.personaId}/route-policy`
  );
  expect(routePolicy.status()).toBe(200);
  const policy = detailFrom(await routePolicy.json());
  expect(policy.personaId).toBe(PACK_A.persona.personaId);
  expect(
    Array.isArray(policy.rules) && policy.rules.length > 0,
    "persona route-policy must include at least one rule"
  ).toBe(true);
  expect(policy.rules[0].route).toBe(PACK_A.strategy.strategyId);

  const activity = await bffGet(
    request,
    `/bff/personas/${PACK_A.persona.personaId}/activity`
  );
  expect(activity.status()).toBe(200);
  const activityRecord = detailFrom(await activity.json());
  expect(activityRecord.personaId).toBe(PACK_A.persona.personaId);
  expect(Array.isArray(activityRecord.sessions)).toBe(true);
  expect(Array.isArray(activityRecord.consultations)).toBe(true);

  const evaluations = await bffGet(
    request,
    `/bff/personas/${PACK_A.persona.personaId}/evaluations`
  );
  expect(evaluations.status()).toBe(200);
  expectRowsContainId(
    rowsFrom(await evaluations.json()),
    PACK_A.persona.evaluationSessionId,
    ["session_id", "id"],
    "persona evaluations"
  );
});

test("deployment - detail links approval, capital pool, stage, and runtime", async ({
  request,
}) => {
  const list = await bffGet(request, "/bff/deployments");
  expect(list.status()).toBe(200);
  expectRowsContainId(
    rowsFrom(await list.json()),
    PACK_A.deployment.planId,
    ["plan_id", "id"],
    "deployment list"
  );

  const detailPath = `/bff/deployments/${PACK_A.deployment.planId}`;
  const detail = await bffGet(request, detailPath);
  expect(detail.status(), `deployment detail must resolve Pack A: ${detailPath}`).toBe(200);
  const deployment = detailFrom(await detail.json());
  expect(deployment.plan_id ?? deployment.id).toBe(PACK_A.deployment.planId);
  expect(deployment.approval_decision_id).toBe(PACK_A.deployment.approvalId);
  expect(deployment.approval_decision?.id).toBe(PACK_A.deployment.approvalId);
  expect(deployment.capital_pool_id).toBe(PACK_A.runtime.poolId);
  expect(deployment.runtime_binding_id).toBe(PACK_A.runtime.runtimeId);
  expect(
    Array.isArray(deployment.stages) && deployment.stages.length > 0,
    "deployment detail must expose stage history"
  ).toBe(true);
});

test("runtime - detail links deployment, stage, capital pool, and artifact", async ({
  request,
}) => {
  const list = await bffGet(request, "/bff/runtimes");
  expect(list.status()).toBe(200);
  expectRowsContainId(
    rowsFrom(await list.json()),
    PACK_A.runtime.runtimeId,
    ["runtime_id", "binding_id", "id"],
    "runtime list"
  );

  const detailPath = `/bff/runtimes/${PACK_A.runtime.runtimeId}`;
  const detail = await bffGet(request, detailPath);
  expect(detail.status(), `runtime detail must resolve Pack A: ${detailPath}`).toBe(200);
  const runtime = detailFrom(await detail.json());
  expect(runtime.runtime_id ?? runtime.binding_id ?? runtime.id).toBe(
    PACK_A.runtime.runtimeId
  );
  expect(runtime.plan_id).toBe(PACK_A.deployment.planId);
  expect(runtime.deployment_stage).toBe(PACK_A.runtime.stage);
  expect(runtime.capital_pool_id).toBe(PACK_A.runtime.poolId);
  expect(runtime.artifact_id).toBe(PACK_A.strategy.artifactId);

  const deployment = await bffGet(
    request,
    `/bff/deployments/${PACK_A.deployment.planId}`
  );
  if (await expect2xxOrTyped404(deployment, `/bff/deployments/${PACK_A.deployment.planId}`, "runtime deployment link")) {
    const linked = detailFrom(await deployment.json());
    expect(linked.runtime_binding_id).toBe(PACK_A.runtime.runtimeId);
  }
});

test("all four families - missing IDs return typed 404, not raw 500", async ({
  request,
}) => {
  const phantomPaths = [
    "/bff/strategies/phantom-id-does-not-exist",
    "/bff/personas/phantom-id-does-not-exist",
    "/bff/deployments/phantom-id-does-not-exist",
    "/bff/runtimes/phantom-id-does-not-exist",
  ];

  for (const path of phantomPaths) {
    const response = await bffGet(request, path);
    const resolved = await expect2xxOrTyped404(response, path, "phantom detail");
    expect(resolved, `${path} should use a typed 404 for missing Pack A IDs`).toBe(false);
  }
});
