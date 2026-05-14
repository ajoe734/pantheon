/**
 * FE-INT-GATE-D01 / F10 - rollback saga dry-run, execution, and stepper.
 *
 * Current status:
 *   The live BFF exposes rollback review/read routes and the final command
 *   facade, but it does not yet expose a RollbackSagaDTO-backed dry-run or
 *   execution response. These tests are therefore registered as fixme by
 *   default and annotated BACKEND-NOT-READY.
 *
 * To enable live execution when the backend contract is ready:
 *   F10_ROLLBACK_SAGA_BACKEND_READY=1
 *
 * Env:
 *   FRONTEND_BASE_URL or PLAYWRIGHT_BASE_URL
 *     default: http://127.0.0.1:5173
 *   BFF_BASE_URL or VITE_BFF_BASE_URL
 *     default: https://pantheon-staging-bff.34.81.225.122.sslip.io
 *   BFF_AUTH_TOKEN
 *     optional; when omitted the dev stub token is used.
 */

import {
  expect,
  test,
  type APIRequestContext,
  type Page,
  type Route,
} from "@playwright/test";

const DEFAULT_FRONTEND_BASE_URL = "http://127.0.0.1:5173";
const DEFAULT_BFF_BASE_URL =
  "https://pantheon-staging-bff.34.81.225.122.sslip.io";
const DEFAULT_DEV_AUTH_TOKEN = "op-fe-gate:operator,approver,admin:mfa";

const BACKEND_READY = process.env.F10_ROLLBACK_SAGA_BACKEND_READY === "1";
const BACKEND_NOT_READY_REASON =
  "BACKEND-NOT-READY: rollback saga dry-run/execute must return RollbackDryRunDTO/RollbackSagaDTO and emit rollback saga SSE events.";

const ROLLBACK_ID = "rollback-f10-paper-001";
const RUNTIME_ID = "runtime-paper-f10";
const RUNTIME_BINDING_ID = "binding-paper-f10-active";
const FALLBACK_ARTIFACT_ID = "artifact-momentum-fallback";
const FALLBACK_ARTIFACT_VERSION = "paper-fallback-2026-05-13";
const SAGA_ID = "rollback-saga-f10-001";

const COMMANDS_PATH = "/bff/v1/commands";
const ROLLBACK_REVIEW_PATH = `/api/v1/operator/rollback-review/${ROLLBACK_ID}`;
const ROLLBACK_SAGA_ROUTE = `/governance-rollback-review?rollback=${ROLLBACK_ID}`;
const SSE_STREAM_PATH = "/bff/events/stream";

const CRASH_TEXT =
  /application error|cannot read properties|undefined is not|uncaught|traceback|typeerror|referenceerror/i;

type JsonRecord = Record<string, unknown>;

type RollbackActionType =
  | "replace"
  | "pause_then_replace"
  | "liquidate_then_replace";

type RollbackStepStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "compensating"
  | "compensated";

type RollbackStepDTO = {
  step_id: string;
  label: string;
  status: RollbackStepStatus;
  owner: string;
  compensation_state?: string;
};

type RollbackDryRunDTO = {
  dry_run_id: string;
  rollback_id: string;
  target: {
    runtime_id: string;
    current_binding_id: string;
    fallback_artifact_id: string;
    fallback_artifact_version: string;
    action_type: RollbackActionType;
  };
  eligibility: {
    eligible: boolean;
    decision: "eligible" | "blocked";
    blockers: string[];
  };
  blast_radius: {
    affected_bindings_count: number;
    affected_capital_pools_count: number;
    affected_open_positions_count: number;
    requires_position_freeze: boolean;
  };
  required_gates: Array<{
    gate_id: string;
    status: "satisfied" | "required" | "blocked";
    owner: string;
  }>;
};

type RollbackSagaDTO = {
  saga_id: string;
  rollback_id: string;
  status: "accepted" | "in_progress" | "succeeded" | "failed" | "compensating" | "compensated";
  action_type: RollbackActionType;
  target: RollbackDryRunDTO["target"];
  steps: RollbackStepDTO[];
  failureReasonCode?: string;
  compensation: {
    state: "not_required" | "pending" | "in_progress" | "completed" | "failed";
    owner: string;
    actions: string[];
  };
};

type RollbackSagaEvent = {
  id: string;
  type:
    | "rollback.saga.started"
    | "rollback.saga.step_updated"
    | "rollback.saga.failed";
  data: {
    saga_id: string;
    rollback_id: string;
    step_id?: string;
    status?: RollbackStepStatus;
    failureReasonCode?: string;
    compensation?: RollbackSagaDTO["compensation"];
  };
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

function booleanAt(value: unknown, label: string): boolean {
  expect(typeof value, `${label} must be a boolean`).toBe("boolean");
  return Boolean(value);
}

function arrayAt<T = unknown>(value: unknown, label: string): T[] {
  expect(Array.isArray(value), `${label} must be an array`).toBe(true);
  return value as T[];
}

function commandHeaders(idempotencyKey: string): Record<string, string> {
  return {
    Accept: "application/json",
    Authorization: authHeader(),
    "Content-Type": "application/json",
    "Idempotency-Key": idempotencyKey,
    "X-Confirm-Token": "confirm-f10-rollback",
    "X-Correlation-Id": "corr-f10-rollback-saga",
    "X-MFA-Token": "000000",
    "X-Request-Id": "req-f10-rollback-saga",
    "X-Trace-Id": "trace-f10-rollback-saga",
  };
}

function envelope<T>(data: T): { status: "accepted"; data: T; meta: JsonRecord } {
  return {
    status: "accepted",
    data,
    meta: {
      contract: "FE-INT-GATE-D01",
      snapshot_at: "2026-05-13T13:30:00Z",
    },
  };
}

const dryRunDto: RollbackDryRunDTO = {
  dry_run_id: "rollback-dry-run-f10-001",
  rollback_id: ROLLBACK_ID,
  target: {
    runtime_id: RUNTIME_ID,
    current_binding_id: RUNTIME_BINDING_ID,
    fallback_artifact_id: FALLBACK_ARTIFACT_ID,
    fallback_artifact_version: FALLBACK_ARTIFACT_VERSION,
    action_type: "pause_then_replace",
  },
  eligibility: {
    eligible: true,
    decision: "eligible",
    blockers: [],
  },
  blast_radius: {
    affected_bindings_count: 1,
    affected_capital_pools_count: 1,
    affected_open_positions_count: 3,
    requires_position_freeze: true,
  },
  required_gates: [
    {
      gate_id: "approval",
      status: "satisfied",
      owner: "governance-svc",
    },
    {
      gate_id: "confirm_token",
      status: "satisfied",
      owner: "bff-command-gate",
    },
    {
      gate_id: "runtime-manager-ready",
      status: "required",
      owner: "runtime-manager-svc",
    },
  ],
};

const acceptedSagaDto: RollbackSagaDTO = {
  saga_id: SAGA_ID,
  rollback_id: ROLLBACK_ID,
  status: "in_progress",
  action_type: "pause_then_replace",
  target: dryRunDto.target,
  steps: [
    {
      step_id: "dry_run",
      label: "Dry-run eligibility",
      status: "completed",
      owner: "rollback-orchestrator",
    },
    {
      step_id: "pause_current_binding",
      label: "Pause current binding",
      status: "running",
      owner: "runtime-manager-svc",
    },
    {
      step_id: "create_replacement_binding",
      label: "Create replacement binding",
      status: "pending",
      owner: "runtime-manager-svc",
    },
    {
      step_id: "cutover_and_audit",
      label: "Cutover and audit",
      status: "pending",
      owner: "rollback-orchestrator",
    },
  ],
  compensation: {
    state: "not_required",
    owner: "rollback-orchestrator",
    actions: [],
  },
};

const failedSagaDto: RollbackSagaDTO = {
  ...acceptedSagaDto,
  status: "compensating",
  failureReasonCode: "RUNTIME_BINDING_CREATE_FAILED",
  steps: acceptedSagaDto.steps.map((step) =>
    step.step_id === "create_replacement_binding"
      ? {
          ...step,
          status: "failed",
          compensation_state: "safe_mode_requested",
        }
      : step,
  ),
  compensation: {
    state: "in_progress",
    owner: "rollback-orchestrator",
    actions: ["resume_old_binding", "enter_safe_mode", "raise_incident"],
  },
};

const sseEvents: RollbackSagaEvent[] = [
  {
    id: "evt-f10-started",
    type: "rollback.saga.started",
    data: {
      saga_id: SAGA_ID,
      rollback_id: ROLLBACK_ID,
      status: "running",
    },
  },
  {
    id: "evt-f10-pause-completed",
    type: "rollback.saga.step_updated",
    data: {
      saga_id: SAGA_ID,
      rollback_id: ROLLBACK_ID,
      step_id: "pause_current_binding",
      status: "completed",
    },
  },
];

const failureSseEvent: RollbackSagaEvent = {
  id: "evt-f10-failed",
  type: "rollback.saga.failed",
  data: {
    saga_id: SAGA_ID,
    rollback_id: ROLLBACK_ID,
    step_id: "create_replacement_binding",
    status: "failed",
    failureReasonCode: failedSagaDto.failureReasonCode,
    compensation: failedSagaDto.compensation,
  },
};

function rollbackCommandPayload(dryRun: boolean): JsonRecord {
  return {
    command: "ExecuteRollback",
    target: {
      type: "Rollback",
      id: ROLLBACK_ID,
    },
    action: dryRun ? "dry_run" : "execute",
    params: {
      rollback_id: ROLLBACK_ID,
      rollback_target_type: "runtime",
      target_id: RUNTIME_BINDING_ID,
      runtime_id: RUNTIME_ID,
      rollback_to_version: FALLBACK_ARTIFACT_VERSION,
      fallback_artifact_id: FALLBACK_ARTIFACT_ID,
      rollback_action_type: "pause_then_replace",
      dry_run: dryRun,
    },
    audit_context: {
      reason: dryRun
        ? "FE-INT-GATE-D01 dry-run eligibility check"
        : "FE-INT-GATE-D01 execute rollback saga",
    },
  };
}

function assertRollbackDryRunDto(value: unknown): RollbackDryRunDTO {
  const dto = recordAt(value, "RollbackDryRunDTO");
  stringAt(dto.dry_run_id ?? dto.dryRunId, "RollbackDryRunDTO.dry_run_id");
  expect(stringAt(dto.rollback_id ?? dto.rollbackId, "RollbackDryRunDTO.rollback_id")).toBe(
    ROLLBACK_ID,
  );

  const target = recordAt(dto.target, "RollbackDryRunDTO.target");
  expect(stringAt(target.runtime_id ?? target.runtimeId, "target.runtime_id")).toBe(
    RUNTIME_ID,
  );
  expect(
    stringAt(
      target.current_binding_id ?? target.currentBindingId,
      "target.current_binding_id",
    ),
  ).toBe(RUNTIME_BINDING_ID);
  expect(
    stringAt(
      target.fallback_artifact_version ?? target.fallbackArtifactVersion,
      "target.fallback_artifact_version",
    ),
  ).toBe(FALLBACK_ARTIFACT_VERSION);
  expect(["replace", "pause_then_replace", "liquidate_then_replace"]).toContain(
    target.action_type ?? target.actionType,
  );

  const eligibility = recordAt(dto.eligibility, "RollbackDryRunDTO.eligibility");
  expect(booleanAt(eligibility.eligible, "eligibility.eligible")).toBe(true);
  expect(arrayAt(eligibility.blockers, "eligibility.blockers")).toHaveLength(0);

  const blastRadius = recordAt(
    dto.blast_radius ?? dto.blastRadius,
    "RollbackDryRunDTO.blast_radius",
  );
  expect(
    numberAt(
      blastRadius.affected_bindings_count ?? blastRadius.affectedBindingsCount,
      "blast_radius.affected_bindings_count",
    ),
  ).toBeGreaterThanOrEqual(1);
  expect(
    numberAt(
      blastRadius.affected_open_positions_count ??
        blastRadius.affectedOpenPositionsCount,
      "blast_radius.affected_open_positions_count",
    ),
  ).toBeGreaterThanOrEqual(0);
  booleanAt(
    blastRadius.requires_position_freeze ?? blastRadius.requiresPositionFreeze,
    "blast_radius.requires_position_freeze",
  );

  const gates = arrayAt<JsonRecord>(
    dto.required_gates ?? dto.requiredGates,
    "RollbackDryRunDTO.required_gates",
  );
  expect(gates.length, "required_gates must not be empty").toBeGreaterThan(0);
  const gateIds = gates.map((gate, index) =>
    stringAt(gate.gate_id ?? gate.gateId ?? gate.id, `required_gates[${index}].gate_id`),
  );
  expect(gateIds).toEqual(
    expect.arrayContaining(["approval", "confirm_token", "runtime-manager-ready"]),
  );

  return dto as unknown as RollbackDryRunDTO;
}

function assertRollbackSagaDto(value: unknown): RollbackSagaDTO {
  const dto = recordAt(value, "RollbackSagaDTO");
  expect(stringAt(dto.saga_id ?? dto.sagaId, "RollbackSagaDTO.saga_id")).toBe(
    SAGA_ID,
  );
  expect(stringAt(dto.rollback_id ?? dto.rollbackId, "RollbackSagaDTO.rollback_id")).toBe(
    ROLLBACK_ID,
  );
  expect([
    "accepted",
    "in_progress",
    "succeeded",
    "failed",
    "compensating",
    "compensated",
  ]).toContain(dto.status);
  expect(["replace", "pause_then_replace", "liquidate_then_replace"]).toContain(
    dto.action_type ?? dto.actionType,
  );

  const steps = arrayAt<JsonRecord>(dto.steps, "RollbackSagaDTO.steps");
  expect(steps.length, "steps must not be empty").toBeGreaterThan(0);
  const stepIds = steps.map((step, index) =>
    stringAt(step.step_id ?? step.stepId ?? step.id, `steps[${index}].step_id`),
  );
  expect(stepIds).toEqual(
    expect.arrayContaining([
      "dry_run",
      "pause_current_binding",
      "create_replacement_binding",
      "cutover_and_audit",
    ]),
  );
  for (const [index, step] of steps.entries()) {
    expect([
      "pending",
      "running",
      "completed",
      "failed",
      "compensating",
      "compensated",
    ]).toContain(step.status);
    stringAt(step.owner, `steps[${index}].owner`);
  }

  const compensation = recordAt(dto.compensation, "RollbackSagaDTO.compensation");
  expect(["not_required", "pending", "in_progress", "completed", "failed"]).toContain(
    compensation.state,
  );
  stringAt(compensation.owner, "compensation.owner");
  arrayAt(compensation.actions, "compensation.actions");

  return dto as unknown as RollbackSagaDTO;
}

async function postRollbackCommand(
  request: APIRequestContext,
  dryRun: boolean,
): Promise<JsonRecord> {
  const response = await request.post(bffUrl(COMMANDS_PATH), {
    headers: commandHeaders(`f10-${dryRun ? "dry-run" : "execute"}-001`),
    data: rollbackCommandPayload(dryRun),
    timeout: 10_000,
  });

  expect(response.status(), await response.text()).toBe(202);
  const body = recordAt(await response.json(), "CommandResponse");
  expect(["accepted", "queued", "completed"]).toContain(body.status);
  return recordAt(body.data, "CommandResponse.data");
}

function mePayload(): JsonRecord {
  const user = {
    id: "operator-f10",
    operator_id: "operator-f10",
    display_name: "F10 Rollback Operator",
    roles: ["operator", "approver", "admin"],
    capabilities: ["runtime.read", "runtime.write", "governance.approve"],
    mfa_verified: true,
  };
  return {
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
      user,
      currentUser: user,
      current_user: user,
      roles: user.roles,
      capabilities: user.capabilities,
      session: {
        id: "session-f10",
        authenticated: true,
        fresh: true,
        mfa_verified: true,
        session_kind: "stub",
        auth_mode: "stub",
        checked_at: "2026-05-13T13:30:00Z",
      },
      feature_flags: {
        sessionAuthMe: true,
      },
    },
    meta: {
      route: "GET /bff/me",
      contract: "FE-INT-GATE-D01",
    },
  };
}

function rollbackReviewPayload(): JsonRecord {
  return {
    rollback_id: ROLLBACK_ID,
    title: "F10 paper rollback saga review",
    rollback_scope: {
      runtime_id: RUNTIME_ID,
      current_binding_id: RUNTIME_BINDING_ID,
      fallback_artifact_id: FALLBACK_ARTIFACT_ID,
      fallback_artifact_version: FALLBACK_ARTIFACT_VERSION,
      action_type: "pause_then_replace",
    },
    affected_bindings: [
      {
        binding_id: RUNTIME_BINDING_ID,
        runtime_id: RUNTIME_ID,
        status: "active",
      },
    ],
    position_impact: [
      {
        instrument_id: "2330.TW",
        current_managed_by_binding_id: RUNTIME_BINDING_ID,
        position_impact_summary: "3 open positions require freeze before cutover.",
      },
    ],
    trigger_evidence: [
      {
        evidence_id: "ev-f10-drift",
        evidence_type: "incident",
        summary: "Paper runtime drift exceeded rollback threshold.",
      },
    ],
    allowedActions: {
      canApproveRollback: true,
      canRejectRollback: true,
      canExecuteRollback: true,
      canDryRunRollback: true,
    },
    meta: {
      snapshot_at: "2026-05-13T13:30:00Z",
      surfaces: {
        rollback_review: { status: "ok" },
        position_data: { status: "ok" },
        rollback_saga: { status: "ok" },
      },
    },
  };
}

function sseBlock(event: RollbackSagaEvent): string {
  return [
    `id: ${event.id}`,
    `event: ${event.type}`,
    `data: ${JSON.stringify({
      id: event.id,
      type: event.type,
      timestamp: "2026-05-13T13:30:00Z",
      data: event.data,
    })}`,
    "",
    "",
  ].join("\n");
}

async function fulfillJson(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({
    status,
    contentType: "application/json",
    headers: {
      "Access-Control-Allow-Headers":
        "Accept, Authorization, Content-Type, Idempotency-Key, X-Confirm-Token, X-Correlation-Id, X-MFA-Token, X-Request-Id, X-Trace-Id",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Origin": "*",
    },
    body: JSON.stringify(body),
  });
}

async function installRollbackSagaFixtureRoutes(
  page: Page,
  options: {
    commandData?: RollbackDryRunDTO | RollbackSagaDTO;
    events?: RollbackSagaEvent[];
  } = {},
): Promise<string[]> {
  const commandActions: string[] = [];

  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (request.method() === "OPTIONS") {
      await route.fulfill({
        status: 204,
        headers: {
          "Access-Control-Allow-Headers":
            "Accept, Authorization, Content-Type, Idempotency-Key, X-Confirm-Token, X-Correlation-Id, X-MFA-Token, X-Request-Id, X-Trace-Id",
          "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
          "Access-Control-Allow-Origin": "*",
        },
      });
      return;
    }

    if (path === "/bff/me") {
      await fulfillJson(route, mePayload());
      return;
    }

    if (path === "/health") {
      await fulfillJson(route, { status: "ok", service: "pantheon-bff" });
      return;
    }

    if (path === ROLLBACK_REVIEW_PATH) {
      await fulfillJson(route, rollbackReviewPayload());
      return;
    }

    if (path === "/api/v1/rollbacks") {
      await fulfillJson(route, {
        items: [
          {
            rollback_id: ROLLBACK_ID,
            runtime_id: RUNTIME_ID,
            action_type: "pause_then_replace",
            status: "pending_execution",
          },
        ],
        meta: { snapshot_at: "2026-05-13T13:30:00Z" },
      });
      return;
    }

    if (path === `/api/v1/runtimes/${RUNTIME_ID}/rollbacks`) {
      await fulfillJson(route, {
        data: [
          {
            rollback_id: ROLLBACK_ID,
            runtime_id: RUNTIME_ID,
            action_type: "pause_then_replace",
            status: "pending_execution",
          },
        ],
        meta: { snapshot_at: "2026-05-13T13:30:00Z" },
      });
      return;
    }

    if (path === COMMANDS_PATH && request.method() === "POST") {
      const payload = (await request.postDataJSON().catch(() => ({}))) as JsonRecord;
      commandActions.push(String(payload.action || ""));
      const data =
        options.commandData ||
        (payload.action === "dry_run" ? dryRunDto : acceptedSagaDto);
      await fulfillJson(route, envelope(data), 202);
      return;
    }

    if (path === SSE_STREAM_PATH) {
      const events = options.events || sseEvents;
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        headers: {
          "Cache-Control": "no-cache",
          "X-SSE-Channel": "rollback",
        },
        body: events.map(sseBlock).join(""),
      });
      return;
    }

    await route.continue();
  });

  return commandActions;
}

async function bodyText(page: Page): Promise<string> {
  return page.locator("body").innerText({ timeout: 10_000 });
}

async function clickFirstVisible(page: Page, labels: RegExp[]): Promise<void> {
  for (const label of labels) {
    const button = page.getByRole("button", { name: label }).first();
    if ((await button.count()) > 0 && (await button.isVisible().catch(() => false))) {
      await button.click();
      return;
    }
    const link = page.getByRole("link", { name: label }).first();
    if ((await link.count()) > 0 && (await link.isVisible().catch(() => false))) {
      await link.click();
      return;
    }
  }
  throw new Error(`No visible control matched: ${labels.map(String).join(", ")}`);
}

async function expectBodyContains(page: Page, patterns: RegExp[], label: string): Promise<void> {
  await expect
    .poll(
      async () => {
        const text = await bodyText(page);
        return patterns.every((pattern) => pattern.test(text));
      },
      { message: `${label} should be rendered`, timeout: 15_000 },
    )
    .toBe(true);
}

test.describe("F10 rollback saga contract", () => {
  test.fixme(!BACKEND_READY, BACKEND_NOT_READY_REASON);

  test(
    "dry-run API exposes eligibility, blast radius, and required gates",
    {
      annotation: BACKEND_READY
        ? []
        : [{ type: "BACKEND-NOT-READY", description: BACKEND_NOT_READY_REASON }],
    },
    async ({ request }) => {
      const data = await postRollbackCommand(request, true);
      assertRollbackDryRunDto(data);
    },
  );

  test(
    "execute API returns RollbackSagaDTO with stepper state",
    {
      annotation: BACKEND_READY
        ? []
        : [{ type: "BACKEND-NOT-READY", description: BACKEND_NOT_READY_REASON }],
    },
    async ({ request }) => {
      const data = await postRollbackCommand(request, false);
      assertRollbackSagaDto(data);
    },
  );

  test(
    "dry-run review renders gates and advances the saga stepper from SSE",
    {
      annotation: BACKEND_READY
        ? []
        : [{ type: "BACKEND-NOT-READY", description: BACKEND_NOT_READY_REASON }],
    },
    async ({ page }) => {
      const commandActions = await installRollbackSagaFixtureRoutes(page);

      await page.goto(frontendUrl(ROLLBACK_SAGA_ROUTE), {
        waitUntil: "domcontentloaded",
      });
      await expect(page.locator("body")).not.toContainText(CRASH_TEXT);

      await clickFirstVisible(page, [/dry[-\s]?run/i, /simulate rollback/i, /check eligibility/i]);
      await expect
        .poll(() => commandActions.includes("dry_run"), {
          message: "dry-run action must call the rollback command facade",
          timeout: 10_000,
        })
        .toBe(true);
      await expectBodyContains(
        page,
        [/eligible/i, /blast radius/i, /required gates/i, /runtime-manager-ready/i],
        "rollback dry-run result",
      );

      await clickFirstVisible(page, [/execute rollback/i, /start rollback/i]);
      await expect
        .poll(() => commandActions.includes("execute"), {
          message: "execute action must call the rollback command facade",
          timeout: 10_000,
        })
        .toBe(true);
      await expectBodyContains(
        page,
        [new RegExp(SAGA_ID), /pause current binding/i, /completed/i],
        "rollback saga stepper",
      );
    },
  );

  test(
    "failure UI renders failureReasonCode and compensation state",
    {
      annotation: BACKEND_READY
        ? []
        : [{ type: "BACKEND-NOT-READY", description: BACKEND_NOT_READY_REASON }],
    },
    async ({ page }) => {
      await installRollbackSagaFixtureRoutes(page, {
        commandData: failedSagaDto,
        events: [failureSseEvent],
      });

      await page.goto(frontendUrl(ROLLBACK_SAGA_ROUTE), {
        waitUntil: "domcontentloaded",
      });
      await clickFirstVisible(page, [/execute rollback/i, /start rollback/i]);

      await expectBodyContains(
        page,
        [
          /RUNTIME_BINDING_CREATE_FAILED/i,
          /compensation/i,
          /safe_mode_requested|enter_safe_mode|resume_old_binding/i,
        ],
        "rollback failure and compensation state",
      );
    },
  );
});
