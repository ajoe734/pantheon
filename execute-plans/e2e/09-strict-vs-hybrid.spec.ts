/**
 * FE-INT-GATE-B08 / F15 - strict vs hybrid BFF fallback behavior.
 *
 * Coverage:
 *   1. Hybrid live mode treats injected 5xx as transport failure, renders the
 *      live-BFF fallback banner, and serves visible seed data.
 *   2. Strict live mode treats injected 5xx as a typed error and never renders
 *      seed data.
 *   3. 4xx BffError envelopes are real backend replies in both modes and must
 *      not activate seed fallback.
 *
 * Env:
 *   FRONTEND_BASE_URL or PLAYWRIGHT_BASE_URL
 *     default: http://127.0.0.1:5173
 *   VITE_BFF_MODE=live
 *     required by the frontend build under test so route injection reaches BFF
 *     calls instead of in-process mock adapters.
 *   VITE_BFF_FALLBACK, BFF_FALLBACK, or PANTHEON_E2E_STRICT=1
 *     strict branch selector. Default is hybrid/auto for this spec.
 */

import { test, expect, type Page, type Route } from "@playwright/test";

const STRATEGIES_ROUTE = "/bff/strategies";
const DEFAULT_FRONTEND_BASE_URL = "http://127.0.0.1:5173";
const SEED_STRATEGY = "Momentum Quant Alpha";

const FALLBACK_ACTIVE_TEXT =
  /hybrid fallback active|serving mock data|資料來源：seed/i;
const STRICT_ERROR_TEXT = /strict typed error|seed fallback blocked/i;
const STRICT =
  process.env.VITE_BFF_FALLBACK === "strict" ||
  process.env.BFF_FALLBACK === "strict" ||
  process.env.PANTHEON_E2E_STRICT === "1";

const SNAPSHOT_AT = "2026-05-13T13:45:00Z";

function frontendUrl(path = "/"): string {
  const base =
    process.env.FRONTEND_BASE_URL ||
    process.env.PLAYWRIGHT_BASE_URL ||
    process.env.PANTHEON_FE_BASE_URL ||
    DEFAULT_FRONTEND_BASE_URL;
  return `${base.replace(/\/$/, "")}${path}`;
}

function exactPath(path: string): (url: URL) => boolean {
  return (url) => url.pathname === path;
}

function corsHeaders(route: Route): Record<string, string> {
  const origin = route.request().headers().origin ?? "*";
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Headers":
      "Accept, Accept-Language, Authorization, Content-Language, Content-Type, Idempotency-Key, If-Match, X-BFF-Api-Version, X-Correlation-Id, X-Idempotency-Key, X-Locale, X-Request-Id, X-Tenant-Id",
    "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
    "Access-Control-Expose-Headers":
      "X-BFF-Api-Version, X-Correlation-Id, X-Request-Id",
    "X-BFF-Api-Version": "2026-05-07",
    "X-Request-Id": "f15-route-injection",
    "X-Correlation-Id": "f15-route-injection",
  };
}

function bffErrorEnvelope(
  code: "BACKEND_UNAVAILABLE" | "STATE_CONFLICT",
  message: string,
) {
  return {
    error: {
      code,
      i18nKey: `errors.${code}`,
      message,
      retryable: code === "BACKEND_UNAVAILABLE",
      userActionable: code === "STATE_CONFLICT",
      correlationId: "f15-route-injection",
    },
  };
}

function liveListEnvelope(surface: string) {
  return {
    items: [],
    cursor: {},
    pageSize: 0,
    estimatedTotal: 0,
    totalCountExact: true,
    meta: {
      snapshot_at: SNAPSHOT_AT,
      surfaces: {
        [surface]: { status: "ok", source: "live" },
      },
    },
  };
}

function meResponse() {
  return {
    user: {
      id: "operator-f15",
      displayName: "F15 Operator",
      email: "operator-f15@pantheon.local",
    },
    tenant: {
      id: "tenant-f15",
      name: "Pantheon F15",
      tz: "Asia/Taipei",
      locale: "zh-TW",
      baseCurrency: "USD",
    },
    roles: ["portfolio_manager", "ops"],
    capabilities: ["runtime.read", "strategy.create", "deployment.request"],
    env: "staging",
    featureFlags: { v5LoopOs: true, sentinel: true },
    serverTime: SNAPSHOT_AT,
    sessionExpiresAt: "2026-05-13T21:45:00Z",
    permissionsVersion: "f15",
    counters: {
      pendingInterventionsCount: 0,
      unreadAuditCount: 0,
      openFindingsCount: 0,
    },
  };
}

async function injectBffResponse(
  page: Page,
  path: string,
  status: number,
  body: unknown,
) {
  await page.route(exactPath(path), async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({
        status: 204,
        headers: corsHeaders(route),
      });
      return;
    }
    await page.waitForTimeout(250);
    await route.fulfill({
      status,
      headers: {
        ...corsHeaders(route),
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });
  });
}

async function installStableShellRoutes(page: Page) {
  const stableRoutes: Array<[string, number, unknown]> = [
    ["/health", 200, { status: "ok", service: "pantheon-f15-route-harness" }],
    ["/bff/me", 200, meResponse()],
    ["/bff/approvals", 200, liveListEnvelope("approvals")],
    ["/bff/alerts", 200, liveListEnvelope("alerts")],
    ["/bff/jobs", 200, liveListEnvelope("jobs")],
    ["/bff/search", 200, []],
  ];

  for (const [path, status, body] of stableRoutes) {
    await page.route(exactPath(path), async (route) => {
      if (route.request().method() === "OPTIONS") {
        await route.fulfill({ status: 204, headers: corsHeaders(route) });
        return;
      }
      await route.fulfill({
        status,
        headers: {
          ...corsHeaders(route),
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
      });
    });
  }
}

async function isolateOtherAncillaryBffRoutes(page: Page) {
  const typedNoopError = bffErrorEnvelope(
    "STATE_CONFLICT",
    "F15 ancillary route isolated from fallback assertion",
  );
  const routes: Array<[string, number, string, unknown]> = [
    ["/bff/incidents", 409, "application/json", typedNoopError],
    ["/bff/audit", 409, "application/json", typedNoopError],
  ];

  for (const [path, status, contentType, body] of routes) {
    await page.route(exactPath(path), async (route) => {
      if (route.request().method() === "OPTIONS") {
        await route.fulfill({ status: 204, headers: corsHeaders(route) });
        return;
      }
      await route.fulfill({
        status,
        headers: {
          ...corsHeaders(route),
          "Content-Type": contentType,
        },
        body: typeof body === "string" ? body : JSON.stringify(body),
      });
    });
  }
}

async function installQuietEventSource(page: Page) {
  await page.addInitScript(() => {
    class PantheonF15EventSource extends EventTarget {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSED = 2;

      readonly url: string;
      readonly withCredentials: boolean;
      readyState = PantheonF15EventSource.CONNECTING;
      onopen: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;

      constructor(url: string | URL, init?: EventSourceInit) {
        super();
        this.url = String(url);
        this.withCredentials = init?.withCredentials ?? false;
      }

      close() {
        this.readyState = PantheonF15EventSource.CLOSED;
      }
    }

    (window as unknown as { EventSource: typeof EventSource }).EventSource =
      PantheonF15EventSource as unknown as typeof EventSource;
  });
}

async function gotoStrategiesAndWaitForInjectedStatus(
  page: Page,
  expectedStatus: number,
) {
  const injectedResponse = page.waitForResponse(
    (response) =>
      response.url().includes(STRATEGIES_ROUTE) &&
      response.request().method() === "GET" &&
      response.status() === expectedStatus,
  );
  await page.goto(frontendUrl("/management/strategies"), {
    waitUntil: "commit",
  });
  await injectedResponse;
  await page.waitForTimeout(500);
}

async function expectNoSeedFallback(page: Page) {
  await expect(page.getByText(FALLBACK_ACTIVE_TEXT)).toHaveCount(0);
  await expect(page.getByText(SEED_STRATEGY)).toHaveCount(0);
}

test.describe("F15 strict vs hybrid fallback", () => {
  test("hybrid 5xx injection falls back to mock with a visible live-BFF banner", async ({
    page,
  }) => {
    test.skip(STRICT, "Run this branch with live + auto fallback.");

    await installQuietEventSource(page);
    await installStableShellRoutes(page);
    await isolateOtherAncillaryBffRoutes(page);
    await injectBffResponse(
      page,
      STRATEGIES_ROUTE,
      503,
      bffErrorEnvelope("BACKEND_UNAVAILABLE", "Injected F15 5xx"),
    );

    await gotoStrategiesAndWaitForInjectedStatus(page, 503);

    await expect(
      page.getByRole("status").filter({ hasText: FALLBACK_ACTIVE_TEXT }),
    ).toBeVisible();
    await expect(page.getByText(SEED_STRATEGY)).toBeVisible();
  });

  test("strict 5xx injection fails closed without showing mock data", async ({
    page,
  }) => {
    test.skip(!STRICT, "Run this branch with live + strict fallback.");

    await installQuietEventSource(page);
    await installStableShellRoutes(page);
    await isolateOtherAncillaryBffRoutes(page);
    await injectBffResponse(
      page,
      STRATEGIES_ROUTE,
      503,
      bffErrorEnvelope("BACKEND_UNAVAILABLE", "Injected F15 5xx"),
    );

    await gotoStrategiesAndWaitForInjectedStatus(page, 503);

    await expect(
      page.getByRole("status").filter({ hasText: STRICT_ERROR_TEXT }),
    ).toBeVisible();
    await expectNoSeedFallback(page);
  });

  test("4xx BffError envelope never falls back to mock", async ({ page }) => {
    await installQuietEventSource(page);
    await installStableShellRoutes(page);
    await isolateOtherAncillaryBffRoutes(page);
    await injectBffResponse(
      page,
      STRATEGIES_ROUTE,
      409,
      bffErrorEnvelope("STATE_CONFLICT", "Injected F15 governed 4xx"),
    );

    await gotoStrategiesAndWaitForInjectedStatus(page, 409);

    await expect(page.getByText(STRICT_ERROR_TEXT)).toHaveCount(0);
    await expectNoSeedFallback(page);
  });
});
