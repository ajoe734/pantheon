/**
 * FE-INT-GATE-C01 / F04 - optimization loop ranking-to-approval timeline.
 *
 * Coverage:
 *   1. /management/loops/optimization renders the ranking -> rebalance ->
 *      approval -> apply -> evolution/promotion stage chain.
 *   2. The awaiting-approval stage exposes an operator path to Approvals or HIQ.
 *   3. The stage timeline renders from canonical stage/entity fields, not from
 *      display-only mock labels.
 *
 * Env:
 *   FRONTEND_BASE_URL or PLAYWRIGHT_BASE_URL
 *     default: http://127.0.0.1:5173
 */

import { expect, test, type Locator, type Page, type Route } from "@playwright/test";

const DEFAULT_FRONTEND_BASE_URL = "http://127.0.0.1:5173";

const OPTIMIZATION_LOOP_PATH = "/management/loops/optimization";
const LOOP_RUN_ID = "loop-c01-optimization";
const RANKING_ID = "ranking-c01-alpha";
const REBALANCE_ID = "rebalance-c01-paper";
const APPROVAL_ID = "approval-c01-rebalance";
const HIQ_ID = "hiq-c01-rebalance";
const EVOLUTION_DECISION_ID = "evo-c01-promotion";

const CRASH_TEXT =
  /application error|cannot read properties|undefined is not|uncaught|traceback|typeerror|referenceerror/i;

const MOCK_ONLY_TIMELINE_FIELDS = new Set([
  "display_label",
  "displayLabel",
  "label",
  "mock_label",
  "mockLabel",
  "mock_stage",
  "mockStage",
  "seed_label",
  "seedLabel",
  "stage_display_name",
  "stageDisplayName",
  "title",
]);

type JsonRecord = Record<string, unknown>;

type StageRecord = {
  stage: "ranking" | "rebalance" | "approval" | "apply" | "evolution_promotion";
  stage_id: string;
  kind: string;
  status: string;
  entity_type: string;
  entity_id: string;
  started_at: string;
  completed_at?: string;
  blocked_by?: string;
  action_href?: string;
  hiq_href?: string;
  next_stage?: string;
};

function frontendUrl(path = "/"): string {
  const base =
    process.env.FRONTEND_BASE_URL ||
    process.env.PLAYWRIGHT_BASE_URL ||
    DEFAULT_FRONTEND_BASE_URL;
  return `${base.replace(/\/$/, "")}${path}`;
}

function nowIso(): string {
  return "2026-05-13T13:45:00Z";
}

function corsHeaders(route: Route): Record<string, string> {
  const origin = route.request().headers().origin ?? "*";
  return {
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Headers":
      "accept,authorization,content-type,idempotency-key,x-correlation-id,x-idempotency-key,x-locale,x-request-id",
    "Access-Control-Allow-Methods": "GET,POST,PATCH,DELETE,OPTIONS",
    "Access-Control-Allow-Origin": origin,
    "X-Correlation-Id": "corr-fe-int-gate-c01",
    "X-Request-Id": "req-fe-int-gate-c01",
  };
}

async function fulfillJson(
  route: Route,
  body: unknown,
  status = 200,
): Promise<void> {
  await route.fulfill({
    status,
    contentType: "application/json",
    headers: corsHeaders(route),
    body: JSON.stringify(body),
  });
}

function listEnvelope(items: unknown[], surface: string): JsonRecord {
  return {
    items,
    meta: {
      snapshot_at: nowIso(),
      surfaces: {
        [surface]: { status: "ok", source: "fe-int-gate-c01" },
      },
      totalCountExact: true,
    },
    pageSize: items.length,
    totalCount: items.length,
    totalCountExact: true,
  };
}

function dataEnvelope(data: unknown, surface: string): JsonRecord {
  return {
    data,
    meta: {
      snapshot_at: nowIso(),
      surfaces: {
        [surface]: { status: "ok", source: "fe-int-gate-c01" },
      },
    },
  };
}

const timelineStages: StageRecord[] = [
  {
    stage: "ranking",
    stage_id: "stage-c01-ranking",
    kind: "ranking",
    status: "completed",
    entity_type: "ranking_snapshot",
    entity_id: RANKING_ID,
    started_at: "2026-05-13T13:00:00Z",
    completed_at: "2026-05-13T13:04:00Z",
    next_stage: "rebalance",
  },
  {
    stage: "rebalance",
    stage_id: "stage-c01-rebalance",
    kind: "rebalance",
    status: "completed",
    entity_type: "rebalance_plan",
    entity_id: REBALANCE_ID,
    started_at: "2026-05-13T13:04:00Z",
    completed_at: "2026-05-13T13:08:00Z",
    next_stage: "approval",
  },
  {
    stage: "approval",
    stage_id: "stage-c01-approval",
    kind: "awaiting_approval",
    status: "blocked",
    entity_type: "approval",
    entity_id: APPROVAL_ID,
    started_at: "2026-05-13T13:08:00Z",
    action_href: `/management/approvals?approval=${APPROVAL_ID}`,
    hiq_href: `/management/interventions?intervention=${HIQ_ID}`,
    next_stage: "apply",
  },
  {
    stage: "apply",
    stage_id: "stage-c01-apply",
    kind: "apply",
    status: "blocked",
    entity_type: "apply_action",
    entity_id: "apply-c01-rebalance",
    started_at: "2026-05-13T13:09:00Z",
    blocked_by: APPROVAL_ID,
    next_stage: "evolution_promotion",
  },
  {
    stage: "evolution_promotion",
    stage_id: "stage-c01-evolution-promotion",
    kind: "evolution promotion",
    status: "pending",
    entity_type: "evolution_decision",
    entity_id: EVOLUTION_DECISION_ID,
    started_at: "2026-05-13T13:10:00Z",
    blocked_by: APPROVAL_ID,
  },
];

const rankingSnapshot = {
  id: RANKING_ID,
  ranking_id: RANKING_ID,
  loop_run_id: LOOP_RUN_ID,
  formula_id: "formula-risk-adjusted-alpha",
  formula_name: "Risk-adjusted alpha ranking v3",
  objective: "maximize risk-adjusted alpha under exposure caps",
  ranked_candidates: [
    {
      rank: 1,
      strategy_id: "strategy-tw-alpha-2330",
      strategy_name: "TW Alpha 2330",
      score: 0.87,
      proposed_weight: 0.52,
      decision: "increase",
    },
    {
      rank: 2,
      strategy_id: "strategy-tw-alpha-2454",
      strategy_name: "TW Alpha 2454",
      score: 0.74,
      proposed_weight: 0.31,
      decision: "hold",
    },
    {
      rank: 3,
      strategy_id: "strategy-tw-alpha-2317",
      strategy_name: "TW Alpha 2317",
      score: 0.42,
      proposed_weight: 0.17,
      decision: "decrease",
    },
  ],
  status: "completed",
  ranked_at: "2026-05-13T13:04:00Z",
};

const rebalancePlan = {
  id: REBALANCE_ID,
  rebalance_id: REBALANCE_ID,
  loop_run_id: LOOP_RUN_ID,
  source_ranking_id: RANKING_ID,
  status: "awaiting_approval",
  approval_id: APPROVAL_ID,
  approval_state: "awaiting_approval",
  target_stage: "paper",
  proposed_orders: [
    {
      symbol: "2330.TW",
      side: "buy",
      target_weight: 0.52,
      delta_weight: 0.12,
    },
    {
      symbol: "2317.TW",
      side: "sell",
      target_weight: 0.17,
      delta_weight: -0.08,
    },
  ],
  generated_at: "2026-05-13T13:08:00Z",
};

const approvalRecord = {
  id: APPROVAL_ID,
  approval_id: APPROVAL_ID,
  kind: "rebalance_apply",
  status: "awaiting_approval",
  target_type: "rebalance_plan",
  target_id: REBALANCE_ID,
  hiq_intervention_id: HIQ_ID,
  requested_by: "optimization-loop",
  requested_at: "2026-05-13T13:08:00Z",
  links: {
    approval: `/management/approvals?approval=${APPROVAL_ID}`,
    approvals: `/management/approvals?approval=${APPROVAL_ID}`,
    hiq: `/management/interventions?intervention=${HIQ_ID}`,
    intervention: `/management/interventions?intervention=${HIQ_ID}`,
  },
  action_descriptors: [
    {
      id: "review-rebalance-approval",
      action_id: "OpenApproval",
      label: "Review approval",
      href: `/management/approvals?approval=${APPROVAL_ID}`,
      method: "GET",
    },
    {
      id: "open-hiq-intervention",
      action_id: "OpenHIQ",
      label: "Open HIQ",
      href: `/management/interventions?intervention=${HIQ_ID}`,
      method: "GET",
    },
  ],
};

const hiqIntervention = {
  id: HIQ_ID,
  intervention_id: HIQ_ID,
  kind: "rebalance_approval",
  title: "C01 rebalance approval HIQ",
  status: "pending_review",
  severity: "high",
  approval_id: APPROVAL_ID,
  target_type: "rebalance_plan",
  target_id: REBALANCE_ID,
  two_man_signature_id: "two-man-c01-rebalance",
};

const applyAction = {
  id: "apply-c01-rebalance",
  action_id: "apply-c01-rebalance",
  loop_run_id: LOOP_RUN_ID,
  rebalance_id: REBALANCE_ID,
  status: "blocked",
  blocked_by: APPROVAL_ID,
  live_capital_side_effects: false,
};

const evolutionDecision = {
  id: EVOLUTION_DECISION_ID,
  evolution_decision_id: EVOLUTION_DECISION_ID,
  loop_run_id: LOOP_RUN_ID,
  status: "waiting_for_approval",
  target_stage: "paper_canary",
  promotion_gate: {
    approval_id: APPROVAL_ID,
    required_approvers: ["risk-owner", "operator"],
    capital_scale_pct: 3,
    gross_scale_pct: 15,
  },
};

const optimizationLoop = {
  id: LOOP_RUN_ID,
  loop_run_id: LOOP_RUN_ID,
  loop_family: "optimization",
  title: "C01 Optimization Loop",
  status: "blocked",
  health: "attention",
  triggered_by: "optimization-loop",
  subject_kind: "rebalance",
  subject_id: REBALANCE_ID,
  subject_name: `C01 paper rebalance ${REBALANCE_ID}`,
  started_at: "2026-05-13T13:00:00Z",
  updated_at: nowIso(),
  current_stage_id: "stage-c01-approval",
  next_action: {
    kind: "awaiting_approval",
    label: "Review approval",
    href: `/management/approvals?approval=${APPROVAL_ID}`,
  },
  nextAction: {
    kind: "awaiting_approval",
    label: "Review approval",
    href: `/management/approvals?approval=${APPROVAL_ID}`,
  },
  timeline: timelineStages,
  stages: timelineStages,
  evidence: [{ kind: "approval", id: APPROVAL_ID }],
  evidence_refs: [{ kind: "approval", id: APPROVAL_ID }],
  ranking: rankingSnapshot,
  rebalance: rebalancePlan,
  approval: approvalRecord,
  apply: applyAction,
  evolution: evolutionDecision,
  promotion: evolutionDecision,
  links: {
    approvals: `/management/approvals?approval=${APPROVAL_ID}`,
    hiq: `/management/interventions?intervention=${HIQ_ID}`,
  },
};

const mePayload = dataEnvelope(
  {
    tenant: {
      id: "tenant-fe-gate",
      default_id: "tenant-fe-gate",
      allowed_ids: ["tenant-fe-gate"],
      scope: "tenant",
    },
    tenant_id: "tenant-fe-gate",
    environment: {
      name: "fe-int-gate",
      deployment_stage: "paper",
      auth_mode: "stub",
      timezone: "UTC",
      strict_auth: false,
    },
    user: {
      id: "op-fe-gate",
      operator_id: "op-fe-gate",
      display_name: "FE Gate Operator",
      roles: ["operator", "reviewer", "approver"],
      capabilities: [
        "runtime.read",
        "optimization.read",
        "approval.read",
        "intervention.read",
      ],
      mfa_verified: true,
    },
    currentUser: {
      id: "op-fe-gate",
      operator_id: "op-fe-gate",
      display_name: "FE Gate Operator",
      roles: ["operator", "reviewer", "approver"],
      capabilities: [
        "runtime.read",
        "optimization.read",
        "approval.read",
        "intervention.read",
      ],
      mfa_verified: true,
    },
    current_user: {
      id: "op-fe-gate",
      operator_id: "op-fe-gate",
      display_name: "FE Gate Operator",
      roles: ["operator", "reviewer", "approver"],
      capabilities: [
        "runtime.read",
        "optimization.read",
        "approval.read",
        "intervention.read",
      ],
      mfa_verified: true,
    },
    roles: ["operator", "reviewer", "approver"],
    capabilities: [
      "runtime.read",
      "optimization.read",
      "approval.read",
      "intervention.read",
    ],
    session: {
      id: "session-fe-gate-c01",
      authenticated: true,
      session_kind: "stub",
      auth_mode: "stub",
      fresh: true,
      mfa_verified: true,
      checked_at: nowIso(),
    },
    feature_flags: { sessionAuthMe: true },
  },
  "me",
);

function isOptimizationPath(path: string): boolean {
  return (
    path === "/bff/v5/optimization-loop" ||
    path === "/bff/v5/optimization/loop" ||
    path === "/bff/v5/optimization/loops" ||
    path === `/bff/v5/optimization/loops/${LOOP_RUN_ID}` ||
    path === `/bff/v5/loop-runs/${LOOP_RUN_ID}`
  );
}

function isRankingPath(path: string): boolean {
  return (
    path === "/bff/v5/optimization/rankings" ||
    path === "/bff/v5/ranking-formulas" ||
    path === "/bff/ranking-formulas" ||
    path === `/bff/v5/optimization/rankings/${RANKING_ID}` ||
    path === `/bff/v5/ranking-formulas/${RANKING_ID}`
  );
}

function isRebalancePath(path: string): boolean {
  return (
    path === "/bff/rebalances" ||
    path === "/bff/v5/rebalances" ||
    path === "/bff/v5/optimization/rebalances" ||
    path === `/bff/rebalances/${REBALANCE_ID}` ||
    path === `/bff/v5/rebalances/${REBALANCE_ID}`
  );
}

function isApprovalPath(path: string): boolean {
  return (
    path === "/bff/approvals" ||
    path === "/bff/v5/approvals" ||
    path === `/bff/approvals/${APPROVAL_ID}` ||
    path === `/bff/v5/approvals/${APPROVAL_ID}`
  );
}

function isHiqPath(path: string): boolean {
  return (
    path === "/bff/v5/interventions" ||
    path === `/bff/v5/interventions/${HIQ_ID}` ||
    path === "/bff/hiq" ||
    path === `/bff/hiq/${HIQ_ID}`
  );
}

function isEvolutionPath(path: string): boolean {
  return (
    path === "/bff/v5/evolution" ||
    path === "/bff/v5/evolution/decisions" ||
    path === `/bff/v5/evolution/decisions/${EVOLUTION_DECISION_ID}` ||
    path === "/bff/v5/deployments"
  );
}

async function installC01Routes(page: Page): Promise<Set<string>> {
  const calls = new Set<string>();

  await page.route(/\/health(?:\?.*)?$/, async (route) => {
    calls.add(new URL(route.request().url()).pathname);
    await fulfillJson(route, { status: "ok", checked_at: nowIso() });
  });

  await page.route((url) => url.pathname.startsWith("/bff/"), async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: corsHeaders(route) });
      return;
    }

    if (path === "/bff/events/stream") {
      calls.add(path);
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        headers: {
          ...corsHeaders(route),
          "Cache-Control": "no-cache",
          "X-SSE-Channel": url.searchParams.get("channel") ?? "system",
        },
        body: ": fe-int-gate-c01 stream stub\n\n",
      });
      return;
    }

    if (path === "/bff/me") {
      calls.add(path);
      await fulfillJson(route, mePayload);
      return;
    }

    if (path === "/bff/v5/loop-runs") {
      calls.add(path);
      await fulfillJson(route, listEnvelope([optimizationLoop], "loop_runs"));
      return;
    }

    if (isOptimizationPath(path)) {
      calls.add(path);
      await fulfillJson(
        route,
        path.endsWith(LOOP_RUN_ID)
          ? dataEnvelope(optimizationLoop, "optimization_loop")
          : listEnvelope([optimizationLoop], "optimization_loop"),
      );
      return;
    }

    if (isRankingPath(path)) {
      calls.add(path);
      await fulfillJson(route, listEnvelope([rankingSnapshot], "rankings"));
      return;
    }

    if (isRebalancePath(path)) {
      calls.add(path);
      await fulfillJson(
        route,
        path.endsWith(REBALANCE_ID)
          ? dataEnvelope(rebalancePlan, "rebalances")
          : listEnvelope([rebalancePlan], "rebalances"),
      );
      return;
    }

    if (isApprovalPath(path)) {
      calls.add(path);
      await fulfillJson(
        route,
        path.endsWith(APPROVAL_ID)
          ? dataEnvelope(approvalRecord, "approvals")
          : listEnvelope([approvalRecord], "approvals"),
      );
      return;
    }

    if (isHiqPath(path)) {
      calls.add(path);
      await fulfillJson(
        route,
        path.endsWith(HIQ_ID)
          ? dataEnvelope(hiqIntervention, "interventions")
          : listEnvelope([hiqIntervention], "interventions"),
      );
      return;
    }

    if (isEvolutionPath(path)) {
      calls.add(path);
      await fulfillJson(
        route,
        path.endsWith(EVOLUTION_DECISION_ID)
          ? dataEnvelope(evolutionDecision, "evolution")
          : listEnvelope([evolutionDecision], "evolution"),
      );
      return;
    }

    calls.add(path);
    await fulfillJson(route, listEnvelope([], "unhandled_bff"));
  });

  return calls;
}

async function bodyText(page: Page): Promise<string> {
  return page.locator("body").innerText({ timeout: 10_000 });
}

async function expectAnyVisibleText(
  page: Page,
  patterns: RegExp[],
  label: string,
): Promise<void> {
  await expect
    .poll(
      async () => {
        const text = await bodyText(page);
        return patterns.some((pattern) => pattern.test(text));
      },
      { message: `${label} should be visible`, timeout: 15_000 },
    )
    .toBe(true);
}

async function gotoOptimizationLoop(page: Page): Promise<void> {
  await page.goto(frontendUrl(`${OPTIMIZATION_LOOP_PATH}?loop=${LOOP_RUN_ID}`), {
    waitUntil: "domcontentloaded",
    timeout: 30_000,
  });
  await page.locator("#root").waitFor({ state: "attached", timeout: 15_000 });
  await expect
    .poll(async () => (await bodyText(page)).trim().length, {
      message: "optimization loop body should render text",
      timeout: 15_000,
    })
    .toBeGreaterThan(0);
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function clickFirstVisible(candidates: Locator[]): Promise<boolean> {
  for (const candidate of candidates) {
    const first = candidate.first();
    if (!(await first.isVisible({ timeout: 1_000 }).catch(() => false))) {
      continue;
    }
    await first.click();
    return true;
  }
  return false;
}

async function clickAwaitingApprovalPath(page: Page): Promise<void> {
  const approvalPattern = new RegExp(escapeRegExp(APPROVAL_ID), "i");
  const hiqPattern = new RegExp(escapeRegExp(HIQ_ID), "i");
  const statusPattern = /awaiting approval|awaiting_approval|pending approval|review approval|open hiq/i;

  const clicked = await clickFirstVisible([
    page.getByRole("link", { name: approvalPattern }),
    page.getByRole("link", { name: hiqPattern }),
    page.getByRole("link", { name: statusPattern }),
    page.getByRole("button", { name: approvalPattern }),
    page.getByRole("button", { name: hiqPattern }),
    page.getByRole("button", { name: statusPattern }),
    page.locator(`a[href*="approvals"][href*="${APPROVAL_ID}"]`),
    page.locator(`a[href*="interventions"][href*="${HIQ_ID}"]`),
    page.locator(`a[href*="hiq"][href*="${HIQ_ID}"]`),
  ]);

  expect(clicked, "awaiting approval must expose an Approvals or HIQ control").toBe(
    true,
  );
}

function collectForbiddenTimelineFields(value: unknown, path = "$"): string[] {
  if (Array.isArray(value)) {
    return value.flatMap((item, index) =>
      collectForbiddenTimelineFields(item, `${path}[${index}]`),
    );
  }
  if (value === null || typeof value !== "object") {
    return [];
  }

  const matches: string[] = [];
  for (const [key, child] of Object.entries(value as JsonRecord)) {
    const childPath = `${path}.${key}`;
    if (MOCK_ONLY_TIMELINE_FIELDS.has(key)) {
      matches.push(childPath);
    }
    matches.push(...collectForbiddenTimelineFields(child, childPath));
  }
  return matches;
}

test.describe("F04 optimization loop timeline", () => {
  test("renders ranking, rebalance, approval, apply, and promotion stages", async ({
    page,
  }) => {
    const calls = await installC01Routes(page);

    await gotoOptimizationLoop(page);

    await expect(page.locator("body")).not.toContainText(CRASH_TEXT);

    await expectAnyVisibleText(
      page,
      [/optimization loop/i, /optimization/i],
      "optimization loop surface",
    );
    await expectAnyVisibleText(page, [/ranking/i, /ranked/i], "ranking stage");
    await expectAnyVisibleText(page, [/rebalance/i], "rebalance stage");
    await expectAnyVisibleText(
      page,
      [/awaiting approval/i, /awaiting_approval/i, /pending approval/i],
      "approval stage",
    );
    await expectAnyVisibleText(page, [/\bapply\b/i, /blocked/i], "apply stage");
    await expectAnyVisibleText(
      page,
      [/evolution/i, /promotion/i, /paper canary/i],
      "evolution promotion stage",
    );

    await expect(page.getByText(REBALANCE_ID, { exact: false })).toBeVisible();

    expect(
      [...calls].some(
        (path) => isOptimizationPath(path) || path === "/bff/v5/loop-runs",
      ),
      "optimization loop page should request a loop/timeline BFF surface",
    ).toBe(true);
  });

  test("awaiting approval links to Approvals or HIQ", async ({ page }) => {
    const calls = await installC01Routes(page);

    await gotoOptimizationLoop(page);
    await clickAwaitingApprovalPath(page);

    await expect
      .poll(
        () => {
          const currentUrl = new URL(page.url());
          const path = currentUrl.pathname;
          return (
            /\/management\/approvals(?:\/|$)/.test(path) ||
            /\/management\/interventions(?:\/|$)/.test(path) ||
            /\/management\/hiq(?:\/|$)/.test(path) ||
            [...calls].some((calledPath) => isApprovalPath(calledPath) || isHiqPath(calledPath))
          );
        },
        {
          message: "awaiting approval should route to Approvals or HIQ",
          timeout: 10_000,
        },
      )
      .toBe(true);
  });

  test("timeline fixture uses canonical stage fields only", () => {
    expect(timelineStages.map((stage) => stage.stage)).toEqual([
      "ranking",
      "rebalance",
      "approval",
      "apply",
      "evolution_promotion",
    ]);
    for (const stage of timelineStages) {
      expect(stage.entity_type, `${stage.stage}.entity_type`).toBeTruthy();
      expect(stage.entity_id, `${stage.stage}.entity_id`).toBeTruthy();
      expect(stage.started_at, `${stage.stage}.started_at`).toBeTruthy();
    }
    expect(
      collectForbiddenTimelineFields(timelineStages),
      "stage timeline must not require display-only mock fields",
    ).toEqual([]);
  });
});
