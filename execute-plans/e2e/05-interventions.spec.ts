/**
 * FE-INT-GATE-B05 / F06 - HIQ intervention actions and two-man gate.
 *
 * Coverage:
 *   1. Browser fetch runs claim / release / escalate / decide against the
 *      canonical /bff/v5/interventions/{id}/{action} routes.
 *   2. /decide returns a CommandResponse envelope and emits an
 *      intervention.decided SSE event on the intervention channel.
 *   3. Same-user /two-man-sign returns a typed TWO_MAN_REQUIRED BFF error.
 *
 * Runner: Playwright browser test with a local BFF/SSE contract harness.
 */

import { expect, test, type Page } from "@playwright/test";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";

const INTERVENTION_ID = "hiq-intervention-b05";
const OPERATOR_ID = "op-fe-gate";
const AUTH_HEADER = `Bearer ${OPERATOR_ID}:operator,reviewer,approver:mfa`;

type InterventionAction = "claim" | "release" | "escalate" | "decide" | "two-man-sign";

type RequestRecord = {
  action: InterventionAction;
  authorization: string | undefined;
  body: Record<string, unknown>;
  idempotencyKey: string | undefined;
  method: string;
  path: string;
};

type SseEnvelope = {
  id: string;
  schemaVersion: 1;
  channel: "intervention";
  type: string;
  occurredAt: string;
  correlationId: string;
  payload: Record<string, unknown>;
};

type CommandResponse = {
  status: "accepted";
  data: {
    action: InterventionAction;
    command: "V5InterventionAction";
    commandId: string;
    command_id: string;
    receipt: {
      command_id: string;
      status: "accepted";
      trackingUrl: string;
    };
    receipt_id: string;
    status: "accepted";
    target: {
      id: string;
      type: "SentinelIntervention";
    };
  };
  meta: {
    durable: true;
    idempotency: {
      idempotencyKey: string;
      replayed: false;
    };
    liveCapitalSideEffects: false;
  };
};

type BrowserActionResult = {
  body: unknown;
  status: number;
};

class InterventionHarness {
  private readonly server: Server;
  private readonly openSseResponses: ServerResponse[] = [];
  private commandSeq = 0;
  private eventSeq = 0;

  readonly requests: RequestRecord[] = [];
  baseUrl = "";

  constructor() {
    this.server = createServer((req, res) => {
      void this.handle(req, res);
    });
  }

  async start(): Promise<void> {
    await new Promise<void>((resolve) => {
      this.server.listen(0, "127.0.0.1", resolve);
    });
    const address = this.server.address() as AddressInfo;
    this.baseUrl = `http://127.0.0.1:${address.port}`;
  }

  async stop(): Promise<void> {
    for (const res of [...this.openSseResponses]) {
      if (!res.writableEnded) {
        res.end();
      }
    }
    await new Promise<void>((resolve, reject) => {
      this.server.close((error) => (error ? reject(error) : resolve()));
    });
  }

  private async handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const url = new URL(req.url ?? "/", this.baseUrl || "http://127.0.0.1");

    if (url.pathname === "/" || url.pathname === "/test-shell") {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end("<!doctype html><title>Pantheon intervention contract test</title>");
      return;
    }

    if (url.pathname === "/bff/events/stream") {
      this.handleSse(req, res, url);
      return;
    }

    const match = url.pathname.match(
      /^\/bff\/v5\/interventions\/([^/]+)\/(claim|release|escalate|decide|two-man-sign)$/,
    );
    if (req.method === "POST" && match) {
      const [, interventionId, action] = match;
      await this.handleAction(req, res, interventionId, action as InterventionAction);
      return;
    }

    this.fulfillJson(res, 404, {
      detail: {
        error: {
          code: "RESOURCE_NOT_FOUND",
          message: `No route for ${req.method} ${url.pathname}`,
        },
      },
    });
  }

  private handleSse(req: IncomingMessage, res: ServerResponse, url: URL): void {
    expect(url.searchParams.get("channel")).toBe("intervention");
    res.writeHead(200, {
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "Content-Type": "text/event-stream",
      "X-Accel-Buffering": "no",
      "X-SSE-Channel": "intervention",
      "X-SSE-Replay-Supported": "true",
    });
    this.openSseResponses.push(res);
    req.on("close", () => {
      const index = this.openSseResponses.indexOf(res);
      if (index >= 0) {
        this.openSseResponses.splice(index, 1);
      }
    });
    res.write(": connected\n\n");
  }

  private async handleAction(
    req: IncomingMessage,
    res: ServerResponse,
    interventionId: string,
    action: InterventionAction,
  ): Promise<void> {
    const body = await readJsonBody(req);
    const authorization = header(req, "authorization");
    const idempotencyKey = header(req, "idempotency-key");
    const request: RequestRecord = {
      action,
      authorization,
      body,
      idempotencyKey,
      method: req.method ?? "",
      path: `/bff/v5/interventions/${interventionId}/${action}`,
    };
    this.requests.push(request);

    if (action === "two-man-sign") {
      const secondOperatorId = String(
        body.secondOperatorId ??
          body.second_operator_id ??
          body.signerId ??
          body.signer_id ??
          "",
      ).trim();
      const actorId = actorFromAuthorization(authorization);
      if (!secondOperatorId || secondOperatorId === actorId) {
        this.fulfillJson(res, 409, {
          detail: {
            error: {
              code: "TWO_MAN_REQUIRED",
              i18nKey: "errors.TWO_MAN_REQUIRED",
              message: "Two-man authorization requires a distinct second operator",
              retryable: false,
              userActionable: true,
              correlationId: String(body.correlationId ?? "corr-b05-two-man"),
              details: {
                actionId: "V5InterventionAction",
                entityType: "SentinelIntervention",
                entityId: interventionId,
                kind: "two_man",
                reason: "TWO_MAN_DISTINCT_OPERATOR_REQUIRED",
              },
            },
          },
        });
        return;
      }
    }

    const command = this.commandResponse(interventionId, action, idempotencyKey);
    if (action === "decide") {
      this.publishDecision(interventionId, body);
    }
    this.fulfillJson(res, 202, command);
  }

  private commandResponse(
    interventionId: string,
    action: InterventionAction,
    idempotencyKey?: string,
  ): CommandResponse {
    this.commandSeq += 1;
    const commandId = `cmd-b05-${action}-${this.commandSeq}`;
    return {
      status: "accepted",
      data: {
        action,
        command: "V5InterventionAction",
        commandId,
        command_id: commandId,
        receipt: {
          command_id: commandId,
          status: "accepted",
          trackingUrl: `/api/v1/operator/commands/${commandId}`,
        },
        receipt_id: commandId,
        status: "accepted",
        target: {
          id: interventionId,
          type: "SentinelIntervention",
        },
      },
      meta: {
        durable: true,
        idempotency: {
          idempotencyKey: idempotencyKey ?? "",
          replayed: false,
        },
        liveCapitalSideEffects: false,
      },
    };
  }

  private publishDecision(interventionId: string, body: Record<string, unknown>): void {
    this.eventSeq += 1;
    const event: SseEnvelope = {
      id: `evt-b05-decision-${this.eventSeq}`,
      schemaVersion: 1,
      channel: "intervention",
      type: "intervention.decided",
      occurredAt: "2026-05-13T13:30:00Z",
      correlationId: String(body.correlationId ?? "corr-b05-decide"),
      payload: {
        decision: String(body.decision ?? "approve"),
        decidedBy: OPERATOR_ID,
        interventionId,
      },
    };
    const block = [
      `id: ${event.id}`,
      `event: ${event.type}`,
      `data: ${JSON.stringify(event)}`,
      "",
      "",
    ].join("\n");
    for (const res of this.openSseResponses) {
      if (!res.writableEnded) {
        res.write(block);
      }
    }
  }

  private fulfillJson(res: ServerResponse, status: number, body: unknown): void {
    res.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
    res.end(JSON.stringify(body));
  }
}

function header(req: IncomingMessage, name: string): string | undefined {
  const value = req.headers[name.toLowerCase()];
  if (Array.isArray(value)) return value[0];
  return value;
}

function actorFromAuthorization(value: string | undefined): string {
  if (!value?.startsWith("Bearer ")) return "";
  return value.slice("Bearer ".length).split(":")[0] ?? "";
}

async function readJsonBody(req: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  const text = Buffer.concat(chunks).toString("utf8").trim();
  if (!text) return {};
  const parsed = JSON.parse(text) as unknown;
  expect(parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)).toBe(true);
  return parsed as Record<string, unknown>;
}

async function installBrowserClient(page: Page, baseUrl: string): Promise<void> {
  await page.goto(`${baseUrl}/test-shell`);
  await page.evaluate(
    ({ authHeader, interventionId }) => {
      const state = {
        errors: 0,
        events: [] as Array<{
          channel: string;
          id: string;
          payload: Record<string, unknown>;
          type: string;
        }>,
        opens: 0,
      };
      const source = new EventSource("/bff/events/stream?channel=intervention", {
        withCredentials: true,
      });
      const handleEvent = (event: MessageEvent<string>) => {
        if (!event.data) return;
        const payload = JSON.parse(event.data) as {
          channel?: string;
          id?: string;
          payload?: Record<string, unknown>;
          type?: string;
        };
        state.events.push({
          channel: String(payload.channel ?? ""),
          id: String(payload.id ?? event.lastEventId),
          payload: payload.payload ?? {},
          type: String(payload.type ?? event.type),
        });
      };
      source.onopen = () => {
        state.opens += 1;
      };
      source.onerror = () => {
        state.errors += 1;
      };
      source.addEventListener("intervention.decided", (event) => {
        handleEvent(event as MessageEvent<string>);
      });
      source.onmessage = handleEvent;

      async function postAction(
        action: string,
        body: Record<string, unknown> = {},
      ): Promise<{ status: number; body: unknown }> {
        const response = await fetch(`/bff/v5/interventions/${interventionId}/${action}`, {
          body: JSON.stringify(body),
          headers: {
            Accept: "application/json",
            Authorization: authHeader,
            "Content-Type": "application/json",
            "Idempotency-Key": `b05-${action}-${crypto.randomUUID()}`,
          },
          method: "POST",
        });
        const json = await response.json();
        return { status: response.status, body: json };
      }

      (window as any).__pantheonB05 = {
        close: () => source.close(),
        postAction,
        state,
      };
    },
    { authHeader: AUTH_HEADER, interventionId: INTERVENTION_ID },
  );
}

async function postAction(
  page: Page,
  action: InterventionAction,
  body: Record<string, unknown> = {},
): Promise<BrowserActionResult> {
  return page.evaluate(
    ({ actionName, payload }) =>
      (window as any).__pantheonB05.postAction(actionName, payload),
    { actionName: action, payload: body },
  );
}

async function sseEvents(page: Page): Promise<Array<{ type: string; payload: Record<string, unknown> }>> {
  return page.evaluate(() => (window as any).__pantheonB05.state.events);
}

function commandResponseAt(value: unknown, label: string): CommandResponse {
  expect(value !== null && typeof value === "object" && !Array.isArray(value), label).toBe(true);
  const response = value as CommandResponse;
  expect(response.status, `${label}.status`).toBe("accepted");
  expect(response.data?.status, `${label}.data.status`).toBe("accepted");
  expect(response.data?.command, `${label}.data.command`).toBe("V5InterventionAction");
  expect(response.data?.commandId || response.data?.command_id, `${label}.data.commandId`).toBeTruthy();
  expect(response.data?.receipt?.trackingUrl, `${label}.data.receipt.trackingUrl`).toContain(
    "/api/v1/operator/commands/",
  );
  expect(response.meta?.durable, `${label}.meta.durable`).toBe(true);
  expect(response.meta?.liveCapitalSideEffects, `${label}.meta.liveCapitalSideEffects`).toBe(false);
  return response;
}

test.describe("F06 HIQ interventions", () => {
  let harness: InterventionHarness;

  test.beforeEach(async () => {
    harness = new InterventionHarness();
    await harness.start();
  });

  test.afterEach(async ({ page }) => {
    await page.evaluate(() => (window as any).__pantheonB05?.close?.()).catch(() => undefined);
    await harness.stop();
  });

  test("runs claim, release, escalate, and decide through CommandResponse routes", async ({
    page,
  }) => {
    await installBrowserClient(page, harness.baseUrl);

    for (const action of ["claim", "release", "escalate", "decide"] as const) {
      const result = await postAction(page, action, {
        correlationId: `corr-b05-${action}`,
        decision: action === "decide" ? "approve" : undefined,
        reason: `FE gate B05 ${action}`,
      });
      expect(result.status, `${action} HTTP status`).toBe(202);
      const response = commandResponseAt(result.body, action);
      expect(response.data.action).toBe(action);
      expect(response.data.target).toEqual({
        id: INTERVENTION_ID,
        type: "SentinelIntervention",
      });
      expect(response.meta.idempotency.idempotencyKey).toContain(`b05-${action}-`);
    }

    expect(harness.requests.map((request) => request.action)).toEqual([
      "claim",
      "release",
      "escalate",
      "decide",
    ]);
    expect(harness.requests.every((request) => request.authorization === AUTH_HEADER)).toBe(true);
    expect(harness.requests.every((request) => request.idempotencyKey?.startsWith("b05-"))).toBe(
      true,
    );
  });

  test("decide returns CommandResponse and publishes intervention.decided SSE", async ({
    page,
  }) => {
    await installBrowserClient(page, harness.baseUrl);

    await expect
      .poll(
        () => page.evaluate(() => (window as any).__pantheonB05.state.opens),
        { message: "intervention SSE stream should open before deciding" },
      )
      .toBeGreaterThan(0);

    const result = await postAction(page, "decide", {
      correlationId: "corr-b05-decide",
      decision: "approve",
      reason: "FE gate B05 decision",
    });
    expect(result.status).toBe(202);
    const response = commandResponseAt(result.body, "decide");
    expect(response.data.action).toBe("decide");

    await expect
      .poll(
        async () => {
          const events = await sseEvents(page);
          return events.find((event) => event.type === "intervention.decided");
        },
        { message: "decide should produce intervention.decided SSE" },
      )
      .toMatchObject({
        payload: {
          decidedBy: OPERATOR_ID,
          decision: "approve",
          interventionId: INTERVENTION_ID,
        },
        type: "intervention.decided",
      });
  });

  test("same-user two-man sign returns TWO_MAN_REQUIRED", async ({ page }) => {
    await installBrowserClient(page, harness.baseUrl);

    const result = await postAction(page, "two-man-sign", {
      correlationId: "corr-b05-two-man",
      requesterId: OPERATOR_ID,
      secondOperatorId: OPERATOR_ID,
    });

    expect(result.status).toBe(409);
    const body = result.body as {
      detail?: {
        error?: {
          code?: string;
          details?: Record<string, unknown>;
          i18nKey?: string;
          message?: string;
        };
      };
    };
    expect(body.detail?.error?.code).toBe("TWO_MAN_REQUIRED");
    expect(body.detail?.error?.i18nKey).toBe("errors.TWO_MAN_REQUIRED");
    expect(body.detail?.error?.message).toMatch(/distinct second operator/i);
    expect(body.detail?.error?.details).toMatchObject({
      entityId: INTERVENTION_ID,
      entityType: "SentinelIntervention",
      kind: "two_man",
      reason: "TWO_MAN_DISTINCT_OPERATOR_REQUIRED",
    });
  });
});
