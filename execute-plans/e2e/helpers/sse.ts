export const DEFAULT_SSE_PATH = "/bff/events/stream";
export const DEFAULT_SSE_STATE_NAME = "__pantheonSse";
export const LAST_EVENT_ID_HEADER = "Last-Event-ID";
export const LAST_EVENT_ID_QUERY_PARAM = "last_event_id";

export type SseBrowserEvent = {
  data: Record<string, unknown>;
  id: string;
  lastEventId: string;
  rawData: string;
  type: string;
};

export type SseBrowserState = {
  connectionUrls: string[];
  errors: number;
  events: SseBrowserEvent[];
  lastEventId: string;
  opens: number;
  readyState: number;
  resyncFetches: Array<{ route: string; status: number }>;
};

export type InstallSseControllerOptions = {
  appendLastEventIdQuery?: boolean;
  baseUrl?: string;
  channel?: string;
  eventTypes?: string[];
  initialLastEventId?: string;
  path?: string;
  resyncOnSystemRequired?: boolean;
  stateName?: string;
  withCredentials?: boolean;
};

export type QuietEventSourceOptions = {
  autoOpen?: boolean;
  stateName?: string;
};

export type E2ePage = {
  addInitScript<Arg>(
    script: (arg: Arg) => unknown | Promise<unknown>,
    arg: Arg,
  ): Promise<void>;
  evaluate<Result>(script: () => Result | Promise<Result>): Promise<Result>;
  evaluate<Result, Arg>(
    script: (arg: Arg) => Result | Promise<Result>,
    arg: Arg,
  ): Promise<Result>;
  waitForFunction<Arg>(
    script: (arg: Arg) => unknown,
    arg: Arg,
    options?: { timeout?: number },
  ): Promise<unknown>;
};

const DEFAULT_EVENT_TYPES = [
  "approval.created",
  "approval.decided",
  "audit.event",
  "intervention.decided",
  "system.connected",
  "system.heartbeat",
  "system.resync_required",
] as const;

export function appendLastEventId(
  url: string,
  lastEventId: string,
  paramName = LAST_EVENT_ID_QUERY_PARAM,
): string {
  if (!lastEventId) return url;
  const parsed = new URL(url, "http://pantheon.local");
  parsed.searchParams.set(paramName, lastEventId);
  if (/^https?:\/\//i.test(url)) return parsed.toString();
  return `${parsed.pathname}${parsed.search}${parsed.hash}`;
}

export function lastEventIdFromHeaders(
  headers: Headers | Record<string, string | string[] | undefined>,
): string | undefined {
  if (typeof (headers as Headers).get === "function") {
    return (
      (headers as Headers).get(LAST_EVENT_ID_HEADER) ??
      (headers as Headers).get(LAST_EVENT_ID_HEADER.toLowerCase()) ??
      undefined
    );
  }
  const record = headers as Record<string, string | string[] | undefined>;
  const value =
    record[LAST_EVENT_ID_HEADER] ??
    record[LAST_EVENT_ID_HEADER.toLowerCase()] ??
    record["Last-Event-Id"] ??
    record["last-event-id"];
  return Array.isArray(value) ? value[0] : value;
}

export function formatSseBlock(
  id: string,
  type: string,
  data: Record<string, unknown>,
  options: { retryMs?: number } = {},
): string {
  const payload = {
    id,
    type,
    timestamp: "2026-05-13T00:00:00Z",
    data,
  };
  return [
    options.retryMs === undefined ? undefined : `retry: ${options.retryMs}`,
    `id: ${id}`,
    `event: ${type}`,
    `data: ${JSON.stringify(payload)}`,
    "",
    "",
  ]
    .filter((line) => line !== undefined)
    .join("\n");
}

export async function installSseController(
  page: E2ePage,
  options: InstallSseControllerOptions = {},
): Promise<void> {
  const stateName = options.stateName ?? DEFAULT_SSE_STATE_NAME;
  await page.evaluate(
    ({
      appendLastEventIdQuery,
      baseUrl,
      channel,
      eventTypes,
      initialLastEventId,
      path,
      resyncOnSystemRequired,
      stateName: browserStateName,
      withCredentials,
    }) => {
      const state: SseBrowserState = {
        connectionUrls: [] as string[],
        errors: 0,
        events: [] as Array<{
          data: Record<string, unknown>;
          id: string;
          lastEventId: string;
          rawData: string;
          type: string;
        }>,
        lastEventId: initialLastEventId ?? "",
        opens: 0,
        readyState: EventSource.CLOSED,
        resyncFetches: [] as Array<{ route: string; status: number }>,
      };
      let source: EventSource | undefined;

      function streamUrl(): string {
        const url = new URL(path, baseUrl || window.location.href);
        if (channel) url.searchParams.set("channel", channel);
        if (appendLastEventIdQuery && state.lastEventId) {
          url.searchParams.set("last_event_id", state.lastEventId);
        }
        return url.toString();
      }

      async function handleEvent(event: MessageEvent<string>): Promise<void> {
        if (!event.data) return;
        let parsed: {
          data?: Record<string, unknown>;
          id?: string;
          payload?: Record<string, unknown>;
          type?: string;
        } = {};
        try {
          parsed = JSON.parse(event.data) as typeof parsed;
        } catch {
          parsed = {};
        }
        const id = parsed.id || event.lastEventId || "";
        const type = parsed.type || event.type;
        const data = parsed.data || parsed.payload || {};
        if (id) state.lastEventId = id;
        state.events.push({
          data,
          id,
          lastEventId: event.lastEventId,
          rawData: event.data,
          type,
        });

        if (resyncOnSystemRequired && type === "system.resync_required") {
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
        source = new EventSource(url, { withCredentials });
        state.readyState = source.readyState;
        source.onopen = () => {
          state.opens += 1;
          state.readyState = source?.readyState ?? EventSource.OPEN;
        };
        source.onerror = () => {
          state.errors += 1;
          state.readyState = source?.readyState ?? EventSource.CLOSED;
        };
        source.onmessage = (event) => {
          void handleEvent(event);
        };
        for (const type of eventTypes) {
          source.addEventListener(type, (event) => {
            void handleEvent(event as MessageEvent<string>);
          });
        }
      }

      (window as unknown as Record<string, unknown>)[browserStateName] = {
        clearLastEventId: () => {
          state.lastEventId = "";
        },
        close: () => {
          source?.close();
          state.readyState = EventSource.CLOSED;
        },
        connect,
        reconnect: (nextLastEventId?: string) => {
          if (nextLastEventId !== undefined) state.lastEventId = nextLastEventId;
          connect();
        },
        setLastEventId: (nextLastEventId: string) => {
          state.lastEventId = nextLastEventId;
        },
        state,
      };

      connect();
    },
    {
      appendLastEventIdQuery: options.appendLastEventIdQuery ?? false,
      baseUrl: options.baseUrl,
      channel: options.channel ?? "system",
      eventTypes: options.eventTypes ?? [...DEFAULT_EVENT_TYPES],
      initialLastEventId: options.initialLastEventId ?? "",
      path: options.path ?? DEFAULT_SSE_PATH,
      resyncOnSystemRequired: options.resyncOnSystemRequired ?? false,
      stateName,
      withCredentials: options.withCredentials ?? true,
    },
  );
}

export async function installQuietEventSource(
  page: E2ePage,
  options: QuietEventSourceOptions = {},
): Promise<void> {
  await page.addInitScript(
    ({ autoOpen, stateName }) => {
      const instances: Array<EventSource & { emit?: (type: string, data: unknown, id?: string) => void }> = [];

      class QuietPantheonEventSource extends EventTarget {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSED = 2;

        readonly url: string;
        readonly withCredentials: boolean;
        readyState = QuietPantheonEventSource.CONNECTING;
        onerror: ((event: Event) => void) | null = null;
        onmessage: ((event: MessageEvent) => void) | null = null;
        onopen: ((event: Event) => void) | null = null;

        constructor(url: string | URL, init?: EventSourceInit) {
          super();
          this.url = String(url);
          this.withCredentials = init?.withCredentials ?? false;
          instances.push(this as unknown as EventSource);
          if (autoOpen) {
            queueMicrotask(() => {
              if (this.readyState === QuietPantheonEventSource.CLOSED) return;
              this.readyState = QuietPantheonEventSource.OPEN;
              const event = new Event("open");
              this.onopen?.(event);
              this.dispatchEvent(event);
            });
          }
        }

        close(): void {
          this.readyState = QuietPantheonEventSource.CLOSED;
        }

        emit(type: string, data: unknown, id = ""): void {
          const event = new MessageEvent(type, {
            data: typeof data === "string" ? data : JSON.stringify(data),
            lastEventId: id,
          });
          if (type === "message") this.onmessage?.(event);
          this.dispatchEvent(event);
        }
      }

      (window as unknown as { EventSource: typeof EventSource }).EventSource =
        QuietPantheonEventSource as unknown as typeof EventSource;
      (window as unknown as Record<string, unknown>)[stateName] = {
        closeAll: () => instances.forEach((source) => source.close()),
        emit: (type: string, data: unknown, id?: string) =>
          instances.forEach((source) => source.emit?.(type, data, id)),
        instances,
      };
    },
    {
      autoOpen: options.autoOpen ?? true,
      stateName: options.stateName ?? `${DEFAULT_SSE_STATE_NAME}Quiet`,
    },
  );
}

export async function sseState(
  page: E2ePage,
  stateName = DEFAULT_SSE_STATE_NAME,
): Promise<SseBrowserState> {
  return page.evaluate((name) => {
    const controller = (window as unknown as Record<string, { state: SseBrowserState }>)[name];
    return controller.state;
  }, stateName);
}

export async function sseEvents(
  page: E2ePage,
  stateName = DEFAULT_SSE_STATE_NAME,
): Promise<SseBrowserEvent[]> {
  return sseState(page, stateName).then((state) => state.events);
}

export async function setLastEventId(
  page: E2ePage,
  lastEventId: string,
  stateName = DEFAULT_SSE_STATE_NAME,
): Promise<void> {
  await page.evaluate(
    ({ name, nextLastEventId }) => {
      const controller = (window as unknown as Record<string, { setLastEventId: (id: string) => void }>)[
        name
      ];
      controller.setLastEventId(nextLastEventId);
    },
    { name: stateName, nextLastEventId: lastEventId },
  );
}

export async function clearLastEventId(
  page: E2ePage,
  stateName = DEFAULT_SSE_STATE_NAME,
): Promise<void> {
  await page.evaluate((name) => {
    const controller = (window as unknown as Record<string, { clearLastEventId: () => void }>)[name];
    controller.clearLastEventId();
  }, stateName);
}

export async function reconnectSse(
  page: E2ePage,
  lastEventId?: string,
  stateName = DEFAULT_SSE_STATE_NAME,
): Promise<void> {
  await page.evaluate(
    ({ name, nextLastEventId }) => {
      const controller = (window as unknown as Record<string, { reconnect: (id?: string) => void }>)[
        name
      ];
      controller.reconnect(nextLastEventId);
    },
    { name: stateName, nextLastEventId: lastEventId },
  );
}

export async function closeSse(
  page: E2ePage,
  stateName = DEFAULT_SSE_STATE_NAME,
): Promise<void> {
  await page.evaluate((name) => {
    const controller = (window as unknown as Record<string, { close: () => void }>)[name];
    controller.close();
  }, stateName);
}

export async function waitForSseOpen(
  page: E2ePage,
  stateName = DEFAULT_SSE_STATE_NAME,
  timeout = 5_000,
): Promise<void> {
  await page.waitForFunction(
    (name) => {
      const controller = (window as unknown as Record<string, { state?: SseBrowserState }>)[name];
      return (controller?.state?.opens ?? 0) > 0;
    },
    stateName,
    { timeout },
  );
}

export async function browserEventSourceOpenState(page: E2ePage): Promise<number> {
  return page.evaluate(() => EventSource.OPEN);
}
