import { test, expect } from "@playwright/test";
import { collectBffNetwork, expectNoFailedBffRequests, expectBffRouteWasCalled, waitForBffIdle } from "./helpers/bff";

test("F01 startup / session bootstrap reaches BFF and renders management shell", async ({ page }) => {
  const net = collectBffNetwork(page);
  await page.goto("/management");
  await waitForBffIdle(page);

  await expectNoFailedBffRequests(net);
  await expect(page.locator("body")).toContainText(/Control Room|Command Center|Management|Pantheon/i);
  await expectBffRouteWasCalled(net, "/bff/me");
  await expectBffRouteWasCalled(net, "/bff/events/stream");
});
