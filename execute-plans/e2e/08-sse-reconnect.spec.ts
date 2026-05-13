/**
 * FE-INT-GATE-B07 - F14 SSE reconnect, replay cursor, and resync.
 *
 * Runner: Playwright browser test.
 *
 * This spec uses a local SSE test server because Playwright route.fulfill()
 * cannot hold a streaming EventSource response open. Resync GET routes are
 * still mocked with page.route so the assertions cover the frontend refetch
 * behavior after system.resync_required.
 */

import { expect, test, type Page, type Route } from "@playwright/test";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";

type SseRequestRecord = {
  url: string;
  channel: string | null;
  lastEventIdHeader: string | undefined;
  lastEventIdQuery: string | null;
};

type ResyncRequestRecord = {
  path: string;
  method: string;
  accept: string | undefined;
};

type OpenSseResponse = {
  req: IncomingMessage;
  res: ServerResponse;
};

class SseHarness {
  private server: Server;
  private openResponses: OpenSseResponse[] = [];

  readonly requests: SseRequestRecord[] = [];
  baseUrl = "";

  constructor() {
    this.server = createServer((req, res) => this.handle(req, res));
  }

  async start(): Promise<void> {
    await new Promise<void>((resolve) => {
      this.server.listen(0, "127.0.0.1", resolve);
    });
    const address = this.server.address() as AddressInfo;
    this.baseUrl = `http://127.0.0.1:${address.port}`;
  }

  async stop(): Promise<void> {
    this.dropOpenSseConnections();
    await new Promise<void>((resolve, reject) => {
      this.server.close((error) => (error ? reject(error) : resolve()));
    });
  }

  dropOpenSseConnections(): number {
    const responses = [...this.openResponses];
    this.openResponses = [];
    for (const { res } of responses) {
      if (!res.writableEnded) {
        res.end();
      }
    }
    return responses.length;
  }

  private handle(req: IncomingMessage, res: ServerResponse): void {
    const url = new URL(req.url ?? "/", this.baseUrl || "http://127.0.0.1");
    if (url.pathname === "/" || url.pathname === "/test-shell") {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end("<!doctype html><title>Pantheon SSE reconnect test</title>");
      return;
    }

    if (url.pathname === "/__test__/drop-sse") {
      const dropped = this.dropOpenSseConnections();
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ dropped }));
      return;
    }

    if (url.pathname === "/bff/events/stream") {
      this.handleSse(req, res, url);
      return;
    }

    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: { code: "RESOURCE_NOT_FOUND" } }));
  }

  private handleSse(req: IncomingMessage, res: ServerResponse, url: URL): void {
    const lastEventIdQuery = url.searchParams.get("last_event_id");
    const record: SseRequestRecord = {
      url: url.toString(),
      channel: url.searchParams.get("channel"),
      lastEventIdHeader: req.headers["last-event-id"] as string | undefined,
      lastEventIdQuery,
    };
    this.requests.push(record);

    res.writeHead(200, {
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "Content-Type": "text/event-stream",
      "X-Accel-Buffering": "no",
      "X-SSE-Channel": "approval",
      "X-SSE-Replay-Supported": "true",
    });

    this.openResponses.push({ req, res });
    req.on("close", () => {
      this.openResponses = this.openResponses.filter((entry) => entry.res !== res);
    });

    if (lastEventIdQuery === "evt-expired") {
      res.write(
        sseBlock("evt-resync-required", "system.resync_required", {
          reason: "SSE_REPLAY_HISTORY_MISSING",
          routes: ["/bff/approvals", "/bff/v5/interventions"],
        }),
      );
      return;
    }

    if (this.requests.length === 1) {
      res.write(
        sseBlock("evt-first", "system.connected", {
          channel: "approval",
          transport: "sse",
        }),
      );
      return;
    }

    res.write(
      sseBlock("evt-heartbeat", "system.heartbeat", {
        channel: "approval",
      }),
    );
  }
}

function sseBlock(id: string, type: string, data: Record<string, unknown>): string {
  const payload = {
    id,
    type,
    timestamp: "2026-05-13T00:00:00Z",
    data,
  };
  return [
    "retry: 20",
    `id: ${id}`,
    `event: ${type}`,
    `data: ${JSON.stringify(payload)}`,
    "",
    "",
  ].join("\n");
}

async function installBrowserSseClient(
  page: Page,
  options: { baseUrl: string; initialLastEventId?: string },
): Promise<void> {
  await page.goto(`${options.baseUrl}/test-shell`);
  await page.evaluate(
    ({ baseUrl, initialLastEventId }) => {
      const knownEventTypes = [
        "approval.created",
        "system.connected",
        "system.heartbeat",
        "system.resync_required",
      ];
      const state = {
        connectionUrls: [] as string[],
        events: [] as Array<{
          id: string;
          lastEventId: string;
          type: string;
          data: Record<string, unknown>;
        }>,
        errors: 0,
        lastEventId: initialLastEventId ?? "",
        opens: 0,
        resyncFetches: [] as Array<{ route: string; status: number }>,
      };
      let source: EventSource | undefined;

      function streamUrl(): string {
        const url = new URL("/bff/events/stream", baseUrl);
        url.searchParams.set("channel", "approval");
        if (state.lastEventId) {
          url.searchParams.set("last_event_id", state.lastEventId);
        }
        return url.toString();
      }

      async function handleEvent(event: MessageEvent<string>): Promise<void> {
        if (!event.data) return;
        const payload = JSON.parse(event.data) as {
          id?: string;
          type?: string;
          data?: Record<string, unknown>;
          payload?: Record<string, unknown>;
        };
        const id = payload.id || event.lastEventId;
        const type = payload.type || event.type;
        const data = payload.data || payload.payload || {};
        if (id) {
          state.lastEventId = id;
        }
        state.events.push({ id, lastEventId: event.lastEventId, type, data });

        if (type === "system.resync_required") {
          const routes = Array.isArray(data.routes) ? data.routes : [];
          for (const route of routes) {
            const response = await fetch(String(route), {
              credentials: "include",
              headers: { Accept: "application/json" },
            });
            state.resyncFetches.push({ route: String(route), status: response.status });
          }
          state.lastEventId = "";
          source?.close();
          connect();
        }
      }

      function connect(): void {
        source?.close();
        const url = streamUrl();
        state.connectionUrls.push(url);
        source = new EventSource(url, { withCredentials: true });
        source.onopen = () => {
          state.opens += 1;
        };
        source.onerror = () => {
          state.errors += 1;
        };
        source.onmessage = (event) => {
          void handleEvent(event);
        };
        for (const type of knownEventTypes) {
          source.addEventListener(type, (event) => {
            void handleEvent(event as MessageEvent<string>);
          });
        }
      }

      (window as any).__pantheonSseTest = {
        connect,
        forceCloseEventSource: () =>
          fetch("/__test__/drop-sse", { credentials: "include" }).then((response) =>
            response.json(),
          ),
        state,
      };

      connect();
    },
    {
      baseUrl: options.baseUrl,
      initialLastEventId: options.initialLastEventId ?? "",
    },
  );
}

async function getSseState(page: Page): Promise<any> {
  return page.evaluate(() => (window as any).__pantheonSseTest.state);
}

async function mockResyncRoute(
  page: Page,
  path: string,
  records: ResyncRequestRecord[],
): Promise<void> {
  await page.route(`**${path}`, async (route: Route) => {
    const request = route.request();
    records.push({
      path,
      method: request.method(),
      accept: request.headers()["accept"],
    });
    await route.fulfill({
      contentType: "application/json",
      json: { data: [], meta: { source: "page.route-resync-mock" } },
      status: 200,
    });
  });
}

test.describe("F14 SSE reconnect and replay", () => {
  let harness: SseHarness;

  test.beforeEach(async () => {
    harness = new SseHarness();
    await harness.start();
  });

  test.afterEach(async () => {
    await harness.stop();
  });

  test("forced EventSource transport close reconnects with Last-Event-ID and receives heartbeat", async ({
    page,
  }) => {
    await installBrowserSseClient(page, { baseUrl: harness.baseUrl });

    await expect
      .poll(() => getSseState(page).then((state) => state.lastEventId), {
        message: "initial SSE event id should be recorded before reconnect",
        timeout: 5_000,
      })
      .toBe("evt-first");

    const dropResult = await page.evaluate(() =>
      (window as any).__pantheonSseTest.forceCloseEventSource(),
    );
    expect(dropResult.dropped).toBeGreaterThan(0);

    await expect
      .poll(() => harness.requests.length, {
        message: "browser EventSource should reconnect after the forced close",
        timeout: 5_000,
      })
      .toBeGreaterThanOrEqual(2);

    const reconnect = harness.requests[1];
    expect(reconnect.channel).toBe("approval");
    expect(reconnect.lastEventIdHeader).toBe("evt-first");

    await expect
      .poll(
        () =>
          getSseState(page).then((state) =>
            state.events.some((event: { type: string }) => event.type === "system.heartbeat"),
          ),
        {
          message: "heartbeat event should be received after reconnect",
          timeout: 5_000,
        },
      )
      .toBe(true);
  });

  test("system.resync_required refetches resync routes and reconnects without stale cursor", async ({
    page,
  }) => {
    const resyncRequests: ResyncRequestRecord[] = [];
    await mockResyncRoute(page, "/bff/approvals", resyncRequests);
    await mockResyncRoute(page, "/bff/v5/interventions", resyncRequests);

    await installBrowserSseClient(page, {
      baseUrl: harness.baseUrl,
      initialLastEventId: "evt-expired",
    });

    await expect
      .poll(() => getSseState(page).then((state) => state.resyncFetches.length), {
        message: "system.resync_required should trigger both resync route fetches",
        timeout: 5_000,
      })
      .toBe(2);

    expect(resyncRequests.map((request) => request.path).sort()).toEqual([
      "/bff/approvals",
      "/bff/v5/interventions",
    ]);
    expect(resyncRequests.every((request) => request.method === "GET")).toBe(true);
    expect(resyncRequests.every((request) => request.accept?.includes("application/json"))).toBe(
      true,
    );

    await expect
      .poll(() => harness.requests.length, {
        message: "client should reconnect after completing resync fetches",
        timeout: 5_000,
      })
      .toBeGreaterThanOrEqual(2);

    expect(harness.requests[0].lastEventIdQuery).toBe("evt-expired");
    const reconnect = harness.requests[harness.requests.length - 1];
    expect(reconnect.lastEventIdQuery).toBeNull();
    expect(reconnect.lastEventIdHeader).toBeUndefined();

    await expect
      .poll(
        () =>
          getSseState(page).then((state) =>
            state.events.some((event: { type: string }) => event.type === "system.heartbeat"),
          ),
        {
          message: "post-resync reconnect should receive a heartbeat event",
          timeout: 5_000,
        },
      )
      .toBe(true);
  });
});
