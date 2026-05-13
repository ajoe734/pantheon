/**
 * FE-INT-GATE-B01 / F01 - startup session contract.
 *
 * Coverage:
 *   1. /bff/me returns the frontend MeResponse shape.
 *   2. Strict live mode does not show a serving-mock / seed fallback banner.
 *   3. Browser-native EventSource reaches the BFF SSE stream and opens.
 *   4. A mocked /bff/me 401 is treated as an auth error, not as permission to
 *      fall back to mock current-user data.
 *
 * Env:
 *   FRONTEND_BASE_URL or PLAYWRIGHT_BASE_URL
 *     default: http://127.0.0.1:5173
 *   BFF_BASE_URL or VITE_BFF_BASE_URL
 *     default: https://pantheon-staging-bff.34.81.225.122.sslip.io
 *   BFF_AUTH_TOKEN
 *     optional; when omitted the dev stub token is used.
 *   VITE_BFF_FALLBACK or BFF_FALLBACK
 *     default: strict
 */

import { expect, test } from "@playwright/test";

const DEFAULT_FRONTEND_BASE_URL = "http://127.0.0.1:5173";
const DEFAULT_BFF_BASE_URL =
  "https://pantheon-staging-bff.34.81.225.122.sslip.io";
const DEFAULT_DEV_AUTH_TOKEN = "op-fe-gate:operator,reviewer:mfa";

const SERVING_MOCK_BANNER =
  /serving[-\s]?mock|mock data|seed fallback|資料來源：seed/i;

type JsonRecord = Record<string, unknown>;

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

function strictFallbackMode(): string {
  return process.env.VITE_BFF_FALLBACK || process.env.BFF_FALLBACK || "strict";
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

function booleanAt(value: unknown, label: string): boolean {
  expect(typeof value, `${label} must be a boolean`).toBe("boolean");
  return Boolean(value);
}

function stringArrayAt(value: unknown, label: string): string[] {
  expect(Array.isArray(value), `${label} must be an array`).toBe(true);
  const items = value as unknown[];
  expect(items.length, `${label} must be non-empty`).toBeGreaterThan(0);
  for (const item of items) {
    expect(typeof item, `${label} items must be strings`).toBe("string");
    expect(String(item).trim(), `${label} items must not be blank`).not.toBe("");
  }
  return items.map(String);
}

async function bodyText(page: import("@playwright/test").Page): Promise<string> {
  return page.locator("body").innerText({ timeout: 10_000 });
}

test.describe("F01 startup session", () => {
  test("asserts MeResponse tenant/env/user/capabilities shape", async ({
    request,
  }) => {
    const tenantId = process.env.BFF_TENANT_ID || process.env.PANTHEON_TENANT_ID;
    const headers: Record<string, string> = {
      Accept: "application/json",
      Authorization: authHeader(),
      "X-Locale": "zh-TW",
    };
    if (tenantId) {
      headers["X-Tenant-Id"] = tenantId;
    }

    const response = await request.get(bffUrl("/bff/me"), {
      headers,
      timeout: 10_000,
    });

    expect(response.status(), await response.text()).toBe(200);
    const payload = recordAt(await response.json(), "MeResponse");
    const data = recordAt(payload.data, "MeResponse.data");
    const meta = recordAt(payload.meta, "MeResponse.meta");

    expect(meta.route).toBe("GET /bff/me");
    expect(meta.contract).toBe("BFF-LUV-GAP-009");

    const tenant = recordAt(data.tenant, "MeResponse.data.tenant");
    stringAt(tenant.id, "tenant.id");
    stringAt(tenant.default_id, "tenant.default_id");
    stringArrayAt(tenant.allowed_ids, "tenant.allowed_ids");
    expect(["tenant", "global"]).toContain(tenant.scope);
    expect(data.tenant_id).toBe(tenant.id);

    const environment = recordAt(
      data.environment,
      "MeResponse.data.environment",
    );
    stringAt(environment.name, "environment.name");
    stringAt(environment.deployment_stage, "environment.deployment_stage");
    stringAt(environment.auth_mode, "environment.auth_mode");
    stringAt(environment.timezone, "environment.timezone");
    booleanAt(environment.strict_auth, "environment.strict_auth");

    const user = recordAt(data.user, "MeResponse.data.user");
    stringAt(user.id, "user.id");
    stringAt(user.operator_id, "user.operator_id");
    stringAt(user.display_name, "user.display_name");
    const roles = stringArrayAt(user.roles, "user.roles");
    const userCapabilities = stringArrayAt(
      user.capabilities,
      "user.capabilities",
    );
    booleanAt(user.mfa_verified, "user.mfa_verified");

    expect(recordAt(data.currentUser, "data.currentUser")).toEqual(user);
    expect(recordAt(data.current_user, "data.current_user")).toEqual(user);
    expect(stringArrayAt(data.roles, "data.roles")).toEqual(roles);
    expect(stringArrayAt(data.capabilities, "data.capabilities")).toEqual(
      userCapabilities,
    );
    expect(userCapabilities).toContain("runtime.read");

    const session = recordAt(data.session, "MeResponse.data.session");
    stringAt(session.id, "session.id");
    expect(["cookie", "bearer", "stub"]).toContain(session.session_kind);
    stringAt(session.auth_mode, "session.auth_mode");
    stringAt(session.checked_at, "session.checked_at");
    booleanAt(session.authenticated, "session.authenticated");
    booleanAt(session.fresh, "session.fresh");
    booleanAt(session.mfa_verified, "session.mfa_verified");

    const featureFlags = recordAt(
      data.feature_flags,
      "MeResponse.data.feature_flags",
    );
    expect(featureFlags.sessionAuthMe).toBe(true);
  });

  test("strict startup does not show a serving-mock banner", async ({
    page,
  }) => {
    expect(strictFallbackMode()).toBe("strict");

    await page.goto(frontendUrl("/"), { waitUntil: "domcontentloaded" });

    await expect
      .poll(async () => await bodyText(page), {
        message: "frontend body should render without serving mock banner",
        timeout: 15_000,
      })
      .not.toMatch(SERVING_MOCK_BANNER);
  });

  test("opens the browser-native SSE EventSource stream", async ({ page }) => {
    const streamUrl = bffUrl("/bff/events/stream?channel=system");

    const opened = await page.evaluate(
      ({ url }) =>
        new Promise<{
          readyState: number;
          openState: number;
          firstMessageType?: string;
        }>(
          (resolve, reject) => {
            const eventSource = new EventSource(url);
            const timeout = window.setTimeout(() => {
              const state = eventSource.readyState;
              eventSource.close();
              reject(new Error(`EventSource did not open; readyState=${state}`));
            }, 10_000);

            eventSource.onopen = () => {
              window.clearTimeout(timeout);
              const state = eventSource.readyState;
              eventSource.close();
              resolve({ readyState: state, openState: EventSource.OPEN });
            };

            eventSource.onmessage = (event) => {
              window.clearTimeout(timeout);
              const state = eventSource.readyState;
              eventSource.close();
              try {
                const payload = JSON.parse(event.data);
                resolve({
                  readyState: state,
                  openState: EventSource.OPEN,
                  firstMessageType:
                    typeof payload.type === "string" ? payload.type : undefined,
                });
              } catch {
                resolve({ readyState: state, openState: EventSource.OPEN });
              }
            };

            eventSource.onerror = () => {
              if (eventSource.readyState === EventSource.CLOSED) {
                window.clearTimeout(timeout);
                reject(new Error("EventSource closed before opening"));
              }
            };
          },
        ),
      { url: streamUrl },
    );

    expect(opened.readyState).toBe(opened.openState);
    if (opened.firstMessageType) {
      expect(opened.firstMessageType).toMatch(/^system\./);
    }
  });

  test("does not fall back to mock current-user data when /bff/me returns 401", async ({
    page,
  }) => {
    expect(strictFallbackMode()).toBe("strict");

    let interceptedMeRequests = 0;
    await page.route("**/bff/me**", async (route) => {
      interceptedMeRequests += 1;
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({
          detail: {
            error: {
              code: "AUTH_REQUIRED",
              message: "FE-INT-GATE-B01 injected /bff/me 401",
              details: {
                precondition_failed: "auth_session",
              },
            },
          },
        }),
      });
    });

    await page.goto(frontendUrl("/"), { waitUntil: "domcontentloaded" });
    await expect
      .poll(() => interceptedMeRequests, {
        message: "startup must request /bff/me",
        timeout: 15_000,
      })
      .toBeGreaterThan(0);

    const text = await bodyText(page);
    expect(text).not.toMatch(SERVING_MOCK_BANNER);
    expect(text).not.toMatch(/op-fe-gate|portfolio_manager|mock operator/i);
  });
});
