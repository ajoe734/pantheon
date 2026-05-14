/**
 * FE-INT-GATE-D02 / F11 - handoff reopen SLA contract.
 *
 * Coverage:
 *   1. Reopening a closed handoff defaults to preserving the active SLA dueAt.
 *   2. Requesting an SLA reset without approval evidence fails closed with
 *      APPROVAL_REQUIRED.
 *   3. An approved SLA reset appends a visible SlaSegment for the operator.
 *
 * Runner: Playwright browser test with a local BFF/SSE contract harness.
 */

import { expect, test, type Page } from "@playwright/test";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";

const HANDOFF_ID = "handoff-f11-paper-001";
const OPERATOR_ID = "op-fe-gate";
const ORIGINAL_DUE_AT = "2026-05-13T18:00:00Z";
const DEFAULT_REOPENED_AT = "2026-05-13T14:00:00Z";
const RESET_REOPENED_AT = "2026-05-13T15:00:00Z";
const RESET_DUE_AT = "2026-05-14T15:00:00Z";
const RESET_APPROVAL_ID = "approval-reset-f11-001";

type JsonRecord = Record<string, unknown>;

type SlaSegment = {
  segment_id: string;
  segmentId: string;
  kind: "initial" | "reopen";
  status: "active" | "closed";
  started_at: string;
  startedAt: string;
  due_at: string;
  dueAt: string;
  reset_sla: boolean;
  resetSla: boolean;
  visible_to_operator: boolean;
  visibleToOperator: boolean;
  reason_code?: string;
  reasonCode?: string;
  approval_id?: string;
  approvalId?: string;
};

type HandoffDto = {
  handoff_id: string;
  handoffId: string;
  status: "closed" | "reopened";
  current_sla_due_at: string;
  currentSlaDueAt: string;
  sla: {
    due_at: string;
    dueAt: string;
    reset_count: number;
    resetCount: number;
  };
  sla_segments: SlaSegment[];
  slaSegments: SlaSegment[];
  audit_events: JsonRecord[];
  auditEvents: JsonRecord[];
};

type RequestRecord = {
  body: JsonRecord;
  method: string;
  path: string;
};

type BrowserResponse = {
  body: JsonRecord;
  status: number;
};

class HandoffSlaHarness {
  private readonly server: Server;
  private readonly openSseResponses: ServerResponse[] = [];
  private eventSeq = 0;
  private handoff: HandoffDto;

  readonly requests: RequestRecord[] = [];
  baseUrl = "";

  constructor() {
    this.handoff = makeInitialHandoff();
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

  snapshot(): HandoffDto {
    return clone(this.handoff);
  }

  private async handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const url = new URL(req.url ?? "/", this.baseUrl || "http://127.0.0.1");

    if (url.pathname === "/" || url.pathname === "/test-shell") {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end("<!doctype html><title>F11 Handoff SLA contract</title><main id=\"app\"></main>");
      return;
    }

    if (url.pathname === "/bff/events/stream") {
      this.handleSse(req, res, url);
      return;
    }

    if (url.pathname === `/bff/handoffs/${HANDOFF_ID}` && req.method === "GET") {
      this.fulfillJson(res, 200, {
        data: this.handoff,
        meta: { route: "GET /bff/handoffs/{handoffId}", contract: "FE-INT-GATE-D02" },
      });
      return;
    }

    if (url.pathname === `/bff/handoffs/${HANDOFF_ID}/reopen` && req.method === "POST") {
      await this.handleReopen(req, res, url.pathname);
      return;
    }

    this.fulfillJson(res, 404, {
      error: {
        code: "RESOURCE_NOT_FOUND",
        message: `No route for ${req.method} ${url.pathname}`,
      },
    });
  }

  private handleSse(req: IncomingMessage, res: ServerResponse, url: URL): void {
    expect(url.searchParams.get("channel")).toBe("handoff");
    res.writeHead(200, {
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "Content-Type": "text/event-stream",
      "X-Accel-Buffering": "no",
      "X-SSE-Channel": "handoff",
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

  private async handleReopen(
    req: IncomingMessage,
    res: ServerResponse,
    path: string,
  ): Promise<void> {
    const body = await readJsonBody(req);
    this.requests.push({ body, method: req.method ?? "", path });

    const resetSla = body.resetSla === true || body.reset_sla === true;
    const approvalId = stringField(
      body.approvalId ?? body.approval_id ?? body.approvalDecisionId ?? body.approval_decision_id,
    );
    if (resetSla && !approvalId) {
      this.fulfillJson(res, 409, {
        error: {
          code: "APPROVAL_REQUIRED",
          i18nKey: "errors.APPROVAL_REQUIRED",
          message: "Approval evidence is required before resetting handoff SLA",
          retryable: false,
          userActionable: true,
          correlationId: stringField(body.correlationId) || "corr-f11-approval-required",
          details: {
            handoffId: HANDOFF_ID,
            kind: "approval",
            precondition_failed: "approval",
            requires_approval: true,
            resetSla: true,
          },
        },
      });
      return;
    }

    const segment = this.appendReopenSegment({
      approvalId,
      reasonCode: stringField(body.reasonCode ?? body.reason_code) || "REQUESTER_REOPENED",
      resetSla,
    });
    this.publishReopenedEvent(segment);

    this.fulfillJson(res, 202, {
      status: "accepted",
      data: {
        handoff: this.handoff,
        handoffId: HANDOFF_ID,
        slaReset: resetSla,
        slaSegment: segment,
      },
      meta: {
        durable: true,
        contract: "FE-INT-GATE-D02",
        idempotency: {
          idempotencyKey: header(req, "idempotency-key") ?? "",
          replayed: false,
        },
      },
    });
  }

  private appendReopenSegment(args: {
    approvalId: string;
    reasonCode: string;
    resetSla: boolean;
  }): SlaSegment {
    const dueAt = args.resetSla ? RESET_DUE_AT : ORIGINAL_DUE_AT;
    const startedAt = args.resetSla ? RESET_REOPENED_AT : DEFAULT_REOPENED_AT;
    const segmentId = args.resetSla ? "sla-segment-reset-f11-001" : "sla-segment-reopen-f11-001";
    const segment: SlaSegment = {
      segment_id: segmentId,
      segmentId,
      kind: "reopen",
      status: "active",
      started_at: startedAt,
      startedAt,
      due_at: dueAt,
      dueAt,
      reset_sla: args.resetSla,
      resetSla: args.resetSla,
      visible_to_operator: true,
      visibleToOperator: true,
      reason_code: args.reasonCode,
      reasonCode: args.reasonCode,
      ...(args.approvalId ? { approval_id: args.approvalId, approvalId: args.approvalId } : {}),
    };

    this.handoff.status = "reopened";
    this.handoff.current_sla_due_at = dueAt;
    this.handoff.currentSlaDueAt = dueAt;
    this.handoff.sla.due_at = dueAt;
    this.handoff.sla.dueAt = dueAt;
    this.handoff.sla.reset_count = args.resetSla ? this.handoff.sla.reset_count + 1 : this.handoff.sla.reset_count;
    this.handoff.sla.resetCount = this.handoff.sla.reset_count;
    this.handoff.sla_segments[0].status = "closed";
    this.handoff.slaSegments[0].status = "closed";
    this.handoff.sla_segments.push(segment);
    this.handoff.slaSegments = this.handoff.sla_segments;
    this.handoff.audit_events.unshift({
      action: args.resetSla ? "handoff.reopen_sla_reset" : "handoff.reopen",
      actor: OPERATOR_ID,
      approvalId: args.approvalId || undefined,
      target: HANDOFF_ID,
      ts: startedAt,
    });
    this.handoff.auditEvents = this.handoff.audit_events;

    return clone(segment);
  }

  private publishReopenedEvent(segment: SlaSegment): void {
    this.eventSeq += 1;
    const event = {
      id: `evt-f11-handoff-reopened-${this.eventSeq}`,
      schemaVersion: 1,
      channel: "handoff",
      type: "handoff.reopened",
      occurredAt: segment.startedAt,
      correlationId: `corr-f11-handoff-${this.eventSeq}`,
      payload: {
        handoffId: HANDOFF_ID,
        reasonCode: segment.reasonCode,
        resetSla: segment.resetSla,
        slaSegment: segment,
      },
    };
    const block = [
      `id: ${event.id}`,
      "event: handoff.reopened",
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
    res.writeHead(status, {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
    });
    res.end(JSON.stringify(body));
  }
}

function makeInitialHandoff(): HandoffDto {
  const initialSegment: SlaSegment = {
    segment_id: "sla-segment-initial-f11-001",
    segmentId: "sla-segment-initial-f11-001",
    kind: "initial",
    status: "active",
    started_at: "2026-05-13T10:00:00Z",
    startedAt: "2026-05-13T10:00:00Z",
    due_at: ORIGINAL_DUE_AT,
    dueAt: ORIGINAL_DUE_AT,
    reset_sla: false,
    resetSla: false,
    visible_to_operator: true,
    visibleToOperator: true,
    reason_code: "INITIAL_HANDOFF",
    reasonCode: "INITIAL_HANDOFF",
  };
  return {
    handoff_id: HANDOFF_ID,
    handoffId: HANDOFF_ID,
    status: "closed",
    current_sla_due_at: ORIGINAL_DUE_AT,
    currentSlaDueAt: ORIGINAL_DUE_AT,
    sla: {
      due_at: ORIGINAL_DUE_AT,
      dueAt: ORIGINAL_DUE_AT,
      reset_count: 0,
      resetCount: 0,
    },
    sla_segments: [initialSegment],
    slaSegments: [initialSegment],
    audit_events: [],
    auditEvents: [],
  };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function header(req: IncomingMessage, name: string): string | undefined {
  const value = req.headers[name.toLowerCase()];
  return Array.isArray(value) ? value[0] : value;
}

function stringField(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

async function readJsonBody(req: IncomingMessage): Promise<JsonRecord> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  const raw = Buffer.concat(chunks).toString("utf8").trim();
  if (!raw) {
    return {};
  }
  const parsed = JSON.parse(raw) as unknown;
  return parsed && typeof parsed === "object" && !Array.isArray(parsed)
    ? (parsed as JsonRecord)
    : {};
}

function unwrapHandoff(value: unknown): HandoffDto {
  const body = value as JsonRecord;
  const data = (body.data ?? body) as JsonRecord;
  return (data.handoff ?? data) as HandoffDto;
}

function segmentsOf(handoff: HandoffDto): SlaSegment[] {
  return (handoff.slaSegments ?? handoff.sla_segments) as SlaSegment[];
}

async function postReopen(
  page: Page,
  baseUrl: string,
  body: JsonRecord,
): Promise<BrowserResponse> {
  return page.evaluate(
    async ({ baseUrl: pageBaseUrl, body: requestBody, handoffId, operatorId }) => {
      const response = await fetch(`${pageBaseUrl}/bff/handoffs/${handoffId}/reopen`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${operatorId}:operator,approver:mfa`,
          "Content-Type": "application/json",
          "Idempotency-Key": `f11-${crypto.randomUUID()}`,
          "X-Correlation-Id": "corr-f11-handoff-sla",
          "X-Trace-Id": "trace-f11-handoff-sla",
        },
        body: JSON.stringify(requestBody),
      });
      return {
        status: response.status,
        body: (await response.json()) as Record<string, unknown>,
      };
    },
    { baseUrl, body, handoffId: HANDOFF_ID, operatorId: OPERATOR_ID },
  );
}

async function installHandoffEventRecorder(page: Page, baseUrl: string): Promise<void> {
  await page.goto(`${baseUrl}/test-shell`);
  await page.evaluate((pageBaseUrl) => {
    const events: Array<Record<string, unknown>> = [];
    const source = new EventSource(`${pageBaseUrl}/bff/events/stream?channel=handoff`);
    source.addEventListener("handoff.reopened", (event) => {
      events.push(JSON.parse((event as MessageEvent<string>).data) as Record<string, unknown>);
    });
    (window as any).__handoffEvents = events;
    (window as any).__handoffEventSource = source;
  }, baseUrl);
}

async function renderHandoffSegments(page: Page, baseUrl: string): Promise<void> {
  await page.goto(`${baseUrl}/test-shell`);
  await page.evaluate(async ({ pageBaseUrl, handoffId }) => {
    async function render(): Promise<void> {
      const response = await fetch(`${pageBaseUrl}/bff/handoffs/${handoffId}`, {
        headers: { Accept: "application/json" },
      });
      const body = (await response.json()) as {
        data: HandoffDto;
      };
      const handoff = body.data;
      const segments = handoff.slaSegments ?? handoff.sla_segments;
      document.body.innerHTML = [
        "<main>",
        `<h1>F11 Handoff SLA ${handoff.handoffId ?? handoff.handoff_id}</h1>`,
        `<p>Current SLA due ${handoff.currentSlaDueAt ?? handoff.current_sla_due_at}</p>`,
        "<section aria-label=\"SLA segments\">",
        "<h2>SlaSegment</h2>",
        "<ul>",
        ...segments.map(
          (segment) =>
            `<li data-testid="sla-segment">${segment.segmentId ?? segment.segment_id} ${segment.kind} ${segment.status} due=${segment.dueAt ?? segment.due_at} reset=${segment.resetSla ?? segment.reset_sla} reason=${segment.reasonCode ?? segment.reason_code ?? ""} approval=${segment.approvalId ?? segment.approval_id ?? ""}</li>`,
        ),
        "</ul>",
        "</section>",
        "</main>",
      ].join("");
      (window as any).__handoff = handoff;
    }
    await render();
    (window as any).__renderHandoff = render;
  }, { pageBaseUrl: baseUrl, handoffId: HANDOFF_ID });
}

let harness: HandoffSlaHarness;

test.beforeEach(async () => {
  harness = new HandoffSlaHarness();
  await harness.start();
});

test.afterEach(async () => {
  await harness.stop();
});

test.describe("F11 handoff reopen SLA", () => {
  test("reopen defaults to preserving the original SLA deadline", async ({ page }) => {
    await installHandoffEventRecorder(page, harness.baseUrl);

    const result = await postReopen(page, harness.baseUrl, {
      reasonCode: "REQUESTER_UPDATED_CONTEXT",
    });

    expect(result.status, JSON.stringify(result.body)).toBe(202);
    const handoff = unwrapHandoff(result.body);
    expect(handoff.currentSlaDueAt).toBe(ORIGINAL_DUE_AT);
    expect(handoff.sla.resetCount).toBe(0);

    const segments = segmentsOf(handoff);
    expect(segments).toHaveLength(2);
    expect(segments[0]).toMatchObject({
      segmentId: "sla-segment-initial-f11-001",
      dueAt: ORIGINAL_DUE_AT,
      resetSla: false,
    });
    expect(segments[1]).toMatchObject({
      segmentId: "sla-segment-reopen-f11-001",
      dueAt: ORIGINAL_DUE_AT,
      resetSla: false,
      visibleToOperator: true,
      reasonCode: "REQUESTER_UPDATED_CONTEXT",
    });

    await expect
      .poll(
        () =>
          page.evaluate(
            () => ((window as any).__handoffEvents as Array<Record<string, unknown>>).length,
          ),
        { message: "handoff.reopened SSE event should be published", timeout: 5_000 },
      )
      .toBeGreaterThan(0);

    const events = await page.evaluate(
      () => (window as any).__handoffEvents as Array<Record<string, unknown>>,
    );
    expect(events[0]).toMatchObject({
      channel: "handoff",
      type: "handoff.reopened",
      payload: {
        handoffId: HANDOFF_ID,
        resetSla: false,
        slaSegment: {
          dueAt: ORIGINAL_DUE_AT,
          segmentId: "sla-segment-reopen-f11-001",
        },
      },
    });
  });

  test("reset SLA without approval evidence returns APPROVAL_REQUIRED", async ({ page }) => {
    await page.goto(`${harness.baseUrl}/test-shell`);

    const result = await postReopen(page, harness.baseUrl, {
      reasonCode: "SLA_CLOCK_RESET_REQUESTED",
      resetSla: true,
    });

    expect(result.status, JSON.stringify(result.body)).toBe(409);
    expect(result.body).toMatchObject({
      error: {
        code: "APPROVAL_REQUIRED",
        details: {
          handoffId: HANDOFF_ID,
          kind: "approval",
          resetSla: true,
          requires_approval: true,
        },
      },
    });

    const snapshot = harness.snapshot();
    expect(snapshot.currentSlaDueAt).toBe(ORIGINAL_DUE_AT);
    expect(snapshot.sla.resetCount).toBe(0);
    expect(segmentsOf(snapshot)).toHaveLength(1);
  });

  test("approved reset appends a visible SlaSegment", async ({ page }) => {
    await renderHandoffSegments(page, harness.baseUrl);
    await expect(page.getByText("sla-segment-initial-f11-001")).toBeVisible();

    const result = await postReopen(page, harness.baseUrl, {
      approvalId: RESET_APPROVAL_ID,
      reasonCode: "RESET_APPROVED_BY_GOVERNANCE",
      resetSla: true,
    });
    expect(result.status, JSON.stringify(result.body)).toBe(202);
    const handoff = unwrapHandoff(result.body);
    expect(handoff.currentSlaDueAt).toBe(RESET_DUE_AT);
    expect(handoff.sla.resetCount).toBe(1);
    expect(segmentsOf(handoff)[1]).toMatchObject({
      approvalId: RESET_APPROVAL_ID,
      dueAt: RESET_DUE_AT,
      reasonCode: "RESET_APPROVED_BY_GOVERNANCE",
      resetSla: true,
      segmentId: "sla-segment-reset-f11-001",
      visibleToOperator: true,
    });

    await page.evaluate(() => (window as any).__renderHandoff());

    await expect(page.getByRole("heading", { name: "SlaSegment" })).toBeVisible();
    await expect(page.getByText("sla-segment-reset-f11-001")).toBeVisible();
    await expect(page.getByText(`due=${RESET_DUE_AT}`)).toBeVisible();
    await expect(page.getByText(`approval=${RESET_APPROVAL_ID}`)).toBeVisible();
    await expect(page.getByText("reason=RESET_APPROVED_BY_GOVERNANCE")).toBeVisible();
    await expect(page.locator("[data-testid='sla-segment']")).toHaveCount(2);
  });
});
