import { test, expect } from "@playwright/test";
import { collectBffNetwork, expectNoFailedBffRequests, expectBffRouteWasCalled, waitForBffIdle } from "./helpers/bff";

test("F06 Human Intervention Queue renders and reaches canonical endpoint", async ({ page }) => {
  const net = collectBffNetwork(page);
  await page.goto("/management/interventions");
  await waitForBffIdle(page);

  await expectNoFailedBffRequests(net);
  await expect(page.locator("body")).toContainText(/Intervention|approval|sentinel|incident|policy/i);
  await expectBffRouteWasCalled(net, "/bff/v5/interventions");
});
