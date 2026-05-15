import type { Page, Request, Response } from "@playwright/test";
import { expect } from "@playwright/test";
import { BFF_BASE_URL } from "./env";

export type BffNetworkEvent = {
  method: string;
  url: string;
  status?: number;
  failed?: string;
};

export function collectBffNetwork(page: Page): {
  requests: BffNetworkEvent[];
  responses: BffNetworkEvent[];
  failures: BffNetworkEvent[];
} {
  const requests: BffNetworkEvent[] = [];
  const responses: BffNetworkEvent[] = [];
  const failures: BffNetworkEvent[] = [];

  page.on("request", (req: Request) => {
    if (req.url().startsWith(BFF_BASE_URL)) {
      requests.push({ method: req.method(), url: req.url() });
    }
  });

  page.on("response", (res: Response) => {
    if (res.url().startsWith(BFF_BASE_URL)) {
      responses.push({ method: res.request().method(), url: res.url(), status: res.status() });
    }
  });

  page.on("requestfailed", (req: Request) => {
    if (req.url().startsWith(BFF_BASE_URL)) {
      failures.push({ method: req.method(), url: req.url(), failed: req.failure()?.errorText });
    }
  });

  return { requests, responses, failures };
}

export async function expectNoFailedBffRequests(events: ReturnType<typeof collectBffNetwork>) {
  expect(events.failures, `BFF request failures: ${JSON.stringify(events.failures, null, 2)}`).toEqual([]);
}

export async function expectBffRouteWasCalled(events: ReturnType<typeof collectBffNetwork>, pathPart: string) {
  const hit = events.requests.some(r => r.url.includes(pathPart)) || events.responses.some(r => r.url.includes(pathPart));
  expect(hit, `Expected BFF route containing ${pathPart} to be called`).toBe(true);
}

export async function waitForBffIdle(page: Page) {
  await page.waitForLoadState("networkidle", { timeout: 30_000 }).catch(() => {});
}
