export type ManagementRouteStatus = "shell" | "active-panel" | "planned-workflow";
export type ManagementPanelKey = "shell" | "evidence" | "loop-truth" | "ooda" | "planned";

export interface ManagementRouteDescriptor {
  path: string;
  label: string;
  workflow: string;
  status: ManagementRouteStatus;
  panel: ManagementPanelKey;
  summary: string;
}

interface RouteRule {
  match: (path: string) => boolean;
  descriptor: Omit<ManagementRouteDescriptor, "path">;
}

function normalizePath(pathname: string): string {
  const clean = String(pathname || "").split("?", 1)[0].split("#", 1)[0] || "/";
  if (clean === "/management.html") return "/management";
  return clean.endsWith("/") && clean !== "/" ? clean.slice(0, -1) : clean;
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
      status: "planned-workflow",
      panel: "planned",
      summary: "Backend routes exist, but the dedicated chat, audit, streaming, and attachment workflow is not mounted yet.",
    },
  },
  {
    match: (path) => path.startsWith("/management/readiness"),
    descriptor: {
      label: "Readiness",
      workflow: "Go-live readiness",
      status: "planned-workflow",
      panel: "planned",
      summary: "Readiness BFF routes exist; the next UI step is one consolidated readiness suite.",
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
      status: "planned-workflow",
      panel: "planned",
      summary: "Queue and governance APIs exist; the UI should converge into one decision workbench instead of duplicate pages.",
    },
  },
  {
    match: (path) =>
      [
        "/management/persona-league",
        "/management/portfolio-book",
        "/management/quarterly-ranking",
        "/management/performance-attribution",
        "/management/cost-attribution",
      ].some((prefix) => path === prefix || path.startsWith(`${prefix}/`)),
    descriptor: {
      label: "Performance Review",
      workflow: "Portfolio, league, ranking, attribution, and cost review",
      status: "planned-workflow",
      panel: "planned",
      summary: "Performance BFF routes exist; this should become one review suite with tabs and drilldowns.",
    },
  },
  {
    match: (path) =>
      [
        "/management/control-room",
        "/management/strategies",
        "/management/personas",
        "/management/capital",
        "/management/deployments",
        "/management/runtimes",
        "/management/rebalance",
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
    return { path, ...rule.descriptor };
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
