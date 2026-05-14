#!/usr/bin/env node
/**
 * probe-bff-authenticated-live.mjs
 *
 * Authenticated smoke probe for Pantheon BFF live endpoints.
 * Probes GET read endpoints and write/precondition paths with a bearer token.
 *
 * Envelope fix (FE-INT-GATE-A08):
 * BFF list endpoints respond with {data: {items, cursor, pageSize, totalCountExact}}.
 * isListEnvelope unwraps the outer `data` key before checking for `items`.
 * Previously the validator read j.items directly, which caused all 27 GET read
 * endpoints to be mis-classified as fail even when returning HTTP 200.
 * Non-list endpoints (/bff/me) use a separate isMeResponse validator and are
 * not affected by this fix.
 */
import fs from "node:fs";
import path from "node:path";
import { URL } from "node:url";

const BFF_BASE_URL = (process.env.PANTHEON_BFF_BASE_URL || process.env.VITE_BFF_BASE_URL || "").replace(/\/$/, "");
const BEARER_TOKEN = process.env.PANTHEON_BFF_SMOKE_BEARER_TOKEN || process.env.BFF_AUTH_TOKEN || "";
const AUDIT_DIR = process.env.PANTHEON_AUDIT_OUT_DIR || ".lovable/audits";
const ROOT = process.cwd();

if (!BFF_BASE_URL) {
  console.error("[auth-smoke] PANTHEON_BFF_BASE_URL is not set; cannot probe.");
  process.exitCode = 1;
  process.exit();
}

if (!BEARER_TOKEN) {
  console.error("[auth-smoke] PANTHEON_BFF_SMOKE_BEARER_TOKEN is not set; cannot probe authenticated endpoints.");
  process.exitCode = 1;
  process.exit();
}

/**
 * isListEnvelope — validates BFF list response envelope.
 *
 * FE-INT-GATE-A08 fix: BFF wraps list responses as {data: {items: [...], ...}}.
 * This validator unwraps `data` first, then checks for an `items` array inside.
 * The previous broken form was: `Array.isArray(j?.items)` — that checked the
 * wrong level and caused all 27 GET read endpoints to report fail.
 */
function isListEnvelope(j) {
  const inner = j?.data;
  return inner != null && Array.isArray(inner.items);
}

/**
 * isMeResponse — validates the /bff/me endpoint.
 *
 * /bff/me is a non-list identity endpoint. BFF may return a flat object or
 * wrap it as {data: {...}}. Either shape is accepted as long as the object
 * contains at least one user-identity field.
 */
function isMeResponse(j) {
  const candidate = j?.data ?? j;
  return (
    candidate != null &&
    typeof candidate === "object" &&
    !Array.isArray(candidate) &&
    (
      typeof candidate.user_id === "string" ||
      typeof candidate.id === "string" ||
      typeof candidate.email === "string" ||
      typeof candidate.sub === "string"
    )
  );
}

/**
 * isBffErrorEnvelope — validates BFF typed error response.
 *
 * Write/precondition endpoints probed with dev-only IDs are expected to
 * return a typed 4xx error rather than a successful list. This is the
 * "expected BffErrorEnvelope" check from the release gate.
 */
function isBffErrorEnvelope(j) {
  if (j == null || typeof j !== "object") return false;
  return (
    typeof j.error === "string" ||
    typeof j.code === "string" ||
    typeof j.detail === "string" ||
    (typeof j.error === "object" && j.error != null && typeof j.error.code === "string")
  );
}

async function fetchEndpoint(route, { method = "GET", body } = {}) {
  const url = new URL(route, BFF_BASE_URL).toString();
  const headers = {
    Authorization: `Bearer ${BEARER_TOKEN}`,
    Accept: "application/json",
    "X-Request-Id": `smoke-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
  };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  try {
    const res = await fetch(url, {
      method,
      headers,
      signal: AbortSignal.timeout(15000),
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    let json = null;
    try {
      json = await res.json();
    } catch {
      // ignore non-JSON body
    }
    return { status: res.status, json, ok: res.ok, error: null };
  } catch (err) {
    return { status: 0, json: null, ok: false, error: String(err) };
  }
}

// Canonical list endpoints checked by the release gate aggregate (Gate 3).
const LIST_ENDPOINTS = [
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
  "/bff/v5/loop-runs",
  "/bff/v5/sentinel/findings",
  "/bff/v5/interventions",
  "/bff/v5/execution/persona-health",
];

// Write/precondition endpoints probed with dev-only IDs.
// Expected to return typed 4xx error envelopes rather than success.
const WRITE_ENDPOINTS = [
  { route: "/bff/actions/strategies/strategy-dev/promote", method: "POST" },
  { route: "/bff/approvals/approval-dev/decide", method: "POST" },
  { route: "/bff/v5/interventions/intervention-dev/decide", method: "POST" },
];

async function main() {
  console.log(`[auth-smoke] BFF base: ${BFF_BASE_URL}`);
  console.log(`[auth-smoke] Probing /bff/me + ${LIST_ENDPOINTS.length} list endpoints + ${WRITE_ENDPOINTS.length} write endpoints`);

  const results = [];
  let passed = 0;
  let total = 0;

  // /bff/me — non-list identity endpoint; uses isMeResponse validator
  {
    const { status, json, error } = await fetchEndpoint("/bff/me");
    const valid = !error && status === 200 && isMeResponse(json);
    total++;
    if (valid) passed++;
    results.push({
      route: "/bff/me",
      method: "GET",
      status: error ? "ERR" : String(status),
      passed: valid,
      note: error ? error.slice(0, 80) : valid ? "MeResponse identity field present" : `unexpected shape or status ${status}`,
    });
  }

  // List endpoints — use isListEnvelope (data.items unwrap)
  for (const route of LIST_ENDPOINTS) {
    const { status, json, error } = await fetchEndpoint(route);
    const valid = !error && status === 200 && isListEnvelope(json);
    total++;
    if (valid) passed++;
    results.push({
      route,
      method: "GET",
      status: error ? "ERR" : String(status),
      passed: valid,
      note: error
        ? error.slice(0, 80)
        : valid
        ? "list envelope ok — data.items array present"
        : `isListEnvelope false: status=${status} data.items=${json?.data?.items != null ? "present" : "absent"}`,
    });
  }

  // Write/precondition endpoints — expected 4xx typed error envelope
  for (const { route, method } of WRITE_ENDPOINTS) {
    const { status, json, error } = await fetchEndpoint(route, { method, body: {} });
    const statusOk = !error && status >= 400 && status < 500;
    const valid = statusOk && isBffErrorEnvelope(json);
    total++;
    if (valid) passed++;
    results.push({
      route,
      method,
      status: error ? "ERR" : String(status),
      passed: valid,
      note: error
        ? error.slice(0, 80)
        : valid
        ? "typed error envelope ok"
        : `unexpected: status=${status} envelope=${isBffErrorEnvelope(json)}`,
    });
  }

  const summary = `Passed: ${passed}/${total}`;
  console.log(`\n[auth-smoke] ${summary}`);

  const now = new Date();
  const ts = now.toISOString().replace(/[:.]/g, "-");

  const mdRows = results.map((r) => {
    const passCell = r.passed ? "✅ pass" : "❌ fail";
    return `| ${passCell} | ${r.status} | ${r.method} | ${r.route} | ${r.note} |`;
  });

  const failed = results.filter((r) => !r.passed);

  const md = [
    "# BFF Authenticated Live Smoke",
    "",
    `Generated: ${now.toISOString()}`,
    `BFF base: ${BFF_BASE_URL}`,
    `${summary}`,
    "",
    "## Envelope note (FE-INT-GATE-A08)",
    "",
    "BFF list endpoints return `{data: {items, cursor, pageSize, totalCountExact}}`.",
    "The `isListEnvelope` validator unwraps the outer `data` key before checking for `items`.",
    "Non-list endpoints (e.g. `/bff/me`) use a separate `isMeResponse` validator that accepts",
    "both flat `{user_id, ...}` and wrapped `{data: {user_id, ...}}` shapes.",
    "Write endpoints are probed with dev-only IDs and are expected to return typed 4xx error envelopes.",
    "",
    "## Results",
    "",
    "| Pass | Status | Method | Route | Note |",
    "|---|---|---|---|---|",
    ...mdRows,
    "",
    failed.length > 0
      ? `## Failures (${failed.length})\n\n${failed.map((r) => `- ${r.method} ${r.route}: ${r.note}`).join("\n")}\n`
      : "## All checks passed\n",
  ].join("\n");

  const auditDir = path.resolve(ROOT, AUDIT_DIR);
  fs.mkdirSync(auditDir, { recursive: true });
  const mdFile = path.join(auditDir, `bff-authenticated-live-smoke-${ts}.md`);
  fs.writeFileSync(mdFile, md, "utf8");
  console.log(`[auth-smoke] Evidence written: ${mdFile}`);
  console.log("");
  console.log(md);

  if (passed < total) {
    process.exitCode = 1;
  }
}

main().catch((err) => {
  console.error("[auth-smoke] Fatal:", err);
  process.exitCode = 1;
});
