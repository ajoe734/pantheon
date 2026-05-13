import { test, expect } from "@playwright/test";
import { collectBffNetwork, expectBffRouteWasCalled, waitForBffIdle } from "./helpers/bff";

test("F14 SSE stream is opened by platform shell", async ({ page }) => {
  const net = collectBffNetwork(page);
  await page.goto("/management/control-room");
  await waitForBffIdle(page);

  await expectBffRouteWasCalled(net, "/bff/events/stream");
  await expect(page.locator("body")).toBeVisible();
});
