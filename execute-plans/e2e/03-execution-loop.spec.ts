/**
 * FE-INT-GATE-B03 / F03 - execution loop persona health contract.
 *
 * Coverage:
 *   1. PersonaHealthMatrix renders mode, status, score, routed strategies,
 *      and open findings from GET /bff/v5/execution/persona-health.
 *   2. Critical/degraded personas expose an operator drill-down to a Sentinel
 *      finding or evidence reference.
 *   3. RedactedEvidenceRef payloads are metadata-only and do not expose raw
 *      secret-bearing evidence.
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

import { expect, test, type Locator, type Page, type Route } from "@playwright/test";

const DEFAULT_FRONTEND_BASE_URL = "http://127.0.0.1:5173";
const DEFAULT_BFF_BASE_URL =
  "https://pantheon-staging-bff.34.81.225.122.sslip.io";
const DEFAULT_DEV_AUTH_TOKEN = "op-fe-gate:operator,reviewer:mfa";
const RUN_LIVE_BFF_CONTRACT =
  process.env.FE_INT_GATE_LIVE_BFF === "1" ||
  process.env.RUN_LIVE_BFF_CONTRACTS === "1";
const SECRET_SENTINEL = "FE_INT_GATE_B03_SHOULD_NOT_RENDER_SECRET";

type JsonRecord = Record<string, unknown>;

const PERSONA_HEALTH_PATH = "/bff/v5/execution/persona-health";
const LOOP_RUNS_PATH = "/bff/v5/loop-runs";
const SENTINEL_FINDINGS_PATH = "/bff/v5/sentinel/findings";
const ME_PATH = "/bff/me";
const HEALTH_PATH = "/health";
const SEARCH_PATH = "/bff/search";
const APPROVALS_PATH = "/bff/approvals";
const ALERTS_PATH = "/bff/alerts";
const JOBS_PATH = "/bff/jobs";
const EVENT_STREAM_PATH = "/bff/events/stream";
const STRATEGY_HEALTH_PATH = "/bff/v5/execution/strategy-health";
const REDACTED_EVIDENCE_PATH =
  "/api/v1/knowledge/evidence/ev-redacted-metric";

const rawSecretEvidence = {
  ref_id: "ev-redacted-metric",
  evidence_type: "metric",
  source_document: {
    source_type: "internal_metric",
    raw_secret_value: SECRET_SENTINEL,
  },
  resolved_link: {
    url: `https://evidence.local/raw?api_key=${SECRET_SENTINEL}`,
  },
  source_note_context: {
    token: SECRET_SENTINEL,
  },
};

const redactedEvidenceRef = {
  ref_id: rawSecretEvidence.ref_id,
  kind: "metric",
  required_capability: "metric.read",
  reason: "insufficient_capability",
  redacted: true,
  display_label: "Metric evidence redacted",
};

const redactedEvidenceDetail = {
  ...redactedEvidenceRef,
  meta: {
    snapshot_at: "2026-05-13T13:00:00Z",
    surfaces: {
      evidence_ref_detail: "ok",
      resolved_link: "ok",
      linked_decisions: "ok",
    },
    redacted_evidence_count: 1,
  },
};

const personaHealthPayload = {
  items: [
    {
      persona_id: "persona-risk",
      personaId: "persona-risk",
      name: "Risk Sentinel",
      persona_name: "Risk Sentinel",
      personaName: "Risk Sentinel",
      mode: "live",
      status: "critical",
      health: "critical",
      score: 19,
      routed_strategies: 4,
      routedStrategies: 4,
      open_findings: 2,
      openFindings: 2,
      updated_at: "2026-05-13T12:55:00Z",
      sentinel_finding_id: "finding-persona-risk",
      evidence_ref_id: "ev-redacted-metric",
      drill_down: {
        kind: "sentinel_finding",
        href: "/management/sentinel?finding=finding-persona-risk",
        sentinel_finding_id: "finding-persona-risk",
        evidence_ref_id: "ev-redacted-metric",
      },
      drillDown: {
        kind: "sentinel_finding",
        href: "/management/sentinel?finding=finding-persona-risk",
        sentinelFindingId: "finding-persona-risk",
        evidenceRefId: "ev-redacted-metric",
      },
    },
    {
      persona_id: "persona-latency",
      personaId: "persona-latency",
      name: "Latency Arbiter",
      persona_name: "Latency Arbiter",
      personaName: "Latency Arbiter",
      mode: "shadow",
      status: "degraded",
      health: "degraded",
      score: 48,
      routed_strategies: 1,
      routedStrategies: 1,
      open_findings: 1,
      openFindings: 1,
      updated_at: "2026-05-13T12:56:00Z",
      sentinel_finding_id: "finding-persona-latency",
      evidence_ref_id: "ev-redacted-metric",
      drill_down: {
        kind: "evidence_ref",
        href: REDACTED_EVIDENCE_PATH,
        sentinel_finding_id: "finding-persona-latency",
        evidence_ref_id: "ev-redacted-metric",
      },
      drillDown: {
        kind: "evidence_ref",
        href: REDACTED_EVIDENCE_PATH,
        sentinelFindingId: "finding-persona-latency",
        evidenceRefId: "ev-redacted-metric",
      },
    },
    {
      persona_id: "persona-hedge",
      personaId: "persona-hedge",
      name: "Hedge Steward",
      persona_name: "Hedge Steward",
      personaName: "Hedge Steward",
      mode: "paper",
      status: "healthy",
      health: "healthy",
      score: 91,
      routed_strategies: 7,
      routedStrategies: 7,
      open_findings: 0,
      openFindings: 0,
      updated_at: "2026-05-13T12:57:00Z",
    },
  ],
  meta: {
    snapshot_at: "2026-05-13T13:00:00Z",
    surfaces: {
      persona_health: { status: "ok", source: "fe-int-gate-b03" },
    },
  },
};

const sentinelFindingsPayload = {
  items: [
    {
      id: "finding-persona-risk",
      finding_id: "finding-persona-risk",
      title: "Risk breach from persona health",
      summary: "Risk Sentinel score fell below the critical threshold.",
      status: "open",
      severity: "critical",
      confidence: 0.93,
      source: "persona-health",
      detected_at: "2026-05-13T12:50:00Z",
      updated_at: "2026-05-13T12:58:00Z",
      persona_ids: ["persona-risk"],
      strategy_ids: ["strategy-momentum"],
      evidence: [redactedEvidenceRef],
      evidence_refs: [redactedEvidenceRef],
      evidenceRefs: [redactedEvidenceRef],
      recommended_action_ids: ["pause_persona_routing"],
      recommendedActionIds: ["pause_persona_routing"],
    },
    {
      id: "finding-persona-latency",
      finding_id: "finding-persona-latency",
      title: "Latency drift from persona health",
      summary: "Latency Arbiter degraded because recent execution p95 widened.",
      status: "open",
      severity: "warning",
      confidence: 0.78,
      source: "persona-health",
      detected_at: "2026-05-13T12:51:00Z",
      updated_at: "2026-05-13T12:59:00Z",
      persona_ids: ["persona-latency"],
      strategy_ids: ["strategy-arbitrage"],
      evidence: [redactedEvidenceRef],
      evidence_refs: [redactedEvidenceRef],
      evidenceRefs: [redactedEvidenceRef],
      recommended_action_ids: ["switch_persona_to_shadow"],
      recommendedActionIds: ["switch_persona_to_shadow"],
    },
  ],
  meta: {
    snapshot_at: "2026-05-13T13:00:00Z",
    surfaces: {
      sentinel_findings: { status: "ok", source: "fe-int-gate-b03" },
    },
  },
};

const mePayload = {
  data: {
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
      roles: ["operator", "reviewer"],
      capabilities: ["runtime.read", "sentinel.read", "risk.incident.read"],
      mfa_verified: true,
    },
    currentUser: {
      id: "op-fe-gate",
      operator_id: "op-fe-gate",
      display_name: "FE Gate Operator",
      roles: ["operator", "reviewer"],
      capabilities: ["runtime.read", "sentinel.read", "risk.incident.read"],
      mfa_verified: true,
    },
    current_user: {
      id: "op-fe-gate",
      operator_id: "op-fe-gate",
      display_name: "FE Gate Operator",
      roles: ["operator", "reviewer"],
      capabilities: ["runtime.read", "sentinel.read", "risk.incident.read"],
      mfa_verified: true,
    },
    roles: ["operator", "reviewer"],
    capabilities: ["runtime.read", "sentinel.read", "risk.incident.read"],
    session: {
      id: "session-fe-gate",
      authenticated: true,
      session_kind: "stub",
      auth_mode: "stub",
      fresh: true,
      mfa_verified: true,
      checked_at: "2026-05-13T13:00:00Z",
    },
    feature_flags: { sessionAuthMe: true },
  },
  meta: {
    route: "GET /bff/me",
    contract: "BFF-LUV-GAP-009",
  },
};

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

function numberAt(value: unknown, label: string): number {
  const numeric = Number(value);
  expect(Number.isFinite(numeric), `${label} must be numeric`).toBe(true);
  return numeric;
}

function itemsAt(value: unknown, label: string): JsonRecord[] {
  const payload = recordAt(value, label);
  expect(Array.isArray(payload.items), `${label}.items must be an array`).toBe(
    true,
  );
  return (payload.items as unknown[]).map((item, index) =>
    recordAt(item, `${label}.items[${index}]`),
  );
}

async function bodyText(page: Page): Promise<string> {
  return page.locator("body").innerText({ timeout: 10_000 });
}

async function fulfillJson(
  route: Route,
  body: unknown,
  status = 200,
): Promise<void> {
  await route.fulfill({
    status,
    contentType: "application/json",
    headers: {
      ...corsHeaders(route),
    },
    body: JSON.stringify(body),
  });
}

async function fulfillEventStream(route: Route): Promise<void> {
  await route.fulfill({
    status: 200,
    contentType: "text/event-stream",
    headers: {
      ...corsHeaders(route),
      "cache-control": "no-cache",
    },
    body: "",
  });
}

function corsHeaders(route: Route): Record<string, string> {
  const origin = route.request().headers()["origin"] || "*";
  return {
    "access-control-allow-origin": origin,
    "access-control-allow-credentials": "true",
    "access-control-allow-methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
    "access-control-allow-headers": [
      "accept",
      "authorization",
      "content-type",
      "idempotency-key",
      "if-match",
      "x-bff-api-version",
      "x-correlation-id",
      "x-locale",
      "x-request-id",
      "x-tenant-id",
    ].join(","),
    "access-control-expose-headers": [
      "x-bff-api-version",
      "x-correlation-id",
      "x-request-id",
    ].join(","),
    vary: "Origin",
  };
}

function listEnvelope(items: unknown[] = []): JsonRecord {
  return {
    items,
    cursor: {},
    pageSize: items.length,
    totalCountExact: true,
    estimatedTotal: items.length,
    meta: {
      snapshot_at: "2026-05-13T13:00:00Z",
      source: "fe-int-gate-b03-fixture",
    },
  };
}

async function installB03Routes(page: Page): Promise<Set<string>> {
  const calls = new Set<string>();

  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (request.method() === "OPTIONS") {
      await route.fulfill({
        status: 204,
        headers: {
          ...corsHeaders(route),
        },
      });
      return;
    }

    if (path === HEALTH_PATH) {
      calls.add(HEALTH_PATH);
      await fulfillJson(route, {
        status: "ok",
        service: "fe-int-gate-b03-fixture",
        checked_at: "2026-05-13T13:00:00Z",
      });
      return;
    }

    if (path === EVENT_STREAM_PATH) {
      calls.add(EVENT_STREAM_PATH);
      await fulfillEventStream(route);
      return;
    }

    if (path === SEARCH_PATH) {
      calls.add(SEARCH_PATH);
      await fulfillJson(route, { items: [] });
      return;
    }

    if ([APPROVALS_PATH, ALERTS_PATH, JOBS_PATH].includes(path)) {
      calls.add(path);
      await fulfillJson(route, listEnvelope());
      return;
    }

    if (path === PERSONA_HEALTH_PATH) {
      calls.add(PERSONA_HEALTH_PATH);
      await fulfillJson(route, personaHealthPayload);
      return;
    }

    if (path === ME_PATH) {
      calls.add(ME_PATH);
      await fulfillJson(route, mePayload);
      return;
    }

    if (path === STRATEGY_HEALTH_PATH) {
      calls.add(STRATEGY_HEALTH_PATH);
      await fulfillJson(route, {
        items: [],
        meta: {
          snapshot_at: "2026-05-13T13:00:00Z",
          surfaces: {
            strategy_health: { status: "ok", source: "fe-int-gate-b03" },
          },
        },
      });
      return;
    }

    if (path === LOOP_RUNS_PATH) {
      calls.add(LOOP_RUNS_PATH);
      await fulfillJson(route, {
        items: [],
        meta: {
          snapshot_at: "2026-05-13T13:00:00Z",
          surfaces: { loop_runs: { status: "ok", source: "fe-int-gate-b03" } },
        },
      });
      return;
    }

    if (path.startsWith(`${SENTINEL_FINDINGS_PATH}/`)) {
      calls.add(path);
      const id = decodeURIComponent(path.slice(SENTINEL_FINDINGS_PATH.length + 1));
      const item = sentinelFindingsPayload.items.find((finding) => finding.id === id);
      await fulfillJson(
        route,
        item
          ? { data: item, meta: sentinelFindingsPayload.meta }
          : {
              detail: {
                error: {
                  code: "RESOURCE_NOT_FOUND",
                  message: `Finding ${id} not found`,
                },
              },
            },
        item ? 200 : 404,
      );
      return;
    }

    if (path === SENTINEL_FINDINGS_PATH) {
      calls.add(SENTINEL_FINDINGS_PATH);
      await fulfillJson(route, sentinelFindingsPayload);
      return;
    }

    if (path === REDACTED_EVIDENCE_PATH) {
      calls.add(REDACTED_EVIDENCE_PATH);
      await fulfillJson(route, redactedEvidenceDetail);
      return;
    }

    await route.continue();
  });

  return calls;
}

async function gotoExecutionPersonas(page: Page): Promise<void> {
  await page.goto(frontendUrl("/management/loops/execution?focus=personas"), {
    waitUntil: "domcontentloaded",
    timeout: 60_000,
  });
  await page.locator("#root").waitFor({ state: "attached", timeout: 15_000 });
}

function personaRow(page: Page, personaName: string): Locator {
  return page.getByRole("row").filter({ hasText: personaName });
}

function drillDownControl(row: Locator): Locator {
  return row.getByRole("link").or(row.getByRole("button")).first();
}

function expectNoSecret(value: unknown, label: string): void {
  const text = JSON.stringify(value);
  expect(text, `${label} must not expose raw secret value`).not.toContain(
    SECRET_SENTINEL,
  );
}

function forbiddenSecretPaths(value: unknown, path = "$"): string[] {
  const forbiddenKeys = new Set([
    "api_key",
    "apikey",
    "credential",
    "credentials",
    "password",
    "raw",
    "raw_payload",
    "raw_secret",
    "raw_secret_value",
    "resolved_link",
    "secret",
    "source_document",
    "source_memory_context",
    "source_note_context",
    "token",
  ]);

  if (!isRecord(value)) {
    if (Array.isArray(value)) {
      return value.flatMap((item, index) => forbiddenSecretPaths(item, `${path}[${index}]`));
    }
    return [];
  }

  const matches: string[] = [];
  for (const [key, child] of Object.entries(value)) {
    const childPath = `${path}.${key}`;
    const keyLower = key.toLowerCase();
    const isSurfaceStatus =
      keyLower === "resolved_link" &&
      path.endsWith(".meta.surfaces") &&
      typeof child === "string";
    if (forbiddenKeys.has(keyLower) && !isSurfaceStatus) {
      matches.push(childPath);
    }
    matches.push(...forbiddenSecretPaths(child, childPath));
  }
  return matches;
}

function assertRedactedEvidenceRef(value: unknown, label: string): void {
  const ref = recordAt(value, label);
  expect(ref.redacted, `${label}.redacted`).toBe(true);
  stringAt(ref.ref_id ?? ref.id, `${label}.ref_id`);
  stringAt(ref.kind, `${label}.kind`);
  stringAt(
    ref.required_capability ?? ref.requiredCapability,
    `${label}.required_capability`,
  );
  stringAt(ref.reason ?? ref.redactionReasonCode, `${label}.reason`);
  expectNoSecret(ref, label);
  expect(forbiddenSecretPaths(ref), `${label} must be metadata-only`).toEqual(
    [],
  );
}

async function assertPersonaDrillsDown(
  page: Page,
  personaName: string,
  expectedFindingId: string,
): Promise<void> {
  const row = personaRow(page, personaName);
  await expect(row, `${personaName} row`).toBeVisible();

  const control = drillDownControl(row);
  await expect(
    control,
    `${personaName} must expose a Sentinel/evidence drill-down control`,
  ).toBeVisible();

  await control.click();

  await expect
    .poll(
      async () => {
        const url = page.url();
        const text = await bodyText(page);
        return (
          url.includes(expectedFindingId) ||
          text.includes(expectedFindingId) ||
          text.includes("ev-redacted-metric") ||
          text.includes("Metric evidence redacted")
        );
      },
      {
        message: `${personaName} drill-down should reach Sentinel finding or evidence`,
        timeout: 10_000,
      },
    )
    .toBe(true);
}

test.describe("F03 execution loop persona health", () => {
  test.describe.configure({ timeout: 60_000 });

  test("asserts persona-health BFF shape for matrix fields", async ({
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

    const response = await request.get(bffUrl(PERSONA_HEALTH_PATH), {
      headers,
      timeout: 10_000,
    });

    expect(response.status(), await response.text()).toBe(200);
    const items = itemsAt(await response.json(), "PersonaHealthResponse");
    expect(items.length, "persona health must not be empty").toBeGreaterThan(0);

    for (const [index, item] of items.entries()) {
      stringAt(
        item.persona_id ?? item.personaId ?? item.id,
        `items[${index}].persona_id`,
      );
      stringAt(
        item.name ?? item.persona_name ?? item.personaName,
        `items[${index}].name`,
      );
      stringAt(item.mode, `items[${index}].mode`);
      stringAt(
        item.status ?? item.health,
        `items[${index}].status_or_health`,
      );
      numberAt(item.score, `items[${index}].score`);
      numberAt(
        item.routed_strategies ?? item.routedStrategies,
        `items[${index}].routed_strategies`,
      );
      numberAt(
        item.open_findings ?? item.openFindings,
        `items[${index}].open_findings`,
      );
    }
  });

  test("renders mode, status, score, routed strategies, and open findings", async ({
    page,
  }) => {
    const calls = await installB03Routes(page);

    await gotoExecutionPersonas(page);

    await expect
      .poll(() => calls.has(PERSONA_HEALTH_PATH), {
        message: "execution loop must request persona health",
        timeout: 15_000,
      })
      .toBe(true);

    const riskRow = personaRow(page, "Risk Sentinel");
    await expect(riskRow).toContainText("live");
    await expect(riskRow).toContainText("critical");
    await expect(riskRow).toContainText("19");
    await expect(riskRow).toContainText("4");
    await expect(riskRow).toContainText("2");

    const latencyRow = personaRow(page, "Latency Arbiter");
    await expect(latencyRow).toContainText("shadow");
    await expect(latencyRow).toContainText("degraded");
    await expect(latencyRow).toContainText("48");
    await expect(latencyRow).toContainText("1");

    const hedgeRow = personaRow(page, "Hedge Steward");
    await expect(hedgeRow).toContainText("paper");
    await expect(hedgeRow).toContainText("healthy");
    await expect(hedgeRow).toContainText("91");
    await expect(hedgeRow).toContainText("7");
    await expect(hedgeRow).toContainText("0");

    expect(await bodyText(page)).not.toContain(SECRET_SENTINEL);
  });

  test("critical and degraded personas drill down to Sentinel finding or evidence", async ({
    page,
  }) => {
    test.fixme(
      true,
      "Product gap: PersonaHealthMatrix currently renders text-only rows and exposes no critical/degraded drill-down control.",
    );

    const calls = await installB03Routes(page);

    await gotoExecutionPersonas(page);

    await assertPersonaDrillsDown(page, "Risk Sentinel", "finding-persona-risk");

    await expect
      .poll(
        () =>
          calls.has(SENTINEL_FINDINGS_PATH) ||
          calls.has(`${SENTINEL_FINDINGS_PATH}/finding-persona-risk`) ||
          calls.has(REDACTED_EVIDENCE_PATH),
        {
          message: "critical drill-down must call Sentinel finding or evidence route",
          timeout: 10_000,
        },
      )
      .toBe(true);

    await gotoExecutionPersonas(page);

    await assertPersonaDrillsDown(
      page,
      "Latency Arbiter",
      "finding-persona-latency",
    );
    await expect
      .poll(
        () =>
          calls.has(SENTINEL_FINDINGS_PATH) ||
          calls.has(`${SENTINEL_FINDINGS_PATH}/finding-persona-latency`) ||
          calls.has(REDACTED_EVIDENCE_PATH),
        {
          message: "degraded drill-down must call Sentinel finding or evidence route",
          timeout: 10_000,
        },
      )
      .toBe(true);

    expect(await bodyText(page)).not.toContain(SECRET_SENTINEL);
  });

  test("redacted evidence references are metadata-only and secret-free", async ({
    page,
  }) => {
    expect(JSON.stringify(rawSecretEvidence)).toContain(SECRET_SENTINEL);
    await installB03Routes(page);

    await gotoExecutionPersonas(page);

    const evidence = await page.evaluate(async (url) => {
      const response = await fetch(url, {
        headers: { Accept: "application/json" },
      });
      return response.json();
    }, bffUrl(REDACTED_EVIDENCE_PATH));

    assertRedactedEvidenceRef(evidence, "RedactedEvidenceRef");
    expect(await bodyText(page)).not.toContain(SECRET_SENTINEL);
  });
});
