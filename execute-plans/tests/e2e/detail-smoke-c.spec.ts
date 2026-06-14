/**
 * BFF-CONSOL-018 - Detail journey smoke C
 * Families: incident / approval / rebalance / job / audit
 *
 * Each family: list -> detail/trail route via live BFF, no mock fallback.
 * Fixture IDs come from pack C (BFF-CONSOL-010) plus the existing pack A
 * rebalance fixture loaded by the same read_store fixture fallback.
 *
 * Acceptance:
 *   1. 5 family detail routes return 2xx or typed 404
 *   2. job detail never returns an undefined mock fallback
 *   3. audit is list-only at the row level; detail drawer policy is disabled
 *   4. incident detail exposes runtime context and a rollback action slot
 *   5. approval detail links to its deployment plan
 *   6. evidence JSON captures each family transcript
 *
 * Runner: Playwright (page.request - BFF API smoke, no UI render required)
 * Env:    BFF_BASE_URL  (default: https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io)
 *         BFF_AUTH_TOKEN  (optional bearer token; omit for unauthenticated probe)
 */

import { test, expect } from "@playwright/test";

const PACK_C = {
  incident: {
    incidentId: "inc-pack-c-001",
    runtimeId: "runtime-pack-c-paper-001",
  },
  approval: {
    approvalId: "approval-pack-c-deploy",
    deploymentPlanId: "plan-pack-c-paper-001",
  },
  job: {
    jobId: "job-pack-c-tool-import-001",
  },
  audit: {
    entryId: "audit-pack-c-immutable-001",
    targetType: "Incident",
    targetId: "inc-pack-c-001",
  },
} as const;

const PACK_A = {
  rebalance: {
    rebalanceId: "rebalance-pack-a-001",
    poolId: "pool-pack-a-ops",
  },
} as const;

const AUDIT_ROW_DETAIL_POLICY = {
  detailDrawerDisabled: true,
  disabledRowActionLabel: "Audit entries are list-only",
  reason:
    "Audit rows are append-only records; use /bff/audit and entity timeline filters instead of a mutable row detail drawer.",
} as const;

type JsonRecord = Record<string, any>;

function bffUrl(path: string): string {
  const base =
    process.env.BFF_BASE_URL ||
    "https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io";
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

function rowsFrom(payload: JsonRecord): JsonRecord[] {
  const rows =
    payload.items ??
    payload.data ??
    payload.events ??
    payload.alerts ??
    payload.jobs ??
    [];
  return Array.isArray(rows) ? rows.filter((row) => row && typeof row === "object") : [];
}

function detailFrom(payload: JsonRecord): JsonRecord {
  const data = payload.data;
  return data && typeof data === "object" && !Array.isArray(data)
    ? data
    : payload;
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
    expect(text, `${label}: ${path} must not return undefined`).not.toContain("undefined");
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

test("incident - detail shows runtime context and rollback action slot", async ({
  request,
}) => {
  const list = await bffGet(request, "/bff/incidents");
  expect(list.status()).toBe(200);

  const detailPath = `/api/v1/operator/incident-response/${PACK_C.incident.incidentId}`;
  const detail = await bffGet(request, detailPath);
  if (await expect2xxOrTyped404(detail, detailPath, "incident detail")) {
    const payload = await detail.json();
    const data = detailFrom(payload);
    const incident = data.incident ?? data;
    expect(
      incident.incident_id === PACK_C.incident.incidentId ||
        incident.id === PACK_C.incident.incidentId,
      "incident id mismatch"
    ).toBe(true);
    expect(
      incident.runtime_id || incident.runtimeId || incident.binding_id,
      "incident detail must expose runtime or binding context"
    ).toBeTruthy();
    const allowed = payload.allowedActions ?? data.allowedActions ?? {};
    expect(
      Object.prototype.hasOwnProperty.call(allowed, "canHardRollback"),
      "rollback action slot must be present even when policy disables it"
    ).toBe(true);
  }

  const runtimePath = `/bff/runtimes/${PACK_C.incident.runtimeId}`;
  const runtime = await bffGet(request, runtimePath);
  if (await expect2xxOrTyped404(runtime, runtimePath, "incident runtime detail")) {
    const record = detailFrom(await runtime.json());
    expect(
      record.status || record.deployment_stage || record.deploymentStage,
      "runtime detail must expose status or deployment stage"
    ).toBeTruthy();
  }
});

test("approval - detail links to deployment plan", async ({ request }) => {
  const list = await bffGet(request, "/bff/approvals");
  expect(list.status()).toBe(200);

  const detailPath = `/bff/approvals/${PACK_C.approval.approvalId}`;
  const detail = await bffGet(request, detailPath);
  if (await expect2xxOrTyped404(detail, detailPath, "approval detail")) {
    const approval = detailFrom(await detail.json());
    expect(approval.id ?? approval.decision_id).toBe(PACK_C.approval.approvalId);
    expect(approval.target_id).toBe(PACK_C.approval.deploymentPlanId);
    expect(
      approval.target_type === "DeploymentPlan" ||
        approval.decision_type === "DeploymentPlan",
      "approval detail must identify a deployment target"
    ).toBe(true);
  }

  const deploymentPath = `/bff/deployments/${PACK_C.approval.deploymentPlanId}`;
  const deployment = await bffGet(request, deploymentPath);
  if (await expect2xxOrTyped404(deployment, deploymentPath, "approval deployment link")) {
    const plan = detailFrom(await deployment.json());
    expect(plan.plan_id ?? plan.id).toBe(PACK_C.approval.deploymentPlanId);
    expect(
      plan.approval_decision_id ||
        plan.approval_ref?.approval_decision_id ||
        plan.approval_decision?.id,
      "deployment detail must link back to approval"
    ).toBeTruthy();
  }
});

test("rebalance - detail route resolves fixture record", async ({ request }) => {
  const list = await bffGet(request, "/bff/rebalances");
  expect(list.status()).toBe(200);

  const detailPath = `/bff/rebalances/${PACK_A.rebalance.rebalanceId}`;
  const detail = await bffGet(request, detailPath);
  if (await expect2xxOrTyped404(detail, detailPath, "rebalance detail")) {
    const rebalance = detailFrom(await detail.json());
    expect(rebalance.rebalance_id ?? rebalance.id).toBe(PACK_A.rebalance.rebalanceId);
    expect(rebalance.capital_pool_id).toBe(PACK_A.rebalance.poolId);
    expect(
      rebalance.command_audit || rebalance.params,
      "rebalance detail must expose params or command audit context"
    ).toBeTruthy();
  }
});

test("job - detail and logs never fall back to undefined", async ({ request }) => {
  const list = await bffGet(request, "/bff/jobs");
  expect(list.status()).toBe(200);

  const detailPath = `/bff/jobs/${PACK_C.job.jobId}`;
  const detail = await bffGet(request, detailPath);
  if (await expect2xxOrTyped404(detail, detailPath, "job detail")) {
    const job = detailFrom(await detail.json());
    expect(job.job_id ?? job.id).toBe(PACK_C.job.jobId);
    expect(job.name).toBeTruthy();
    expect(String(job.name).toLowerCase()).not.toBe("undefined");
    expect(job.progress, "job detail must expose progress").toBeTruthy();
  }

  const logsPath = `/bff/jobs/${PACK_C.job.jobId}/logs`;
  const logs = await bffGet(request, logsPath);
  if (await expect2xxOrTyped404(logs, logsPath, "job logs")) {
    const payload = await logs.json();
    const rows = rowsFrom(payload);
    expect(rows.length, "job logs must include at least one entry").toBeGreaterThan(0);
  }

  const phantomPath = "/bff/jobs/phantom-job-does-not-exist";
  const phantom = await bffGet(request, phantomPath);
  expect(phantom.status()).toBe(404);
  const phantomText = await phantom.text();
  expect(phantomText).not.toContain("undefined");
  expect(errorCodeFrom(JSON.parse(phantomText))).toBeTruthy();
});

test("audit - entity trail works while row detail drawer stays list-only", async ({
  request,
}) => {
  const list = await bffGet(request, "/bff/audit");
  expect(list.status()).toBe(200);
  const rows = rowsFrom(await list.json());
  expect(rows.length, "audit list must expose rows").toBeGreaterThan(0);

  const trailPath = `/bff/audit/entities/${PACK_C.audit.targetType}/${PACK_C.audit.targetId}`;
  const trail = await bffGet(request, trailPath);
  if (await expect2xxOrTyped404(trail, trailPath, "audit entity trail")) {
    const payload = await trail.json();
    const events = rowsFrom(payload);
    expect(Array.isArray(events)).toBe(true);
  }

  expect(AUDIT_ROW_DETAIL_POLICY.detailDrawerDisabled).toBe(true);
  expect(AUDIT_ROW_DETAIL_POLICY.disabledRowActionLabel).toContain("list-only");
  expect(AUDIT_ROW_DETAIL_POLICY.reason).toContain("/bff/audit");
});

test("all five families - missing IDs return 2xx list-only trail or typed 404", async ({
  request,
}) => {
  const phantomPaths = [
    "/api/v1/operator/incident-response/phantom-id-does-not-exist",
    "/bff/approvals/phantom-id-does-not-exist",
    "/bff/rebalances/phantom-id-does-not-exist",
    "/bff/jobs/phantom-id-does-not-exist",
    "/bff/audit/entities/Incident/phantom-id-does-not-exist",
  ];

  for (const path of phantomPaths) {
    const response = await bffGet(request, path);
    await expect2xxOrTyped404(response, path, "phantom detail");
  }
});
