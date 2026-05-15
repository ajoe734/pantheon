import { test, expect } from "@playwright/test";
import { collectBffNetwork, expectNoFailedBffRequests, expectBffRouteWasCalled, waitForBffIdle } from "./helpers/bff";

test("F03 Execution Loop renders persona trading health", async ({ page }) => {
  const net = collectBffNetwork(page);
  await page.goto("/management/loops/execution?focus=personas");
  await waitForBffIdle(page);

  await expectNoFailedBffRequests(net);
  await expect(page.locator("body")).toContainText(/Execution|Persona|Health|status|score/i);
  await expectBffRouteWasCalled(net, "/bff/v5/execution/persona-health");
});
