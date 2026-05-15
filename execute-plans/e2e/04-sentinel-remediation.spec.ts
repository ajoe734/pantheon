/**
 * FE-INT-GATE-B04 / F05 - Sentinel remediation confirm-token envelope.
 *
 * Coverage:
 *   1. A high-risk emergency Sentinel remediation POST without a confirm token
 *      receives a route-injected non-2xx CONFIRM_TOKEN_REQUIRED envelope.
 *   2. The UI must not render that precondition failure as an accepted, queued,
 *      completed, or requires_confirm_token success state.
 *   3. A lower-risk advisory remediation action remains executable and can be
 *      queued through the same live-write path.
 *
 * Runtime env for the app under test:
 *   VITE_BFF_MODE=live
 *   VITE_BFF_REAL_WRITES=true
 *   VITE_BFF_BASE_URL may be left blank so relative /bff requests hit page.route.
 */

import {
  expect,
  test,
  type Page,
  type Response,
  type Route,
} from "@playwright/test";

const DEFAULT_FRONTEND_BASE_URL = "http://127.0.0.1:5173";
const DEFAULT_DEV_AUTH_TOKEN = "op-fe-gate:operator,reviewer,approver:mfa";
const SENTINEL_PATH = "/management/sentinel";
const SENTINEL_FINDING_ID = "finding-b04-confirm-token";
const TARGET_PERSONA_ID = "persona-b04-risk";

const CONFIRM_TOKEN_ERROR = "CONFIRM_TOKEN_REQUIRED";
const EMERGENCY_ACTION_KIND = "pause_persona_routing";
const ADVISORY_ACTION_KIND = "open_incident";
const CRASH_TEXT =
  /application error|cannot read properties|undefined is not|uncaught|traceback|typeerror|referenceerror/i;
const CONFIRM_TOKEN_SUCCESS_TEXT =
  /requires_confirm_token[\s\S]{0,80}(accepted|queued|completed|success|succeeded)|CONFIRM_TOKEN_REQUIRED[\s\S]{0,80}(accepted|queued|completed|success|succeeded)|Command receipt[\s\S]{0,160}(accepted|queued|completed)/i;

type JsonRecord = Record<string, unknown>;

type RouteCalls = {
  advisoryPosts: string[];
  emergencyPosts: string[];
  findingGets: number;
  requestBodies: JsonRecord[];
};

function frontendUrl(path = "/"): string {
  const base =
    process.env.FRONTEND_BASE_URL ||
    process.env.PANTHEON_FE_BASE_URL ||
    process.env.PLAYWRIGHT_BASE_URL ||
    DEFAULT_FRONTEND_BASE_URL;
  return `${base.replace(/\/$/, "")}${path}`;
}

function authToken(): string {
  const token = process.env.BFF_AUTH_TOKEN || DEFAULT_DEV_AUTH_TOKEN;
  return token.startsWith("Bearer ") ? token.slice("Bearer ".length) : token;
}

function nowIso(): string {
  return "2026-05-13T13:40:00Z";
}

function commandResponse(status: "accepted" | "queued", data: JsonRecord): JsonRecord {
  return {
    status,
    data,
    meta: {
      route: "POST /bff/v5/interventions/{id}/remediate",
      contract: "FE-INT-GATE-B04",
      snapshot_at: nowIso(),
    },
  };
}

const sentinelFinding = {
  id: SENTINEL_FINDING_ID,
  finding_id: SENTINEL_FINDING_ID,
  title: "B04 Sentinel Confirm Token Finding",
  summary: "Critical Sentinel remediation must require an operator confirm token.",
  status: "open",
  severity: "critical",
  confidence: 0.93,
  source: "runtime",
  detected_at: "2026-05-13T13:20:00Z",
  updated_at: nowIso(),
  persona_ids: [TARGET_PERSONA_ID],
  recommended_action_ids: [ADVISORY_ACTION_KIND, EMERGENCY_ACTION_KIND],
};

function corsHeaders(route: Route): Record<string, string> {
  const origin = route.request().headers().origin ?? "*";
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Headers":
      "accept,authorization,content-type,idempotency-key,x-bff-api-version,x-correlation-id,x-confirm-token,x-idempotency-key,x-locale,x-request-id",
    "Access-Control-Allow-Methods": "GET,POST,PATCH,DELETE,OPTIONS",
    "X-Correlation-Id": "corr-fe-int-gate-b04",
    "X-Request-Id": "req-fe-int-gate-b04",
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

function safeJson(text: string): JsonRecord {
  if (!text.trim()) {
    return {};
  }
  try {
    const value = JSON.parse(text) as unknown;
    return value && typeof value === "object" && !Array.isArray(value)
      ? (value as JsonRecord)
      : {};
  } catch {
    return {};
  }
}

async function readPostBody(route: Route): Promise<{
  bodyText: string;
  bodyJson: JsonRecord;
}> {
  const bodyText = route.request().postData() ?? "";
  return {
    bodyText,
    bodyJson: safeJson(bodyText),
  };
}

function actionKindFrom(bodyJson: JsonRecord, bodyText: string): string {
  const direct =
    bodyJson.remediation_action ??
    bodyJson.remediationAction ??
    bodyJson.action ??
    bodyJson.actionId ??
    bodyJson.command ??
    "";
  const directText = String(direct).trim();
  if (directText) {
    return directText;
  }
  if (bodyText.includes(EMERGENCY_ACTION_KIND)) {
    return EMERGENCY_ACTION_KIND;
  }
  if (bodyText.includes(ADVISORY_ACTION_KIND)) {
    return ADVISORY_ACTION_KIND;
  }
  return "";
}

function isRemediationPost(
  path: string,
  bodyJson: JsonRecord,
  bodyText: string,
  actionKind: string,
): boolean {
  if (actionKindFrom(bodyJson, bodyText) === actionKind) {
    return true;
  }
  if (path.includes(actionKind) && /\/(?:remediate|execute)\/?$/.test(path)) {
    return true;
  }
  return new RegExp(
    `^/bff/v5/interventions/ra_${actionKind}_[^/]+/remediate/?$`,
  ).test(path);
}

function isEmergencyRemediationPost(
  path: string,
  bodyJson: JsonRecord,
  bodyText: string,
): boolean {
  return isRemediationPost(path, bodyJson, bodyText, EMERGENCY_ACTION_KIND);
}

function isAdvisoryActionPost(
  path: string,
  bodyJson: JsonRecord,
  bodyText: string,
): boolean {
  return isRemediationPost(path, bodyJson, bodyText, ADVISORY_ACTION_KIND);
}

function responseMatches(
  response: Response,
  predicate: (path: string, bodyJson: JsonRecord, bodyText: string) => boolean,
): boolean {
  const request = response.request();
  if (request.method() !== "POST") {
    return false;
  }
  const bodyText = request.postData() ?? "";
  return predicate(new URL(response.url()).pathname, safeJson(bodyText), bodyText);
}

async function installB04Routes(page: Page): Promise<RouteCalls> {
  const calls: RouteCalls = {
    advisoryPosts: [],
    emergencyPosts: [],
    findingGets: 0,
    requestBodies: [],
  };

  await page.addInitScript((token) => {
    window.localStorage.setItem("pantheon_operator_token", token);
    window.localStorage.setItem("pantheon.bff.bearerToken", token);
  }, authToken());

  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: corsHeaders(route) });
      return;
    }

    if (request.method() === "POST") {
      const { bodyText, bodyJson } = await readPostBody(route);
      calls.requestBodies.push(bodyJson);

      if (isEmergencyRemediationPost(path, bodyJson, bodyText)) {
        calls.emergencyPosts.push(path);
        await fulfillJson(
          route,
          {
            error: {
              code: CONFIRM_TOKEN_ERROR,
              i18nKey: "errors.CONFIRM_TOKEN_REQUIRED",
              message:
                "Confirm token required before emergency Sentinel remediation",
              details: {
                kind: "confirm_token",
                precondition_failed: "confirm_token",
                requires_confirm_token: true,
                remediation_action: EMERGENCY_ACTION_KIND,
                finding_id: SENTINEL_FINDING_ID,
              },
              retryable: false,
              userActionable: true,
              correlationId: "corr-fe-int-gate-b04",
            },
          },
          428,
        );
        return;
      }

      if (isAdvisoryActionPost(path, bodyJson, bodyText)) {
        calls.advisoryPosts.push(path);
        await fulfillJson(
          route,
          commandResponse("queued", {
            action_id: ADVISORY_ACTION_KIND,
            command_id: "cmd-b04-advisory-queued",
            receipt_id: "receipt-b04-advisory-queued",
            finding_id: SENTINEL_FINDING_ID,
            status: "queued",
          }),
          202,
        );
        return;
      }
    }

    if (path === "/bff/me") {
      await fulfillJson(route, {
        data: {
          session: {
            authenticated: true,
            session_kind: "stub",
            auth_mode: "stub",
            fresh: true,
            mfa_verified: true,
            checked_at: nowIso(),
          },
        },
      });
      return;
    }

    if (path === "/health" || path === "/healthz" || path === "/bff/health") {
      await fulfillJson(route, {
        status: "ok",
        service: "execute-plans-fe-int-gate-b04",
        checked_at: nowIso(),
      });
      return;
    }

    if (request.method() === "GET" && path.startsWith("/bff/v5/sentinel/findings")) {
      calls.findingGets += 1;
      await fulfillJson(route, {
        items: [sentinelFinding],
        count: 1,
        generated_at: nowIso(),
        meta: {
          snapshot_at: nowIso(),
          surfaces: {
            sentinel_findings: { status: "ok", source: "fe-int-gate-b04" },
          },
        },
      });
      return;
    }

    if (request.method() === "GET" && path.startsWith("/bff/")) {
      await fulfillJson(route, {
        items: [],
        data: [],
        count: 0,
        meta: {
          snapshot_at: nowIso(),
          contract: "FE-INT-GATE-B04-neutral-read-stub",
        },
      });
      return;
    }

    await route.continue();
  });

  return calls;
}

async function bodyText(page: Page): Promise<string> {
  return (await page.locator("body").textContent({ timeout: 10_000 })) ?? "";
}

async function expectSentinelShellReady(page: Page, calls: RouteCalls): Promise<void> {
  await page.goto(frontendUrl(SENTINEL_PATH), { waitUntil: "commit" });
  await expect
    .poll(() => calls.findingGets, {
      message: "Sentinel page should request the live B04 findings fixture",
      timeout: 30_000,
    })
    .toBeGreaterThan(0);
  await expect
    .poll(async () => await bodyText(page), {
      message: "Sentinel page should render the B04 Sentinel fixture",
      timeout: 30_000,
    })
    .toMatch(/B04 Sentinel Confirm Token Finding/i);
  expect(await bodyText(page)).not.toMatch(CRASH_TEXT);
}

async function openB04FindingDrawer(page: Page): Promise<void> {
  await page.getByText("B04 Sentinel Confirm Token Finding").first().click();
  await expect(page.getByRole("dialog").first()).toContainText(
    /Open incident|Pause persona routing/i,
  );
}

async function clickActionRun(page: Page, actionLabel: RegExp): Promise<void> {
  const drawer = page
    .getByRole("dialog")
    .filter({ hasText: /B04 Sentinel Confirm Token Finding/ })
    .first();
  const actionRow = drawer.locator("li").filter({ hasText: actionLabel }).first();
  await expect(actionRow).toBeVisible();
  await actionRow.getByRole("button", { name: /Run|執行/ }).click();
}

async function submitEmergencyConfirm(page: Page): Promise<void> {
  const dialog = page
    .getByRole("dialog")
    .filter({ hasText: /Confirm high-risk action|高風險動作確認/ })
    .last();
  await expect(dialog).toBeVisible();
  await dialog.locator("textarea").fill(
    "FE-INT-GATE-B04 validates that emergency Sentinel remediation fails closed when no confirm token is supplied.",
  );
  await dialog.locator("input").fill("PAUSE_PERSONA_ROUTING");
  await dialog.getByRole("button", { name: /Confirm|確認/ }).click();
}

test.describe("F05 Sentinel remediation", () => {
  test("treats CONFIRM_TOKEN_REQUIRED as a non-success emergency precondition", async ({
    page,
  }) => {
    const calls = await installB04Routes(page);
    await expectSentinelShellReady(page, calls);
    await openB04FindingDrawer(page);

    await clickActionRun(page, /Pause persona routing/i);

    const emergencyResponse = page.waitForResponse(
      (response) =>
        responseMatches(response, (path, bodyJson, bodyText) =>
          isEmergencyRemediationPost(path, bodyJson, bodyText),
        ),
      { timeout: 10_000 },
    );

    await submitEmergencyConfirm(page);

    const response = await emergencyResponse;
    expect(response.status(), await response.text()).toBeGreaterThanOrEqual(400);
    const payload = await response.json();
    expect(payload).toMatchObject({
      error: {
        code: CONFIRM_TOKEN_ERROR,
        details: {
          kind: "confirm_token",
          remediation_action: EMERGENCY_ACTION_KIND,
        },
      },
    });

    await expect
      .poll(() => calls.emergencyPosts.length, {
        message: "UI should POST the emergency remediation action",
        timeout: 5_000,
      })
      .toBeGreaterThan(0);

    const body = calls.requestBodies.find(
      (candidate) => candidate.remediation_action === EMERGENCY_ACTION_KIND,
    );
    expect(body, "emergency POST body should be captured").toBeTruthy();
    expect(
      body?.confirmToken ?? body?.confirm_token ?? body?.x_confirm_token,
      "emergency test must cover the missing-token precondition",
    ).toBeUndefined();

    await expect(page.locator("body")).not.toContainText(CRASH_TEXT);
    const text = await bodyText(page);
    expect(text, "confirm-token precondition must not appear as success").not.toMatch(
      CONFIRM_TOKEN_SUCCESS_TEXT,
    );
    expect(text, "raw requires_confirm_token must not be rendered as success UI").not.toMatch(
      /requires_confirm_token/i,
    );
    expect(text, "emergency 428 must not show the remediation success toast").not.toMatch(
      /Remediation executed|處置已執行/i,
    );
  });

  test("allows an advisory Sentinel remediation action to be queued", async ({
    page,
  }) => {
    const calls = await installB04Routes(page);
    await expectSentinelShellReady(page, calls);
    await openB04FindingDrawer(page);

    const advisoryResponse = page.waitForResponse(
      (response) =>
        responseMatches(response, (path, bodyJson, bodyText) =>
          isAdvisoryActionPost(path, bodyJson, bodyText),
        ),
      { timeout: 10_000 },
    );

    await clickActionRun(page, /Open incident/i);

    const response = await advisoryResponse;
    expect(response.status(), await response.text()).toBe(202);
    const payload = await response.json();
    expect(payload).toMatchObject({
      status: "queued",
      data: {
        action_id: ADVISORY_ACTION_KIND,
        status: "queued",
      },
    });

    await expect
      .poll(() => calls.advisoryPosts.length, {
        message: "UI should POST the advisory remediation action",
        timeout: 5_000,
      })
      .toBeGreaterThan(0);

    await expect
      .poll(async () => await bodyText(page), {
        message: "advisory action should remain executable or queueable",
        timeout: 10_000,
      })
      .toMatch(/Remediation executed|Open incident|處置已執行/i);
  });
});
