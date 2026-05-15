#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const ROOT = process.cwd();

const argv = new Map();
for (let i = 2; i < process.argv.length; i += 1) {
  const arg = process.argv[i];
  if (!arg.startsWith("--")) continue;
  const key = arg.slice(2);
  const next = process.argv[i + 1];
  if (next && !next.startsWith("--")) {
    argv.set(key, next);
    i += 1;
  } else {
    argv.set(key, "true");
  }
}

const AUDIT_DIR = path.resolve(ROOT, argv.get("audit-dir") || process.env.PANTHEON_AUDIT_OUT_DIR || ".lovable/audits");
const PLAYWRIGHT_REPORT_DIR = path.resolve(ROOT, argv.get("playwright-report") || "playwright-report");
const TEST_RESULTS_DIR = path.resolve(ROOT, argv.get("test-results") || "test-results");
const OUT_PATH = path.resolve(ROOT, argv.get("out") || path.join(AUDIT_DIR, "release-gate-summary.md"));
const JSON_OUT_PATH = path.resolve(ROOT, argv.get("json-out") || path.join(AUDIT_DIR, "release-gate-summary.json"));
const CHECKLIST_TEMPLATE_ARG = argv.get("checklist") || process.env.PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE || "";
const CHECKLIST_TEMPLATE_PATH = CHECKLIST_TEMPLATE_ARG ? path.resolve(ROOT, CHECKLIST_TEMPLATE_ARG) : "";
const CHECKLIST_OUT_PATH = path.resolve(ROOT, argv.get("checklist-out") || process.env.PANTHEON_RELEASE_GATE_CHECKLIST_OUT || path.join(".lovable", "audits", "Release_Gate_Checklist.md"));
const RUN_URL = process.env.PANTHEON_RELEASE_GATE_RUN_URL ||
  (process.env.GITHUB_SERVER_URL && process.env.GITHUB_REPOSITORY && process.env.GITHUB_RUN_ID
    ? `${process.env.GITHUB_SERVER_URL}/${process.env.GITHUB_REPOSITORY}/actions/runs/${process.env.GITHUB_RUN_ID}`
    : "");
const DEFAULT_OWNER = process.env.PANTHEON_RELEASE_GATE_OWNER || "Codex";
const IGNORED_AUDIT_DIR_NAMES = new Set(["archive", "archives", "baseline"]);

const STATUS_WEIGHT = {
  pass: 0,
  skip: 1,
  warn: 2,
  missing: 3,
  fail: 4,
};

const GATE_OWNERS = {
  0: DEFAULT_OWNER,
  1: process.env.PANTHEON_FE_CI_OWNER || "Gemini",
  2: process.env.PANTHEON_CONTRACT_OWNER || DEFAULT_OWNER,
  3: process.env.PANTHEON_BFF_GATE_OWNER || DEFAULT_OWNER,
  4: process.env.PANTHEON_HOSTED_FE_OWNER || "Gemini",
  5: process.env.PANTHEON_E2E_OWNER || DEFAULT_OWNER,
  6: process.env.PANTHEON_A11Y_PERF_OWNER || "Codex2",
  7: DEFAULT_OWNER,
};

const gateTitles = {
  0: "Preconditions",
  1: "Static / Build / Unit",
  2: "Contract Drift",
  3: "BFF Route Probes",
  4: "Browser Hosted E2E",
  5: "Playwright User Flows",
  6: "A11y / Perf",
  7: "Release Decision",
};

function exists(filePath) {
  try {
    return fs.existsSync(filePath);
  } catch {
    return false;
  }
}

function readText(filePath) {
  if (!filePath || !exists(filePath)) return "";
  return fs.readFileSync(filePath, "utf8");
}

function readJson(filePath) {
  const text = readText(filePath);
  if (!text.trim()) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function listFiles(dir) {
  if (!exists(dir)) return [];
  const out = [];
  const stack = [dir];
  while (stack.length) {
    const current = stack.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) {
        if (IGNORED_AUDIT_DIR_NAMES.has(entry.name.toLowerCase())) continue;
        stack.push(full);
      } else if (entry.isFile()) {
        out.push(full);
      }
    }
  }
  return out;
}

const auditFiles = listFiles(AUDIT_DIR);

function latestAuditFile(patterns) {
  const tests = patterns.map((pattern) => pattern instanceof RegExp ? pattern : new RegExp(pattern));
  return auditFiles
    .filter((file) => tests.some((pattern) => pattern.test(path.basename(file))))
    .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs)[0] || "";
}

function rel(filePath) {
  if (!filePath) return "";
  const relative = path.relative(ROOT, filePath).replaceAll(path.sep, "/");
  if (!relative) return ".";
  return relative.startsWith("..") ? filePath : relative;
}

function evidenceLink(filePath, label = "") {
  if (!filePath) return "missing evidence";
  if (/^https?:\/\//i.test(filePath)) return `[${label || filePath}](${filePath})`;
  const text = label || rel(filePath);
  if (RUN_URL) return `[${text}](${RUN_URL})`;
  return `[${text}](${rel(filePath)})`;
}

function evidencePath(filePath) {
  if (!filePath) return "";
  if (/^https?:\/\//i.test(filePath)) return filePath;
  return path.resolve(ROOT, filePath);
}

function escapeMd(value) {
  return String(value).replaceAll("|", "\\|").replaceAll("\n", " ");
}

function statusLabel(status) {
  return String(status || "missing").toUpperCase();
}

function checkbox(status) {
  return status === "pass" ? "[x]" : "[ ]";
}

function makeCheck(label, status, details = {}) {
  return {
    label,
    status,
    owner: details.owner || "",
    evidence: details.evidence || "",
    note: details.note || "",
  };
}

function stepToStatus(outcome) {
  const value = String(outcome || "").toLowerCase();
  if (["success", "passed", "pass", "ok"].includes(value)) return "pass";
  if (["skipped", "skip"].includes(value)) return "skip";
  if (["failure", "failed", "timed_out", "timedout", "cancelled", "canceled"].includes(value)) return "fail";
  return "missing";
}

function worstStatus(statuses) {
  return statuses.reduce((worst, current) => {
    return STATUS_WEIGHT[current] > STATUS_WEIGHT[worst] ? current : worst;
  }, "pass");
}

function gateStatus(checks) {
  return worstStatus(checks.map((check) => check.status));
}

function getGitValue(args) {
  try {
    return execFileSync("git", args, { cwd: ROOT, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch {
    return "";
  }
}

function parseNumberAfter(text, label) {
  const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = text.match(new RegExp(`${escaped}\\s*:?\\s*(-?\\d+)`, "i"));
  return match ? Number(match[1]) : null;
}

function parseBoolAfter(text, label) {
  const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = text.match(new RegExp(`${escaped}\\s*:?\\s*(true|false)`, "i"));
  if (!match) return null;
  return match[1].toLowerCase() === "true";
}

function parseTables(text) {
  const rows = [];
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim().startsWith("|")) continue;
    const cells = line.split("|").slice(1, -1).map((cell) => cell.trim());
    if (cells.length < 2) continue;
    if (cells.some((cell) => /^-+$/.test(cell.replaceAll(":", "")))) continue;
    if (cells.some((cell) => ["status", "method", "path", "pass"].includes(cell.toLowerCase()))) continue;
    rows.push(cells);
  }
  return rows;
}

function routeRows(text) {
  const rows = new Map();
  for (const cells of parseTables(text)) {
    if (cells.length < 3) continue;
    const [status, method, route] = cells;
    if (route?.startsWith("/")) rows.set(route, { status, method, route, cells });
  }
  return rows;
}

function authRows(text) {
  const rows = new Map();
  for (const cells of parseTables(text)) {
    if (cells.length < 4) continue;
    const [passCell, status, method, route] = cells;
    if (!route?.startsWith("/")) continue;
    const passed = /pass|true|yes|\[x\]/i.test(passCell) || passCell.includes("\u2705");
    rows.set(route, { passed, status, method, route, cells });
  }
  return rows;
}

function allRowsPass(rows, paths) {
  const present = paths.map((route) => rows.get(route)).filter(Boolean);
  if (present.length !== paths.length) return { status: "missing", note: `found ${present.length}/${paths.length} rows` };
  const failed = present.filter((row) => !row.passed);
  return failed.length
    ? { status: "fail", note: `${failed.length}/${paths.length} rows failed` }
    : { status: "pass", note: `${paths.length}/${paths.length} rows passed` };
}

function getStepOutcomes() {
  const file = latestAuditFile([/^release-gate-step-outcomes\.json$/]);
  const json = readJson(file);
  return { file, json: json || {} };
}

function stepInfo(stepOutcomes, key, fallbackEvidence = "") {
  const raw = stepOutcomes.json?.[key];
  if (typeof raw === "string") {
    return { outcome: raw, status: stepToStatus(raw), evidence: fallbackEvidence };
  }
  return {
    outcome: raw?.outcome || "",
    status: stepToStatus(raw?.outcome),
    evidence: raw?.evidence || fallbackEvidence,
  };
}

function stepCheck(stepOutcomes, key, label, fallbackEvidence, owner) {
  const info = stepInfo(stepOutcomes, key, fallbackEvidence);
  const evidence = evidencePath(info.evidence);
  return makeCheck(label, info.status, {
    owner: info.status === "pass" ? "" : owner,
    evidence: evidence || stepOutcomes.file,
    note: info.outcome ? `outcome: ${info.outcome}` : "step outcome missing",
  });
}

function missingEvidenceStatus(stepStatus) {
  if (stepStatus === "fail") return "fail";
  if (stepStatus === "skip") return "skip";
  return "missing";
}

function missingProbeNote(label, stepOutcome) {
  return stepOutcome ? `${label} outcome: ${stepOutcome}; markdown evidence missing` : `${label} markdown evidence missing`;
}

function envPresent(...names) {
  return names.some((name) => String(process.env[name] || "").trim());
}

function markdownHasException(idOrLabel) {
  const env = process.env.PANTHEON_RELEASE_GATE_EXCEPTIONS || "";
  const file = latestAuditFile([/^release-gate-exceptions\.md$/]);
  const text = `${env}\n${readText(file)}`.toLowerCase();
  if (!text.trim()) return false;
  return text.includes(String(idOrLabel).toLowerCase());
}

function buildGate0(hosted) {
  const sha = process.env.PANTHEON_FRONTEND_SHA || process.env.GITHUB_SHA || getGitValue(["rev-parse", "HEAD"]);
  const trackedDirty = getGitValue(["status", "--short", "--untracked-files=no"]);
  const bffShaPresent = envPresent("PANTHEON_BFF_SHA", "PANTHEON_BACKEND_SHA", "PANTHEON_PANTHEON_SHA");
  const feUrlPresent = envPresent("PANTHEON_FE_BASE_URL");
  const bffUrlPresent = envPresent("PANTHEON_BFF_BASE_URL", "VITE_BFF_BASE_URL");
  const authPresent = envPresent("PANTHEON_BFF_SMOKE_BEARER_TOKEN", "BFF_AUTH_TOKEN", "PANTHEON_TEST_OIDC_PATH");
  const noOldUrl = hosted.exists
    ? hosted.oldHitCount === 0 && hosted.containsOld !== true
    : null;
  const noOldStatus = noOldUrl === true ? "pass" : noOldUrl === false ? "fail" : hosted.missingStatus || "missing";
  const hostedEvidence = hosted.file || hosted.stepEvidence;

  return [
    makeCheck("`execute-plans` branch is clean and points to release candidate SHA.", trackedDirty ? "fail" : sha ? "pass" : "missing", {
      owner: trackedDirty || !sha ? GATE_OWNERS[0] : "",
      evidence: RUN_URL || ROOT,
      note: trackedDirty ? "tracked worktree changes present" : sha ? `frontend SHA: ${sha.slice(0, 12)}` : "SHA missing",
    }),
    makeCheck("`pantheon` backend/BFF SHA is recorded.", bffShaPresent ? "pass" : "missing", {
      owner: bffShaPresent ? "" : GATE_OWNERS[0],
      evidence: RUN_URL || ROOT,
      note: bffShaPresent ? "backend/BFF SHA env present" : "set PANTHEON_BFF_SHA or PANTHEON_BACKEND_SHA",
    }),
    makeCheck("`PANTHEON_FE_BASE_URL` points to intended Lovable deployment.", feUrlPresent ? "pass" : "missing", {
      owner: feUrlPresent ? "" : GATE_OWNERS[0],
      evidence: RUN_URL || ROOT,
      note: process.env.PANTHEON_FE_BASE_URL || "missing",
    }),
    makeCheck("`PANTHEON_BFF_BASE_URL` points to intended BFF.", bffUrlPresent ? "pass" : "missing", {
      owner: bffUrlPresent ? "" : GATE_OWNERS[0],
      evidence: RUN_URL || ROOT,
      note: process.env.PANTHEON_BFF_BASE_URL || process.env.VITE_BFF_BASE_URL || "missing",
    }),
    makeCheck("No obsolete BFF URL appears in hosted JS bundle.", noOldStatus, {
      owner: noOldUrl === true ? "" : GATE_OWNERS[4],
      evidence: hostedEvidence,
      note: noOldUrl === null ? hosted.missingNote || "hosted browser probe missing" : `old URL hit count: ${hosted.oldHitCount}`,
    }),
    makeCheck("Auth token or test OIDC path available for authenticated smoke.", authPresent ? "pass" : "missing", {
      owner: authPresent ? "" : GATE_OWNERS[3],
      evidence: RUN_URL || ROOT,
      note: authPresent ? "auth input present" : "PANTHEON_BFF_SMOKE_BEARER_TOKEN or test OIDC path missing",
    }),
  ];
}

function buildGate1(stepOutcomes) {
  return [
    stepCheck(stepOutcomes, "install", "`npm ci` completed.", ".lovable/audits/npm-ci.log", GATE_OWNERS[1]),
    stepCheck(stepOutcomes, "lint", "`npm run lint` passes.", ".lovable/audits/npm-run-lint.log", GATE_OWNERS[1]),
    stepCheck(stepOutcomes, "test", "`npm run test` passes.", ".lovable/audits/npm-run-test.log", GATE_OWNERS[1]),
    stepCheck(stepOutcomes, "build", "`npm run build` passes.", ".lovable/audits/npm-run-build.log", GATE_OWNERS[1]),
    stepCheck(stepOutcomes, "contract", "`npm run test:contract` passes.", ".lovable/audits/contract-drift.log", GATE_OWNERS[2]),
  ];
}

function buildGate2(stepOutcomes) {
  const contract = stepInfo(stepOutcomes, "contract", ".lovable/audits/contract-drift.log");
  const evidence = evidencePath(contract.evidence) || latestAuditFile([/contract-drift/i]);
  const status = contract.status;
  const details = {
    owner: status === "pass" ? "" : GATE_OWNERS[2],
    evidence,
    note: contract.outcome ? `contract drift outcome: ${contract.outcome}` : "contract drift outcome missing",
  };
  return [
    makeCheck("`paths.ts` canonical paths exist in OpenAPI.", status, details),
    makeCheck("`ActionCommandStatus` is named schema.", status, details),
    makeCheck("ErrorCode list matches 26-code master.", status, details),
    makeCheck("SSE channels match AsyncAPI.", status, details),
    makeCheck("EvidenceKind capability map matches DTO catalog.", status, details),
    makeCheck("`correlationId` required in backend-facing AsyncAPI.", status, details),
  ];
}

function analyzeRouteProbe(stepOutcomes) {
  const step = stepInfo(stepOutcomes, "route_probe", ".lovable/audits/bff-route-probe-anonymous.log");
  const file = latestAuditFile([/^bff-route-probe-anonymous-.*\.md$/]);
  const text = readText(file);
  const rows = routeRows(text);
  const canonical404 = parseNumberAfter(text, "Canonical 404 count");
  const transportErrors = parseNumberAfter(text, "Transport errors");
  return {
    exists: Boolean(text),
    file,
    rows,
    canonical404,
    transportErrors,
    missingStatus: missingEvidenceStatus(step.status),
    missingNote: missingProbeNote("anonymous route probe", step.outcome),
    stepStatus: step.status,
    stepOutcome: step.outcome,
    stepEvidence: evidencePath(step.evidence),
  };
}

function analyzeAuthSmoke(stepOutcomes) {
  const step = stepInfo(stepOutcomes, "auth_smoke", ".lovable/audits/bff-authenticated-live-smoke.log");
  const file = latestAuditFile([/^bff-authenticated-live-smoke-.*\.md$/]) || evidencePath(step.evidence);
  const text = readText(file);
  const rows = authRows(text);
  const summary = text.match(/Passed:\s*(\d+)\s*\/\s*(\d+)/i);
  return {
    exists: Boolean(text),
    file,
    rows,
    passed: summary ? Number(summary[1]) : null,
    total: summary ? Number(summary[2]) : null,
    missingStatus: missingEvidenceStatus(step.status),
    missingNote: missingProbeNote("authenticated smoke", step.outcome),
    stepStatus: step.status,
    stepOutcome: step.outcome,
  };
}

function buildGate3(routeProbe, authSmoke) {
  const routeEvidence = routeProbe.file || routeProbe.stepEvidence;
  const authEvidence = authSmoke.file || routeEvidence;
  const healthStatus = [routeProbe.rows.get("/health")?.status, routeProbe.rows.get("/healthz")?.status].includes("200");
  const openapiStatus = routeProbe.rows.get("/openapi.json")?.status === "200";
  const streamStatus = routeProbe.rows.get("/bff/events/stream")?.status;
  const protectedRows = [...routeProbe.rows.values()].filter((row) => row.route.startsWith("/bff/") && row.route !== "/bff/events/stream");
  const protectedValid = protectedRows.length > 0 && protectedRows.every((row) => ["401", "403"].includes(String(row.status)));
  const no404 = routeProbe.canonical404 === 0;

  const readListPaths = [
    "/bff/strategies",
    "/bff/personas",
    "/bff/capital-pools",
    "/bff/rebalances",
    "/bff/deployments",
    "/bff/jobs",
    "/bff/alerts",
    "/bff/incidents",
    "/bff/audit",
    "/bff/artifacts",
    "/bff/runtimes",
    "/bff/mcp-servers",
    "/bff/mcp-tools",
    "/bff/skills",
    "/bff/channels",
    "/bff/tools",
    "/bff/ranking-formulas",
    "/bff/research-experiments",
    "/bff/agora/signals",
    "/bff/agora/inbox",
    "/bff/agora/journal",
    "/bff/agora/postmortems",
    "/bff/agora/ask/sessions",
  ];
  const v5Paths = [
    "/bff/v5/loop-runs",
    "/bff/v5/sentinel/findings",
    "/bff/v5/interventions",
    "/bff/v5/execution/persona-health",
  ];
  const writePaths = [
    "/bff/actions/strategies/strategy-dev/promote",
    "/bff/approvals/approval-dev/decide",
    "/bff/v5/interventions/intervention-dev/decide",
  ];
  const routeStatus = (condition) => routeProbe.exists ? condition ? "pass" : "fail" : routeProbe.missingStatus;
  const routeOwner = (condition) => routeProbe.exists && condition ? "" : GATE_OWNERS[3];
  const routeNote = (note) => routeProbe.exists ? note : routeProbe.missingNote;
  const authStatus = (condition) => authSmoke.exists ? condition ? "pass" : "fail" : authSmoke.missingStatus;
  const authOwner = (condition) => authSmoke.exists && condition ? "" : GATE_OWNERS[3];
  const authNote = (note) => authSmoke.exists ? note : authSmoke.missingNote;
  const authMissingResult = { status: authSmoke.missingStatus, note: authSmoke.missingNote };
  const listResult = authSmoke.exists ? allRowsPass(authSmoke.rows, readListPaths) : authMissingResult;
  const v5Result = authSmoke.exists ? allRowsPass(authSmoke.rows, v5Paths) : authMissingResult;
  const writeResult = authSmoke.exists ? allRowsPass(authSmoke.rows, writePaths) : authMissingResult;
  const meRow = authSmoke.rows.get("/bff/me");
  const authAllPassed = authSmoke.exists && authSmoke.passed !== null && authSmoke.total !== null && authSmoke.passed === authSmoke.total;

  return [
    makeCheck("Anonymous: `/health` or `/healthz` returns 200.", routeStatus(healthStatus), {
      owner: routeOwner(healthStatus),
      evidence: routeEvidence,
      note: routeNote("anonymous route probe"),
    }),
    makeCheck("Anonymous: `/openapi.json` returns 200.", routeStatus(openapiStatus), {
      owner: routeOwner(openapiStatus),
      evidence: routeEvidence,
      note: routeNote(`status: ${routeProbe.rows.get("/openapi.json")?.status || "missing"}`),
    }),
    makeCheck("Anonymous: `/bff/events/stream` returns 200 or proper stream open.", routeStatus(streamStatus === "200"), {
      owner: routeOwner(streamStatus === "200"),
      evidence: routeEvidence,
      note: routeNote(`status: ${streamStatus || "missing"}`),
    }),
    makeCheck("Anonymous: canonical protected routes return 401/403, not 404.", routeStatus(protectedValid), {
      owner: routeOwner(protectedValid),
      evidence: routeEvidence,
      note: routeNote(`${protectedRows.length} protected route rows`),
    }),
    makeCheck("Anonymous: no canonical route returns 404.", routeStatus(no404), {
      owner: routeOwner(no404),
      evidence: routeEvidence,
      note: routeNote(`canonical 404 count: ${routeProbe.canonical404 ?? "missing"}`),
    }),
    makeCheck("Authenticated: `/bff/me` returns MeResponse.", authStatus(Boolean(meRow?.passed)), {
      owner: authOwner(Boolean(meRow?.passed)),
      evidence: authEvidence,
      note: authNote(`passed: ${Boolean(meRow?.passed)}`),
    }),
    makeCheck("Authenticated: entity list endpoints return ListResponse.", listResult.status, {
      owner: listResult.status === "pass" ? "" : GATE_OWNERS[3],
      evidence: authEvidence,
      note: listResult.note,
    }),
    makeCheck("Authenticated: v5 endpoints return expected DTO envelope.", v5Result.status, {
      owner: v5Result.status === "pass" ? "" : GATE_OWNERS[3],
      evidence: authEvidence,
      note: v5Result.note,
    }),
    makeCheck("Authenticated: write/precondition tests return expected BffErrorEnvelope.", writeResult.status, {
      owner: writeResult.status === "pass" ? "" : GATE_OWNERS[3],
      evidence: authEvidence,
      note: writeResult.note,
    }),
    makeCheck("Authenticated: safe write / dry-run endpoints do not create live capital side effects.", authStatus(authAllPassed), {
      owner: authOwner(authAllPassed),
      evidence: authEvidence,
      note: authNote(`authenticated smoke passed ${authSmoke.passed}/${authSmoke.total}`),
    }),
  ];
}

function analyzeHostedProbe(stepOutcomes) {
  const step = stepInfo(stepOutcomes, "browser_probe", ".lovable/audits/hosted-browser-bff-probe.log");
  const file = latestAuditFile([/^hosted-browser-bff-probe-.*\.md$/]);
  const text = readText(file);
  const requestCount = parseNumberAfter(text, "request count");
  const responseCount = parseNumberAfter(text, "response count");
  const failedCount = parseNumberAfter(text, "failed count");
  const oldHitCount = parseNumberAfter(text, "old BFF URL hit count");
  const containsBff = parseBoolAfter(text, "contains intended BFF URL");
  const containsOld = parseBoolAfter(text, "contains old BFF URL");
  const pass = parseBoolAfter(text, "pass");
  const consoleErrorsSection = text.match(/## Console errors\s+([\s\S]*?)(?:\n## |\n?$)/i)?.[1] || "";
  const corsErrors = /cors/i.test(consoleErrorsSection) && !/none/i.test(consoleErrorsSection.trim());
  return {
    exists: Boolean(text),
    file,
    requestCount,
    responseCount,
    failedCount,
    oldHitCount: oldHitCount ?? (containsOld === false ? 0 : containsOld === true ? 1 : null),
    containsBff,
    containsOld,
    pass,
    corsErrors,
    missingStatus: missingEvidenceStatus(step.status),
    missingNote: missingProbeNote("hosted browser probe", step.outcome),
    stepStatus: step.status,
    stepOutcome: step.outcome,
    stepEvidence: evidencePath(step.evidence),
  };
}

function buildGate4(hosted) {
  const evidence = hosted.file || hosted.stepEvidence;
  const loaded = hosted.exists && hosted.pass !== null;
  const noOld = hosted.oldHitCount === 0 && hosted.containsOld !== true;
  const responsesMatch = hosted.requestCount !== null && hosted.requestCount > 0 && hosted.responseCount === hosted.requestCount;
  const noFailed = hosted.failedCount === 0;
  const hostedStatus = (condition) => hosted.exists ? condition ? "pass" : "fail" : hosted.missingStatus;
  const hostedOwner = (condition) => hosted.exists && condition ? "" : GATE_OWNERS[4];
  const hostedNote = (note) => hosted.exists ? note : hosted.missingNote;
  return [
    makeCheck("Hosted page loads.", hostedStatus(loaded), {
      owner: hostedOwner(loaded),
      evidence,
      note: hostedNote(`probe pass: ${hosted.pass}`),
    }),
    makeCheck("Hosted JS bundle contains intended BFF URL.", hostedStatus(hosted.containsBff), {
      owner: hostedOwner(hosted.containsBff),
      evidence,
      note: hostedNote(`contains intended BFF URL: ${hosted.containsBff ?? "missing"}`),
    }),
    makeCheck("Hosted JS bundle does not contain obsolete BFF URL.", hostedStatus(noOld), {
      owner: hostedOwner(noOld),
      evidence,
      note: hostedNote(`old BFF URL hit count: ${hosted.oldHitCount ?? "missing"}`),
    }),
    makeCheck("CORS preflight passes.", hostedStatus(!hosted.corsErrors && noFailed), {
      owner: hostedOwner(!hosted.corsErrors && noFailed),
      evidence,
      note: hostedNote("inferred from browser network and console"),
    }),
    makeCheck("Browser receives responses for all BFF requests.", hostedStatus(responsesMatch), {
      owner: hostedOwner(responsesMatch),
      evidence,
      note: hostedNote(`responses ${hosted.responseCount ?? "?"}/${hosted.requestCount ?? "?"}`),
    }),
    makeCheck("No failed BFF requests.", hostedStatus(noFailed), {
      owner: hostedOwner(noFailed),
      evidence,
      note: hostedNote(`failed count: ${hosted.failedCount ?? "missing"}`),
    }),
    makeCheck("No CORS console errors.", hostedStatus(!hosted.corsErrors), {
      owner: hostedOwner(!hosted.corsErrors),
      evidence,
      note: hostedNote(hosted.corsErrors ? "CORS text found in console errors" : "no CORS console errors detected"),
    }),
  ];
}

function analyzePlaywright() {
  const jsonFile = latestAuditFile([/^playwright-results\.json$/]) || path.join(AUDIT_DIR, "playwright-results.json");
  const report = readJson(jsonFile);
  const specs = [];

  function visitSuite(suite, parents = []) {
    const nextParents = suite.title ? [...parents, suite.title] : parents;
    for (const spec of suite.specs || []) {
      const results = [];
      for (const test of spec.tests || []) {
        for (const result of test.results || []) {
          results.push(result.status || "");
        }
      }
      const failed = results.some((status) => ["failed", "timedOut", "interrupted"].includes(status));
      const skipped = results.length > 0 && results.every((status) => status === "skipped");
      const passed = results.length > 0 && !failed && !skipped;
      specs.push({
        title: [...nextParents, spec.title].filter(Boolean).join(" > "),
        file: spec.file || suite.file || "",
        status: failed ? "fail" : skipped ? "skip" : passed ? "pass" : "missing",
        results,
      });
    }
    for (const child of suite.suites || []) visitSuite(child, nextParents);
  }

  for (const suite of report?.suites || []) visitSuite(suite);

  const lastRunFile = path.join(TEST_RESULTS_DIR, ".last-run.json");
  const lastRun = readJson(lastRunFile);
  return {
    jsonFile: exists(jsonFile) ? jsonFile : "",
    report,
    specs,
    lastRunFile: exists(lastRunFile) ? lastRunFile : "",
    lastRun,
    htmlReport: exists(path.join(PLAYWRIGHT_REPORT_DIR, "index.html")) ? path.join(PLAYWRIGHT_REPORT_DIR, "index.html") : "",
  };
}

function specMatches(spec, matcher) {
  const haystack = `${spec.file}\n${spec.title}`.toLowerCase();
  return matcher.test(haystack);
}

function checkFlow(playwright, flowId, label, matcher, options = {}) {
  const matches = playwright.specs.filter((spec) => specMatches(spec, matcher));
  const evidence = playwright.jsonFile || playwright.htmlReport || playwright.lastRunFile;
  if (!matches.length) {
    if (options.optionalException && markdownHasException(flowId)) {
      return makeCheck(label, "warn", {
        owner: GATE_OWNERS[5],
        evidence,
        note: `${flowId} marked by release-gate exception`,
      });
    }
    return makeCheck(label, "missing", {
      owner: GATE_OWNERS[5],
      evidence,
      note: `${flowId} not found in Playwright JSON report`,
    });
  }
  const status = worstStatus(matches.map((spec) => spec.status));
  return makeCheck(label, status, {
    owner: status === "pass" ? "" : GATE_OWNERS[5],
    evidence,
    note: `${matches.length} matching spec(s)`,
  });
}

function buildGate5(playwright) {
  return [
    checkFlow(playwright, "F01", "F01 Startup / Session Bootstrap.", /\bf01\b|01-startup-session/),
    checkFlow(playwright, "F02", "F02 Control Room.", /\bf02\b|02-control-room/),
    checkFlow(playwright, "F03", "F03 Execution Loop.", /\bf03\b|03-execution-loop/),
    checkFlow(playwright, "F04", "F04 Optimization Loop.", /\bf04\b|04-optimization-loop/),
    checkFlow(playwright, "F05", "F05 Sentinel.", /\bf05\b|04-sentinel-remediation/),
    checkFlow(playwright, "F06", "F06 HIQ.", /\bf06\b|05-interventions|hiq/),
    checkFlow(playwright, "F07", "F07 Entity Registry.", /\bf07\b|entity registry|06-entity-registry/),
    checkFlow(playwright, "F08", "F08 Create Write Intent.", /\bf08\b|create write intent|write-intent/),
    checkFlow(playwright, "F09", "F09 High-Risk Confirm.", /\bf09\b|high-risk|07-high-risk-confirm/),
    checkFlow(playwright, "F10", "F10 Rollback Saga, or marked backend-not-ready.", /\bf10\b|rollback saga|10-rollback/, { optionalException: true }),
    checkFlow(playwright, "F11", "F11 Handoff SLA, or marked backend-not-ready.", /\bf11\b|handoff sla|11-handoff/, { optionalException: true }),
    checkFlow(playwright, "F12", "F12 Approval Governance.", /\bf12\b|approval governance|12-approval/),
    checkFlow(playwright, "F13", "F13 Agora.", /\bf13\b|13-agora|agora/),
    checkFlow(playwright, "F14", "F14 SSE reconnect.", /\bf14\b|sse reconnect|08-sse/),
    checkFlow(playwright, "F15", "F15 strict/hybrid fallback.", /\bf15\b|strict.*hybrid|09-strict/),
    checkFlow(playwright, "F16", "F16 audit/correlation.", /\bf16\b|audit.*correlation|correlation/),
  ];
}

function buildGate6(playwright) {
  const evidence = playwright.jsonFile || playwright.htmlReport || playwright.lastRunFile;
  const axeSpecs = playwright.specs.filter((spec) => specMatches(spec, /\bf17\b|17-a11y|axe|a11y/));
  const axeStatus = axeSpecs.length ? worstStatus(axeSpecs.map((spec) => spec.status)) : playwright.lastRun?.status === "failed" ? "fail" : "missing";
  const focusSpecs = playwright.specs.filter((spec) => specMatches(spec, /focus/));
  const motionSpecs = playwright.specs.filter((spec) => specMatches(spec, /reduced motion|motion/));
  const perfSpecs = playwright.specs.filter((spec) => specMatches(spec, /\bf18\b|18-perf|performance|budget/));
  const ssePerfSpecs = playwright.specs.filter((spec) => specMatches(spec, /sse.*rerender|rerender.*sse/));

  function statusFor(matches) {
    return matches.length ? worstStatus(matches.map((spec) => spec.status)) : "missing";
  }

  return [
    makeCheck("v5 axe smoke critical/serious = 0.", axeStatus, {
      owner: axeStatus === "pass" ? "" : GATE_OWNERS[6],
      evidence,
      note: axeSpecs.length ? `${axeSpecs.length} axe/a11y spec(s)` : "axe/a11y report missing or last run failed",
    }),
    makeCheck("overlay focus handling works.", statusFor(focusSpecs), {
      owner: statusFor(focusSpecs) === "pass" ? "" : GATE_OWNERS[6],
      evidence,
      note: focusSpecs.length ? `${focusSpecs.length} focus spec(s)` : "focus evidence missing",
    }),
    makeCheck("reduced motion respected.", statusFor(motionSpecs), {
      owner: statusFor(motionSpecs) === "pass" ? "" : GATE_OWNERS[6],
      evidence,
      note: motionSpecs.length ? `${motionSpecs.length} reduced-motion spec(s)` : "reduced-motion evidence missing",
    }),
    makeCheck("Control Room and entity list are within performance budget.", statusFor(perfSpecs), {
      owner: statusFor(perfSpecs) === "pass" ? "" : GATE_OWNERS[6],
      evidence,
      note: perfSpecs.length ? `${perfSpecs.length} performance spec(s)` : "performance evidence missing",
    }),
    makeCheck("SSE stream does not trigger unbounded rerender.", statusFor(ssePerfSpecs), {
      owner: statusFor(ssePerfSpecs) === "pass" ? "" : GATE_OWNERS[6],
      evidence,
      note: ssePerfSpecs.length ? `${ssePerfSpecs.length} SSE rerender spec(s)` : "SSE rerender evidence missing",
    }),
  ];
}

function buildGate7(previousGates) {
  const priorChecks = Object.entries(previousGates)
    .filter(([gate]) => gate !== "7")
    .flatMap(([, checks]) => checks);
  const criticalStatus = worstStatus(priorChecks.map((check) => check.status));
  const failures = priorChecks.filter((check) => ["fail", "missing"].includes(check.status));
  const exceptionsFile = latestAuditFile([/^release-gate-exceptions\.md$/]);
  const exceptionsPresent = Boolean(process.env.PANTHEON_RELEASE_GATE_EXCEPTIONS || exceptionsFile);
  const evidencePresent = auditFiles.length > 0;
  const shaRecorded = envPresent("PANTHEON_FRONTEND_SHA", "GITHUB_SHA") && envPresent("PANTHEON_BFF_SHA", "PANTHEON_BACKEND_SHA", "PANTHEON_PANTHEON_SHA") && envPresent("PANTHEON_BFF_BASE_URL", "VITE_BFF_BASE_URL");

  return [
    makeCheck("All critical gates pass.", criticalStatus === "pass" ? "pass" : "fail", {
      owner: criticalStatus === "pass" ? "" : GATE_OWNERS[7],
      evidence: JSON_OUT_PATH,
      note: `${failures.length} failing or missing check(s)`,
    }),
    makeCheck("Exceptions documented with owner and expiry.", failures.length === 0 || exceptionsPresent ? "pass" : "fail", {
      owner: failures.length === 0 || exceptionsPresent ? "" : GATE_OWNERS[7],
      evidence: exceptionsFile || JSON_OUT_PATH,
      note: failures.length === 0 ? "no exceptions needed" : exceptionsPresent ? "exceptions present" : "exceptions missing",
    }),
    makeCheck("Evidence written to `.lovable/audits/`.", evidencePresent ? "pass" : "missing", {
      owner: evidencePresent ? "" : GATE_OWNERS[7],
      evidence: AUDIT_DIR,
      note: `${auditFiles.length} audit file(s) found`,
    }),
    makeCheck("Backend SHA + frontend SHA + BFF URL recorded.", shaRecorded ? "pass" : "missing", {
      owner: shaRecorded ? "" : GATE_OWNERS[7],
      evidence: RUN_URL || ROOT,
      note: shaRecorded ? "release identifiers present" : "one or more release identifiers missing",
    }),
  ];
}

function renderGate(gateNo, checks) {
  const lines = [`## Gate ${gateNo} - ${gateTitles[gateNo]}`, ""];
  for (const check of checks) {
    const pieces = [`- ${checkbox(check.status)} ${check.label} - ${statusLabel(check.status)}`];
    if (check.status !== "pass") {
      pieces.push(`owner: ${check.owner || GATE_OWNERS[gateNo]}`);
      pieces.push(`evidence: ${evidenceLink(check.evidence)}`);
    } else if (check.evidence) {
      pieces.push(`evidence: ${evidenceLink(check.evidence)}`);
    }
    if (check.note) pieces.push(`note: ${check.note}`);
    lines.push(pieces.join("; "));
  }
  return lines.join("\n");
}

function renderSummary(gates) {
  const rows = Object.entries(gates).map(([gateNo, checks]) => {
    const status = gateStatus(checks);
    const failed = checks.filter((check) => check.status !== "pass");
    const owner = failed[0]?.owner || (status === "pass" ? "" : GATE_OWNERS[gateNo]);
    const evidence = failed[0]?.evidence || checks.find((check) => check.evidence)?.evidence || JSON_OUT_PATH;
    return `| Gate ${gateNo} | ${statusLabel(status)} | ${failed.length} | ${escapeMd(owner || "-")} | ${escapeMd(evidenceLink(evidence))} |`;
  });
  return [
    "| Gate | Status | Open checks | Owner | Evidence |",
    "|---|---|---:|---|---|",
    ...rows,
  ].join("\n");
}

function autoTickChecklist(gates) {
  if (!CHECKLIST_TEMPLATE_PATH || !exists(CHECKLIST_TEMPLATE_PATH)) return;
  const template = readText(CHECKLIST_TEMPLATE_PATH);
  const gateStatusMap = {};
  for (const [gateNo, checks] of Object.entries(gates)) {
    gateStatusMap[gateNo] = gateStatus(checks);
  }
  const sha = process.env.PANTHEON_FRONTEND_SHA || process.env.GITHUB_SHA || getGitValue(["rev-parse", "HEAD"]);
  const runAt = new Date().toISOString();

  const updated = template.split("\n").map((line) => {
    const tagMatch = line.match(/<!--\s*release-gate:(\d+)\s*-->/);
    if (!tagMatch) return line;
    const gateNo = tagMatch[1];
    if (gateStatusMap[gateNo] === "pass") {
      return line.replace(/^(\s*)-\s+\[\s*\]/, "$1- [x]");
    }
    return line;
  }).join("\n");

  const header = `<!-- auto-ticked: ${runAt} sha:${sha ? sha.slice(0, 12) : "unknown"} -->\n`;
  fs.mkdirSync(path.dirname(CHECKLIST_OUT_PATH), { recursive: true });
  fs.writeFileSync(CHECKLIST_OUT_PATH, header + updated, "utf8");
  console.log(`[checklist] auto-tick written: ${rel(CHECKLIST_OUT_PATH)}`);
}

function main() {
  const stepOutcomes = getStepOutcomes();
  const routeProbe = analyzeRouteProbe(stepOutcomes);
  const authSmoke = analyzeAuthSmoke(stepOutcomes);
  const hosted = analyzeHostedProbe(stepOutcomes);
  const playwright = analyzePlaywright();

  const gates = {
    0: buildGate0(hosted),
    1: buildGate1(stepOutcomes),
    2: buildGate2(stepOutcomes),
    3: buildGate3(routeProbe, authSmoke),
    4: buildGate4(hosted),
    5: buildGate5(playwright),
    6: buildGate6(playwright),
  };
  gates[7] = buildGate7(gates);

  autoTickChecklist(gates);

  const overall = worstStatus(Object.values(gates).map(gateStatus));
  const generatedAt = new Date().toISOString();
  const markdown = [
    "<!-- pantheon-release-gate-summary -->",
    "# Pantheon FE-BFF Release Gate Summary",
    "",
    `Generated: ${generatedAt}`,
    `Overall: ${statusLabel(overall)}`,
    RUN_URL ? `Run: ${RUN_URL}` : "",
    "",
    renderSummary(gates),
    "",
    ...Object.entries(gates).map(([gateNo, checks]) => renderGate(gateNo, checks)),
    "",
  ].filter((line) => line !== "").join("\n");

  const json = {
    generatedAt,
    overall,
    auditDir: rel(AUDIT_DIR),
    runUrl: RUN_URL,
    checklistOut: CHECKLIST_TEMPLATE_PATH ? rel(CHECKLIST_OUT_PATH) : "",
    gates,
  };

  fs.mkdirSync(path.dirname(OUT_PATH), { recursive: true });
  fs.writeFileSync(OUT_PATH, markdown, "utf8");
  fs.writeFileSync(JSON_OUT_PATH, `${JSON.stringify(json, null, 2)}\n`, "utf8");
  console.log(markdown);

  if (["fail", "missing"].includes(overall)) {
    process.exitCode = 1;
  }
}

main();
