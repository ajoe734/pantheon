import { test, expect } from "@playwright/test";

const pages = [
  "/management/control-room",
  "/management/loops/research",
  "/management/loops/execution",
  "/management/loops/optimization",
  "/management/sentinel",
  "/management/interventions",
];

for (const route of pages) {
  test(`F17 a11y smoke route loads ${route}`, async ({ page }) => {
    await page.goto(route);
    await page.waitForLoadState("networkidle").catch(() => {});
    await expect(page.locator("body")).toBeVisible();

    // Lightweight smoke in this file. For full axe assertions, install axe-core in browser context
    // or use @axe-core/playwright. The repo already has axe-core for Vitest; this route smoke
    // ensures v5 pages mount before deeper axe checks.
    await expect(page.locator("[role='alert']").first()).not.toContainText(/undefined|null/i);
  });
}
