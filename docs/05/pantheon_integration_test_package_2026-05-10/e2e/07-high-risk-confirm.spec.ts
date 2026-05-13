import { test, expect } from "@playwright/test";
import { collectBffNetwork, expectNoFailedBffRequests, waitForBffIdle } from "./helpers/bff";

test("F09 high-risk surfaces do not show requires_* as success text", async ({ page }) => {
  const net = collectBffNetwork(page);
  await page.goto("/management/strategies");
  await waitForBffIdle(page);

  await expectNoFailedBffRequests(net);
  await expect(page.locator("body")).not.toContainText("requires_confirm_token");
  await expect(page.locator("body")).not.toContainText("requires_approval");
  await expect(page.locator("body")).not.toContainText("requires_two_man");
});
