/**
 * BFF-CONSOL-016 - Detail journey smoke A
 * Families: strategy / persona / deployment / runtime
 *
 * Each family: list -> detail -> related tabs via live BFF routes.
 * Fixture IDs come from Pack A (BFF-CONSOL-008).
 *
 * Acceptance:
 *   1. 4 family detail routes return 2xx for Pack A fixture IDs
 *   2. related tabs expose at least one non-empty fixture-backed entry
 *   3. missing detail IDs return typed 404, not raw 500
 *   4. strategy detail links specs/experiments/artifacts/lineage/audit
 *   5. persona detail shows route-policy and activity
 *   6. evidence JSON captures each family route and status
 *
 * Runner: Playwright (page.request - BFF API smoke, no UI render required)
 * Env:    BFF_BASE_URL (default: https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io)
 *         BFF_AUTH_TOKEN, PANTHEON_BFF_SMOKE_BEARER_TOKEN, or PANTHEON_BFF_SMOKE_TOKEN
 */

import { test, expect } from "@playwright/test";

const PACK_A = {
  strategy: {
    strategyId: "strategy-pack-a-momentum",
    specId: "spec-pack-a-momentum-v1",
    experimentId: "exp-pack-a-momentum-001",
    artifactId: "artifact-pack-a-momentum-v1",
    lineageId: "lineage-pack-a-strategy-artifact",
    auditId: "audit-pack-a-strategy-approved",
  },
  persona: {
    personaId: "persona-pack-a-momentum",
    activitySessionId: "session-pack-a-momentum-activity",
    evaluationId: "eval-pack-a-momentum-001",
    auditId: "audit-pack-a-persona-policy",
  },
  deployment: {
    planId: "plan-pack-a-paper-001",
    approvalId: "approval-pack-a-deploy",
    poolId: "pool-pack-a-ops",
    runtimeId: "runtime-pack-a-paper-001",
  },
  runtime: {
    runtimeId: "runtime-pack-a-paper-001",
    planId: "plan-pack-a-paper-001",
    artifactId: "artifact-pack-a-momentum-v1",
    poolId: "pool-pack-a-ops",
  },
} as const;

type JsonRecord = Record<string, unknown>;

function bffUrl(path: string): string {
  const base =
    process.env.BFF_BASE_URL ||
    "https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io";
  return `${base.replace(/\/$/, "")}${path}`;
}

function authHeaders(): Record<string, string> {
  const token =
    process.env.BFF_AUTH_TOKEN ||
    process.env.PANTHEON_BFF_SMOKE_BEARER_TOKEN ||
    process.env.PANTHEON_BFF_SMOKE_TOKEN;
  if (!token) {
    return {};
  }
  return {
    Authorization: token.startsWith("Bearer ") ? token : `Bearer ${token}`,
  };
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

function asRecord(value: unknown): JsonRecord {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as JsonRecord;
  }
  return {};
}

function rowsFrom(payload: JsonRecord): JsonRecord[] {
  const rows =
    payload.data ??
    payload.items ??
    payload.records ??
    payload.results ??
    [];
  return Array.isArray(rows)
    ? rows.filter((row) => row && typeof row === "object").map(asRecord)
    : [];
}

function detailFrom(payload: JsonRecord): JsonRecord {
  const data = payload.data;
  return data && typeof data === "object" && !Array.isArray(data)
    ? asRecord(data)
    : payload;
}

function arrayField(record: JsonRecord, key: string): JsonRecord[] {
  const value = record[key];
  return Array.isArray(value)
    ? value.filter((row) => row && typeof row === "object").map(asRecord)
    : [];
}

function stringArrayField(record: JsonRecord, key: string): string[] {
  const value = record[key];
  return Array.isArray(value) ? value.map(String) : [];
}

function expectRowsContain(
  rows: JsonRecord[],
  id: string,
  keys: string[],
  label: string
): void {
  expect(
    rows.some((row) => keys.some((key) => row[key] === id)),
    `${label} must contain ${id}`
  ).toBe(true);
}

function errorCodeFrom(payload: JsonRecord): string | undefined {
  return (
    asRecord(asRecord(payload.detail).error).code ??
    asRecord(payload.error).code ??
    payload.code ??
    asRecord(payload.detail).code
  ) as string | undefined;
}

async function expectOkJson(
  response: import("@playwright/test").APIResponse,
  path: string,
  label: string
): Promise<JsonRecord> {
  const status = response.status();
  expect(status, `${label}: ${path} returned raw server error`).toBeLessThan(500);
  expect(
    status >= 200 && status < 300,
    `${label}: ${path} returned unexpected status ${status}`
  ).toBe(true);
  return (await response.json()) as JsonRecord;
}

async function expectTypedObjectNotFound(
  response: import("@playwright/test").APIResponse,
  path: string,
  label: string
): Promise<void> {
  expect(response.status(), `${label}: ${path} should be typed 404`).toBe(404);
  const text = await response.text();
  expect(text, `${label}: ${path} must not return undefined`).not.toContain(
    "undefined"
  );
  const payload = JSON.parse(text) as JsonRecord;
  expect(errorCodeFrom(payload), `${label}: ${path} must carry error code`).toBe(
    "OBJECT_NOT_FOUND"
  );
}

test("strategy - detail links specs, experiments, artifacts, lineage, and audit", async ({
  request,
}) => {
  const list = await expectOkJson(
    await bffGet(request, "/bff/strategies"),
    "/bff/strategies",
    "strategy list"
  );
  expectRowsContain(
    rowsFrom(list),
    PACK_A.strategy.strategyId,
    ["id", "strategy_id"],
    "strategy list"
  );

  const detailPath = `/bff/strategies/${PACK_A.strategy.strategyId}`;
  const strategy = detailFrom(
    await expectOkJson(await bffGet(request, detailPath), detailPath, "strategy detail")
  );
  expect(strategy.id).toBe(PACK_A.strategy.strategyId);
  expect(stringArrayField(strategy, "personaIds")).toContain(
    PACK_A.persona.personaId
  );
  expect(strategy.capitalPoolId).toBe(PACK_A.deployment.poolId);

  const specsPath = `${detailPath}/specs`;
  const specs = rowsFrom(
    await expectOkJson(await bffGet(request, specsPath), specsPath, "strategy specs tab")
  );
  expectRowsContain(specs, PACK_A.strategy.specId, ["spec_version_id", "id"], "strategy specs tab");

  const experimentsPath = `${detailPath}/experiments`;
  const experiments = rowsFrom(
    await expectOkJson(
      await bffGet(request, experimentsPath),
      experimentsPath,
      "strategy experiments tab"
    )
  );
  expectRowsContain(
    experiments,
    PACK_A.strategy.experimentId,
    ["experiment_id", "id"],
    "strategy experiments tab"
  );

  const artifactsPath = `${detailPath}/artifacts`;
  const artifacts = rowsFrom(
    await expectOkJson(
      await bffGet(request, artifactsPath),
      artifactsPath,
      "strategy artifacts tab"
    )
  );
  expectRowsContain(
    artifacts,
    PACK_A.strategy.artifactId,
    ["artifact_id", "id"],
    "strategy artifacts tab"
  );

  const lineagePath = `${detailPath}/lineage`;
  const lineage = detailFrom(
    await expectOkJson(
      await bffGet(request, lineagePath),
      lineagePath,
      "strategy lineage tab"
    )
  );
  const edges = arrayField(lineage, "edges");
  expectRowsContain(
    edges,
    PACK_A.strategy.lineageId,
    ["id", "lineage_id"],
    "strategy lineage tab"
  );

  const auditPath = `${detailPath}/audit`;
  const audit = rowsFrom(
    await expectOkJson(await bffGet(request, auditPath), auditPath, "strategy audit tab")
  );
  expectRowsContain(
    audit,
    PACK_A.strategy.auditId,
    ["entry_id", "id"],
    "strategy audit tab"
  );
});

test("persona - detail shows route policy, activity, evaluations, and audit", async ({
  request,
}) => {
  const list = await expectOkJson(
    await bffGet(request, "/bff/personas"),
    "/bff/personas",
    "persona list"
  );
  expectRowsContain(
    rowsFrom(list),
    PACK_A.persona.personaId,
    ["id", "persona_id"],
    "persona list"
  );

  const detailPath = `/bff/personas/${PACK_A.persona.personaId}`;
  const persona = detailFrom(
    await expectOkJson(await bffGet(request, detailPath), detailPath, "persona detail")
  );
  expect(persona.id).toBe(PACK_A.persona.personaId);
  expect(persona.routedStrategies).toBeGreaterThan(0);

  const policyPath = `${detailPath}/route-policy`;
  const policy = detailFrom(
    await expectOkJson(
      await bffGet(request, policyPath),
      policyPath,
      "persona route-policy tab"
    )
  );
  const rules = arrayField(policy, "rules");
  expect(rules.length, "persona route-policy tab must be non-empty").toBeGreaterThan(0);
  expect(rules.some((rule) => rule.route === PACK_A.strategy.strategyId)).toBe(true);

  const activityPath = `${detailPath}/activity`;
  const activity = detailFrom(
    await expectOkJson(
      await bffGet(request, activityPath),
      activityPath,
      "persona activity tab"
    )
  );
  expectRowsContain(
    arrayField(activity, "sessions"),
    PACK_A.persona.activitySessionId,
    ["session_id", "id"],
    "persona activity tab"
  );

  const evaluationsPath = `${detailPath}/evaluations`;
  const evaluations = rowsFrom(
    await expectOkJson(
      await bffGet(request, evaluationsPath),
      evaluationsPath,
      "persona evaluations tab"
    )
  );
  expectRowsContain(
    evaluations,
    PACK_A.persona.evaluationId,
    ["session_id", "id"],
    "persona evaluations tab"
  );

  const auditPath = `${detailPath}/audit`;
  const audit = rowsFrom(
    await expectOkJson(await bffGet(request, auditPath), auditPath, "persona audit tab")
  );
  expectRowsContain(
    audit,
    PACK_A.persona.auditId,
    ["entry_id", "id"],
    "persona audit tab"
  );
});

test("deployment - detail links approval, stages, capital pool, and runtime", async ({
  request,
}) => {
  const list = await expectOkJson(
    await bffGet(request, "/bff/deployments"),
    "/bff/deployments",
    "deployment list"
  );
  expectRowsContain(
    rowsFrom(list),
    PACK_A.deployment.planId,
    ["plan_id", "id"],
    "deployment list"
  );

  const detailPath = `/bff/deployments/${PACK_A.deployment.planId}`;
  const deployment = detailFrom(
    await expectOkJson(
      await bffGet(request, detailPath),
      detailPath,
      "deployment detail"
    )
  );
  expect(deployment.plan_id ?? deployment.id).toBe(PACK_A.deployment.planId);
  expect(deployment.capital_pool_id).toBe(PACK_A.deployment.poolId);
  expect(deployment.runtime_binding_id).toBe(PACK_A.deployment.runtimeId);
  expectRowsContain(
    arrayField(deployment, "stages"),
    "paper",
    ["stage"],
    "deployment stages"
  );
  expect(deployment.approval_decision_id).toBe(PACK_A.deployment.approvalId);
  expect(asRecord(deployment.approval_decision).id).toBe(
    PACK_A.deployment.approvalId
  );

  const approvalPath = `/bff/approvals/${PACK_A.deployment.approvalId}`;
  const approval = detailFrom(
    await expectOkJson(
      await bffGet(request, approvalPath),
      approvalPath,
      "deployment approval link"
    )
  );
  expect(approval.id ?? approval.decision_id).toBe(PACK_A.deployment.approvalId);

  const runtimePath = `/bff/runtimes/${PACK_A.deployment.runtimeId}`;
  const runtime = detailFrom(
    await expectOkJson(
      await bffGet(request, runtimePath),
      runtimePath,
      "deployment runtime link"
    )
  );
  expect(runtime.runtime_id ?? runtime.id).toBe(PACK_A.deployment.runtimeId);
});

test("runtime - detail links deployment plan, artifact, and capital pool", async ({
  request,
}) => {
  const list = await expectOkJson(
    await bffGet(request, "/bff/runtimes"),
    "/bff/runtimes",
    "runtime list"
  );
  expectRowsContain(
    rowsFrom(list),
    PACK_A.runtime.runtimeId,
    ["runtime_id", "binding_id", "id"],
    "runtime list"
  );

  const detailPath = `/bff/runtimes/${PACK_A.runtime.runtimeId}`;
  const runtime = detailFrom(
    await expectOkJson(await bffGet(request, detailPath), detailPath, "runtime detail")
  );
  expect(runtime.runtime_id ?? runtime.id).toBe(PACK_A.runtime.runtimeId);
  expect(runtime.plan_id).toBe(PACK_A.runtime.planId);
  expect(runtime.artifact_id).toBe(PACK_A.runtime.artifactId);
  expect(runtime.capital_pool_id).toBe(PACK_A.runtime.poolId);
  expect(runtime.deployment_stage).toBe("paper");
  expect(runtime.status).toBeTruthy();

  const deploymentPath = `/bff/deployments/${PACK_A.runtime.planId}`;
  const deployment = detailFrom(
    await expectOkJson(
      await bffGet(request, deploymentPath),
      deploymentPath,
      "runtime deployment link"
    )
  );
  expect(deployment.runtime_binding_id).toBe(PACK_A.runtime.runtimeId);
});

test("degraded detail paths - missing IDs return typed 404", async ({ request }) => {
  const phantomPaths = [
    ["/bff/strategies/phantom-id-does-not-exist", "strategy phantom detail"],
    ["/bff/personas/phantom-id-does-not-exist", "persona phantom detail"],
    ["/bff/deployments/phantom-id-does-not-exist", "deployment phantom detail"],
    ["/bff/runtimes/phantom-id-does-not-exist", "runtime phantom detail"],
  ] as const;

  for (const [path, label] of phantomPaths) {
    await expectTypedObjectNotFound(await bffGet(request, path), path, label);
  }
});
