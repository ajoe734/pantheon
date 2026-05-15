import { test, expect } from "@playwright/test";
import { collectBffNetwork, expectNoFailedBffRequests, expectBffRouteWasCalled, waitForBffIdle } from "./helpers/bff";

const registries = [
  ["/management/strategies", "/bff/strategies", /Strategy|Strategies|策略/i],
  ["/management/personas", "/bff/personas", /Persona|Personas|人格/i],
  ["/management/capital", "/bff/capital-pools", /Capital|資金/i],
  ["/management/deployments", "/bff/deployments", /Deployment|Deployments|部署/i],
  ["/management/artifacts", "/bff/artifacts", /Artifact|Artifacts|產物/i],
] as const;

for (const [route, bffPath, text] of registries) {
  test(`F07 registry renders ${route}`, async ({ page }) => {
    const net = collectBffNetwork(page);
    await page.goto(route);
    await waitForBffIdle(page);

    await expectNoFailedBffRequests(net);
    await expect(page.locator("body")).toContainText(text);
    await expectBffRouteWasCalled(net, bffPath);
  });
}
