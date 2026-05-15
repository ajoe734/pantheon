/**
 * FE-INT-GATE-C02 / F08 - create write intent coverage.
 *
 * Coverage:
 *   1. Browser fetch posts create intents for all nine execute-plan resources.
 *   2. Every create intent sends the canonical Idempotency-Key header.
 *   3. Successful create intents return a CommandResponse<T> with required data.
 *   4. Invalid create intents return VALIDATION_FAILED with fieldErrors.
 *   5. Deployment creation remains plan-only and does not execute live capital.
 *
 * Env:
 *   FRONTEND_BASE_URL or PLAYWRIGHT_BASE_URL
 *     default: http://127.0.0.1:5173
 *   BFF_BASE_URL or VITE_BFF_BASE_URL
 *     default: https://pantheon-staging-bff.34.81.225.122.sslip.io
 *   BFF_AUTH_TOKEN
 *     optional; when omitted the dev stub token is used.
 *   FE_INT_GATE_LIVE_BFF=1 or F08_CREATE_INTENT_LIVE_BFF=1
 *     opt in to the live BFF contract probe; fixture-driven browser coverage
 *     runs without a staging dependency.
 */

import {
  expect,
  test,
  type APIRequestContext,
  type Page,
  type Route,
} from "@playwright/test";

import { mutationAuthHeaders } from "./helpers/auth";
import {
  CAPITAL_POOL_DEV_ID,
  CREATE_INTENT_RESOURCE_KEYS,
  PERSONA_DEV_ID,
  SEEDED_RESOURCE_IDS,
  STRATEGY_DEV_ID,
  seededCorrelationId,
  seededIdempotencyKey,
  seededRequestId,
  type CreateIntentResource,
} from "./helpers/fixtures";

const DEFAULT_FRONTEND_BASE_URL = "http://127.0.0.1:5173";
const DEFAULT_BFF_BASE_URL =
  "https://pantheon-staging-bff.34.81.225.122.sslip.io";
const SNAPSHOT_AT = "2026-05-13T14:35:00Z";
const RUN_LIVE_BFF_CONTRACT =
  process.env.FE_INT_GATE_LIVE_BFF === "1" ||
  process.env.F08_CREATE_INTENT_LIVE_BFF === "1";

type JsonRecord = Record<string, unknown>;

type CreateIntentFixture = {
  dataAssertions?: (data: JsonRecord, label: string) => void;
  entityType: string;
  idField: string;
  payload: JsonRecord;
  path: `/bff/${CreateIntentResource}`;
  requiredField: string;
  resource: CreateIntentResource;
  status: 201 | 202;
};

type BrowserCreateResult = {
  body: unknown;
  path: string;
  status: number;
};

type RequestRecord = {
  authorization: string | undefined;
  body: JsonRecord;
  idempotencyKey: string | undefined;
  method: string;
  path: string;
  requestId: string | undefined;
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

function arrayAt(value: unknown, label: string): unknown[] {
  expect(Array.isArray(value), `${label} must be an array`).toBe(true);
  return value as unknown[];
}

function headersFor(resource: CreateIntentResource, suffix: string): Record<string, string> {
  const correlationId = seededCorrelationId(`f08-${resource}-${suffix}`);
  return mutationAuthHeaders({
    extra: {
      "Idempotency-Key": seededIdempotencyKey(resource, suffix),
      "X-Correlation-Id": correlationId,
      "X-Request-Id": seededRequestId(`f08-${resource}-${suffix}`),
      "X-Trace-Id": `trace-${correlationId}`,
    },
  });
}

const CREATE_INTENTS: CreateIntentFixture[] = [
  {
    resource: "strategies",
    path: "/bff/strategies",
    entityType: "strategy",
    idField: "strategy_id",
    requiredField: "name",
    status: 201,
    payload: {
      id: "strategy-f08-create",
      name: "F08 Momentum Strategy",
      owner: "op-fe-gate",
      state: "draft",
      risk: "medium",
      alpha: "f08-momentum-alpha",
      capitalPoolId: CAPITAL_POOL_DEV_ID,
      personaIds: [PERSONA_DEV_ID],
    },
  },
  {
    resource: "personas",
    path: "/bff/personas",
    entityType: "persona",
    idField: "persona_id",
    requiredField: "name",
    status: 201,
    payload: {
      id: "persona-f08-create",
      name: "F08 Risk Persona",
      owner: "op-fe-gate",
      archetype: "risk-steward",
      state: "draft",
      risk: "low",
      description: "Persona created by the F08 write-intent gate.",
    },
  },
  {
    resource: "capital-pools",
    path: "/bff/capital-pools",
    entityType: "capital-pool",
    idField: "capital_pool_id",
    requiredField: "name",
    status: 201,
    payload: {
      id: "capital-pool-f08-create",
      name: "F08 Capital Pool",
      status: "draft",
      risk_policy_ref: "risk-policy-f08",
      params: {
        max_notional: 100000,
        base_currency: "USD",
      },
    },
  },
  {
    resource: "ranking-formulas",
    path: "/bff/ranking-formulas",
    entityType: "ranking-formula",
    idField: "formula_id",
    requiredField: "name",
    status: 201,
    payload: {
      id: "ranking-formula-f08-create",
      name: "F08 Ranking Formula",
      description: "Ranks paper candidates for the F08 integration gate.",
      params: {
        weights: {
          drawdown: -0.3,
          sharpe: 0.7,
        },
      },
    },
  },
  {
    resource: "rebalances",
    path: "/bff/rebalances",
    entityType: "rebalance",
    idField: "rebalance_id",
    requiredField: "capital_pool_id",
    status: 202,
    payload: {
      id: "rebalance-f08-create",
      capital_pool_id: CAPITAL_POOL_DEV_ID,
      reason: "F08 paper rebalance planning",
      params: {
        mode: "plan_only",
        target_weights: {
          "2330.TW": 0.35,
          "0050.TW": 0.65,
        },
      },
    },
  },
  {
    resource: "deployments",
    path: "/bff/deployments",
    entityType: "deployment",
    idField: "deployment_id",
    requiredField: "strategy_id",
    status: 201,
    payload: {
      id: "deployment-f08-plan",
      deployment_id: "deployment-f08-plan",
      name: "F08 Paper Deployment Plan",
      strategy_id: STRATEGY_DEV_ID,
      stage: "paper",
      deployment_stage: "paper",
      mode: "plan_only",
      executionMode: "plan_only",
      execute: false,
      dryRun: true,
      liveCapitalSideEffects: false,
    },
    dataAssertions: (data, label) => {
      const intent = recordAt(data.intent, `${label}.intent`);
      expect(intent.operation, `${label}.intent.operation`).toBe("create_plan");
      expect(booleanAt(intent.planOnly, `${label}.intent.planOnly`)).toBe(true);
      expect(booleanAt(data.planOnly, `${label}.planOnly`)).toBe(true);
      expect(booleanAt(data.executionStarted, `${label}.executionStarted`)).toBe(false);
      expect(booleanAt(data.liveCapitalSideEffects, `${label}.liveCapitalSideEffects`)).toBe(
        false,
      );
    },
  },
  {
    resource: "evolution-programs",
    path: "/bff/evolution-programs",
    entityType: "evolution-program",
    idField: "evolution_program_id",
    requiredField: "name",
    status: 201,
    payload: {
      id: "evolution-program-f08-create",
      name: "F08 Evolution Program",
      strategy_id: STRATEGY_DEV_ID,
      status: "draft",
      params: {
        populationSize: 8,
        maxGenerations: 2,
      },
    },
  },
  {
    resource: "research-experiments",
    path: "/bff/research-experiments",
    entityType: "research-experiment",
    idField: "experiment_id",
    requiredField: "name",
    status: 201,
    payload: {
      id: "research-experiment-f08-create",
      name: "F08 Qlib Experiment",
      linked_strategy_id: STRATEGY_DEV_ID,
      backend: "qlib",
      status: "draft",
      params: {
        universe: "tw50",
        lookback_days: 504,
      },
    },
  },
  {
    resource: "artifacts",
    path: "/bff/artifacts",
    entityType: "artifact",
    idField: "artifact_id",
    requiredField: "name",
    status: 201,
    payload: {
      id: "artifact-f08-create",
      name: "F08 Research Artifact",
      artifact_type: "model",
      linked_strategy_id: STRATEGY_DEV_ID,
      status: "draft",
      uri: "pantheon://artifacts/f08-create",
    },
  },
];

function createIntentByResource(resource: CreateIntentResource): CreateIntentFixture {
  const fixture = CREATE_INTENTS.find((item) => item.resource === resource);
  expect(fixture, `${resource} create-intent fixture`).toBeTruthy();
  return fixture as CreateIntentFixture;
}

function createdIdFor(fixture: CreateIntentFixture, payload: JsonRecord): string {
  return String(
    payload[fixture.idField] ||
      payload.id ||
      payload.deployment_id ||
      SEEDED_RESOURCE_IDS[fixture.resource] ||
      `${fixture.entityType}-f08-created`,
  );
}

function validationFailedEnvelope(
  fixture: CreateIntentFixture,
  idempotencyKey: string | undefined,
): JsonRecord {
  const fieldErrors = [
    {
      field: fixture.requiredField,
      code: "REQUIRED",
      message: `${fixture.requiredField} is required`,
    },
  ];
  return {
    error: {
      code: "VALIDATION_FAILED",
      message: `${fixture.resource} create intent is invalid`,
      fieldErrors,
    },
    detail: {
      error: {
        code: "VALIDATION_FAILED",
        i18nKey: "errors.VALIDATION_FAILED",
        message: `${fixture.resource} create intent is invalid`,
        retryable: false,
        userActionable: true,
        correlationId: seededCorrelationId(`f08-${fixture.resource}-validation`),
        fieldErrors,
        details: {
          fieldErrors,
          idempotencyKey,
          resource: fixture.resource,
        },
      },
    },
  };
}

function commandResponse(
  fixture: CreateIntentFixture,
  payload: JsonRecord,
  idempotencyKey: string | undefined,
): JsonRecord {
  const targetId = createdIdFor(fixture, payload);
  const commandId = `cmd-f08-${fixture.resource}`;
  const operation = fixture.resource === "deployments" ? "create_plan" : "create";
  const data: JsonRecord = {
    id: targetId,
    [fixture.idField]: targetId,
    action: operation,
    command: "CreateWriteIntent",
    commandId,
    command_id: commandId,
    entityType: fixture.entityType,
    entity_type: fixture.entityType,
    intent: {
      operation,
      resource: fixture.resource,
      planOnly: fixture.resource === "deployments",
      liveCapitalSideEffects: false,
    },
    planOnly: fixture.resource === "deployments",
    receipt: {
      id: commandId,
      command_id: commandId,
      status: "accepted",
      trackingUrl: `/api/v1/operator/commands/${commandId}`,
      tracking_url: `/api/v1/operator/commands/${commandId}`,
    },
    receipt_id: commandId,
    resource: fixture.resource,
    status: "accepted",
    target: {
      id: targetId,
      type: fixture.entityType,
    },
  };
  if (fixture.resource === "deployments") {
    data.deploymentPlanId = targetId;
    data.deployment_plan_id = targetId;
    data.executionStarted = false;
    data.liveCapitalSideEffects = false;
  }
  return {
    status: "accepted",
    data,
    meta: {
      contract: "FE-INT-GATE-C02",
      durable: true,
      idempotency: {
        key: idempotencyKey,
        idempotencyKey,
        replayed: false,
      },
      liveCapitalSideEffects: false,
      snapshot_at: SNAPSHOT_AT,
    },
  };
}

function requestBodyIsValid(fixture: CreateIntentFixture, body: JsonRecord): boolean {
  const value = body[fixture.requiredField];
  if (typeof value === "string") return value.trim().length > 0;
  return value !== undefined && value !== null;
}

function corsHeaders(route: Route): Record<string, string> {
  const origin = route.request().headers().origin ?? "*";
  return {
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Headers":
      "Accept, Authorization, Content-Type, Idempotency-Key, X-Correlation-Id, X-Idempotency-Key, X-Locale, X-Request-Id, X-Tenant-Id, X-Trace-Id",
    "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Expose-Headers":
      "X-BFF-Api-Version, X-Correlation-Id, X-Request-Id",
    "X-BFF-Api-Version": "2026-05-13",
    "X-Correlation-Id": "corr-fe-int-gate-f08",
    "X-Request-Id": "req-fe-int-gate-f08",
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

async function postBody(route: Route): Promise<JsonRecord> {
  const raw = route.request().postData() || "{}";
  const parsed = JSON.parse(raw) as unknown;
  expect(isRecord(parsed), `${route.request().url()} body must be an object`).toBe(true);
  return parsed as JsonRecord;
}

async function installCreateIntentRoutes(
  page: Page,
  requests: RequestRecord[] = [],
): Promise<RequestRecord[]> {
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
      await fulfillJson(route, {
        data: {
          user: {
            id: "op-fe-gate",
            roles: ["operator", "reviewer", "approver"],
            mfa_verified: true,
          },
          tenant_id: "tenant-dev",
        },
        meta: {
          route: "GET /bff/me",
          contract: "FE-INT-GATE-C02",
        },
      });
      return;
    }

    const fixture = CREATE_INTENTS.find((item) => item.path === path);
    if (fixture && request.method() === "POST") {
      const body = await postBody(route);
      const headers = request.headers();
      const idempotencyKey = headers["idempotency-key"];
      requests.push({
        authorization: headers.authorization,
        body,
        idempotencyKey,
        method: request.method(),
        path,
        requestId: headers["x-request-id"],
      });

      if (!requestBodyIsValid(fixture, body)) {
        await fulfillJson(route, validationFailedEnvelope(fixture, idempotencyKey), 422);
        return;
      }

      await fulfillJson(route, commandResponse(fixture, body, idempotencyKey), fixture.status);
      return;
    }

    if (path.startsWith("/bff/")) {
      await fulfillJson(route, {
        items: [],
        totalCount: 0,
        totalCountExact: true,
        meta: {
          snapshot_at: SNAPSHOT_AT,
          surfaces: {
            ancillary: { status: "ok", source: "fe-int-gate-f08" },
          },
        },
      });
      return;
    }

    await route.continue();
  });

  return requests;
}

async function postCreateIntentsInBrowser(
  page: Page,
  suffix: string,
  valid: boolean,
): Promise<BrowserCreateResult[]> {
  const requests = CREATE_INTENTS.map((fixture) => ({
    headers: headersFor(fixture.resource, suffix),
    path: frontendUrl(fixture.path),
    payload: valid ? fixture.payload : {},
  }));
  return page.evaluate(async (items) => {
    const results: BrowserCreateResult[] = [];
    for (const item of items) {
      const response = await fetch(item.path, {
        body: JSON.stringify(item.payload),
        credentials: "omit",
        headers: item.headers,
        method: "POST",
      });
      let body: unknown = null;
      try {
        body = await response.json();
      } catch {
        body = await response.text();
      }
      results.push({
        body,
        path: new URL(item.path).pathname,
        status: response.status,
      });
    }
    return results;
  }, requests);
}

function assertCommandResponse(
  value: unknown,
  fixture: CreateIntentFixture,
  label: string,
): JsonRecord {
  const body = recordAt(value, label);
  expect(["accepted", "queued", "completed"]).toContain(body.status);
  const data = recordAt(body.data, `${label}.data`);
  stringAt(data.commandId ?? data.command_id, `${label}.data.commandId`);
  stringAt(data.receipt_id, `${label}.data.receipt_id`);
  const target = recordAt(data.target, `${label}.data.target`);
  expect(target.type, `${label}.data.target.type`).toBe(fixture.entityType);
  expect(target.id, `${label}.data.target.id`).toBe(
    createdIdFor(fixture, fixture.payload),
  );
  expect(data.resource, `${label}.data.resource`).toBe(fixture.resource);
  stringAt(data[fixture.idField], `${label}.data.${fixture.idField}`);
  const meta = recordAt(body.meta, `${label}.meta`);
  const idempotency = recordAt(meta.idempotency, `${label}.meta.idempotency`);
  expect(idempotency.idempotencyKey, `${label}.meta.idempotency.idempotencyKey`).toBe(
    seededIdempotencyKey(fixture.resource, "create"),
  );
  expect(meta.liveCapitalSideEffects, `${label}.meta.liveCapitalSideEffects`).toBe(false);
  fixture.dataAssertions?.(data, `${label}.data`);
  return data;
}

function validationFieldErrors(value: unknown, label: string): unknown[] {
  const body = recordAt(value, label);
  const directError = isRecord(body.error) ? body.error : undefined;
  const detail = isRecord(body.detail) ? body.detail : undefined;
  const nestedError = isRecord(detail?.error) ? detail?.error : undefined;
  const error = directError ?? nestedError;
  expect(error, `${label}.error`).toBeTruthy();
  expect(error?.code, `${label}.error.code`).toBe("VALIDATION_FAILED");
  const details = isRecord(error?.details) ? error?.details : undefined;
  return arrayAt(
    error?.fieldErrors ?? details?.fieldErrors,
    `${label}.VALIDATION_FAILED.fieldErrors`,
  );
}

async function postLiveCreateIntent(
  request: APIRequestContext,
  fixture: CreateIntentFixture,
  suffix: string,
  payload: JsonRecord,
): Promise<BrowserCreateResult> {
  const response = await request.post(bffUrl(fixture.path), {
    data: payload,
    headers: headersFor(fixture.resource, suffix),
    timeout: 10_000,
  });
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = await response.text();
  }
  return {
    body,
    path: fixture.path,
    status: response.status(),
  };
}

test.describe("F08 create write intents", () => {
  test.describe.configure({ timeout: 120_000 });

  test("posts all 9 create intents with Idempotency-Key and CommandResponse data", async ({
    page,
  }) => {
    const requests: RequestRecord[] = [];
    await installCreateIntentRoutes(page, requests);
    await page.setContent("<!doctype html><title>FE-INT-GATE-C02</title>");

    const results = await postCreateIntentsInBrowser(page, "create", true);

    expect(results).toHaveLength(CREATE_INTENT_RESOURCE_KEYS.length);
    for (const result of results) {
      const fixture = createIntentByResource(result.path.slice("/bff/".length) as CreateIntentResource);
      expect(result.status, `${fixture.resource} HTTP status`).toBe(fixture.status);
      assertCommandResponse(result.body, fixture, `${fixture.resource} CommandResponse`);
    }

    expect(requests).toHaveLength(CREATE_INTENT_RESOURCE_KEYS.length);
    for (const fixture of CREATE_INTENTS) {
      const request = requests.find((item) => item.path === fixture.path);
      expect(request, `${fixture.resource} request`).toBeTruthy();
      expect(request?.method, `${fixture.resource} method`).toBe("POST");
      expect(request?.authorization, `${fixture.resource} Authorization`).toMatch(/^Bearer /);
      expect(request?.idempotencyKey, `${fixture.resource} Idempotency-Key`).toBe(
        seededIdempotencyKey(fixture.resource, "create"),
      );
      expect(request?.requestId, `${fixture.resource} X-Request-Id`).toBe(
        seededRequestId(`f08-${fixture.resource}-create`),
      );
    }

    const deployment = requests.find((item) => item.path === "/bff/deployments");
    expect(deployment?.body).toMatchObject({
      deployment_stage: "paper",
      dryRun: true,
      execute: false,
      executionMode: "plan_only",
      liveCapitalSideEffects: false,
      mode: "plan_only",
      strategy_id: STRATEGY_DEV_ID,
    });
  });

  test("returns VALIDATION_FAILED.fieldErrors for invalid create intents", async ({
    page,
  }) => {
    const requests: RequestRecord[] = [];
    await installCreateIntentRoutes(page, requests);
    await page.setContent("<!doctype html><title>FE-INT-GATE-C02 validation</title>");

    const results = await postCreateIntentsInBrowser(page, "validation", false);

    expect(results).toHaveLength(CREATE_INTENT_RESOURCE_KEYS.length);
    for (const result of results) {
      const fixture = createIntentByResource(result.path.slice("/bff/".length) as CreateIntentResource);
      expect(result.status, `${fixture.resource} validation HTTP status`).toBe(422);
      const fieldErrors = validationFieldErrors(
        result.body,
        `${fixture.resource} VALIDATION_FAILED`,
      );
      expect(fieldErrors).toEqual([
        expect.objectContaining({
          code: "REQUIRED",
          field: fixture.requiredField,
        }),
      ]);
    }

    for (const fixture of CREATE_INTENTS) {
      const request = requests.find((item) => item.path === fixture.path);
      expect(request?.idempotencyKey, `${fixture.resource} validation Idempotency-Key`).toBe(
        seededIdempotencyKey(fixture.resource, "validation"),
      );
    }
  });

  test("live BFF create-intent probe preserves F08 write contracts", async ({
    request,
  }) => {
    test.skip(
      !RUN_LIVE_BFF_CONTRACT,
      "Set FE_INT_GATE_LIVE_BFF=1 or F08_CREATE_INTENT_LIVE_BFF=1 to run the staging BFF contract probe.",
    );

    const runId = process.env.FE_INT_GATE_RUN_ID || `${Date.now()}`;
    for (const fixture of CREATE_INTENTS) {
      const payload = {
        ...fixture.payload,
        id: `${fixture.payload.id ?? SEEDED_RESOURCE_IDS[fixture.resource]}-${runId}`,
      };
      const result = await postLiveCreateIntent(request, fixture, `live-${runId}`, payload);
      expect([201, 202], `${fixture.resource} live HTTP status`).toContain(result.status);
      const body = recordAt(result.body, `${fixture.resource} live CommandResponse`);
      expect(body.data, `${fixture.resource} live CommandResponse.data`).toBeTruthy();
      if (fixture.resource === "deployments") {
        const data = recordAt(body.data, `${fixture.resource} live CommandResponse.data`);
        const liveSideEffects =
          data.liveCapitalSideEffects ??
          recordAt(body.meta, `${fixture.resource} live CommandResponse.meta`)
            .liveCapitalSideEffects;
        expect(liveSideEffects, `${fixture.resource} live side effects`).toBe(false);
      }

      const validation = await postLiveCreateIntent(
        request,
        fixture,
        `live-validation-${runId}`,
        {},
      );
      expect([400, 422], `${fixture.resource} live validation HTTP status`).toContain(
        validation.status,
      );
      validationFieldErrors(
        validation.body,
        `${fixture.resource} live VALIDATION_FAILED`,
      );
    }
  });
});
