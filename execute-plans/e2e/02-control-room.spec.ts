/**
 * FE-INT-GATE-B02 / F02 - Control Room drill-down and empty-data gate.
 *
 * Coverage:
 *   1. /management/control-room renders KPI cards plus loop, sentinel, and
 *      intervention data from the v5 control-room read model.
 *   2. Drill-down affordances can reach loop, sentinel, and intervention
 *      surfaces without relying on mock/seed fallback banners.
 *   3. An empty-but-valid control-room payload renders without crashing.
 *
 * Env:
 *   FRONTEND_BASE_URL or PLAYWRIGHT_BASE_URL
 *     default: http://127.0.0.1:5173
 *   BFF_BASE_URL or VITE_BFF_BASE_URL
 *     default: https://pantheon-staging-bff.34.81.225.122.sslip.io
 *   FE_INT_GATE_LIVE_BFF=1 or RUN_LIVE_BFF_CONTRACTS=1
 *     opt in to the live BFF contract probe; fixture-driven UI coverage runs
 *     without a staging dependency.
 */

import { expect, test, type Page, type Route } from "@playwright/test";

const DEFAULT_FRONTEND_BASE_URL = "http://127.0.0.1:5173";
const DEFAULT_BFF_BASE_URL =
  "https://pantheon-staging-bff.34.81.225.122.sslip.io";
const DEFAULT_DEV_AUTH_TOKEN = "op-fe-gate:operator,reviewer,approver:mfa";
const RUN_LIVE_BFF_CONTRACT =
  process.env.FE_INT_GATE_LIVE_BFF === "1" ||
  process.env.RUN_LIVE_BFF_CONTRACTS === "1";

const CONTROL_ROOM_PATH = "/management/control-room";
const SERVING_MOCK_BANNER =
  /serving[-\s]?mock|mock data|fallback data|hybrid fallback active|seed fallback active|資料來源：seed/i;
const CRASH_TEXT =
  /application error|cannot read properties|undefined is not|uncaught|traceback|typeerror|referenceerror/i;

type JsonRecord = Record<string, unknown>;

type RouteFixture = {
  controlRoom: JsonRecord;
  loopDetail?: JsonRecord;
  sentinelDetail?: JsonRecord;
  interventionDetail?: JsonRecord;
};

function corsHeaders(route: Route, extra: Record<string, string> = {}): Record<string, string> {
  const origin = route.request().headers()["origin"] || "*";
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
    "Access-Control-Allow-Headers":
      "accept,accept-language,authorization,content-type,idempotency-key,if-match,x-bff-api-version,x-correlation-id,x-locale,x-request-id,x-tenant-id",
    "Access-Control-Expose-Headers":
      "x-bff-api-version,x-correlation-id,x-request-id",
    ...extra,
  };
}

function frontendUrl(path = "/"): string {
  const base =
    process.env.FRONTEND_BASE_URL ||
    process.env.PLAYWRIGHT_BASE_URL ||
    DEFAULT_FRONTEND_BASE_URL;
  return `${base.replace(/\/$/, "")}${path}`;
}

function bffUrl(path: string): string {
  const base =
    process.env.BFF_BASE_URL ||
    process.env.VITE_BFF_BASE_URL ||
    DEFAULT_BFF_BASE_URL;
  return `${base.replace(/\/$/, "")}${path}`;
}

function authHeader(): string {
  const token = process.env.BFF_AUTH_TOKEN || DEFAULT_DEV_AUTH_TOKEN;
  return token.startsWith("Bearer ") ? token : `Bearer ${token}`;
}

function nowIso(): string {
  return "2026-05-13T13:30:00Z";
}

function envelope(data: JsonRecord): JsonRecord {
  return {
    data,
    meta: {
      route: "GET /bff/me",
      contract: "FE-INT-GATE-B02",
      snapshot_at: nowIso(),
    },
  };
}

function fixtureItems(controlRoom: JsonRecord, key: string): unknown[] {
  const surface = controlRoom[key];
  if (!surface || typeof surface !== "object" || Array.isArray(surface)) {
    return [];
  }
  const items = (surface as JsonRecord).items;
  return Array.isArray(items) ? items : [];
}

const LOOP_RECORD = {
  id: "loop-fixture-1",
  loop_run_id: "loop-fixture-1",
  title: "B02 Execution Loop Drilldown",
  loop_family: "execution",
  status: "running",
  severity: "high",
  health: "degraded",
  runtime_id: "runtime-b02-paper-001",
  binding_id: "binding-b02-paper-001",
  capital_pool_id: "pool-b02-ops",
  started_at: "2026-05-13T12:40:00Z",
  updated_at: "2026-05-13T13:10:00Z",
  activePeriod: {
    start: "2026-05-13T12:40:00Z",
    end: null,
  },
  links: {
    route: "/management/loops/execution",
    detail: "/bff/v5/loop-runs/loop-fixture-1",
  },
};

const SENTINEL_RECORD = {
  id: "sentinel-fixture-1",
  finding_id: "sentinel-fixture-1",
  title: "B02 Sentinel Finding Drilldown",
  status: "open",
  severity: "warning",
  category: "risk",
  related_loop_run_id: LOOP_RECORD.id,
  runtime_id: LOOP_RECORD.runtime_id,
  created_at: "2026-05-13T12:55:00Z",
  updated_at: "2026-05-13T13:11:00Z",
  links: {
    route: "/management/sentinel",
    detail: "/bff/v5/sentinel/findings/sentinel-fixture-1",
  },
};

const INTERVENTION_RECORD = {
  id: "intervention-fixture-1",
  intervention_id: "intervention-fixture-1",
  kind: "risk_breach",
  target_type: "binding",
  target_id: "binding-b02-paper-001",
  title: "B02 Intervention Drilldown",
  status: "pending_approval",
  severity: "critical",
  finding_id: SENTINEL_RECORD.id,
  risk_level: "critical",
  requested_by: "operator-b02",
  created_at: "2026-05-13T13:00:00Z",
  links: {
    route: "/management/interventions",
    detail: "/bff/v5/interventions/intervention-fixture-1",
  },
  remediation_skeleton: {
    two_man_rule_enforced: true,
    governance_policy_ref: "KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md",
    remediation_actions_available: ["reduce_exposure", "pause_binding"],
  },
};
const INTERVENTION_DISPLAY_TITLE = "risk breach · binding:binding-b02-paper-001";

const NON_EMPTY_CONTROL_ROOM: JsonRecord = {
  kpis: {
    active_loops: 1,
    open_findings: 1,
    pending_interventions: 1,
    degraded_surfaces: 0,
  },
  kpi_cards: [
    { id: "active-loops", label: "Active loops", value: 1 },
    { id: "open-findings", label: "Open findings", value: 1 },
    { id: "pending-interventions", label: "Pending interventions", value: 1 },
    { id: "degraded-surfaces", label: "Degraded surfaces", value: 0 },
  ],
  loops: {
    items: [LOOP_RECORD],
    meta: {
      snapshot_at: nowIso(),
      surfaces: {
        loop_runs: { status: "ok", source: "fixture" },
      },
    },
  },
  sentinel: {
    items: [SENTINEL_RECORD],
    meta: {
      snapshot_at: nowIso(),
      surfaces: {
        sentinel_findings: { status: "ok", source: "fixture" },
      },
    },
  },
  interventions: {
    items: [INTERVENTION_RECORD],
    meta: {
      snapshot_at: nowIso(),
      surfaces: {
        interventions: { status: "ok", source: "fixture" },
      },
    },
  },
  meta: {
    snapshot_at: nowIso(),
    surfaces: {
      control_room: { status: "ok", source: "fixture" },
    },
  },
};

const EMPTY_CONTROL_ROOM: JsonRecord = {
  kpis: {
    active_loops: 0,
    open_findings: 0,
    pending_interventions: 0,
    degraded_surfaces: 0,
  },
  kpi_cards: [
    { id: "active-loops", label: "Active loops", value: 0 },
    { id: "open-findings", label: "Open findings", value: 0 },
    { id: "pending-interventions", label: "Pending interventions", value: 0 },
    { id: "degraded-surfaces", label: "Degraded surfaces", value: 0 },
  ],
  loops: {
    items: [],
    meta: {
      snapshot_at: nowIso(),
      surfaces: {
        loop_runs: { status: "ok", source: "empty-fixture" },
      },
    },
  },
  sentinel: {
    items: [],
    meta: {
      snapshot_at: nowIso(),
      surfaces: {
        sentinel_findings: { status: "ok", source: "empty-fixture" },
      },
    },
  },
  interventions: {
    items: [],
    meta: {
      snapshot_at: nowIso(),
      surfaces: {
        interventions: { status: "ok", source: "empty-fixture" },
      },
    },
  },
  meta: {
    snapshot_at: nowIso(),
    surfaces: {
      control_room: { status: "ok", source: "empty-fixture" },
    },
  },
};

const ME_RESPONSE = envelope({
  tenant_id: "tenant-b02",
  tenant: {
    id: "tenant-b02",
    default_id: "tenant-b02",
    allowed_ids: ["tenant-b02"],
    scope: "tenant",
  },
  environment: {
    name: "frontend-integration-gate",
    deployment_stage: "paper",
    auth_mode: "stub",
    timezone: "UTC",
    strict_auth: false,
  },
  user: {
    id: "operator-b02",
    operator_id: "operator-b02",
    display_name: "B02 Operator",
    roles: ["operator", "reviewer", "approver"],
    capabilities: ["runtime.read", "intervention.read"],
    mfa_verified: true,
  },
  currentUser: {
    id: "operator-b02",
    operator_id: "operator-b02",
    display_name: "B02 Operator",
    roles: ["operator", "reviewer", "approver"],
    capabilities: ["runtime.read", "intervention.read"],
    mfa_verified: true,
  },
  current_user: {
    id: "operator-b02",
    operator_id: "operator-b02",
    display_name: "B02 Operator",
    roles: ["operator", "reviewer", "approver"],
    capabilities: ["runtime.read", "intervention.read"],
    mfa_verified: true,
  },
  roles: ["operator", "reviewer", "approver"],
  capabilities: ["runtime.read", "intervention.read"],
  session: {
    id: "session-b02",
    authenticated: true,
    fresh: true,
    mfa_verified: true,
    session_kind: "stub",
    auth_mode: "stub",
    checked_at: nowIso(),
  },
  feature_flags: {
    sessionAuthMe: true,
  },
});

async function fulfillJson(route: Route, body: JsonRecord): Promise<void> {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    headers: corsHeaders(route),
    body: JSON.stringify(body),
  });
}

async function fulfillEventStream(route: Route): Promise<void> {
  await route.fulfill({
    status: 200,
    contentType: "text/event-stream",
    headers: corsHeaders(route, {
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    }),
    body: ": FE-INT-GATE-B02 fixture stream\n\n",
  });
}

function emptyList(surface: string): JsonRecord {
  return {
    items: [],
    meta: {
      snapshot_at: nowIso(),
      surfaces: { [surface]: { status: "ok", source: "fixture" } },
    },
  };
}

async function installBffFixtureRoutes(
  page: Page,
  fixture: RouteFixture,
  seenDetailPaths: string[] = [],
): Promise<void> {
  await page.route("**/bff/me**", async (route) => {
    await fulfillJson(route, ME_RESPONSE);
  });
  await page.route(/\/health(?:\?.*)?$/, async (route) => {
    await fulfillJson(route, { status: "ok", checked_at: nowIso() });
  });
  await page.route(/\/bff\/(?:alerts|approvals|jobs)(?:\?.*)?$/, async (route) => {
    const surface = new URL(route.request().url()).pathname.split("/").pop() || "shell";
    await fulfillJson(route, emptyList(surface));
  });
  await page.route(/\/bff\/search(?:\?.*)?$/, async (route) => {
    await fulfillJson(route, {
      items: [],
      results: [],
      meta: {
        snapshot_at: nowIso(),
        surfaces: { search: { status: "ok", source: "fixture" } },
      },
    });
  });
  await page.route(/\/bff\/events\/stream(?:\?.*)?$/, fulfillEventStream);
  await page.route("**/bff/v5/control-room**", async (route) => {
    await fulfillJson(route, fixture.controlRoom);
  });
  await page.route(/\/bff\/v5\/loop-runs(?:\?.*)?$/, async (route) => {
    await fulfillJson(route, {
      items: fixtureItems(fixture.controlRoom, "loops"),
      meta: {
        snapshot_at: nowIso(),
        surfaces: { loop_runs: { status: "ok", source: "fixture" } },
      },
    });
  });
  await page.route(/\/bff\/v5\/sentinel\/findings(?:\?.*)?$/, async (route) => {
    await fulfillJson(route, {
      items: fixtureItems(fixture.controlRoom, "sentinel"),
      meta: {
        snapshot_at: nowIso(),
        surfaces: { sentinel_findings: { status: "ok", source: "fixture" } },
      },
    });
  });
  await page.route(/\/bff\/v5\/interventions(?:\?.*)?$/, async (route) => {
    await fulfillJson(route, {
      items: fixtureItems(fixture.controlRoom, "interventions"),
      meta: {
        snapshot_at: nowIso(),
        surfaces: { interventions: { status: "ok", source: "fixture" } },
      },
    });
  });
  await page.route("**/bff/v5/execution/persona-health**", async (route) => {
    await fulfillJson(route, {
      items: [
        {
          persona_id: "persona-b02",
          name: "B02 Persona",
          status: "active",
          health: "ok",
        },
      ],
      meta: { snapshot_at: nowIso(), surfaces: { persona_health: { status: "ok" } } },
    });
  });
  await page.route("**/bff/v5/execution/strategy-health**", async (route) => {
    await fulfillJson(route, {
      items: [
        {
          strategy_id: "strategy-b02",
          name: "B02 Strategy",
          status: "paper",
          health: "ok",
        },
      ],
      meta: { snapshot_at: nowIso(), surfaces: { strategy_health: { status: "ok" } } },
    });
  });
  await page.route("**/bff/v5/loop-runs/loop-fixture-1**", async (route) => {
    seenDetailPaths.push(new URL(route.request().url()).pathname);
    await fulfillJson(route, fixture.loopDetail ?? LOOP_RECORD);
  });
  await page.route(
    "**/bff/v5/sentinel/findings/sentinel-fixture-1**",
    async (route) => {
      seenDetailPaths.push(new URL(route.request().url()).pathname);
      await fulfillJson(route, fixture.sentinelDetail ?? SENTINEL_RECORD);
    },
  );
  await page.route("**/bff/v5/interventions/intervention-fixture-1**", async (route) => {
    seenDetailPaths.push(new URL(route.request().url()).pathname);
    await fulfillJson(route, fixture.interventionDetail ?? INTERVENTION_RECORD);
  });
}

function collectPageFailures(page: Page): string[] {
  const failures: string[] = [];
  page.on("pageerror", (error) => failures.push(error.message));
  page.on("console", (message) => {
    const text = message.text();
    if (message.type() === "error" && CRASH_TEXT.test(text)) {
      failures.push(text);
    }
  });
  return failures;
}

async function gotoControlRoom(page: Page): Promise<void> {
  await page.goto(frontendUrl(CONTROL_ROOM_PATH), {
    waitUntil: "domcontentloaded",
    timeout: 30_000,
  });
  await page.locator("#root").waitFor({ state: "attached", timeout: 15_000 });
  await expect
    .poll(async () => (await bodyText(page)).trim().length, {
      message: "Control Room body should render text",
      timeout: 15_000,
    })
    .toBeGreaterThan(0);
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
    .poll(async () => {
      const text = await bodyText(page);
      return patterns.some((pattern) => pattern.test(text));
    }, { message: `${label} should be visible`, timeout: 15_000 })
    .toBe(true);
}

async function clickFirstCandidate(page: Page, selectors: string[]): Promise<boolean> {
  for (const selector of selectors) {
    const locator = page.locator(selector).first();
    if ((await locator.count()) === 0) {
      continue;
    }
    if (!(await locator.isVisible().catch(() => false))) {
      continue;
    }
    await locator.click();
    return true;
  }
  return false;
}

async function clickDrilldown(
  page: Page,
  label: string,
  selectors: string[],
): Promise<void> {
  await expect(page.getByText(label, { exact: false }).first()).toBeVisible({
    timeout: 15_000,
  });
  if (await clickFirstCandidate(page, selectors)) {
    return;
  }
  await page.getByText(label, { exact: false }).first().click();
}

test.describe("F02 Control Room", () => {
  test.describe.configure({ timeout: 60_000 });

  test("renders KPI cards, loops, sentinel findings, and interventions", async ({
    page,
  }) => {
    const failures = collectPageFailures(page);
    await installBffFixtureRoutes(page, {
      controlRoom: NON_EMPTY_CONTROL_ROOM,
    });

    await gotoControlRoom(page);

    await expect(page.locator("body")).not.toContainText(SERVING_MOCK_BANNER);
    await expect(page.locator("body")).not.toContainText(CRASH_TEXT);

    await expectAnyVisibleText(
      page,
      [/control room/i, /控制/i],
      "Control Room heading",
    );
    await expectAnyVisibleText(page, [/active loops/i, /\bloops?\b/i], "loop KPI");
    await expectAnyVisibleText(
      page,
      [/open findings/i, /sentinel/i, /finding/i],
      "sentinel KPI",
    );
    await expectAnyVisibleText(
      page,
      [/pending interventions/i, /interventions?/i, /待處理介入|人為介入|介入佇列/],
      "intervention KPI",
    );

    await expect(page.getByText(LOOP_RECORD.title, { exact: false }).first()).toBeVisible();
    await expect(page.getByText(SENTINEL_RECORD.title, { exact: false }).first()).toBeVisible();
    await expect(page.getByText(INTERVENTION_DISPLAY_TITLE, { exact: false }).first()).toBeVisible();
    expect(failures, "page should not emit console/page errors").toEqual([]);
  });

  test("drill-down link reaches the loop surface", async ({ page }) => {
    const seenDetailPaths: string[] = [];
    await installBffFixtureRoutes(
      page,
      { controlRoom: NON_EMPTY_CONTROL_ROOM },
      seenDetailPaths,
    );

    await gotoControlRoom(page);
    await clickDrilldown(page, LOOP_RECORD.title, [
      `a:has-text("${LOOP_RECORD.title}")`,
      `button:has-text("${LOOP_RECORD.title}")`,
      `tr:has-text("${LOOP_RECORD.title}") a`,
      `tr:has-text("${LOOP_RECORD.title}") button`,
      `[role="row"]:has-text("${LOOP_RECORD.title}") a`,
      `[role="row"]:has-text("${LOOP_RECORD.title}") button`,
      `article:has-text("${LOOP_RECORD.title}") a`,
      `article:has-text("${LOOP_RECORD.title}") button`,
      `section:has-text("${LOOP_RECORD.title}") a`,
      `section:has-text("${LOOP_RECORD.title}") button`,
      `[data-testid*="loop"] a`,
      `[data-testid*="loop"] button`,
    ]);

    await expect
      .poll(
        () => {
          const path = new URL(page.url()).pathname;
          return (
            /\/management\/loops(?:\/|$)/.test(path) ||
            seenDetailPaths.includes("/bff/v5/loop-runs/loop-fixture-1")
          );
        },
        { message: "loop drill-down should reach loop route or detail endpoint", timeout: 7_000 },
      )
      .toBe(true);
  });

  test("drill-down link reaches the sentinel surface", async ({ page }) => {
    const seenDetailPaths: string[] = [];
    await installBffFixtureRoutes(
      page,
      { controlRoom: NON_EMPTY_CONTROL_ROOM },
      seenDetailPaths,
    );

    await gotoControlRoom(page);
    await clickDrilldown(page, SENTINEL_RECORD.title, [
      `a:has-text("${SENTINEL_RECORD.title}")`,
      `button:has-text("${SENTINEL_RECORD.title}")`,
      `tr:has-text("${SENTINEL_RECORD.title}") a`,
      `tr:has-text("${SENTINEL_RECORD.title}") button`,
      `[role="row"]:has-text("${SENTINEL_RECORD.title}") a`,
      `[role="row"]:has-text("${SENTINEL_RECORD.title}") button`,
      `article:has-text("${SENTINEL_RECORD.title}") a`,
      `article:has-text("${SENTINEL_RECORD.title}") button`,
      `section:has-text("${SENTINEL_RECORD.title}") a`,
      `section:has-text("${SENTINEL_RECORD.title}") button`,
      `[data-testid*="sentinel"] a`,
      `[data-testid*="sentinel"] button`,
      `[data-testid*="finding"] a`,
      `[data-testid*="finding"] button`,
    ]);

    await expect
      .poll(
        () => {
          const path = new URL(page.url()).pathname;
          return (
            /\/management\/sentinel(?:\/|$)/.test(path) ||
            seenDetailPaths.includes("/bff/v5/sentinel/findings/sentinel-fixture-1")
          );
        },
        {
          message: "sentinel drill-down should reach sentinel route or detail endpoint",
          timeout: 7_000,
        },
      )
      .toBe(true);
  });

  test("drill-down link reaches the intervention surface", async ({ page }) => {
    const seenDetailPaths: string[] = [];
    await installBffFixtureRoutes(
      page,
      { controlRoom: NON_EMPTY_CONTROL_ROOM },
      seenDetailPaths,
    );

    await gotoControlRoom(page);
    await clickDrilldown(page, INTERVENTION_DISPLAY_TITLE, [
      `a:has-text("${INTERVENTION_DISPLAY_TITLE}")`,
      `button:has-text("${INTERVENTION_DISPLAY_TITLE}")`,
      `tr:has-text("${INTERVENTION_DISPLAY_TITLE}") a`,
      `tr:has-text("${INTERVENTION_DISPLAY_TITLE}") button`,
      `[role="row"]:has-text("${INTERVENTION_DISPLAY_TITLE}") a`,
      `[role="row"]:has-text("${INTERVENTION_DISPLAY_TITLE}") button`,
      `article:has-text("${INTERVENTION_DISPLAY_TITLE}") a`,
      `article:has-text("${INTERVENTION_DISPLAY_TITLE}") button`,
      `section:has-text("${INTERVENTION_DISPLAY_TITLE}") a`,
      `section:has-text("${INTERVENTION_DISPLAY_TITLE}") button`,
      `[data-testid*="intervention"] a`,
      `[data-testid*="intervention"] button`,
    ]);

    await expect
      .poll(
        () => {
          const path = new URL(page.url()).pathname;
          return (
            /\/management\/interventions(?:\/|$)/.test(path) ||
            seenDetailPaths.includes("/bff/v5/interventions/intervention-fixture-1")
          );
        },
        {
          message: "intervention drill-down should reach intervention route or detail endpoint",
          timeout: 7_000,
        },
      )
      .toBe(true);
  });

  test("renders empty control-room data without crashing", async ({ page }) => {
    const failures = collectPageFailures(page);
    await installBffFixtureRoutes(page, {
      controlRoom: EMPTY_CONTROL_ROOM,
    });

    await gotoControlRoom(page);

    const text = await bodyText(page);
    expect(text.trim(), "page body should not be blank").not.toBe("");
    expect(text).not.toMatch(SERVING_MOCK_BANNER);
    expect(text).not.toMatch(CRASH_TEXT);
    expect(failures, "empty fixture should not emit console/page errors").toEqual([]);
  });

  test("live control-room API preserves composed read-model shape", async ({
    request,
  }) => {
    test.skip(
      !RUN_LIVE_BFF_CONTRACT,
      "Set FE_INT_GATE_LIVE_BFF=1 to run the staging BFF contract probe.",
    );

    const response = await request.get(bffUrl("/bff/v5/control-room"), {
      headers: {
        Accept: "application/json",
        Authorization: authHeader(),
        "X-Locale": "zh-TW",
      },
      timeout: 10_000,
    });

    expect(response.status(), await response.text()).toBe(200);
    const payload = (await response.json()) as JsonRecord;
    for (const key of ["loops", "sentinel", "interventions", "meta"]) {
      expect(payload[key], `control-room payload must include ${key}`).toBeTruthy();
    }
    for (const key of ["loops", "sentinel", "interventions"]) {
      const surface = payload[key] as JsonRecord;
      expect(Array.isArray(surface.items), `${key}.items must be an array`).toBe(true);
    }
    const meta = payload.meta as JsonRecord;
    expect(meta.surfaces, "control-room meta.surfaces must be present").toBeTruthy();
  });
});
