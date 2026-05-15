/**
 * FE-INT-GATE-D05 / F18 - Perf and stability soft-fail budgets.
 *
 * Coverage:
 *   1. Control Room load budget plus 30s SSE-driven DOM rerender proxy.
 *   2. Entity registry first-page load budget and DataTable density stability.
 *   3. Sentinel list load budget.
 *   4. LineageGraph warns when the graph exceeds 500 nodes.
 *
 * Budgets are soft by default so this spec can enter CI without blocking the
 * gate while baselines settle. Set FE_INT_GATE_PERF_STRICT=1 to turn budget
 * overruns into hard failures.
 */

import { expect, test, type Page, type Route, type TestInfo } from "@playwright/test";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";

const DEFAULT_FRONTEND_BASE_URL = "http://127.0.0.1:5173";

const CONTROL_ROOM_PATH = "/management/control-room";
const ENTITY_LIST_PATH = "/management/strategies";
const SENTINEL_PATH = "/management/sentinel";
const LINEAGE_PATH = "/management/lineage?root=strategy-f18-wide";

const STRICT_BUDGETS = process.env.FE_INT_GATE_PERF_STRICT === "1";
const SSE_WINDOW_MS = Number(process.env.FE_INT_GATE_SSE_WINDOW_MS ?? "30000");
const SSE_EVENT_INTERVAL_MS = Number(process.env.FE_INT_GATE_SSE_EVENT_INTERVAL_MS ?? "1000");

const BUDGETS = {
  controlRoomLoadMs: 4_000,
  entityFirstPageLoadMs: 4_000,
  sentinelListLoadMs: 4_000,
  sseMutationBatchesPer30s: 180,
} as const;

const CRASH_TEXT =
  /application error|cannot read properties|undefined is not|uncaught|traceback|typeerror|referenceerror/i;
const SERVING_MOCK_BANNER =
  /serving[-\s]?mock|mock data|seed fallback|資料來源：seed/i;

type JsonRecord = Record<string, unknown>;

type RouteCounters = {
  controlRoom: number;
  loopRuns: number;
  me: number;
  personaHealth: number;
  sentinelFindings: number;
  strategies: number;
  strategyHealth: number;
  interventions: number;
};

type BudgetResult = {
  id: string;
  label: string;
  actual: number;
  max: number;
  unit: string;
};

type MutationCounterSnapshot = {
  batches: number;
  records: number;
};

type OpenSseResponse = {
  req: IncomingMessage;
  res: ServerResponse;
  timer?: ReturnType<typeof setInterval>;
};

class SsePerfHarness {
  private server: Server;
  private openResponses: OpenSseResponse[] = [];
  private sequence = 0;

  readonly requests: string[] = [];
  baseUrl = "";

  constructor() {
    this.server = createServer((req, res) => this.handle(req, res));
  }

  async start(): Promise<void> {
    await new Promise<void>((resolve) => {
      this.server.listen(0, "127.0.0.1", resolve);
    });
    const address = this.server.address() as AddressInfo;
    this.baseUrl = `http://127.0.0.1:${address.port}`;
  }

  async stop(): Promise<void> {
    for (const entry of this.openResponses) {
      if (entry.timer) clearInterval(entry.timer);
      if (!entry.res.writableEnded) entry.res.end();
    }
    this.openResponses = [];
    await new Promise<void>((resolve, reject) => {
      this.server.close((error) => (error ? reject(error) : resolve()));
    });
  }

  private handle(req: IncomingMessage, res: ServerResponse): void {
    const url = new URL(req.url ?? "/", this.baseUrl || "http://127.0.0.1");
    if (url.pathname === "/bff/events/stream") {
      this.handleSse(req, res);
      return;
    }

    res.writeHead(404, {
      "Content-Type": "application/json",
      ...sseCorsHeaders(req),
    });
    res.end(JSON.stringify({ error: { code: "RESOURCE_NOT_FOUND" } }));
  }

  private handleSse(req: IncomingMessage, res: ServerResponse): void {
    this.requests.push(req.url ?? "/bff/events/stream");
    res.writeHead(200, {
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "Content-Type": "text/event-stream",
      "X-Accel-Buffering": "no",
      ...sseCorsHeaders(req),
    });

    const entry: OpenSseResponse = { req, res };
    this.openResponses.push(entry);
    req.on("close", () => {
      if (entry.timer) clearInterval(entry.timer);
      this.openResponses = this.openResponses.filter((candidate) => candidate !== entry);
    });

    res.write(sseBlock(this.nextEvent("system", "system.connected")));
    entry.timer = setInterval(() => {
      if (res.writableEnded) return;
      res.write(
        sseBlock(
          this.nextEvent("sentinel", "sentinel.finding.status_changed", {
            findingId: "finding-f18-001",
            status: "open",
          }),
        ),
      );
    }, SSE_EVENT_INTERVAL_MS);
  }

  private nextEvent(
    channel: string,
    type: string,
    payload: JsonRecord = {},
  ): JsonRecord {
    this.sequence += 1;
    return {
      schemaVersion: 1,
      id: `evt-f18-${this.sequence}`,
      channel,
      type,
      occurredAt: nowIso(),
      correlationId: `corr-f18-${this.sequence}`,
      payload,
    };
  }
}

function sseCorsHeaders(req: IncomingMessage): Record<string, string> {
  return {
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Origin": req.headers.origin ?? "*",
  };
}

function sseBlock(event: JsonRecord): string {
  const id = String(event.id);
  const type = String(event.type);
  return [`id: ${id}`, `event: ${type}`, `data: ${JSON.stringify(event)}`, "", ""].join(
    "\n",
  );
}

function frontendUrl(path = "/"): string {
  const base =
    process.env.FRONTEND_BASE_URL ||
    process.env.PLAYWRIGHT_BASE_URL ||
    process.env.PANTHEON_FE_BASE_URL ||
    DEFAULT_FRONTEND_BASE_URL;
  return `${base.replace(/\/$/, "")}${path}`;
}

function nowIso(): string {
  return "2026-05-13T14:00:00Z";
}

function envelope(data: JsonRecord): JsonRecord {
  return {
    data,
    meta: {
      route: "GET /bff/me",
      contract: "FE-INT-GATE-D05",
      snapshot_at: nowIso(),
    },
  };
}

function corsHeaders(route: Route): Record<string, string> {
  return {
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Headers":
      "authorization,content-type,idempotency-key,x-correlation-id,x-locale,x-request-id,x-tenant-id",
    "Access-Control-Allow-Origin": route.request().headers()["origin"] ?? "*",
  };
}

async function fulfillJson(
  route: Route,
  body: JsonRecord,
  status = 200,
): Promise<void> {
  await route.fulfill({
    status,
    contentType: "application/json",
    headers: corsHeaders(route),
    body: JSON.stringify(body),
  });
}

function routeCounters(): RouteCounters {
  return {
    controlRoom: 0,
    loopRuns: 0,
    me: 0,
    personaHealth: 0,
    sentinelFindings: 0,
    strategies: 0,
    strategyHealth: 0,
    interventions: 0,
  };
}

const ME_RESPONSE = envelope({
  tenant: {
    id: "tenant-fe-gate",
    default_id: "tenant-fe-gate",
    allowed_ids: ["tenant-fe-gate"],
    scope: "tenant",
  },
  tenant_id: "tenant-fe-gate",
  environment: {
    name: "frontend-integration-gate",
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
    capabilities: ["runtime.read", "sentinel.read", "intervention.read"],
    mfa_verified: true,
  },
  currentUser: {
    id: "op-fe-gate",
    operator_id: "op-fe-gate",
    display_name: "FE Gate Operator",
    roles: ["operator", "reviewer", "approver"],
    capabilities: ["runtime.read", "sentinel.read", "intervention.read"],
    mfa_verified: true,
  },
  current_user: {
    id: "op-fe-gate",
    operator_id: "op-fe-gate",
    display_name: "FE Gate Operator",
    roles: ["operator", "reviewer", "approver"],
    capabilities: ["runtime.read", "sentinel.read", "intervention.read"],
    mfa_verified: true,
  },
  roles: ["operator", "reviewer", "approver"],
  capabilities: ["runtime.read", "sentinel.read", "intervention.read"],
  session: {
    id: "session-fe-gate-d05",
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

const LOOP_RUNS = Array.from({ length: 9 }, (_, index) => {
  const kind = (["research", "execution", "optimization"] as const)[index % 3];
  return {
    id: `loop-f18-${index + 1}`,
    loop_run_id: `loop-f18-${index + 1}`,
    title: `F18 ${kind} loop ${index + 1}`,
    subject_name: `F18 ${kind} loop ${index + 1}`,
    loop_kind: kind,
    status: index === 1 ? "blocked" : "running",
    runtime_id: `runtime-f18-${index + 1}`,
    binding_id: `binding-f18-${index + 1}`,
    started_at: "2026-05-13T13:00:00Z",
    updated_at: nowIso(),
    stages: [
      {
        id: `loop-f18-${index + 1}-stage-1`,
        name: "Monitor",
        status: index === 1 ? "blocked" : "running",
      },
    ],
  };
});

const SENTINEL_FINDINGS = Array.from({ length: 120 }, (_, index) => {
  const severity = index % 9 === 0 ? "critical" : index % 3 === 0 ? "warning" : "watch";
  return {
    id: `finding-f18-${String(index + 1).padStart(3, "0")}`,
    finding_id: `finding-f18-${String(index + 1).padStart(3, "0")}`,
    title: `F18 Sentinel Finding ${String(index + 1).padStart(3, "0")}`,
    summary: `Synthetic F18 stability finding ${index + 1}`,
    status: index % 7 === 0 ? "mitigating" : "open",
    severity,
    confidence: severity === "critical" ? 0.93 : severity === "warning" ? 0.78 : 0.58,
    source: "runtime",
    detected_at: "2026-05-13T13:30:00Z",
    updated_at: nowIso(),
    strategy_ids: [`strategy-f18-${(index % 6) + 1}`],
    persona_ids: [`persona-f18-${(index % 5) + 1}`],
    recommended_action_ids: index % 4 === 0 ? ["reduce_allocation"] : ["draft_plan"],
  };
});

const INTERVENTIONS = Array.from({ length: 12 }, (_, index) => ({
  id: `intervention-f18-${index + 1}`,
  intervention_id: `intervention-f18-${index + 1}`,
  kind: index % 3 === 0 ? "risk_breach" : "hiq_sentinel",
  target_type: "strategy",
  target_id: `strategy-f18-${(index % 6) + 1}`,
  title: `F18 Intervention ${index + 1}`,
  status: "pending",
  severity: index % 3 === 0 ? "critical" : "warning",
  description: `F18 pending intervention ${index + 1}`,
  triggered_at: "2026-05-13T13:45:00Z",
  updated_at: nowIso(),
}));

const CONTROL_ROOM_RESPONSE = {
  loops: {
    items: LOOP_RUNS,
    meta: { snapshot_at: nowIso(), surfaces: { loop_runs: { status: "ok" } } },
  },
  sentinel: {
    items: SENTINEL_FINDINGS.slice(0, 20),
    meta: { snapshot_at: nowIso(), surfaces: { sentinel_findings: { status: "ok" } } },
  },
  interventions: {
    items: INTERVENTIONS,
    meta: { snapshot_at: nowIso(), surfaces: { interventions: { status: "ok" } } },
  },
  meta: {
    snapshot_at: nowIso(),
    surfaces: { control_room: { status: "ok", source: "fe-int-gate-d05" } },
  },
};

const PERSONA_HEALTH_RESPONSE = {
  items: Array.from({ length: 10 }, (_, index) => ({
    id: `persona-f18-${index + 1}`,
    persona_id: `persona-f18-${index + 1}`,
    name: `F18 Persona ${index + 1}`,
    mode: index % 3 === 0 ? "live" : "paper",
    status: index % 4 === 0 ? "degraded" : "healthy",
    score: index % 4 === 0 ? 49 : 91,
    routed_strategies: 2 + index,
    open_findings: index % 4 === 0 ? 1 : 0,
    updated_at: nowIso(),
  })),
  meta: { snapshot_at: nowIso() },
};

const STRATEGY_HEALTH_RESPONSE = {
  items: Array.from({ length: 10 }, (_, index) => ({
    id: `strategy-f18-${index + 1}`,
    strategy_id: `strategy-f18-${index + 1}`,
    name: `F18 Strategy Health ${index + 1}`,
    status: index % 5 === 0 ? "critical" : "healthy",
    score: index % 5 === 0 ? 22 : 88,
    pnl_30d: 0.03,
    drawdown: -0.02,
    open_findings: index % 5 === 0 ? 2 : 0,
    updated_at: nowIso(),
  })),
  meta: { snapshot_at: nowIso() },
};

const WIDE_LINEAGE_STRATEGY = {
  id: "strategy-f18-wide",
  name: "F18 Wide Lineage Strategy",
  state: "deployed",
  risk: "medium",
  owner: "perf-gate",
  updatedAt: nowIso(),
  alpha: "mom-f18-wide",
  pnl30d: 0.034,
  sharpe: 1.42,
  drawdown: -0.031,
  capitalPoolId: "pool-f18-wide",
  personaIds: Array.from({ length: 502 }, (_, index) => `persona-f18-wide-${index + 1}`),
};

const STRATEGIES_RESPONSE = {
  items: [
    WIDE_LINEAGE_STRATEGY,
    ...Array.from({ length: 49 }, (_, index) => ({
      id: `strategy-f18-${index + 1}`,
      name: `F18 Strategy ${String(index + 1).padStart(3, "0")}`,
      state: index % 6 === 0 ? "review" : "deployed",
      risk: index % 5 === 0 ? "high" : "low",
      owner: `owner-${index % 4}`,
      updatedAt: nowIso(),
      alpha: `alpha-f18-${index + 1}`,
      pnl30d: (index % 2 === 0 ? 1 : -1) * (0.01 + index / 1000),
      sharpe: 1.1 + index / 100,
      drawdown: -0.01 * (index % 5),
      capitalPoolId: `pool-f18-${index % 3}`,
      personaIds: [`persona-f18-${index % 5}`],
    })),
  ],
  cursor: {},
  pageSize: 50,
  estimatedTotal: 50,
  totalCountExact: true,
  meta: { snapshot_at: nowIso(), surfaces: { strategies: { status: "ok" } } },
};

const TOPBAR_APPROVALS_RESPONSE = {
  items: [],
  cursor: {},
  pageSize: 0,
  estimatedTotal: 0,
  totalCountExact: true,
  meta: { snapshot_at: nowIso(), surfaces: { approvals: { status: "ok", source: "live" } } },
};

const TOPBAR_ALERTS_RESPONSE = {
  items: [],
  cursor: {},
  pageSize: 0,
  totalCountExact: false,
  meta: { snapshot_at: nowIso(), surfaces: { alerts: { status: "ok", source: "live" } } },
};

const TOPBAR_JOBS_RESPONSE = {
  items: [],
  cursor: {},
  pageSize: 0,
  estimatedTotal: 0,
  totalCountExact: true,
  meta: { snapshot_at: nowIso(), surfaces: { jobs: { status: "ok", source: "live" } } },
};

async function installPerfRoutes(page: Page, counters: RouteCounters): Promise<void> {
  await page.route(/\/bff\/me(?:\?.*)?$/, async (route) => {
    counters.me += 1;
    await fulfillJson(route, ME_RESPONSE);
  });
  await page.route(/\/health(?:\?.*)?$/, async (route) => {
    await fulfillJson(route, { status: "ok", checked_at: nowIso() });
  });
  await page.route(/\/bff\/v5\/control-room(?:\?.*)?$/, async (route) => {
    counters.controlRoom += 1;
    await fulfillJson(route, CONTROL_ROOM_RESPONSE);
  });
  await page.route(/\/bff\/v5\/loop-runs(?:\?.*)?$/, async (route) => {
    counters.loopRuns += 1;
    await fulfillJson(route, { items: LOOP_RUNS, meta: { snapshot_at: nowIso() } });
  });
  await page.route(/\/bff\/v5\/execution\/persona-health(?:\?.*)?$/, async (route) => {
    counters.personaHealth += 1;
    await fulfillJson(route, PERSONA_HEALTH_RESPONSE);
  });
  await page.route(/\/bff\/v5\/execution\/strategy-health(?:\?.*)?$/, async (route) => {
    counters.strategyHealth += 1;
    await fulfillJson(route, STRATEGY_HEALTH_RESPONSE);
  });
  await page.route(/\/bff\/v5\/sentinel\/findings(?:\?.*)?$/, async (route) => {
    counters.sentinelFindings += 1;
    await fulfillJson(route, {
      items: SENTINEL_FINDINGS,
      meta: { snapshot_at: nowIso(), surfaces: { sentinel_findings: { status: "ok" } } },
    });
  });
  await page.route(/\/bff\/v5\/interventions(?:\?.*)?$/, async (route) => {
    counters.interventions += 1;
    await fulfillJson(route, {
      items: INTERVENTIONS,
      meta: { snapshot_at: nowIso(), surfaces: { interventions: { status: "ok" } } },
    });
  });
  await page.route(/\/bff\/strategies(?:\?.*)?$/, async (route) => {
    counters.strategies += 1;
    await fulfillJson(route, STRATEGIES_RESPONSE);
  });
  await page.route(/\/bff\/approvals(?:\?.*)?$/, async (route) => {
    await fulfillJson(route, TOPBAR_APPROVALS_RESPONSE);
  });
  await page.route(/\/bff\/alerts(?:\?.*)?$/, async (route) => {
    await fulfillJson(route, TOPBAR_ALERTS_RESPONSE);
  });
  await page.route(/\/bff\/jobs(?:\?.*)?$/, async (route) => {
    await fulfillJson(route, TOPBAR_JOBS_RESPONSE);
  });
  await page.route(/\/bff\/search(?:\?.*)?$/, async (route) => {
    await fulfillJson(route, {
      items: [],
      meta: { snapshot_at: nowIso(), surfaces: { search: { status: "ok" } } },
    });
  });
}

async function installEventSourceRedirect(page: Page, baseUrl: string): Promise<void> {
  await page.addInitScript(
    ({ redirectedBaseUrl }) => {
      const NativeEventSource = window.EventSource;
      if (!NativeEventSource) return;
      class RedirectedEventSource extends NativeEventSource {
        constructor(url: string | URL, eventSourceInitDict?: EventSourceInit) {
          let nextUrl = String(url);
          try {
            const parsed = new URL(nextUrl, window.location.href);
            if (parsed.pathname === "/bff/events/stream") {
              nextUrl = new URL(`${parsed.pathname}${parsed.search}`, redirectedBaseUrl).toString();
            }
          } catch {
            // Keep the original URL if parsing fails.
          }
          super(nextUrl, eventSourceInitDict);
        }
      }
      Object.defineProperties(RedirectedEventSource, {
        CONNECTING: { value: NativeEventSource.CONNECTING },
        OPEN: { value: NativeEventSource.OPEN },
        CLOSED: { value: NativeEventSource.CLOSED },
      });
      window.EventSource = RedirectedEventSource as typeof EventSource;
    },
    { redirectedBaseUrl: baseUrl },
  );
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

async function bodyText(page: Page): Promise<string> {
  return page.locator("body").innerText({ timeout: 10_000 });
}

async function gotoAndWaitForText(
  page: Page,
  path: string,
  patterns: RegExp[],
  label: string,
): Promise<number> {
  const startedAt = Date.now();
  await page.goto(frontendUrl(path), { waitUntil: "domcontentloaded", timeout: 30_000 });
  await page.locator("#root").waitFor({ state: "attached", timeout: 15_000 });
  await expect
    .poll(
      async () => {
        const text = await bodyText(page);
        return patterns.some((pattern) => pattern.test(text));
      },
      { message: `${label} should render expected fixture text`, timeout: 20_000 },
    )
    .toBe(true);
  return Date.now() - startedAt;
}

async function startMutationCounter(page: Page): Promise<void> {
  await page.evaluate(() => {
    const root = document.querySelector("#root") ?? document.body;
    const state = {
      batches: 0,
      records: 0,
      observer: undefined as MutationObserver | undefined,
    };
    state.observer = new MutationObserver((mutations) => {
      state.batches += 1;
      state.records += mutations.length;
    });
    state.observer.observe(root, {
      attributes: true,
      characterData: true,
      childList: true,
      subtree: true,
      attributeFilter: ["aria-busy", "class", "data-state", "style"],
    });
    (window as unknown as { __f18MutationCounter?: typeof state }).__f18MutationCounter = state;
  });
}

async function stopMutationCounter(page: Page): Promise<MutationCounterSnapshot> {
  return page.evaluate(() => {
    const state = (window as unknown as {
      __f18MutationCounter?: MutationCounterSnapshot & { observer?: MutationObserver };
    }).__f18MutationCounter;
    state?.observer?.disconnect();
    return {
      batches: state?.batches ?? 0,
      records: state?.records ?? 0,
    };
  });
}

async function dataTableRowStyleHeights(page: Page): Promise<string[]> {
  return page.evaluate(() =>
    Array.from(document.querySelectorAll("table tbody tr"))
      .slice(0, 10)
      .map((row) => (row as HTMLElement).style.height),
  );
}

function recordBudget(testInfo: TestInfo, result: BudgetResult): void {
  const passed = result.actual <= result.max;
  const description = `${result.id} ${result.label}: ${Math.round(result.actual)}${result.unit} <= ${result.max}${result.unit}`;
  testInfo.annotations.push({
    type: passed ? "perf-budget-pass" : "perf-budget-soft-fail",
    description,
  });
  if (!passed) {
    console.warn(`[FE-INT-GATE-D05] soft budget overrun: ${description}`);
  }
  if (STRICT_BUDGETS) {
    expect(result.actual, description).toBeLessThanOrEqual(result.max);
  }
}

function annotateSoftGap(testInfo: TestInfo, description: string): void {
  testInfo.annotations.push({ type: "perf-budget-soft-gap", description });
  console.warn(`[FE-INT-GATE-D05] ${description}`);
}

test.describe("F18 perf and stability soft-fail budgets", () => {
  test.describe.configure({ timeout: Math.max(75_000, SSE_WINDOW_MS + 45_000) });

  test("keeps Control Room load and SSE rerender proxy within soft budgets", async ({
    page,
  }, testInfo) => {
    const harness = new SsePerfHarness();
    await harness.start();
    try {
      await installEventSourceRedirect(page, harness.baseUrl);
      const counters = routeCounters();
      const failures = collectPageFailures(page);
      await installPerfRoutes(page, counters);

      const loadMs = await gotoAndWaitForText(
        page,
        CONTROL_ROOM_PATH,
        [/control room/i, /F18 Sentinel Finding 001/i, /F18 .* loop/i],
        "Control Room",
      );
      recordBudget(testInfo, {
        id: "control_room_load",
        label: "Control Room load",
        actual: loadMs,
        max: BUDGETS.controlRoomLoadMs,
        unit: "ms",
      });

      expect(await bodyText(page)).not.toMatch(SERVING_MOCK_BANNER);
      expect(failures, "Control Room should not emit console/page errors").toEqual([]);

      await startMutationCounter(page);
      await page.waitForTimeout(SSE_WINDOW_MS);
      const mutations = await stopMutationCounter(page);
      const normalizedMutationBudget =
        BUDGETS.sseMutationBatchesPer30s * (SSE_WINDOW_MS / 30_000);
      recordBudget(testInfo, {
        id: "sse_rerender_proxy",
        label: `SSE DOM mutation batches over ${Math.round(SSE_WINDOW_MS / 1000)}s`,
        actual: mutations.batches,
        max: normalizedMutationBudget,
        unit: " batches",
      });
      testInfo.annotations.push({
        type: "sse-mutation-records",
        description: `records=${mutations.records}; requests=${harness.requests.length}; control_room_reads=${counters.controlRoom}; sentinel_reads=${counters.sentinelFindings}`,
      });
      if (harness.requests.length === 0) {
        annotateSoftGap(
          testInfo,
          "No browser EventSource request reached the local SSE harness; frontend under test is likely not built in live BFF mode.",
        );
      }
    } finally {
      await harness.stop();
    }
  });

  test("keeps entity registry first page budget and DataTable density stable", async ({
    page,
  }, testInfo) => {
    const counters = routeCounters();
    const failures = collectPageFailures(page);
    await installPerfRoutes(page, counters);

    const loadMs = await gotoAndWaitForText(
      page,
      ENTITY_LIST_PATH,
      [/F18 Wide Lineage Strategy/i, /F18 Strategy 001/i],
      "Strategy entity list",
    );
    recordBudget(testInfo, {
      id: "entity_first_page_load",
      label: "Entity list first page load",
      actual: loadMs,
      max: BUDGETS.entityFirstPageLoadMs,
      unit: "ms",
    });

    const rows = page.locator("table tbody tr");
    await expect(rows.first(), "DataTable should render at least one body row").toBeVisible();
    await expect
      .poll(() => rows.count(), { message: "strategy list should render fixture rows", timeout: 5_000 })
      .toBeGreaterThan(10);

    const before = await dataTableRowStyleHeights(page);
    expect(before.length, "DataTable row height sample should not be empty").toBeGreaterThan(0);
    expect(
      before.every((height) => height === "48px"),
      `comfortable density row heights should remain 48px; got ${before.join(", ")}`,
    ).toBe(true);

    await rows.first().hover();
    await page.waitForTimeout(100);
    const after = await dataTableRowStyleHeights(page);
    expect(after).toEqual(before);
    expect(failures, "entity list should not emit console/page errors").toEqual([]);
    testInfo.annotations.push({
      type: "fixture-route-reads",
      description: `strategies=${counters.strategies}`,
    });
  });

  test("keeps Sentinel list load within soft budget", async ({ page }, testInfo) => {
    const counters = routeCounters();
    const failures = collectPageFailures(page);
    await installPerfRoutes(page, counters);

    const loadMs = await gotoAndWaitForText(
      page,
      SENTINEL_PATH,
      [/F18 Sentinel Finding 001/i, /\bcritical\b/i, /\bwarning\b/i],
      "Sentinel list",
    );
    recordBudget(testInfo, {
      id: "sentinel_list_load",
      label: "Sentinel list load",
      actual: loadMs,
      max: BUDGETS.sentinelListLoadMs,
      unit: "ms",
    });

    await expect(page.getByText("F18 Sentinel Finding 001", { exact: false }).first()).toBeVisible();
    expect(await bodyText(page)).not.toMatch(CRASH_TEXT);
    expect(failures, "Sentinel list should not emit console/page errors").toEqual([]);
    testInfo.annotations.push({
      type: "fixture-route-reads",
      description: `sentinel_findings=${counters.sentinelFindings}`,
    });
  });

  test("warns when LineageGraph receives more than 500 nodes", async ({ page }) => {
    const counters = routeCounters();
    const failures = collectPageFailures(page);
    await installPerfRoutes(page, counters);

    await gotoAndWaitForText(
      page,
      LINEAGE_PATH,
      [/F18 Wide Lineage Strategy/i, /Lineage has 506 nodes/i],
      "Lineage graph",
    );

    await expect(
      page.getByText(
        /Lineage has 506 nodes \(> 500\); server-side layout \/ clustered summary required\./i,
      ),
    ).toBeVisible();
    expect(failures, "Lineage graph should not emit console/page errors").toEqual([]);
    expect(counters.strategies, "Lineage graph fixture should read the live strategies route").toBeGreaterThan(0);
  });
});
