export type ManagementRouteStatus = "shell" | "active-panel" | "retired-route" | "planned-workflow";
export type ManagementPanelKey =
  | "shell"
  | "evidence"
  | "loop-truth"
  | "ooda"
  | "ai-ops"
  | "readiness-suite"
  | "decision-workbench"
  | "performance-review"
  | "promotion-allocation"
  | "planned";

export interface ManagementRouteDescriptor {
  path: string;
  label: string;
  workflow: string;
  status: ManagementRouteStatus;
  panel: ManagementPanelKey;
  summary: string;
  canonicalPath?: string;
  redirectTo?: string;
}

interface RouteRule {
  match: (path: string) => boolean;
  descriptor: Omit<ManagementRouteDescriptor, "path"> | ((path: string) => Omit<ManagementRouteDescriptor, "path">);
}

function normalizePath(pathname: string): string {
  const clean = String(pathname || "").split("?", 1)[0].split("#", 1)[0] || "/";
  if (clean === "/management.html") return "/management";
  return clean.endsWith("/") && clean !== "/" ? clean.slice(0, -1) : clean;
}

const PROMOTION_ALLOCATION_CANONICAL_PATH = "/management/promotion-allocation";

const promotionAllocationLegacyPrefixes = [
  "/management/promotion-reviews",
  "/management/quarterly-ranking",
  "/management/persona-league",
  "/management/rebalance",
  "/management/rebalances",
  "/management/capital",
  "/management/capital-pools",
  "/management/ranking",
  "/management/ranking/formulas",
  "/management/ranking-formulas",
];

function isLegacyPromotionAllocationRoute(path: string): boolean {
  return (
    promotionAllocationLegacyPrefixes.some((prefix) => path === prefix || path.startsWith(`${prefix}/`))
    || path === "/management/readiness/capital-binding-live"
  );
}

function legacyRouteId(path: string, prefixes: string[]): string | undefined {
  for (const prefix of prefixes) {
    if (!path.startsWith(`${prefix}/`)) continue;
    const suffix = path.slice(prefix.length + 1).split("/", 1)[0];
    return suffix ? decodeURIComponent(suffix) : undefined;
  }
  return undefined;
}

function promotionAllocationRedirect(path: string): string {
  const params = new URLSearchParams();

  if (path === "/management/promotion-reviews" || path.startsWith("/management/promotion-reviews/")) {
    params.set("tab", "promotion-review");
    const reviewId = legacyRouteId(path, ["/management/promotion-reviews"]);
    if (reviewId) params.set("review_id", reviewId);
  } else if (
    path === "/management/rebalance"
    || path.startsWith("/management/rebalance/")
    || path === "/management/rebalances"
    || path.startsWith("/management/rebalances/")
    || path === "/management/capital"
    || path.startsWith("/management/capital/")
    || path === "/management/capital-pools"
    || path.startsWith("/management/capital-pools/")
    || path === "/management/readiness/capital-binding-live"
  ) {
    params.set("tab", "quarterly-capital");
    const rebalanceId = legacyRouteId(path, ["/management/rebalance", "/management/rebalances"]);
    const capitalId = legacyRouteId(path, ["/management/capital", "/management/capital-pools"]);
    if (rebalanceId) params.set("rebalance_id", rebalanceId);
    if (capitalId) params.set("capital_id", capitalId);
  } else if (
    path === "/management/ranking"
    || path.startsWith("/management/ranking/")
    || path === "/management/ranking-formulas"
    || path.startsWith("/management/ranking-formulas/")
  ) {
    params.set("tab", "formula-policy");
    const formulaId = legacyRouteId(path, ["/management/ranking/formulas", "/management/ranking-formulas"]);
    if (formulaId) params.set("formula_id", formulaId);
  } else {
    params.set("tab", "paper-candidates");
  }

  return `${PROMOTION_ALLOCATION_CANONICAL_PATH}?${params.toString()}`;
}

function retiredPromotionAllocationDescriptor(path: string): Omit<ManagementRouteDescriptor, "path"> {
  return {
    label: "Promotion & Allocation",
    workflow: "Paper promotion, formula policy, and quarterly capital",
    status: "retired-route",
    panel: "promotion-allocation",
    summary: "Legacy management URL retired. Operators are redirected to the canonical Promotion & Allocation workbench.",
    canonicalPath: PROMOTION_ALLOCATION_CANONICAL_PATH,
    redirectTo: promotionAllocationRedirect(path),
  };
}

const routeRules: RouteRule[] = [
  {
    match: (path) => path === "/management",
    descriptor: {
      label: "Management Shell",
      workflow: "Evidence and loop truth",
      status: "shell",
      panel: "shell",
      summary: "Current shell route. It exposes live-evidence and loop-truth panels while deeper workflows are mounted deliberately.",
    },
  },
  {
    match: (path) => path === "/management/evidence",
    descriptor: {
      label: "Evidence Explorer",
      workflow: "Evidence and loop truth",
      status: "active-panel",
      panel: "evidence",
      summary: "Served by the active shell through the BFF Live Evidence panel.",
    },
  },
  {
    match: (path) => path.startsWith("/management/loops") || path === "/management/loop-truth",
    descriptor: {
      label: "Loop Truth",
      workflow: "Evidence and loop truth",
      status: "active-panel",
      panel: "loop-truth",
      summary: "Served by the active shell through the Loop Truth panel.",
    },
  },
  {
    match: (path) => path === "/management/ooda" || path.startsWith("/management/ooda/"),
    descriptor: {
      label: "OODA Packets",
      workflow: "Decision replay evidence",
      status: "active-panel",
      panel: "ooda",
      summary: "Served by the active shell through the OODA packet list and replay drawer.",
    },
  },
  {
    match: (path) => path.startsWith("/management/ai") || path.startsWith("/management/nl"),
    descriptor: {
      label: "Management AI Ops",
      workflow: "AI audit and conversation control",
      status: "active-panel",
      panel: "ai-ops",
      summary: "Served by the active shell through the Management AI/NL workflow panel.",
    },
  },
  {
    match: (path) => path === PROMOTION_ALLOCATION_CANONICAL_PATH || path.startsWith(`${PROMOTION_ALLOCATION_CANONICAL_PATH}/`),
    descriptor: {
      label: "Promotion & Allocation",
      workflow: "Paper promotion, formula policy, and quarterly capital",
      status: "active-panel",
      panel: "promotion-allocation",
      summary: "Served by the active shell through the promotion and allocation workbench.",
      canonicalPath: PROMOTION_ALLOCATION_CANONICAL_PATH,
    },
  },
  {
    match: isLegacyPromotionAllocationRoute,
    descriptor: retiredPromotionAllocationDescriptor,
  },
  {
    match: (path) => path.startsWith("/management/readiness"),
    descriptor: {
      label: "Readiness",
      workflow: "Go-live readiness",
      status: "active-panel",
      panel: "readiness-suite",
      summary: "Served by the active shell through the consolidated readiness suite.",
    },
  },
  {
    match: (path) =>
      [
        "/management/human-inbox",
        "/management/interventions",
        "/management/hiq",
        "/management/sentinel",
        "/management/governance",
        "/management/approvals",
        "/management/alerts",
        "/management/incidents",
      ].some((prefix) => path === prefix || path.startsWith(`${prefix}/`)),
    descriptor: {
      label: "Decision Workbench",
      workflow: "Operator queue and governance receipts",
      status: "active-panel",
      panel: "decision-workbench",
      summary: "Served by the active shell through the decision and operations workbench.",
    },
  },
  {
    match: (path) =>
      [
        "/management/persona-fleet",
        "/management/portfolio-book",
        "/management/performance-attribution",
        "/management/cost-attribution",
      ].some((prefix) => path === prefix || path.startsWith(`${prefix}/`)),
    descriptor: {
      label: "Performance Review",
      workflow: "Portfolio, league, ranking, attribution, and cost review",
      status: "active-panel",
      panel: "performance-review",
      summary: "Served by the active shell through the performance review suite.",
    },
  },
  {
    match: (path) =>
      [
        "/management/control-room",
        "/management/strategies",
        "/management/personas",
        "/management/deployments",
        "/management/runtimes",
        "/management/evolution",
        "/management/experiments",
        "/management/tools",
        "/management/mcp",
        "/management/skills",
        "/management/channels",
      ].some((prefix) => path === prefix || path.startsWith(`${prefix}/`)),
    descriptor: {
      label: "Management Registry",
      workflow: "Registry and cockpit consolidation",
      status: "planned-workflow",
      panel: "planned",
      summary: "Historical route name recognized. It is intentionally served by the shell until the registry workflow is rebuilt.",
    },
  },
];

export function describeManagementRoute(pathname: string): ManagementRouteDescriptor {
  const path = normalizePath(pathname);
  const rule = routeRules.find((candidate) => candidate.match(path));
  if (rule) {
    const descriptor = typeof rule.descriptor === "function" ? rule.descriptor(path) : rule.descriptor;
    return { path, ...descriptor };
  }
  return {
    path,
    label: "Unmapped Management Route",
    workflow: "Unmapped",
    status: "planned-workflow",
    panel: "planned",
    summary: "This management URL is recognized by the shell fallback but has no dedicated workflow mapping yet.",
  };
}
