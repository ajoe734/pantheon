import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page, type Route } from "@playwright/test";

const CRASH_TEXT =
  /application error|cannot read properties|undefined is not|uncaught|traceback|typeerror|referenceerror/i;

const BLOCKING_IMPACTS = new Set(["critical", "serious"]);

const V5_PAGE_SCENARIOS = [
  {
    name: "control room",
    path: "/management/control-room",
    ready: /Control Room|loops|Sentinel|Intervention|Findings/i,
  },
  {
    name: "research loop",
    path: "/management/loops/research",
    ready: /Research|runs|review/i,
  },
  {
    name: "execution loop PersonaHealthMatrix",
    path: "/management/loops/execution?focus=personas",
    ready: /Execution|Persona|Health|score/i,
  },
  {
    name: "optimization loop",
    path: "/management/loops/optimization",
    ready: /Optimization|approval|rebalance|runs/i,
  },
  {
    name: "sentinel",
    path: "/management/sentinel",
    ready: /Sentinel|Findings|critical|confidence/i,
  },
  {
    name: "interventions",
    path: "/management/interventions",
    ready: /Interventions|Human Intervention Queue|approval|sentinel/i,
  },
] as const;

const NOW = "2026-05-13T14:45:00Z";

const loopRuns = [
  {
    id: "loop-exec-f17",
    loop_kind: "execution",
    status: "running",
    subject_kind: "strategy",
    subject_id: "strategy-alpha",
    subject_name: "Alpha Execution Loop",
    triggered_by: "scheduler",
    updated_at: NOW,
    stages: [
      { id: "exec-prepare", name: "Prepare", status: "succeeded", started_at: "2026-05-13T14:20:00Z", completed_at: "2026-05-13T14:25:00Z" },
      { id: "exec-route", name: "Route", status: "running", started_at: "2026-05-13T14:25:00Z" },
      { id: "exec-review", name: "Review", status: "pending" },
    ],
    current_stage_id: "exec-route",
    next_action: { kind: "automatic", label: "Monitor fills" },
  },
  {
    id: "loop-research-f17",
    loop_kind: "research",
    status: "blocked",
    subject_kind: "research",
    subject_id: "research-alpha",
    subject_name: "Research Baseline Review",
    triggered_by: "research_lead",
    updated_at: NOW,
    stages: [
      { id: "research-design", name: "Design", status: "succeeded", started_at: "2026-05-13T13:00:00Z", completed_at: "2026-05-13T13:10:00Z" },
      { id: "research-review", name: "Review", status: "blocked", started_at: "2026-05-13T13:10:00Z" },
    ],
    current_stage_id: "research-review",
    next_action: { kind: "awaiting_human_decision", label: "Approve feature set" },
  },
  {
    id: "loop-optimization-f17",
    loop_kind: "optimization",
    status: "running",
    subject_kind: "rebalance",
    subject_id: "rebalance-alpha",
    subject_name: "Optimization Rebalance",
    triggered_by: "capital_manager",
    updated_at: NOW,
    stages: [
      { id: "opt-solve", name: "Solve", status: "running", started_at: "2026-05-13T14:00:00Z" },
      { id: "opt-approval", name: "Approval", status: "pending" },
    ],
    current_stage_id: "opt-solve",
    next_action: { kind: "awaiting_approval", label: "Review approval" },
    evidence_refs: [{ kind: "approval", id: "approval-f17" }],
  },
];

const personaHealth = [
  {
    persona_id: "persona-alpha",
    persona_name: "Alpha Operator",
    mode: "live",
    status: "healthy",
    score: 92,
    routed_strategies: 4,
    open_findings: 0,
    updated_at: NOW,
  },
  {
    persona_id: "persona-beta",
    persona_name: "Beta Risk",
    mode: "shadow",
    status: "watch",
    score: 74,
    routed_strategies: 2,
    open_findings: 1,
    updated_at: NOW,
  },
];

const sentinelFindings = [
  {
    id: "finding-f17-critical",
    finding_id: "finding-f17-critical",
    title: "Critical routing drift",
    summary: "Persona routing drift requires emergency override review.",
    status: "open",
    severity: "critical",
    confidence: 0.93,
    source: "runtime",
    detected_at: "2026-05-13T14:10:00Z",
    updated_at: NOW,
    persona_ids: ["persona-beta"],
    recommended_action_ids: ["open_incident", "route_to_backup_runtime", "pause_persona_routing"],
  },
  {
    id: "finding-f17-watch",
    finding_id: "finding-f17-watch",
    title: "Execution quality watch",
    summary: "Execution quality dropped below the observation threshold.",
    status: "acknowledged",
    severity: "watch",
    confidence: 0.67,
    source: "persona-health",
    detected_at: "2026-05-13T13:50:00Z",
    updated_at: NOW,
    persona_ids: ["persona-alpha"],
    recommended_action_ids: ["open_incident"],
  },
];

const interventions = [
  {
    id: "intervention-f17-critical",
    intervention_id: "intervention-f17-critical",
    kind: "risk_breach",
    target_type: "persona",
    target_id: "persona-beta",
    description: "Critical routing drift needs operator decision.",
    triggered_at: "2026-05-13T14:12:00Z",
    updated_at: NOW,
  },
  {
    id: "intervention-f17-sentinel",
    intervention_id: "intervention-f17-sentinel",
    kind: "hiq_sentinel",
    target_type: "finding",
    target_id: "finding-f17-watch",
    description: "Sentinel finding requires human triage.",
    triggered_at: "2026-05-13T14:18:00Z",
    updated_at: NOW,
  },
];

type AxeViolation = {
  help: string;
  id: string;
  impact: string | null;
  nodes: Array<{
    failureSummary?: string;
    target: string[];
  }>;
};

function corsHeaders(route: Route): Record<string, string> {
  const origin = route.request().headers().origin ?? "*";
  return {
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Headers":
      "accept,authorization,content-type,idempotency-key,x-bff-api-version,x-correlation-id,x-confirm-token,x-idempotency-key,x-locale,x-request-id",
    "Access-Control-Allow-Methods": "GET,POST,PATCH,DELETE,OPTIONS",
    "Access-Control-Allow-Origin": origin,
    "X-BFF-Api-Version": "2026-05-10",
    "X-Correlation-Id": "corr-fe-int-gate-d04",
    "X-Request-Id": "req-fe-int-gate-d04",
  };
}

async function fulfillJson(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({
    status,
    contentType: "application/json",
    headers: corsHeaders(route),
    body: JSON.stringify(body),
  });
}

async function installV5A11yRoutes(page: Page): Promise<void> {
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: corsHeaders(route) });
      return;
    }

    if (path === "/health") {
      await fulfillJson(route, { status: "ok", service: "f17-a11y-harness" });
      return;
    }

    if (path === "/bff/events/stream") {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        headers: {
          ...corsHeaders(route),
          "Cache-Control": "no-cache",
          Connection: "keep-alive",
        },
        body: ": connected\n\n",
      });
      return;
    }

    if (path === "/bff/v5/control-room") {
      await fulfillJson(route, {
        meta: { snapshot_at: NOW },
        loops: { items: loopRuns },
        sentinel: { items: sentinelFindings },
        interventions: { items: interventions },
      });
      return;
    }

    if (path === "/bff/v5/loop-runs") {
      const kind = url.searchParams.get("kind");
      const items = kind ? loopRuns.filter((run) => run.loop_kind === kind) : loopRuns;
      await fulfillJson(route, { items });
      return;
    }

    if (path === "/bff/v5/execution/persona-health") {
      await fulfillJson(route, { items: personaHealth });
      return;
    }

    if (path === "/bff/v5/sentinel/findings") {
      await fulfillJson(route, { items: sentinelFindings });
      return;
    }

    if (path === "/bff/v5/interventions") {
      await fulfillJson(route, { items: interventions });
      return;
    }

    if (path.startsWith("/bff/")) {
      await fulfillJson(route, { items: [] });
      return;
    }

    await route.fallback();
  });
}

async function gotoReady(page: Page, path: string, ready: RegExp): Promise<void> {
  await installV5A11yRoutes(page);
  await page.goto(path);
  await page.waitForLoadState("domcontentloaded");
  await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => {});

  await expect(page.locator("body")).toBeVisible();
  await expect(page.locator("body")).not.toContainText(CRASH_TEXT);
  await expect(page.locator("body")).toContainText(ready, { timeout: 15_000 });
}

function formatAxeViolations(violations: AxeViolation[]): string {
  if (violations.length === 0) {
    return "No critical/serious axe violations.";
  }

  return violations
    .map((violation) => {
      const nodes = violation.nodes
        .slice(0, 3)
        .map((node) => {
          const target = node.target.join(" ");
          const summary = node.failureSummary?.replace(/\s+/g, " ").trim();
          return summary ? `${target} :: ${summary}` : target;
        })
        .join(" | ");
      return `${violation.id} [${violation.impact}] ${violation.help} :: ${nodes}`;
    })
    .join("\n");
}

async function expectCriticalSeriousAxeClean(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();

  const blocking = results.violations.filter((violation): violation is AxeViolation =>
    BLOCKING_IMPACTS.has(String(violation.impact)),
  );

  expect(blocking, formatAxeViolations(blocking)).toHaveLength(0);
}

async function firstVisible(locator: Locator): Promise<Locator> {
  const count = await locator.count();
  for (let index = 0; index < count; index += 1) {
    const candidate = locator.nth(index);
    if (await candidate.isVisible().catch(() => false)) {
      return candidate;
    }
  }
  throw new Error("No visible locator candidate found");
}

test.describe("F17 axe a11y gate for v5 pages", () => {
  for (const scenario of V5_PAGE_SCENARIOS) {
    test(`critical/serious axe violations are zero on ${scenario.name}`, async ({ page }) => {
      await gotoReady(page, scenario.path, scenario.ready);
      await expectCriticalSeriousAxeClean(page);
    });
  }

  test("drawer focus returns to the trigger after keyboard close", async ({ page }) => {
    await gotoReady(
      page,
      "/management/loops/execution?focus=personas",
      /Execution|Persona|Health|score/i,
    );

    const trigger = await firstVisible(page.locator('tbody tr[tabindex="0"]'));
    await trigger.focus();
    await expect(trigger).toBeFocused();

    await page.keyboard.press("Enter");
    await expect(page.getByRole("dialog")).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(trigger).toBeFocused();
  });

  test("ESC closes only the top overlay before closing the underlying drawer", async ({ page }) => {
    await gotoReady(page, "/management/sentinel", /Sentinel|Findings|critical|confidence/i);

    const findingTrigger = await firstVisible(
      page.locator("ul button").filter({ hasText: /critical|warning|watch/i }),
    );
    await findingTrigger.focus();
    await findingTrigger.click();

    const drawer = page.getByRole("dialog").first();
    await expect(drawer).toBeVisible();

    const emergencyRun = drawer
      .getByRole("button")
      .filter({ hasText: /run|執行/i })
      .last();
    await emergencyRun.click();

    const overlays = page.locator('[role="dialog"]');
    await expect(overlays).toHaveCount(2);
    await expect(overlays.filter({ hasText: /高風險|Confirm|pause_persona_routing|Emergency rollback/i }).last()).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(page.locator('[role="dialog"]')).toHaveCount(1);
    await expect(drawer).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(page.locator('[role="dialog"]')).toHaveCount(0);
    await expect(findingTrigger).toBeFocused();
  });

  test("motion-safe v5 status indicators respect reduced motion", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await gotoReady(page, "/management/loops/execution?focus=personas", /Execution|Persona|Health|score/i);

    const indicators = page.locator('[class*="motion-safe:animate-pulse"]');
    await expect(indicators.first()).toBeVisible();

    const animated = await indicators.evaluateAll((nodes) =>
      nodes
        .filter((node) => {
          const element = node as HTMLElement;
          return element.offsetParent !== null;
        })
        .map((node) => {
          const style = window.getComputedStyle(node as Element);
          const durationMs = style.animationDuration
            .split(",")
            .map((value) => value.trim())
            .map((value) => value.endsWith("ms") ? Number.parseFloat(value) : Number.parseFloat(value) * 1000)
            .filter((value) => Number.isFinite(value));
          const iterationCounts = style.animationIterationCount
            .split(",")
            .map((value) => value.trim())
            .map((value) => value === "infinite" ? Number.POSITIVE_INFINITY : Number.parseFloat(value))
            .filter((value) => Number.isFinite(value));
          return {
            animationDuration: style.animationDuration,
            animationIterationCount: style.animationIterationCount,
            animationName: style.animationName,
            maxDurationMs: Math.max(0, ...durationMs),
            maxIterationCount: Math.max(0, ...iterationCounts),
          };
        }),
    );

    expect(
      animated.every(
        (style) =>
          style.animationName === "none" ||
          (style.maxDurationMs <= 1 && style.maxIterationCount <= 1),
      ),
      `Expected reduced-motion indicators to have no active animation: ${JSON.stringify(animated, null, 2)}`,
    ).toBe(true);
  });
});
