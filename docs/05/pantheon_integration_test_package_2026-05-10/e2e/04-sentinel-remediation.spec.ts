import { test, expect } from "@playwright/test";
import { collectBffNetwork, expectNoFailedBffRequests, expectBffRouteWasCalled, waitForBffIdle } from "./helpers/bff";

test("F05 Sentinel findings list opens without BFF transport failure", async ({ page }) => {
  const net = collectBffNetwork(page);
  await page.goto("/management/sentinel");
  await waitForBffIdle(page);

  await expectNoFailedBffRequests(net);
  await expect(page.locator("body")).toContainText(/Sentinel|finding|severity|confidence|remediation/i);
  await expectBffRouteWasCalled(net, "/bff/v5/sentinel/findings");
});
