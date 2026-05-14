/**
 * FE-INT-GATE-D03 / F13 - Agora signal, ask, and journal contracts.
 *
 * Coverage:
 *   1. Signal feedback writes an audit record and publishes a signal SSE event.
 *   2. Ask creates a session, streams ask.message.delta events, and exposes the
 *      completed transcript through the REST session read.
 *   3. Journal writes use application/merge-patch+json and reject invalid
 *      patches atomically.
 *
 * Runner: Playwright browser test with a local BFF/SSE contract harness.
 */

import { expect, test, type Page } from "@playwright/test";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";

const SIGNAL_ID = "signal-f13-alpha-001";
const JOURNAL_ID = "journal-f13-decision-001";
const OPERATOR_ID = "op-fe-gate";
const ASK_SESSION_ID = "ask-session-f13-001";
const ASK_MESSAGE_ID = "ask-message-f13-assistant-001";
const SNAPSHOT_AT = "2026-05-13T14:45:00Z";
const ASK_SSE_AVAILABLE = process.env.F13_AGORA_ASK_SSE_AVAILABLE !== "0";

type JsonRecord = Record<string, unknown>;

type SseChannel = "ask" | "audit" | "signal";

type RequestRecord = {
  body: JsonRecord;
  contentType: string | undefined;
  idempotencyKey: string | undefined;
  method: string;
  path: string;
};

type AuditEntry = {
  audit_id: string;
  auditId: string;
  action: string;
  actor_id: string;
  actorId: string;
  target_ref: string;
  targetRef: string;
  trace_id: string;
  traceId: string;
  created_at: string;
  createdAt: string;
};

type FeedbackResponse = {
  status: "accepted";
  data: {
    feedback_id: string;
    feedbackId: string;
    signal_id: string;
    signalId: string;
    decision: "agree" | "disagree" | "flag_suspicious";
    confidence: number;
    audit_entry: AuditEntry;
    auditEntry: AuditEntry;
  };
  meta: {
    durable: true;
    liveCapitalSideEffects: false;
  };
};

type AskMessage = {
  message_id: string;
  messageId: string;
  role: "operator" | "assistant";
  content: string;
  seq: number;
  created_at: string;
  createdAt: string;
};

type AskSession = {
  session_id: string;
  sessionId: string;
  status: "active" | "completed";
  prompt: string;
  transcript: AskMessage[];
  messages: AskMessage[];
  updated_at: string;
  updatedAt: string;
};

type JournalEntry = {
  entry_id: string;
  entryId: string;
  title: string;
  outcome: "pending" | "good" | "neutral" | "bad";
  rationale: string;
  updated_at: string;
  updatedAt: string;
  version: number;
};

type BrowserResponse = {
  body: JsonRecord;
  status: number;
};

type F13TestState = {
  events: Array<{ channel: string; id: string; payload: Record<string, unknown>; type: string }>;
  opens: Record<string, number>;
};

type F13Window = Window & {
  __pantheonF13?: {
    close: () => void;
    getJson: (path: string) => Promise<BrowserResponse>;
    patchJournal: (body: Record<string, unknown>) => Promise<BrowserResponse>;
    postJson: (path: string, body: Record<string, unknown>) => Promise<BrowserResponse>;
    state: F13TestState;
  };
};

class AgoraHarness {
  private readonly server: Server;
  private readonly openSseResponses = new Map<SseChannel, ServerResponse[]>();
  private readonly auditEntries: AuditEntry[] = [];
  private readonly askSessions = new Map<string, AskSession>();
  private journal: JournalEntry;
  private auditSeq = 0;
  private eventSeq = 0;

  readonly requests: RequestRecord[] = [];
  baseUrl = "";

  constructor() {
    this.journal = initialJournal();
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
    for (const responses of this.openSseResponses.values()) {
      for (const res of [...responses]) {
        if (!res.writableEnded) {
          res.end();
        }
      }
    }
    await new Promise<void>((resolve, reject) => {
      this.server.close((error) => (error ? reject(error) : resolve()));
    });
  }

  snapshotJournal(): JournalEntry {
    return clone(this.journal);
  }

  private async handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const url = new URL(req.url ?? "/", this.baseUrl || "http://127.0.0.1");

    if (url.pathname === "/" || url.pathname === "/test-shell") {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end("<!doctype html><title>F13 Agora contract</title><main id=\"app\"></main>");
      return;
    }

    if (url.pathname === "/bff/events/stream") {
      this.handleSse(req, res, url);
      return;
    }

    if (
      url.pathname === `/bff/agora/signals/${SIGNAL_ID}/feedback` &&
      req.method === "POST"
    ) {
      await this.handleSignalFeedback(req, res, url.pathname);
      return;
    }

    if (url.pathname === "/bff/audit" && req.method === "GET") {
      const targetRef = url.searchParams.get("target_ref") ?? "";
      const items = targetRef
        ? this.auditEntries.filter((entry) => entry.target_ref === targetRef)
        : this.auditEntries;
      this.fulfillJson(res, 200, {
        data: items,
        items,
        meta: { totalCountExact: true, contract: "FE-INT-GATE-D03" },
      });
      return;
    }

    if (url.pathname === "/bff/agora/ask" && req.method === "POST") {
      await this.handleAsk(req, res, url.pathname);
      return;
    }

    if (
      url.pathname === `/bff/agora/ask/sessions/${ASK_SESSION_ID}` &&
      req.method === "GET"
    ) {
      const session = this.askSessions.get(ASK_SESSION_ID);
      if (!session) {
        this.fulfillJson(res, 404, {
          error: { code: "RESOURCE_NOT_FOUND", message: "Ask session was not found" },
        });
        return;
      }
      this.fulfillJson(res, 200, {
        data: session,
        meta: { route: "GET /bff/agora/ask/sessions/{id}", contract: "FE-INT-GATE-D03" },
      });
      return;
    }

    if (url.pathname === `/bff/agora/journal/${JOURNAL_ID}` && req.method === "GET") {
      this.fulfillJson(res, 200, {
        data: this.journal,
        meta: { etag: `W/"${this.journal.version}"`, contract: "FE-INT-GATE-D03" },
      });
      return;
    }

    if (url.pathname === `/bff/agora/journal/${JOURNAL_ID}` && req.method === "PATCH") {
      await this.handleJournalPatch(req, res, url.pathname);
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
    const channel = url.searchParams.get("channel") as SseChannel | null;
    if (!channel || !["ask", "audit", "signal"].includes(channel)) {
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
      "X-SSE-Channel": channel,
      "X-SSE-Replay-Supported": "true",
    });

    const responses = this.openSseResponses.get(channel) ?? [];
    responses.push(res);
    this.openSseResponses.set(channel, responses);
    req.on("close", () => {
      const open = this.openSseResponses.get(channel) ?? [];
      this.openSseResponses.set(
        channel,
        open.filter((entry) => entry !== res),
      );
    });
    res.write(": connected\n\n");
  }

  private async handleSignalFeedback(
    req: IncomingMessage,
    res: ServerResponse,
    path: string,
  ): Promise<void> {
    const body = await readJsonBody(req);
    const request = this.recordRequest(req, path, body);
    const decision = stringField(body.decision) || "agree";
    const confidence = Number(body.confidence ?? 0);

    const auditEntry = this.appendAudit({
      action: "agora.signal_feedback.recorded",
      targetRef: `signal:${SIGNAL_ID}`,
      traceId: stringField(body.traceId ?? body.trace_id) || "trace-f13-feedback",
    });
    const response: FeedbackResponse = {
      status: "accepted",
      data: {
        feedback_id: "feedback-f13-001",
        feedbackId: "feedback-f13-001",
        signal_id: SIGNAL_ID,
        signalId: SIGNAL_ID,
        decision: decision as FeedbackResponse["data"]["decision"],
        confidence,
        audit_entry: auditEntry,
        auditEntry,
      },
      meta: {
        durable: true,
        liveCapitalSideEffects: false,
      },
    };

    this.publish("signal", "signal.feedback.recorded", {
      signalId: SIGNAL_ID,
      feedbackId: response.data.feedbackId,
      decision,
      confidence,
      auditId: auditEntry.auditId,
      idempotencyKey: request.idempotencyKey,
    });
    this.publish("audit", "operator.audit.updated", {
      auditEntry,
      targetRef: auditEntry.targetRef,
    });
    this.fulfillJson(res, 202, response);
  }

  private async handleAsk(
    req: IncomingMessage,
    res: ServerResponse,
    path: string,
  ): Promise<void> {
    const body = await readJsonBody(req);
    this.recordRequest(req, path, body);
    const prompt = stringField(body.prompt) || "Explain the signal drift";
    const answerDeltas = ["Earnings revisions lifted ", "forward momentum risk."];
    const assistantContent = answerDeltas.join("");
    const session: AskSession = {
      session_id: ASK_SESSION_ID,
      sessionId: ASK_SESSION_ID,
      status: "completed",
      prompt,
      transcript: [
        {
          message_id: "ask-message-f13-operator-001",
          messageId: "ask-message-f13-operator-001",
          role: "operator",
          content: prompt,
          seq: 0,
          created_at: SNAPSHOT_AT,
          createdAt: SNAPSHOT_AT,
        },
        {
          message_id: ASK_MESSAGE_ID,
          messageId: ASK_MESSAGE_ID,
          role: "assistant",
          content: assistantContent,
          seq: 1,
          created_at: SNAPSHOT_AT,
          createdAt: SNAPSHOT_AT,
        },
      ],
      messages: [],
      updated_at: SNAPSHOT_AT,
      updatedAt: SNAPSHOT_AT,
    };
    session.messages = session.transcript;
    this.askSessions.set(ASK_SESSION_ID, session);

    this.publish("ask", "ask.session.started", {
      sessionId: ASK_SESSION_ID,
      personaIds: ["persona-risk-f13"],
      at: SNAPSHOT_AT,
    });
    for (const [index, delta] of answerDeltas.entries()) {
      this.publish("ask", "ask.message.delta", {
        sessionId: ASK_SESSION_ID,
        messageId: ASK_MESSAGE_ID,
        personaId: "persona-risk-f13",
        delta,
        seq: index + 1,
        at: SNAPSHOT_AT,
      });
    }
    this.publish("ask", "ask.message.completed", {
      sessionId: ASK_SESSION_ID,
      messageId: ASK_MESSAGE_ID,
      at: SNAPSHOT_AT,
    });

    this.fulfillJson(res, 202, {
      status: "accepted",
      data: {
        session_id: ASK_SESSION_ID,
        sessionId: ASK_SESSION_ID,
        status: "streaming",
      },
      meta: {
        transcriptUrl: `/bff/agora/ask/sessions/${ASK_SESSION_ID}`,
        contract: "FE-INT-GATE-D03",
      },
    });
  }

  private async handleJournalPatch(
    req: IncomingMessage,
    res: ServerResponse,
    path: string,
  ): Promise<void> {
    const body = await readJsonBody(req);
    const request = this.recordRequest(req, path, body);
    if (!request.contentType?.toLowerCase().startsWith("application/merge-patch+json")) {
      this.fulfillJson(res, 415, {
        error: {
          code: "UNSUPPORTED_MEDIA_TYPE",
          message: "Journal PATCH requires application/merge-patch+json",
        },
      });
      return;
    }

    const nextOutcome = body.outcome === undefined ? this.journal.outcome : stringField(body.outcome);
    if (!["pending", "good", "neutral", "bad"].includes(nextOutcome)) {
      this.fulfillJson(res, 422, {
        error: {
          code: "INVALID_PARAMS",
          message: "Invalid journal outcome; patch rejected atomically",
          details: {
            atomic: true,
            rejectedField: "outcome",
          },
        },
      });
      return;
    }

    const nextJournal: JournalEntry = {
      ...this.journal,
      ...(typeof body.title === "string" ? { title: body.title } : {}),
      ...(typeof body.rationale === "string" ? { rationale: body.rationale } : {}),
      outcome: nextOutcome as JournalEntry["outcome"],
      updated_at: SNAPSHOT_AT,
      updatedAt: SNAPSHOT_AT,
      version: this.journal.version + 1,
    };
    this.journal = nextJournal;
    this.fulfillJson(res, 200, {
      data: nextJournal,
      meta: {
        atomic: true,
        contentType: request.contentType,
        etag: `W/"${nextJournal.version}"`,
      },
    });
  }

  private appendAudit(args: { action: string; targetRef: string; traceId: string }): AuditEntry {
    this.auditSeq += 1;
    const auditId = `audit-f13-${this.auditSeq}`;
    const entry: AuditEntry = {
      audit_id: auditId,
      auditId,
      action: args.action,
      actor_id: OPERATOR_ID,
      actorId: OPERATOR_ID,
      target_ref: args.targetRef,
      targetRef: args.targetRef,
      trace_id: args.traceId,
      traceId: args.traceId,
      created_at: SNAPSHOT_AT,
      createdAt: SNAPSHOT_AT,
    };
    this.auditEntries.unshift(entry);
    return clone(entry);
  }

  private publish(channel: SseChannel, type: string, payload: JsonRecord): void {
    this.eventSeq += 1;
    const event = {
      id: `evt-f13-${channel}-${this.eventSeq}`,
      schemaVersion: 1,
      channel,
      type,
      occurredAt: SNAPSHOT_AT,
      payload,
    };
    const block = [
      `id: ${event.id}`,
      `event: ${type}`,
      `data: ${JSON.stringify(event)}`,
      "",
      "",
    ].join("\n");
    for (const res of this.openSseResponses.get(channel) ?? []) {
      if (!res.writableEnded) {
        res.write(block);
      }
    }
  }

  private recordRequest(req: IncomingMessage, path: string, body: JsonRecord): RequestRecord {
    const record: RequestRecord = {
      body,
      contentType: header(req, "content-type"),
      idempotencyKey: header(req, "idempotency-key"),
      method: req.method ?? "",
      path,
    };
    this.requests.push(record);
    return record;
  }

  private fulfillJson(res: ServerResponse, status: number, body: unknown): void {
    res.writeHead(status, {
      "Access-Control-Allow-Origin": "*",
      "Content-Type": "application/json; charset=utf-8",
    });
    res.end(JSON.stringify(body));
  }
}

function initialJournal(): JournalEntry {
  return {
    entry_id: JOURNAL_ID,
    entryId: JOURNAL_ID,
    title: "Investigate signal drift",
    outcome: "pending",
    rationale: "Initial decision requires Agora review.",
    updated_at: "2026-05-13T13:00:00Z",
    updatedAt: "2026-05-13T13:00:00Z",
    version: 1,
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
  if (!raw) return {};
  const parsed = JSON.parse(raw) as unknown;
  return parsed && typeof parsed === "object" && !Array.isArray(parsed)
    ? (parsed as JsonRecord)
    : {};
}

function recordAt(value: unknown, label: string): JsonRecord {
  expect(value !== null && typeof value === "object" && !Array.isArray(value), label).toBe(true);
  return value as JsonRecord;
}

function dataAt(value: unknown, label: string): JsonRecord {
  const body = recordAt(value, label);
  return recordAt(body.data ?? body, `${label}.data`);
}

async function installAgoraClient(page: Page, baseUrl: string): Promise<void> {
  await page.goto(`${baseUrl}/test-shell`);
  await page.evaluate(({ journalId, pageBaseUrl, operatorId }) => {
    type StoredEvent = {
      channel: string;
      id: string;
      payload: Record<string, unknown>;
      type: string;
    };
    const state = {
      events: [] as StoredEvent[],
      opens: { ask: 0, audit: 0, signal: 0 } as Record<string, number>,
    };
    const sources: EventSource[] = [];

    function connect(channel: "ask" | "audit" | "signal", events: string[]) {
      const source = new EventSource(`${pageBaseUrl}/bff/events/stream?channel=${channel}`);
      source.onopen = () => {
        state.opens[channel] += 1;
      };
      const record = (event: MessageEvent<string>) => {
        if (!event.data) return;
        const envelope = JSON.parse(event.data) as {
          channel?: string;
          id?: string;
          payload?: Record<string, unknown>;
          type?: string;
        };
        state.events.push({
          channel: String(envelope.channel ?? channel),
          id: String(envelope.id ?? event.lastEventId),
          payload: envelope.payload ?? {},
          type: String(envelope.type ?? event.type),
        });
      };
      for (const eventName of events) {
        source.addEventListener(eventName, (event) => record(event as MessageEvent<string>));
      }
      source.onmessage = record;
      sources.push(source);
    }

    connect("signal", ["signal.feedback.recorded"]);
    connect("audit", ["operator.audit.updated"]);
    connect("ask", ["ask.session.started", "ask.message.delta", "ask.message.completed"]);

    async function postJson(path: string, body: Record<string, unknown>): Promise<BrowserResponse> {
      const response = await fetch(`${pageBaseUrl}${path}`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${operatorId}:operator,reviewer,approver:mfa`,
          "Content-Type": "application/json",
          "Idempotency-Key": `f13-${crypto.randomUUID()}`,
          "X-Correlation-Id": "corr-f13-agora",
          "X-Trace-Id": "trace-f13-agora",
        },
        body: JSON.stringify(body),
      });
      return {
        status: response.status,
        body: (await response.json()) as Record<string, unknown>,
      };
    }

    async function getJson(path: string): Promise<BrowserResponse> {
      const response = await fetch(`${pageBaseUrl}${path}`, {
        headers: { Accept: "application/json" },
      });
      return {
        status: response.status,
        body: (await response.json()) as Record<string, unknown>,
      };
    }

    async function patchJournal(body: Record<string, unknown>): Promise<BrowserResponse> {
      const response = await fetch(`${pageBaseUrl}/bff/agora/journal/${journalId}`, {
        method: "PATCH",
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${operatorId}:operator,reviewer,approver:mfa`,
          "Content-Type": "application/merge-patch+json",
          "Idempotency-Key": `f13-journal-${crypto.randomUUID()}`,
          "X-Correlation-Id": "corr-f13-journal",
          "X-Trace-Id": "trace-f13-journal",
        },
        body: JSON.stringify(body),
      });
      return {
        status: response.status,
        body: (await response.json()) as Record<string, unknown>,
      };
    }

    (window as unknown as F13Window).__pantheonF13 = {
      close: () => sources.forEach((source) => source.close()),
      getJson,
      patchJournal,
      postJson,
      state,
    };
  }, { journalId: JOURNAL_ID, pageBaseUrl: baseUrl, operatorId: OPERATOR_ID });
}

async function waitForSseOpen(page: Page, channel: SseChannel): Promise<void> {
  await expect
    .poll(
      () =>
        page.evaluate(
          (name) => (window as unknown as F13Window).__pantheonF13!.state.opens[name],
          channel,
        ),
      { message: `${channel} SSE stream should open`, timeout: 5_000 },
    )
    .toBeGreaterThan(0);
}

async function eventsByType(page: Page, type: string): Promise<JsonRecord[]> {
  return page.evaluate(
    (eventType) =>
      (window as unknown as F13Window).__pantheonF13!.state.events.filter(
        (event) => event.type === eventType,
      ),
    type,
  );
}

async function postJson(page: Page, path: string, body: JsonRecord): Promise<BrowserResponse> {
  return page.evaluate(
    ({ requestPath, payload }) => (window as unknown as F13Window).__pantheonF13!.postJson(requestPath, payload),
    { requestPath: path, payload: body },
  );
}

async function getJson(page: Page, path: string): Promise<BrowserResponse> {
  return page.evaluate(
    (requestPath) => (window as unknown as F13Window).__pantheonF13!.getJson(requestPath),
    path,
  );
}

async function patchJournal(page: Page, body: JsonRecord): Promise<BrowserResponse> {
  return page.evaluate(
    (payload) => (window as unknown as F13Window).__pantheonF13!.patchJournal(payload),
    body,
  );
}

let harness: AgoraHarness;

test.beforeEach(async () => {
  harness = new AgoraHarness();
  await harness.start();
});

test.afterEach(async ({ page }) => {
  await page.evaluate(() => (window as unknown as F13Window).__pantheonF13?.close?.()).catch(() => undefined);
  await harness.stop();
});

test.describe("F13 Agora signal, ask, and journal", () => {
  test("signal feedback writes audit evidence and publishes signal SSE", async ({ page }) => {
    await installAgoraClient(page, harness.baseUrl);
    await waitForSseOpen(page, "signal");
    await waitForSseOpen(page, "audit");

    const result = await postJson(page, `/bff/agora/signals/${SIGNAL_ID}/feedback`, {
      decision: "flag_suspicious",
      confidence: 4,
      reason: "Conviction exceeds the paper risk budget; require Agora review.",
      traceId: "trace-f13-feedback",
    });

    expect(result.status, JSON.stringify(result.body)).toBe(202);
    const feedback = dataAt(result.body, "feedback response") as FeedbackResponse["data"];
    expect(feedback).toMatchObject({
      signalId: SIGNAL_ID,
      decision: "flag_suspicious",
      confidence: 4,
      auditEntry: {
        action: "agora.signal_feedback.recorded",
        actorId: OPERATOR_ID,
        targetRef: `signal:${SIGNAL_ID}`,
        traceId: "trace-f13-feedback",
      },
    });

    const auditResult = await getJson(page, `/bff/audit?target_ref=signal:${SIGNAL_ID}`);
    expect(auditResult.status, JSON.stringify(auditResult.body)).toBe(200);
    const auditItems = auditResult.body.items as AuditEntry[];
    expect(auditItems).toHaveLength(1);
    expect(auditItems[0].auditId).toBe(feedback.auditEntry.auditId);

    await expect
      .poll(() => eventsByType(page, "signal.feedback.recorded"), {
        message: "signal.feedback.recorded SSE event should be published",
        timeout: 5_000,
      })
      .toContainEqual(
        expect.objectContaining({
          channel: "signal",
          payload: expect.objectContaining({
            auditId: feedback.auditEntry.auditId,
            confidence: 4,
            decision: "flag_suspicious",
            signalId: SIGNAL_ID,
          }),
          type: "signal.feedback.recorded",
        }),
      );

    const auditEvents = await eventsByType(page, "operator.audit.updated");
    expect(auditEvents).toContainEqual(
      expect.objectContaining({
        channel: "audit",
        payload: expect.objectContaining({
          targetRef: `signal:${SIGNAL_ID}`,
        }),
      }),
    );
  });

  test("ask streams deltas and exposes the completed transcript through REST", async ({
    page,
  }) => {
    test.skip(
      !ASK_SSE_AVAILABLE,
      "Set F13_AGORA_ASK_SSE_AVAILABLE=1 when the ask.message.delta SSE stream is available.",
    );

    await installAgoraClient(page, harness.baseUrl);
    await waitForSseOpen(page, "ask");

    const askResult = await postJson(page, "/bff/agora/ask", {
      prompt: "Explain why the signal drift changed.",
      personaIds: ["persona-risk-f13"],
    });
    expect(askResult.status, JSON.stringify(askResult.body)).toBe(202);
    expect(dataAt(askResult.body, "ask response")).toMatchObject({
      sessionId: ASK_SESSION_ID,
      status: "streaming",
    });

    await expect
      .poll(() => eventsByType(page, "ask.message.delta"), {
        message: "ask.message.delta events should be streamed",
        timeout: 5_000,
      })
      .toHaveLength(2);

    const deltaEvents = await eventsByType(page, "ask.message.delta");
    const deltas = deltaEvents
      .map((event) => recordAt(event.payload, "delta payload").delta)
      .join("");
    expect(deltas).toBe("Earnings revisions lifted forward momentum risk.");

    await expect
      .poll(() => eventsByType(page, "ask.message.completed"), {
        message: "ask.message.completed SSE event should be streamed",
        timeout: 5_000,
      })
      .toHaveLength(1);

    const transcriptResult = await getJson(page, `/bff/agora/ask/sessions/${ASK_SESSION_ID}`);
    expect(transcriptResult.status, JSON.stringify(transcriptResult.body)).toBe(200);
    const session = dataAt(transcriptResult.body, "ask transcript") as AskSession;
    expect(session.sessionId).toBe(ASK_SESSION_ID);
    expect(session.status).toBe("completed");
    expect(session.transcript).toContainEqual(
      expect.objectContaining({
        messageId: ASK_MESSAGE_ID,
        role: "assistant",
        content: deltas,
      }),
    );
  });

  test("journal PATCH uses merge-patch media type and rejects invalid patches atomically", async ({
    page,
  }) => {
    await installAgoraClient(page, harness.baseUrl);

    const patchResult = await patchJournal(page, {
      title: "Raise risk watch",
      outcome: "neutral",
      rationale: "Agora reviewed the signal and kept it in paper-only monitoring.",
    });
    expect(patchResult.status, JSON.stringify(patchResult.body)).toBe(200);
    const patched = dataAt(patchResult.body, "journal patch") as JournalEntry;
    expect(patched).toMatchObject({
      entryId: JOURNAL_ID,
      title: "Raise risk watch",
      outcome: "neutral",
      version: 2,
    });

    const journalPatchRequests = harness.requests.filter(
      (request) => request.method === "PATCH" && request.path === `/bff/agora/journal/${JOURNAL_ID}`,
    );
    expect(journalPatchRequests[0].contentType).toMatch(/^application\/merge-patch\+json/i);

    const invalidPatch = await patchJournal(page, {
      title: "This title must not be persisted",
      outcome: "impossible",
    });
    expect(invalidPatch.status, JSON.stringify(invalidPatch.body)).toBe(422);
    expect(invalidPatch.body).toMatchObject({
      error: {
        code: "INVALID_PARAMS",
        details: {
          atomic: true,
          rejectedField: "outcome",
        },
      },
    });

    const readback = await getJson(page, `/bff/agora/journal/${JOURNAL_ID}`);
    expect(readback.status, JSON.stringify(readback.body)).toBe(200);
    expect(dataAt(readback.body, "journal readback")).toMatchObject({
      entryId: JOURNAL_ID,
      title: "Raise risk watch",
      outcome: "neutral",
      version: 2,
    });
    expect(harness.snapshotJournal().title).toBe("Raise risk watch");
  });
});
