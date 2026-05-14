/**
 * FE-INT-GATE-C04 / F16 - audit and correlation chain.
 *
 * Coverage:
 *   1. Browser writes send X-Request-Id and receive the same value back.
 *   2. X-Correlation-Id stays consistent across request, response, audit log,
 *      and audit SSE.
 *   3. The durable audit event and audit SSE event share correlationId.
 *   4. Mock overlay audit renders only in mock mode and uses an ephemeral badge.
 *
 * Runner: Playwright browser test with a local BFF/SSE contract harness.
 */

import { expect, test, type Page } from "@playwright/test";
import {
  createServer,
  type IncomingHttpHeaders,
  type IncomingMessage,
  type Server,
  type ServerResponse,
} from "node:http";
import type { AddressInfo } from "node:net";

import { mutationAuthHeaders } from "./helpers/auth";
import {
  OPERATOR_DEV_ID,
  STRATEGY_DEV_ID,
  seededCorrelationId,
  seededIdempotencyKey,
  seededRequestId,
} from "./helpers/fixtures";
import {
  closeSse,
  installSseController,
  sseEvents,
  waitForSseOpen,
} from "./helpers/sse";

const SNAPSHOT_AT = "2026-05-13T15:10:00Z";
const SSE_STATE_NAME = "__pantheonF16AuditSse";
const AUDIT_ACTION_PATH = `/bff/strategies/${STRATEGY_DEV_ID}/audit-actions`;
const MOCK_BADGE_TEST_ID = "mock-audit-ephemeral-badge";

type JsonRecord = Record<string, unknown>;
type SourceMode = "live" | "mock";

type OverlayBadge = {
  kind: "mock_overlay_audit";
  label: string;
  ephemeral: true;
  ttlMs: number;
};

type AuditEntry = {
  audit_id: string;
  auditId: string;
  action: string;
  actor_id: string;
  actorId: string;
  target_ref: string;
  targetRef: string;
  request_id: string;
  requestId: string;
  correlation_id: string;
  correlationId: string;
  trace_id: string;
  traceId: string;
  source_mode: SourceMode;
  sourceMode: SourceMode;
  mock_mode: boolean;
  mockMode: boolean;
  created_at: string;
  createdAt: string;
  overlayBadge?: OverlayBadge;
};

type RequestRecord = {
  authorization: string | undefined;
  body: JsonRecord;
  correlationId: string | undefined;
  idempotencyKey: string | undefined;
  method: string;
  path: string;
  requestId: string | undefined;
  sourceMode: string | undefined;
  traceId: string | undefined;
};

type BrowserAuditResult = {
  body: JsonRecord;
  responseHeaders: {
    correlationId: string | null;
    requestId: string | null;
  };
  status: number;
};

type AuditPostInput = {
  badgeTestId: string;
  body: JsonRecord;
  headers: Record<string, string>;
  path: string;
  sourceMode: SourceMode;
};

class AuditCorrelationHarness {
  private readonly server: Server;
  private readonly auditEntries: AuditEntry[] = [];
  private readonly openSseResponses: ServerResponse[] = [];
  private auditSeq = 0;
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
      if (!res.writableEnded) res.end();
    }
    await new Promise<void>((resolve, reject) => {
      this.server.close((error) => (error ? reject(error) : resolve()));
    });
  }

  private async handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const url = new URL(req.url ?? "/", this.baseUrl || "http://127.0.0.1");

    if (url.pathname === "/" || url.pathname === "/test-shell") {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(
        [
          "<!doctype html>",
          "<title>F16 Audit Correlation contract</title>",
          "<main id=\"app\" aria-label=\"F16 audit correlation contract\"></main>",
        ].join(""),
      );
      return;
    }

    if (url.pathname === "/bff/events/stream") {
      this.handleSse(req, res, url);
      return;
    }

    if (url.pathname === AUDIT_ACTION_PATH && req.method === "POST") {
      await this.handleAuditAction(req, res, url.pathname);
      return;
    }

    if (url.pathname === "/bff/audit" && req.method === "GET") {
      const correlationId =
        url.searchParams.get("correlation_id") ?? url.searchParams.get("correlationId");
      const targetRef = url.searchParams.get("target_ref") ?? url.searchParams.get("targetRef");
      const items = this.auditEntries.filter((entry) => {
        if (correlationId && entry.correlationId !== correlationId) return false;
        if (targetRef && entry.targetRef !== targetRef) return false;
        return true;
      });
      this.fulfillJson(res, 200, {
        data: items,
        items,
        meta: {
          contract: "FE-INT-GATE-C04",
          snapshot_at: SNAPSHOT_AT,
          totalCountExact: true,
        },
      });
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
    if (url.searchParams.get("channel") !== "audit") {
      this.fulfillJson(res, 400, {
        error: { code: "INVALID_PARAMS", message: "Unsupported SSE channel" },
      });
      return;
    }

    res.writeHead(200, {
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "Content-Type": "text/event-stream",
      "X-Accel-Buffering": "no",
      "X-SSE-Channel": "audit",
      "X-SSE-Replay-Supported": "true",
    });

    this.openSseResponses.push(res);
    req.on("close", () => {
      const index = this.openSseResponses.indexOf(res);
      if (index >= 0) this.openSseResponses.splice(index, 1);
    });
    res.write(": connected\n\n");
  }

  private async handleAuditAction(
    req: IncomingMessage,
    res: ServerResponse,
    path: string,
  ): Promise<void> {
    const body = await readJsonBody(req);
    const requestId = headerValue(req.headers, "x-request-id") || "req-f16-generated";
    const correlationId =
      headerValue(req.headers, "x-correlation-id") || "corr-f16-generated";
    const traceId = headerValue(req.headers, "x-trace-id") || `trace-${correlationId}`;
    const sourceMode = normalizeSourceMode(
      headerValue(req.headers, "x-pantheon-source-mode") || stringField(body.sourceMode),
    );

    this.requests.push({
      authorization: headerValue(req.headers, "authorization"),
      body,
      correlationId,
      idempotencyKey: headerValue(req.headers, "idempotency-key"),
      method: req.method ?? "",
      path,
      requestId,
      sourceMode,
      traceId,
    });

    const auditEntry = this.appendAuditEntry({
      action: stringField(body.action) || "strategy.audit.updated",
      correlationId,
      requestId,
      sourceMode,
      targetRef: stringField(body.targetRef ?? body.target_ref) || `strategy:${STRATEGY_DEV_ID}`,
      traceId,
    });
    this.publishAuditEvent(auditEntry);

    this.fulfillJson(
      res,
      202,
      {
        status: "accepted",
        data: {
          audit_event: auditEntry,
          auditEvent: auditEntry,
          auditId: auditEntry.auditId,
          correlationId,
          requestId,
          targetRef: auditEntry.targetRef,
        },
        meta: {
          contract: "FE-INT-GATE-C04",
          durable: true,
          echo: {
            correlationId,
            requestId,
          },
          overlay: {
            badge: auditEntry.overlayBadge ?? null,
            sourceMode,
          },
          snapshot_at: SNAPSHOT_AT,
        },
      },
      { "X-Correlation-Id": correlationId, "X-Request-Id": requestId },
    );
  }

  private appendAuditEntry(input: {
    action: string;
    correlationId: string;
    requestId: string;
    sourceMode: SourceMode;
    targetRef: string;
    traceId: string;
  }): AuditEntry {
    this.auditSeq += 1;
    const auditId = `audit-f16-${this.auditSeq}`;
    const overlayBadge =
      input.sourceMode === "mock"
        ? {
            kind: "mock_overlay_audit" as const,
            label: "Mock audit",
            ephemeral: true as const,
            ttlMs: 600,
          }
        : undefined;
    const entry: AuditEntry = {
      audit_id: auditId,
      auditId,
      action: input.action,
      actor_id: OPERATOR_DEV_ID,
      actorId: OPERATOR_DEV_ID,
      target_ref: input.targetRef,
      targetRef: input.targetRef,
      request_id: input.requestId,
      requestId: input.requestId,
      correlation_id: input.correlationId,
      correlationId: input.correlationId,
      trace_id: input.traceId,
      traceId: input.traceId,
      source_mode: input.sourceMode,
      sourceMode: input.sourceMode,
      mock_mode: input.sourceMode === "mock",
      mockMode: input.sourceMode === "mock",
      created_at: SNAPSHOT_AT,
      createdAt: SNAPSHOT_AT,
      ...(overlayBadge ? { overlayBadge } : {}),
    };
    this.auditEntries.unshift(entry);
    return entry;
  }

  private publishAuditEvent(entry: AuditEntry): void {
    this.eventSeq += 1;
    const eventId = `evt-f16-audit-${this.eventSeq}`;
    const envelope = {
      id: eventId,
      type: "audit.event",
      timestamp: SNAPSHOT_AT,
      data: {
        auditEvent: entry,
        auditId: entry.auditId,
        correlationId: entry.correlationId,
        requestId: entry.requestId,
        sourceMode: entry.sourceMode,
        targetRef: entry.targetRef,
        ...(entry.overlayBadge ? { overlayBadge: entry.overlayBadge } : {}),
      },
    };
    const block = [
      `id: ${eventId}`,
      "event: audit.event",
      `data: ${JSON.stringify(envelope)}`,
      "",
      "",
    ].join("\n");

    for (const res of [...this.openSseResponses]) {
      if (!res.writableEnded) res.write(block);
    }
  }

  private fulfillJson(
    res: ServerResponse,
    status: number,
    body: unknown,
    extraHeaders: Record<string, string> = {},
  ): void {
    res.writeHead(status, {
      "Content-Type": "application/json; charset=utf-8",
      "Access-Control-Expose-Headers": "X-Correlation-Id, X-Request-Id",
      "X-BFF-Api-Version": "2026-05-13",
      ...extraHeaders,
    });
    res.end(JSON.stringify(body));
  }
}

function normalizeSourceMode(value: string): SourceMode {
  return value === "mock" ? "mock" : "live";
}

function headerValue(headers: IncomingHttpHeaders, name: string): string | undefined {
  const value = headers[name.toLowerCase()];
  return Array.isArray(value) ? value[0] : value;
}

async function readJsonBody(req: IncomingMessage): Promise<JsonRecord> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  if (!chunks.length) return {};
  const parsed = JSON.parse(Buffer.concat(chunks).toString("utf8")) as unknown;
  expect(isRecord(parsed), "request body must be an object").toBe(true);
  return parsed as JsonRecord;
}

function isRecord(value: unknown): value is JsonRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function recordAt(value: unknown, label: string): JsonRecord {
  expect(isRecord(value), `${label} must be an object`).toBe(true);
  return value as JsonRecord;
}

function arrayAt(value: unknown, label: string): unknown[] {
  expect(Array.isArray(value), `${label} must be an array`).toBe(true);
  return value as unknown[];
}

function stringField(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function auditEntryFromResponse(value: unknown): AuditEntry {
  const body = recordAt(value, "audit response");
  const data = recordAt(body.data, "audit response.data");
  return recordAt(data.auditEvent ?? data.audit_event, "audit response.data.auditEvent") as AuditEntry;
}

function headersFor(sourceMode: SourceMode, suffix: string): Record<string, string> {
  const correlationId = seededCorrelationId(`f16-${suffix}`);
  return mutationAuthHeaders({
    extra: {
      "Idempotency-Key": seededIdempotencyKey("strategies", `f16-${suffix}`),
      "X-Correlation-Id": correlationId,
      "X-Pantheon-Source-Mode": sourceMode,
      "X-Request-Id": seededRequestId(`f16-${suffix}`),
      "X-Trace-Id": `trace-${correlationId}`,
    },
  });
}

async function postAuditActionFromBrowser(
  page: Page,
  sourceMode: SourceMode,
  suffix: string,
): Promise<BrowserAuditResult> {
  const input: AuditPostInput = {
    badgeTestId: MOCK_BADGE_TEST_ID,
    path: AUDIT_ACTION_PATH,
    sourceMode,
    headers: headersFor(sourceMode, suffix),
    body: {
      action: "strategy.audit.updated",
      correlationId: seededCorrelationId(`f16-${suffix}`),
      sourceMode,
      targetRef: `strategy:${STRATEGY_DEV_ID}`,
    },
  };

  return page.evaluate(async ({ badgeTestId, body, headers, path, sourceMode: mode }) => {
    const response = await fetch(path, {
      body: JSON.stringify(body),
      credentials: "include",
      headers,
      method: "POST",
    });
    const parsed = (await response.json()) as JsonRecord;
    const auditEvent = (
      (parsed.data as JsonRecord | undefined)?.auditEvent ??
      (parsed.data as JsonRecord | undefined)?.audit_event
    ) as JsonRecord | undefined;
    const overlayBadge = auditEvent?.overlayBadge as JsonRecord | undefined;

    if (mode === "mock" && overlayBadge?.ephemeral === true) {
      const badge = document.createElement("div");
      badge.dataset.testid = badgeTestId;
      badge.dataset.correlationId = String(auditEvent?.correlationId ?? "");
      badge.textContent = `${String(overlayBadge.label ?? "Mock audit")} ${String(
        auditEvent?.auditId ?? "",
      )}`;
      document.body.appendChild(badge);
      window.setTimeout(() => badge.remove(), Number(overlayBadge.ttlMs ?? 600));
    }

    return {
      body: parsed,
      responseHeaders: {
        correlationId: response.headers.get("X-Correlation-Id"),
        requestId: response.headers.get("X-Request-Id"),
      },
      status: response.status,
    };
  }, input);
}

async function getAuditLog(page: Page, correlationId: string): Promise<JsonRecord> {
  return page.evaluate(async (id) => {
    const response = await fetch(`/bff/audit?correlation_id=${encodeURIComponent(id)}`, {
      headers: { Accept: "application/json" },
    });
    return (await response.json()) as JsonRecord;
  }, correlationId);
}

async function auditSsePayload(page: Page, auditId: string): Promise<JsonRecord | null> {
  const events = await sseEvents(page, SSE_STATE_NAME);
  const event = events.find(
    (item) => item.type === "audit.event" && item.data.auditId === auditId,
  );
  return event?.data ?? null;
}

let harness: AuditCorrelationHarness;

test.beforeEach(async () => {
  harness = new AuditCorrelationHarness();
  await harness.start();
});

test.afterEach(async ({ page }) => {
  await closeSse(page, SSE_STATE_NAME).catch(() => undefined);
  await harness.stop();
});

test.describe("F16 audit and correlation chain", () => {
  test("echoes request ids and keeps correlation ids aligned through audit and SSE", async ({
    page,
  }) => {
    await page.goto(`${harness.baseUrl}/test-shell`);
    await installSseController(page, {
      baseUrl: harness.baseUrl,
      channel: "audit",
      eventTypes: ["audit.event"],
      path: "/bff/events/stream",
      stateName: SSE_STATE_NAME,
    });
    await waitForSseOpen(page, SSE_STATE_NAME);

    const suffix = "chain";
    const expectedRequestId = seededRequestId(`f16-${suffix}`);
    const expectedCorrelationId = seededCorrelationId(`f16-${suffix}`);
    const result = await postAuditActionFromBrowser(page, "live", suffix);

    expect(result.status, JSON.stringify(result.body)).toBe(202);
    expect(result.responseHeaders.requestId, "X-Request-Id response echo").toBe(
      expectedRequestId,
    );
    expect(result.responseHeaders.correlationId, "X-Correlation-Id response echo").toBe(
      expectedCorrelationId,
    );

    const request = harness.requests.at(-1);
    expect(request?.authorization, "Authorization header").toMatch(/^Bearer /);
    expect(request?.idempotencyKey, "Idempotency-Key header").toBe(
      seededIdempotencyKey("strategies", `f16-${suffix}`),
    );
    expect(request?.requestId, "X-Request-Id request header").toBe(expectedRequestId);
    expect(request?.correlationId, "X-Correlation-Id request header").toBe(
      expectedCorrelationId,
    );

    const responseAudit = auditEntryFromResponse(result.body);
    expect(responseAudit.requestId, "response audit requestId").toBe(expectedRequestId);
    expect(responseAudit.correlationId, "response audit correlationId").toBe(
      expectedCorrelationId,
    );
    expect(responseAudit.mockMode, "live audit is not a mock overlay").toBe(false);

    const auditLog = await getAuditLog(page, expectedCorrelationId);
    const auditItems = arrayAt(auditLog.items, "audit log items") as AuditEntry[];
    expect(auditItems).toHaveLength(1);
    expect(auditItems[0]).toMatchObject({
      auditId: responseAudit.auditId,
      correlationId: expectedCorrelationId,
      requestId: expectedRequestId,
      targetRef: `strategy:${STRATEGY_DEV_ID}`,
    });

    await expect
      .poll(() => auditSsePayload(page, responseAudit.auditId), {
        message: "audit.event SSE payload should share request/correlation ids",
        timeout: 5_000,
      })
      .toMatchObject({
        auditId: responseAudit.auditId,
        correlationId: expectedCorrelationId,
        requestId: expectedRequestId,
        targetRef: `strategy:${STRATEGY_DEV_ID}`,
      });

    await expect(page.getByTestId(MOCK_BADGE_TEST_ID)).toHaveCount(0);
  });

  test("renders ephemeral overlay audit badge only for mock mode", async ({ page }) => {
    await page.goto(`${harness.baseUrl}/test-shell`);

    const liveResult = await postAuditActionFromBrowser(page, "live", "overlay-live");
    expect(liveResult.status, JSON.stringify(liveResult.body)).toBe(202);
    const liveAudit = auditEntryFromResponse(liveResult.body);
    expect(liveAudit.mockMode, "live audit mockMode").toBe(false);
    expect(liveAudit.overlayBadge, "live audit overlay badge").toBeUndefined();
    await expect(page.getByTestId(MOCK_BADGE_TEST_ID)).toHaveCount(0);

    const mockResult = await postAuditActionFromBrowser(page, "mock", "overlay-mock");
    expect(mockResult.status, JSON.stringify(mockResult.body)).toBe(202);
    const mockAudit = auditEntryFromResponse(mockResult.body);
    expect(mockAudit).toMatchObject({
      correlationId: seededCorrelationId("f16-overlay-mock"),
      mockMode: true,
      sourceMode: "mock",
      overlayBadge: {
        ephemeral: true,
        kind: "mock_overlay_audit",
        ttlMs: 600,
      },
    });

    const badge = page.getByTestId(MOCK_BADGE_TEST_ID);
    await expect(badge).toBeVisible();
    await expect(badge).toContainText(mockAudit.auditId);
    await expect(badge).toHaveAttribute("data-correlation-id", mockAudit.correlationId);
    await expect
      .poll(() => page.getByTestId(MOCK_BADGE_TEST_ID).count(), {
        message: "mock overlay audit badge should expire",
        timeout: 3_000,
      })
      .toBe(0);
  });
});
