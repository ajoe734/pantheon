import { test, expect } from "@playwright/test";

test("F15 live fallback banner is not visible on healthy strict/hybrid startup", async ({ page }) => {
  await page.goto("/management/control-room");
  await page.waitForLoadState("networkidle").catch(() => {});
  const banner = page.getByText(/live BFF unavailable|serving mock data/i);
  await expect(banner).toHaveCount(0);
});
