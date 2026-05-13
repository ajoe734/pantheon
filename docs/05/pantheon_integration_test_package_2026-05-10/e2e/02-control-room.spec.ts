import { test, expect } from "@playwright/test";
import { collectBffNetwork, expectNoFailedBffRequests, expectBffRouteWasCalled, waitForBffIdle } from "./helpers/bff";

test("F02 Control Room renders loop / sentinel / intervention overview", async ({ page }) => {
  const net = collectBffNetwork(page);
  await page.goto("/management/control-room");
  await waitForBffIdle(page);

  await expectNoFailedBffRequests(net);
  await expect(page.locator("body")).toContainText(/Control Room|loops|Sentinel|Intervention|Findings/i);
  await expectBffRouteWasCalled(net, "/bff/v5/control-room");
});
