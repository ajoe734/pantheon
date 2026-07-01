#!/usr/bin/env node
// MGMT-LOAD-006: management console load/perf release gate.
//
// Aggregates route-load, request-waterfall, bundle-size, and BFF fanout
// evidence produced by the MGMT-LOAD-001/003/004/005 probes into a single
// pass/fail gate, plus dependency-task pass-eligibility. This is a
// Pantheon-side sibling of `execute-plans/scripts/aggregate-release-gate.mjs`
// (which gates BFF live-evidence/RBAC/SSE production acceptance); this
// script gates management-console load/perf regressions specifically. See
// docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-006-release-load-gate.md
// and support/sidecars/MGMT-LOAD-006/MGMT-LOAD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md
// for the required fail-closed manifest shape and artifact path contract
// consumed by MGMT-LOAD-007.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";

const REPO_ROOT = process.cwd();

const DEFAULT_AUDIT_DIR =
  process.env.PANTHEON_LOAD_GATE_AUDIT_DIR ||
  "docs/04/pantheon_management_console_load_gap_2026-07-01/archive";

function parseArgs(argv) {
  const args = { auditDir: DEFAULT_AUDIT_DIR, outDir: null, bundleFile: null };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--audit-dir") args.auditDir = argv[++i];
    else if (arg === "--out-dir") args.outDir = argv[++i];
    else if (arg === "--bundle-file") args.bundleFile = argv[++i];
    else if (arg === "--route-timing") args.routeTimingFile = argv[++i];
    else if (arg === "--waterfall") args.waterfallFile = argv[++i];
    else if (arg === "--bff-fanout") args.bffFanoutFile = argv[++i];
    else if (arg === "--budgets") args.budgetsFile = argv[++i];
    else if (arg === "--dependencies") args.dependenciesFile = argv[++i];
  }
  if (!args.outDir) args.outDir = args.auditDir;
  return args;
}

// Budgets sourced from
// docs/04/pantheon_management_console_load_gap_2026-07-01/MANAGEMENT_CONSOLE_LOAD_GAP_SPEC.md
// and docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-004-management-route-code-split.md.
const DEFAULT_BUDGETS = {
  initialManagementJsGzipBudgetBytes: 800 * 1024,
  evidenceRouteChunkGzipBudgetBytes: 150 * 1024,
  firstRowOrEmptyP95BudgetMs: 2500,
  nonPrimaryStartupRequestBudget: 2,
  duplicateJobsRequestBudget: 0,
  bffFanout: {
    "/health": 200,
    "/bff/management/evidence": 750,
    "/bff/management/shell-summary": 200,
  },
};

const DEPENDENCY_TASK_IDS = [
  "MGMT-LOAD-001",
  "MGMT-LOAD-002",
  "MGMT-LOAD-003",
  "MGMT-LOAD-004",
  "MGMT-LOAD-005",
];

const TERMINAL_PASS_STATUSES = new Set(["done", "review_approved", "superseded"]);

function loadBudgets(file) {
  if (!file) return DEFAULT_BUDGETS;
  const raw = JSON.parse(fs.readFileSync(file, "utf8"));
  return { ...DEFAULT_BUDGETS, ...raw, bffFanout: { ...DEFAULT_BUDGETS.bffFanout, ...(raw.bffFanout || {}) } };
}

function latestFile(dir, pattern) {
  if (!fs.existsSync(dir)) return null;
  const matches = fs
    .readdirSync(dir)
    .filter((name) => pattern.test(name))
    .sort();
  if (matches.length === 0) return null;
  return path.join(dir, matches[matches.length - 1]);
}

function readJson(file) {
  if (!file || !fs.existsSync(file)) return null;
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (err) {
    return { __parseError: String(err) };
  }
}

function makeCheck(label, status, note, evidence) {
  return { label, status, note: note ?? "", evidence: evidence ?? null };
}

const STATUS_RANK = { pass: 0, skip: 1, warn: 2, missing: 3, fail: 4 };

function worstStatus(checks) {
  let worst = "pass";
  for (const check of checks) {
    if (STATUS_RANK[check.status] > STATUS_RANK[worst]) worst = check.status;
  }
  return worst;
}

function gzipSize(filePath) {
  const buf = fs.readFileSync(filePath);
  return zlib.gzipSync(buf, { level: 9 }).length;
}

// --- Gate 1: dependency task pass-eligibility -----------------------------

function dependencyStatus(taskId) {
  try {
    const out = execFileSync("python3", ["scripts/ai_status.py", "show", taskId], {
      cwd: REPO_ROOT,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
    const parsed = JSON.parse(out);
    if (parsed.task) {
      return { status: parsed.task.status, owner: parsed.task.owner, reviewer: parsed.task.reviewer, source: "active" };
    }
    if (parsed.snapshot) {
      return {
        status: parsed.snapshot.terminal_status,
        outcome: parsed.snapshot.terminal_outcome,
        source: "archive",
      };
    }
    return { status: "unknown", source: "unparsed" };
  } catch (err) {
    return { status: "unknown", source: "error", error: String(err).slice(0, 200) };
  }
}

function buildDependencyGate(dependenciesFile) {
  const overrides = readJson(dependenciesFile) || {};
  const rows = DEPENDENCY_TASK_IDS.map((taskId) => {
    const info = overrides[taskId] || dependencyStatus(taskId);
    const passEligible = TERMINAL_PASS_STATUSES.has(info.status);
    return { taskId, ...info, passEligible };
  });
  const checks = rows.map((row) =>
    makeCheck(
      `Dependency ${row.taskId} is terminal or reviewer-approved.`,
      row.passEligible ? "pass" : "fail",
      `status:${row.status ?? "unknown"} source:${row.source ?? "n/a"}`,
      null,
    ),
  );
  return { rows, checks };
}

// --- Gate 2: bundle budget -------------------------------------------------

function buildBundleGate(bundleFile, budgets) {
  const file = bundleFile || latestFile(path.dirname(bundleFile || "."), /bundle/);
  const data = bundleFile ? readJson(bundleFile) : null;
  if (!data) {
    return {
      data: null,
      checks: [
        makeCheck(
          "Bundle-size evidence is present.",
          "missing",
          "no bundle-budget-*.json supplied; run frontend-checkout `npm run probe:bundle-budget` and pass --bundle-file",
          bundleFile,
        ),
      ],
    };
  }
  const initialBytes = data.results?.initialManagementJsGzipBytes;
  const evidenceBytes = data.results?.evidenceRouteChunkGzipBytes;
  const checks = [
    makeCheck(
      `Initial management JS gzip <= ${budgets.initialManagementJsGzipBudgetBytes} bytes.`,
      typeof initialBytes === "number"
        ? initialBytes <= budgets.initialManagementJsGzipBudgetBytes
          ? "pass"
          : "fail"
        : "missing",
      `observed:${initialBytes ?? "n/a"} budget:${budgets.initialManagementJsGzipBudgetBytes}`,
      bundleFile,
    ),
    makeCheck(
      `Evidence route chunk gzip <= ${budgets.evidenceRouteChunkGzipBudgetBytes} bytes.`,
      typeof evidenceBytes === "number"
        ? evidenceBytes <= budgets.evidenceRouteChunkGzipBudgetBytes
          ? "pass"
          : "fail"
        : "missing",
      `observed:${evidenceBytes ?? "n/a"} budget:${budgets.evidenceRouteChunkGzipBudgetBytes}`,
      bundleFile,
    ),
  ];
  return { data, checks };
}

// --- Gate 3: route timing / readiness --------------------------------------

function buildRouteTimingGate(routeTimingFile, budgets) {
  const data = readJson(routeTimingFile);
  if (!data) {
    return {
      data: null,
      checks: [makeCheck("Route-timing evidence is present.", "missing", `no file at ${routeTimingFile}`, routeTimingFile)],
    };
  }
  const firstRow = data.milestones?.firstRowOrEmptyVisibleMs;
  const checks = [
    makeCheck(
      "Route probe did not use `networkidle` as the readiness signal.",
      data.usedNetworkidle === false ? "pass" : "fail",
      `usedNetworkidle:${data.usedNetworkidle}`,
      routeTimingFile,
    ),
    makeCheck(
      "Route probe completed without error.",
      data.error ? "fail" : "pass",
      data.error ? String(data.error).slice(0, 200) : "no error",
      routeTimingFile,
    ),
    makeCheck(
      `First row/empty-state visible <= ${budgets.firstRowOrEmptyP95BudgetMs} ms.`,
      typeof firstRow === "number"
        ? firstRow <= budgets.firstRowOrEmptyP95BudgetMs
          ? "pass"
          : "fail"
        : "missing",
      `observed:${firstRow ?? "n/a"} budget:${budgets.firstRowOrEmptyP95BudgetMs}`,
      routeTimingFile,
    ),
  ];
  return { data, checks };
}

// --- Gate 4: startup request waterfall (non-primary count, duplicate jobs) --

const ASSET_PATTERN = /\.(js|css|png|svg|jpg|jpeg|webp|woff2?|ico|map)$/i;
const SSE_PATH = "/bff/events/stream";

function classifyRequest(entry, routeTiming) {
  if (entry.path === SSE_PATH || entry.note?.toLowerCase().includes("sse")) return "sse";
  if (routeTiming && entry.path === routeTiming.primaryApiPath) return "primary";
  if (routeTiming && entry.path === routeTiming.routePath) return "document";
  if (ASSET_PATTERN.test(entry.path || "")) return "asset";
  if ((entry.path || "").startsWith("/bff/")) return "non_primary_bff";
  return "other";
}

function buildWaterfallGate(waterfallFile, routeTiming, budgets) {
  const entries = readJson(waterfallFile);
  if (!Array.isArray(entries)) {
    return {
      classified: [],
      checks: [
        makeCheck("Request-waterfall evidence is present.", "missing", `no array at ${waterfallFile}`, waterfallFile),
      ],
    };
  }
  const cutoffMs = routeTiming?.milestones?.firstRowOrEmptyVisibleMs ?? Infinity;
  const classified = entries.map((entry) => ({ ...entry, kind: classifyRequest(entry, routeTiming) }));
  const beforeFirstRow = classified.filter((entry) => (entry.startMs ?? 0) <= cutoffMs);
  const nonPrimary = beforeFirstRow.filter((entry) => entry.kind === "non_primary_bff");
  const jobsRequests = beforeFirstRow.filter((entry) => (entry.path || "") === "/bff/jobs");
  const duplicateJobsCount = Math.max(0, jobsRequests.length - 1);

  const checks = [
    makeCheck(
      `Non-primary BFF startup requests <= ${budgets.nonPrimaryStartupRequestBudget}.`,
      nonPrimary.length <= budgets.nonPrimaryStartupRequestBudget ? "pass" : "fail",
      `observed:${nonPrimary.length} budget:${budgets.nonPrimaryStartupRequestBudget} paths:${nonPrimary.map((e) => e.path).join(",") || "none"}`,
      waterfallFile,
    ),
    makeCheck(
      `Duplicate startup /bff/jobs requests <= ${budgets.duplicateJobsRequestBudget}.`,
      duplicateJobsCount <= budgets.duplicateJobsRequestBudget ? "pass" : "fail",
      `observed:${jobsRequests.length} duplicate:${duplicateJobsCount} budget:${budgets.duplicateJobsRequestBudget}`,
      waterfallFile,
    ),
  ];
  return { classified, beforeFirstRow, nonPrimary, duplicateJobsCount, checks };
}

// --- Gate 5: BFF fanout latency --------------------------------------------

function buildFanoutGate(bffFanoutFile, budgets) {
  const data = readJson(bffFanoutFile);
  if (!data) {
    return {
      data: null,
      checks: [makeCheck("BFF fanout evidence is present.", "missing", `no file at ${bffFanoutFile}`, bffFanoutFile)],
    };
  }
  const checks = Object.entries(budgets.bffFanout).map(([route, budgetMs]) => {
    const observed = data.summary?.[route]?.p95Ms;
    return makeCheck(
      `${route} fanout p95 <= ${budgetMs} ms.`,
      typeof observed === "number" ? (observed <= budgetMs ? "pass" : "fail") : "missing",
      `observed:${observed ?? "n/a"} budget:${budgetMs}`,
      bffFanoutFile,
    );
  });
  return { data, checks };
}

// --- Artifact emission ------------------------------------------------------

function writeArtifactCopy(outDir, name, data) {
  if (data == null) return null;
  const outPath = path.join(outDir, name);
  fs.writeFileSync(outPath, JSON.stringify(data, null, 2), "utf8");
  return outPath;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const budgets = loadBudgets(args.budgetsFile);

  const routeTimingFile = args.routeTimingFile || latestFile(args.auditDir, /^route-timing-.*\.json$/);
  const waterfallFile = args.waterfallFile || latestFile(args.auditDir, /^request-waterfall-.*\.json$/);
  const bffFanoutFile = args.bffFanoutFile || latestFile(args.auditDir, /^bff-fanout-baseline-.*\.json$/);
  const bundleFile = args.bundleFile || latestFile(args.auditDir, /^bundle-budget-.*\.json$/);

  const dependencyGate = buildDependencyGate(args.dependenciesFile);
  const routeTimingGate = buildRouteTimingGate(routeTimingFile, budgets);
  const bundleGate = buildBundleGate(bundleFile, budgets);
  const waterfallGate = buildWaterfallGate(waterfallFile, routeTimingGate.data, budgets);
  const fanoutGate = buildFanoutGate(bffFanoutFile, budgets);

  const gates = {
    "0_dependencies": dependencyGate.checks,
    "1_bundle": bundleGate.checks,
    "2_route_timing": routeTimingGate.checks,
    "3_startup_requests": waterfallGate.checks,
    "4_bff_fanout": fanoutGate.checks,
  };

  const allChecks = Object.values(gates).flat();
  const overall = worstStatus(allChecks);
  const pass = overall === "pass" || overall === "skip" || overall === "warn";

  fs.mkdirSync(args.outDir, { recursive: true });
  const now = new Date().toISOString().slice(0, 10);

  const manifest = {
    schemaVersion: 1,
    taskId: "MGMT-LOAD-006",
    generatedAt: new Date().toISOString(),
    auditDir: args.auditDir,
    inputs: {
      routeTimingFile,
      waterfallFile,
      bffFanoutFile,
      bundleFile,
    },
    budgets,
    dependencyEvidence: dependencyGate.rows,
    gates,
    result: {
      pass,
      overall,
      failures: allChecks.filter((c) => c.status === "fail").map((c) => c.label),
      missing: allChecks.filter((c) => c.status === "missing").map((c) => c.label),
    },
  };

  const manifestPath = path.join(args.outDir, `release-load-gate-${now}.json`);
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2), "utf8");

  writeArtifactCopy(args.outDir, `release-route-timing-${now}.json`, routeTimingGate.data);
  writeArtifactCopy(args.outDir, `release-request-waterfall-${now}.json`, waterfallGate.classified.length ? waterfallGate.classified : null);
  writeArtifactCopy(args.outDir, `release-bundle-${now}.json`, bundleGate.data);
  writeArtifactCopy(args.outDir, `release-bff-fanout-${now}.json`, fanoutGate.data);

  const md = [
    `# MGMT-LOAD-006 Release Load Gate`,
    ``,
    `Generated: ${manifest.generatedAt}`,
    `Audit dir: \`${args.auditDir}\``,
    `Overall: **${overall}** (pass=${pass})`,
    ``,
    ...Object.entries(gates).flatMap(([gateName, checks]) => [
      `## ${gateName}`,
      ``,
      `| Status | Check | Note |`,
      `|---|---|---|`,
      ...checks.map((c) => `| ${c.status} | ${c.label} | ${c.note} |`),
      ``,
    ]),
  ].join("\n");

  const mdPath = path.join(args.outDir, `release-load-gate-${now}.md`);
  fs.writeFileSync(mdPath, md, "utf8");
  console.log(md);

  if (!pass) process.exitCode = 1;
}

main();

export { classifyRequest, buildBundleGate, buildRouteTimingGate, buildWaterfallGate, buildFanoutGate, worstStatus, gzipSize };
