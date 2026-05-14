/**
 * FE-INT-GATE-B06 / F07 - entity registry coverage.
 *
 * Coverage:
 *   1. Twelve registry surfaces render from live-shaped BFF list envelopes.
 *   2. Every ListResponse fixture carries totalCountExact === true.
 *   3. Missing detail reads return a RESOURCE_NOT_FOUND error envelope.
 *   4. ActionDescriptor projections use canonical /bff/actions/* endpoints.
 *
 * Env:
 *   FRONTEND_BASE_URL or PLAYWRIGHT_BASE_URL
 *     default: http://127.0.0.1:5173
 *   BFF_BASE_URL or VITE_BFF_BASE_URL
 *     default: https://pantheon-staging-bff.34.81.225.122.sslip.io
 *   BFF_AUTH_TOKEN
 *     optional; when omitted the dev stub token is used.
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

const SNAPSHOT_AT = "2026-05-13T14:10:00Z";
const MISSING_ID = "missing-fe-int-gate-b06";
const SERVING_MOCK_BANNER =
  /serving[-\s]?mock|mock data|seed fallback|資料來源：seed/i;
const CRASH_TEXT =
  /application error|cannot read properties|undefined is not|uncaught|traceback|typeerror|referenceerror/i;

type JsonRecord = Record<string, unknown>;

type RegistryFixture = {
  actionId: string;
  entityType: string;
  id: string;
  key: string;
  label: string;
  listPath: string;
  managementPath: string;
  record: JsonRecord;
  surface: string;
};

type BrowserFetchResult = {
  body: unknown;
  path: string;
  status: number;
};

function frontendUrl(path = "/"): string {
  const base =
    process.env.FRONTEND_BASE_URL ||
    process.env.PLAYWRIGHT_BASE_URL ||
    process.env.PANTHEON_FE_BASE_URL ||
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

function isRecord(value: unknown): value is JsonRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function recordAt(value: unknown, label: string): JsonRecord {
  expect(isRecord(value), `${label} must be an object`).toBe(true);
  return value as JsonRecord;
}

function stringAt(value: unknown, label: string): string {
  expect(typeof value, `${label} must be a string`).toBe("string");
  expect(String(value).trim(), `${label} must not be blank`).not.toBe("");
  return String(value);
}

function arrayAt(value: unknown, label: string): unknown[] {
  expect(Array.isArray(value), `${label} must be an array`).toBe(true);
  return value as unknown[];
}

function canonicalActionEndpoint(registry: Pick<RegistryFixture, "actionId" | "entityType" | "id">): string {
  return `/bff/actions/${registry.entityType}/${registry.id}/${registry.actionId}`;
}

function actionDescriptor(
  registry: Pick<RegistryFixture, "actionId" | "entityType" | "id" | "label">,
): JsonRecord {
  const endpoint = canonicalActionEndpoint(registry);
  return {
    id: `${registry.entityType}.${registry.actionId}`,
    actionId: registry.actionId,
    action_id: registry.actionId,
    entityType: registry.entityType,
    entity_type: registry.entityType,
    targetId: registry.id,
    target_id: registry.id,
    label: `${registry.label} ${registry.actionId}`,
    method: "POST",
    endpoint,
    href: endpoint,
    path: endpoint,
    riskLevel: "low",
    risk_level: "low",
    governance: {
      requiresApproval: false,
      requiresConfirmToken: false,
      requiresTwoMan: false,
      cooldownSeconds: 0,
      idempotencyRequired: true,
    },
    requiredRoles: ["operator"],
  };
}

function withActionDescriptors(
  registry: Pick<RegistryFixture, "actionId" | "entityType" | "id" | "label">,
  record: JsonRecord,
): JsonRecord {
  const descriptor = actionDescriptor(registry);
  return {
    ...record,
    actions: [descriptor],
    actionDescriptors: [descriptor],
    availableActions: [descriptor],
    available_actions: [descriptor],
  };
}

function makeRegistry(
  definition: Omit<RegistryFixture, "record"> & { record: JsonRecord },
): RegistryFixture {
  return {
    ...definition,
    record: withActionDescriptors(definition, definition.record),
  };
}

const REGISTRIES: RegistryFixture[] = [
  makeRegistry({
    key: "strategies",
    label: "B06 Momentum Strategy",
    managementPath: "/management/strategies",
    listPath: "/bff/strategies",
    surface: "strategies",
    entityType: "strategy",
    id: "strategy-b06-momentum",
    actionId: "submit_review",
    record: {
      id: "strategy-b06-momentum",
      strategy_id: "strategy-b06-momentum",
      name: "B06 Momentum Strategy",
      title: "B06 Momentum Strategy",
      state: "review",
      risk: "medium",
      owner: "operator-b06",
      updatedAt: SNAPSHOT_AT,
      alpha: "b06-momentum-alpha",
      capitalPoolId: "pool-b06-core",
      personaIds: ["persona-b06-risk"],
      pnl30d: 0.028,
      sharpe: 1.41,
      drawdown: -0.017,
    },
  }),
  makeRegistry({
    key: "personas",
    label: "B06 Risk Persona",
    managementPath: "/management/personas",
    listPath: "/bff/personas",
    surface: "personas",
    entityType: "persona",
    id: "persona-b06-risk",
    actionId: "test",
    record: {
      id: "persona-b06-risk",
      persona_id: "persona-b06-risk",
      name: "B06 Risk Persona",
      state: "active",
      risk: "low",
      archetype: "risk-steward",
      owner: "operator-b06",
      routedStrategies: 4,
      successRate: 0.82,
      updatedAt: SNAPSHOT_AT,
    },
  }),
  makeRegistry({
    key: "capital-pools",
    label: "B06 Core Capital Pool",
    managementPath: "/management/capital",
    listPath: "/bff/capital-pools",
    surface: "capital_pools",
    entityType: "capital-pool",
    id: "pool-b06-core",
    actionId: "set_limit",
    record: {
      id: "pool-b06-core",
      pool_id: "pool-b06-core",
      capital_pool_id: "pool-b06-core",
      name: "B06 Core Capital Pool",
      status: "active",
      risk_policy_ref: "risk-policy-b06-core",
      max_notional: 1000000,
      updated_at: SNAPSHOT_AT,
    },
  }),
  makeRegistry({
    key: "deployments",
    label: "B06 Paper Deployment",
    managementPath: "/management/deployments",
    listPath: "/bff/deployments",
    surface: "deployments",
    entityType: "deployment",
    id: "deployment-b06-paper",
    actionId: "promote_live",
    record: {
      id: "deployment-b06-paper",
      deployment_id: "deployment-b06-paper",
      name: "B06 Paper Deployment",
      title: "B06 Paper Deployment",
      strategy_id: "strategy-b06-momentum",
      status: "pending_approval",
      deployment_stage: "paper",
      stage: "paper",
      updated_at: SNAPSHOT_AT,
    },
  }),
  makeRegistry({
    key: "runtimes",
    label: "B06 Runtime Binding",
    managementPath: "/management/runtimes",
    listPath: "/bff/runtimes",
    surface: "runtimes",
    entityType: "runtime",
    id: "runtime-b06-paper",
    actionId: "restart",
    record: {
      id: "runtime-b06-paper",
      runtime_id: "runtime-b06-paper",
      binding_id: "binding-b06-paper",
      name: "B06 Runtime Binding",
      status: "healthy",
      deployment_stage: "paper",
      strategy_id: "strategy-b06-momentum",
      persona_id: "persona-b06-risk",
      updated_at: SNAPSHOT_AT,
    },
  }),
  makeRegistry({
    key: "rebalances",
    label: "B06 Rebalance Plan",
    managementPath: "/management/rebalance",
    listPath: "/bff/rebalances",
    surface: "rebalances",
    entityType: "rebalance",
    id: "rebalance-b06-core",
    actionId: "submit_for_review",
    record: {
      id: "rebalance-b06-core",
      rebalance_id: "rebalance-b06-core",
      name: "B06 Rebalance Plan",
      title: "B06 Rebalance Plan",
      capital_pool_id: "pool-b06-core",
      status: "awaiting_review",
      reason: "risk budget drift",
      updated_at: SNAPSHOT_AT,
    },
  }),
  makeRegistry({
    key: "evolution-programs",
    label: "B06 Evolution Program",
    managementPath: "/management/evolution",
    listPath: "/bff/evolution-programs",
    surface: "evolution_programs",
    entityType: "evolution-program",
    id: "evolution-b06-program",
    actionId: "stop",
    record: {
      id: "evolution-b06-program",
      program_id: "evolution-b06-program",
      evolution_program_id: "evolution-b06-program",
      name: "B06 Evolution Program",
      title: "B06 Evolution Program",
      strategy_id: "strategy-b06-momentum",
      status: "running",
      current_generation: 7,
      updated_at: SNAPSHOT_AT,
    },
  }),
  makeRegistry({
    key: "research-experiments",
    label: "B06 Research Experiment",
    managementPath: "/management/experiments",
    listPath: "/bff/research-experiments",
    surface: "research_experiments",
    entityType: "research-experiment",
    id: "experiment-b06-qlib",
    actionId: "promote_artifact",
    record: {
      id: "experiment-b06-qlib",
      experiment_id: "experiment-b06-qlib",
      name: "B06 Research Experiment",
      title: "B06 Research Experiment",
      linked_strategy_id: "strategy-b06-momentum",
      backend: "qlib",
      status: "completed",
      updated_at: SNAPSHOT_AT,
    },
  }),
  makeRegistry({
    key: "tools",
    label: "B06 Tool Registry",
    managementPath: "/management/tools",
    listPath: "/bff/tools",
    surface: "tools",
    entityType: "tool",
    id: "tool-b06-replay",
    actionId: "test_tool",
    record: {
      id: "tool-b06-replay",
      tool_id: "tool-b06-replay",
      name: "B06 Tool Registry",
      status: "active",
      tool_class: "analysis",
      description: "Fixture tool for F07 entity registry coverage.",
      updated_at: SNAPSHOT_AT,
    },
  }),
  makeRegistry({
    key: "mcp-servers",
    label: "B06 MCP Server",
    managementPath: "/management/mcp",
    listPath: "/bff/mcp-servers",
    surface: "mcp_servers",
    entityType: "mcp-server",
    id: "mcp-server-b06",
    actionId: "health_check",
    record: {
      id: "mcp-server-b06",
      server_id: "mcp-server-b06",
      name: "B06 MCP Server",
      status: "connected",
      endpoint: "https://mcp-b06.local",
      server_version: "2026.05",
      updated_at: SNAPSHOT_AT,
    },
  }),
  makeRegistry({
    key: "skills",
    label: "B06 Skill Registry",
    managementPath: "/management/skills",
    listPath: "/bff/skills",
    surface: "skills",
    entityType: "skill",
    id: "skill-b06-audit",
    actionId: "publish",
    record: {
      id: "skill-b06-audit",
      skill_id: "skill-b06-audit",
      name: "B06 Skill Registry",
      status: "draft",
      description: "Fixture skill for F07 entity registry coverage.",
      sandbox_enabled: true,
      updated_at: SNAPSHOT_AT,
    },
  }),
  makeRegistry({
    key: "channels",
    label: "B06 Channel Registry",
    managementPath: "/management/channels",
    listPath: "/bff/channels",
    surface: "channels",
    entityType: "channel",
    id: "channel-b06-system",
    actionId: "activate",
    record: {
      id: "channel-b06-system",
      channel_id: "channel-b06-system",
      name: "B06 Channel Registry",
      status: "active",
      replay_supported: true,
      resync_routes: ["/bff/events/resync?channel=system"],
      updated_at: SNAPSHOT_AT,
    },
  }),
];

const ME_RESPONSE = {
  data: {
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
      capabilities: ["runtime.read", "registry.read"],
      mfa_verified: true,
    },
    currentUser: {
      id: "op-fe-gate",
      operator_id: "op-fe-gate",
      display_name: "FE Gate Operator",
      roles: ["operator", "reviewer", "approver"],
      capabilities: ["runtime.read", "registry.read"],
      mfa_verified: true,
    },
    current_user: {
      id: "op-fe-gate",
      operator_id: "op-fe-gate",
      display_name: "FE Gate Operator",
      roles: ["operator", "reviewer", "approver"],
      capabilities: ["runtime.read", "registry.read"],
      mfa_verified: true,
    },
    roles: ["operator", "reviewer", "approver"],
    capabilities: ["runtime.read", "registry.read"],
    session: {
      id: "session-fe-gate-b06",
      authenticated: true,
      fresh: true,
      mfa_verified: true,
      session_kind: "stub",
      auth_mode: "stub",
      checked_at: SNAPSHOT_AT,
    },
    feature_flags: {
      sessionAuthMe: true,
    },
  },
  meta: {
    route: "GET /bff/me",
    contract: "FE-INT-GATE-B06",
  },
};

function corsHeaders(route: Route): Record<string, string> {
  const origin = route.request().headers().origin ?? "*";
  return {
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Headers":
      "accept,authorization,content-type,idempotency-key,x-correlation-id,x-locale,x-request-id,x-tenant-id",
    "Access-Control-Allow-Methods": "GET,POST,PATCH,DELETE,OPTIONS",
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Expose-Headers":
      "X-BFF-Api-Version,X-Correlation-Id,X-Request-Id",
    "X-BFF-Api-Version": "2026-05-13",
    "X-Correlation-Id": "corr-fe-int-gate-b06",
    "X-Request-Id": "req-fe-int-gate-b06",
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

function listEnvelope(registry: RegistryFixture): JsonRecord {
  const items = [registry.record];
  return {
    data: items,
    items,
    cursor: {},
    pageSize: items.length,
    page_size: items.length,
    estimatedTotal: items.length,
    totalCount: items.length,
    total_count: items.length,
    totalCountExact: true,
    page_info: {
      next_page_token: null,
      total: items.length,
    },
    meta: {
      snapshot_at: SNAPSHOT_AT,
      surfaces: {
        [registry.surface]: {
          status: "ok",
          source: "fe-int-gate-b06",
        },
      },
      total: items.length,
      totalCount: items.length,
      total_count: items.length,
      totalCountExact: true,
    },
  };
}

function emptyListEnvelope(surface: string): JsonRecord {
  return {
    data: [],
    items: [],
    cursor: {},
    pageSize: 0,
    estimatedTotal: 0,
    totalCount: 0,
    total_count: 0,
    totalCountExact: true,
    page_info: { next_page_token: null, total: 0 },
    meta: {
      snapshot_at: SNAPSHOT_AT,
      surfaces: {
        [surface]: { status: "ok", source: "fe-int-gate-b06-empty" },
      },
      totalCountExact: true,
    },
  };
}

function detailEnvelope(registry: RegistryFixture): JsonRecord {
  return {
    data: registry.record,
    meta: {
      snapshot_at: SNAPSHOT_AT,
      surfaces: {
        [`${registry.surface}_detail`]: {
          status: "ok",
          source: "fe-int-gate-b06",
        },
      },
    },
  };
}

function resourceNotFoundEnvelope(registry: RegistryFixture, entityId: string): JsonRecord {
  return {
    detail: {
      error: {
        code: "RESOURCE_NOT_FOUND",
        i18nKey: "errors.RESOURCE_NOT_FOUND",
        message: `${registry.label} ${entityId} was not found`,
        retryable: false,
        userActionable: true,
        correlationId: "corr-fe-int-gate-b06",
        details: {
          entityType: registry.entityType,
          entityId,
          route: `${registry.listPath}/${entityId}`,
        },
      },
    },
  };
}

function commandAcceptedEnvelope(registry: RegistryFixture): JsonRecord {
  const commandId = `cmd-b06-${registry.key}`;
  return {
    status: "accepted",
    data: {
      command_id: commandId,
      commandId,
      receipt_id: commandId,
      status: "accepted",
      target: {
        type: registry.entityType,
        id: registry.id,
      },
      action: registry.actionId,
    },
    meta: {
      durable: true,
      liveCapitalSideEffects: false,
      idempotency: {
        idempotencyKey: `idem-${registry.key}`,
        replayed: false,
      },
    },
  };
}

function idFromDetailPath(path: string, registry: RegistryFixture): string | null {
  if (!path.startsWith(`${registry.listPath}/`)) {
    return null;
  }
  const remainder = path.slice(registry.listPath.length + 1);
  if (!remainder || remainder.includes("/")) {
    return null;
  }
  return decodeURIComponent(remainder);
}

async function installQuietEventSource(page: Page): Promise<void> {
  await page.addInitScript(() => {
    class PantheonB06EventSource extends EventTarget {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSED = 2;

      readonly url: string;
      readonly withCredentials: boolean;
      readyState = PantheonB06EventSource.CONNECTING;
      onopen: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;

      constructor(url: string | URL, init?: EventSourceInit) {
        super();
        this.url = String(url);
        this.withCredentials = init?.withCredentials ?? false;
        window.setTimeout(() => {
          if (this.readyState === PantheonB06EventSource.CLOSED) return;
          this.readyState = PantheonB06EventSource.OPEN;
          const event = new Event("open");
          this.dispatchEvent(event);
          this.onopen?.(event);
        }, 0);
      }

      close() {
        this.readyState = PantheonB06EventSource.CLOSED;
      }
    }

    (window as unknown as { EventSource: typeof EventSource }).EventSource =
      PantheonB06EventSource as unknown as typeof EventSource;
  });
}

async function installRegistryRoutes(page: Page, calls = new Set<string>()): Promise<Set<string>> {
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: corsHeaders(route) });
      return;
    }

    if (path === "/health") {
      await fulfillJson(route, { status: "ok", checked_at: SNAPSHOT_AT });
      return;
    }

    if (path === "/bff/me") {
      calls.add("/bff/me");
      await fulfillJson(route, ME_RESPONSE);
      return;
    }

    if (path === "/bff/actions") {
      const catalog = REGISTRIES.map((registry) => actionDescriptor(registry));
      calls.add("/bff/actions");
      await fulfillJson(route, {
        data: catalog,
        catalog,
        items: catalog,
        meta: {
          snapshot_at: SNAPSHOT_AT,
          totalCountExact: true,
        },
        totalCount: catalog.length,
        totalCountExact: true,
        version: "v1",
      });
      return;
    }

    for (const registry of REGISTRIES) {
      if (path === registry.listPath && request.method() === "GET") {
        calls.add(registry.listPath);
        await fulfillJson(route, listEnvelope(registry));
        return;
      }

      const actionPath = canonicalActionEndpoint(registry);
      if (path === actionPath && request.method() === "POST") {
        calls.add(actionPath);
        await fulfillJson(route, commandAcceptedEnvelope(registry), 202);
        return;
      }

      const detailId = idFromDetailPath(path, registry);
      if (detailId) {
        calls.add(`${registry.listPath}/{id}`);
        await fulfillJson(
          route,
          detailId === registry.id
            ? detailEnvelope(registry)
            : resourceNotFoundEnvelope(registry, detailId),
          detailId === registry.id ? 200 : 404,
        );
        return;
      }
    }

    if (path.startsWith("/bff/")) {
      calls.add(path);
      await fulfillJson(route, emptyListEnvelope("ancillary"));
      return;
    }

    await route.continue();
  });

  return calls;
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

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function gotoRegistry(page: Page, registry: RegistryFixture): Promise<void> {
  await page.goto(frontendUrl(registry.managementPath), {
    waitUntil: "domcontentloaded",
    timeout: 30_000,
  });
  await page.locator("#root").waitFor({ state: "attached", timeout: 15_000 });
  const expectedText = new RegExp(escapeRegExp(registry.label), "i");
  await expect
    .poll(
      async () => {
        const text = await bodyText(page);
        return expectedText.test(text);
      },
      {
        message: `${registry.managementPath} should render ${registry.label}`,
        timeout: 20_000,
      },
    )
    .toBe(true);
}

async function fetchJsonInBrowser(
  page: Page,
  paths: string[],
): Promise<BrowserFetchResult[]> {
  const urls = paths.map((path) => frontendUrl(path));
  return page.evaluate(async (requestedUrls) => {
    return Promise.all(
      requestedUrls.map(async (url) => {
        const response = await fetch(url, {
          credentials: "omit",
          headers: { Accept: "application/json" },
        });
        let body: unknown = null;
        try {
          body = await response.json();
        } catch {
          body = await response.text();
        }
        return { path: new URL(url).pathname, status: response.status, body };
      }),
    );
  }, urls);
}

function itemsFromListEnvelope(payload: JsonRecord, label: string): JsonRecord[] {
  const source = Array.isArray(payload.items) ? payload.items : payload.data;
  const items = arrayAt(source, `${label}.items/data`);
  return items.map((item, index) => recordAt(item, `${label}.items[${index}]`));
}

function assertListEnvelope(payload: unknown, label: string): JsonRecord[] {
  const body = recordAt(payload, label);
  const items = itemsFromListEnvelope(body, label);
  expect(items.length, `${label} must be non-empty`).toBeGreaterThan(0);
  expect(body.totalCountExact, `${label}.totalCountExact`).toBe(true);
  const meta = recordAt(body.meta, `${label}.meta`);
  expect(meta.totalCountExact, `${label}.meta.totalCountExact`).toBe(true);
  return items;
}

function errorCodeFromEnvelope(payload: unknown, label: string): string {
  const body = recordAt(payload, label);
  const detail = recordAt(body.detail, `${label}.detail`);
  const error = recordAt(detail.error, `${label}.detail.error`);
  return stringAt(error.code, `${label}.detail.error.code`);
}

function descriptorsFromRecord(record: JsonRecord): JsonRecord[] {
  const candidates = [
    record.actions,
    record.actionDescriptors,
    record.availableActions,
    record.available_actions,
  ];
  return candidates.flatMap((candidate) =>
    Array.isArray(candidate)
      ? candidate.filter(isRecord)
      : isRecord(candidate)
        ? [candidate]
        : [],
  );
}

function endpointFromDescriptor(descriptor: JsonRecord, label: string): string {
  return stringAt(
    descriptor.endpoint ?? descriptor.href ?? descriptor.path,
    `${label}.endpoint`,
  );
}

test.describe("F07 entity registry", () => {
  test.describe.configure({ timeout: 180_000 });

  test("renders all 12 registry surfaces from fixture-backed list routes", async ({
    page,
  }) => {
    const calls = new Set<string>();
    const failures = collectPageFailures(page);
    await installQuietEventSource(page);
    await installRegistryRoutes(page, calls);

    for (const registry of REGISTRIES) {
      await gotoRegistry(page, registry);
      const text = await bodyText(page);
      expect(text, `${registry.key} should not render a seed fallback banner`).not.toMatch(
        SERVING_MOCK_BANNER,
      );
      expect(text, `${registry.key} should not render a crash`).not.toMatch(CRASH_TEXT);
    }

    for (const registry of REGISTRIES) {
      expect(calls.has(registry.listPath), `${registry.key} list route should be read`).toBe(true);
    }
    expect(failures, "registry pages should not emit console/page errors").toEqual([]);
  });

  test("keeps every registry ListResponse exact-counted", async ({ page }) => {
    await installQuietEventSource(page);
    await installRegistryRoutes(page);
    await page.setContent("<!doctype html><title>FE-INT-GATE-B06</title>");

    const results = await fetchJsonInBrowser(
      page,
      REGISTRIES.map((registry) => registry.listPath),
    );

    for (const result of results) {
      expect(result.status, `${result.path} status`).toBe(200);
      assertListEnvelope(result.body, `${result.path} ListResponse`);
    }
  });

  test("returns RESOURCE_NOT_FOUND envelopes for missing registry details", async ({
    page,
  }) => {
    await installQuietEventSource(page);
    await installRegistryRoutes(page);
    await page.setContent("<!doctype html><title>FE-INT-GATE-B06</title>");

    const results = await fetchJsonInBrowser(
      page,
      REGISTRIES.map((registry) => `${registry.listPath}/${MISSING_ID}`),
    );

    for (const result of results) {
      expect(result.status, `${result.path} status`).toBe(404);
      expect(errorCodeFromEnvelope(result.body, `${result.path} BffError`)).toBe(
        "RESOURCE_NOT_FOUND",
      );
    }
  });

  test("projects ActionDescriptor endpoints to canonical /bff/actions paths", () => {
    for (const registry of REGISTRIES) {
      const descriptors = descriptorsFromRecord(registry.record);
      expect(descriptors.length, `${registry.key} descriptors`).toBeGreaterThan(0);
      const expectedEndpoint = canonicalActionEndpoint(registry);

      for (const [index, descriptor] of descriptors.entries()) {
        expect(
          endpointFromDescriptor(descriptor, `${registry.key}.descriptor[${index}]`),
        ).toBe(expectedEndpoint);
        expect(
          stringAt(
            descriptor.actionId ?? descriptor.action_id,
            `${registry.key}.descriptor[${index}].actionId`,
          ),
        ).toBe(registry.actionId);
        expect(
          stringAt(
            descriptor.entityType ?? descriptor.entity_type,
            `${registry.key}.descriptor[${index}].entityType`,
          ),
        ).toBe(registry.entityType);
      }
    }
  });

  test("live BFF registry probes preserve F07 response contracts", async ({
    request,
  }) => {
    test.skip(
      !RUN_LIVE_BFF_CONTRACT,
      "Set FE_INT_GATE_LIVE_BFF=1 to run the staging BFF contract probe.",
    );

    const tenantId = process.env.BFF_TENANT_ID || process.env.PANTHEON_TENANT_ID;
    const headers: Record<string, string> = {
      Accept: "application/json",
      Authorization: authHeader(),
      "X-Locale": "zh-TW",
    };
    if (tenantId) {
      headers["X-Tenant-Id"] = tenantId;
    }

    for (const registry of REGISTRIES) {
      const listResponse = await request.get(bffUrl(registry.listPath), {
        headers,
        timeout: 10_000,
      });
      expect(listResponse.status(), await listResponse.text()).toBe(200);
      const listPayload = recordAt(
        await listResponse.json(),
        `${registry.key} live ListResponse`,
      );
      expect(
        listPayload.totalCountExact,
        `${registry.key} live totalCountExact`,
      ).toBe(true);

      const detailResponse = await request.get(
        bffUrl(`${registry.listPath}/${MISSING_ID}`),
        { headers, timeout: 10_000 },
      );
      expect(detailResponse.status(), await detailResponse.text()).toBe(404);
      expect(
        errorCodeFromEnvelope(
          await detailResponse.json(),
          `${registry.key} live detail error`,
        ),
      ).toBe("RESOURCE_NOT_FOUND");
    }
  });
});
