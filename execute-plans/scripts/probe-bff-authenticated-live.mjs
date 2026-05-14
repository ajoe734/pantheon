#!/usr/bin/env node
/**
 * probe-bff-authenticated-live.mjs
 *
 * Authenticated smoke probe for Pantheon BFF live endpoints.
 * Probes GET read endpoints and write/precondition paths with a bearer token.
 *
 * Envelope correction (FE-INT-GATE-A11):
 * The FE-INT-GATE-A11 target list envelope uses parallel top-level arrays:
 * {data: [...], items: [...], page_info: {next_page_token, total}, meta: {...}}.
 * isListEnvelope validates top-level `items` plus `page_info.total`.
 * During cutover, some live surfaces still return documented route DTO variants
 * such as {items, page_info, meta}, {data, page_info, meta}, alert summaries,
 * or v5 list DTOs; the smoke accepts those explicitly instead of unwrapping a
 * nonexistent {data: {items}} wrapper.
 * Non-list endpoints (/bff/me) use a separate isMeResponse validator for the
 * wrapped {data: {user, tenant, capabilities, ...}} identity response.
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
 * FE-INT-GATE-A11 fix: BFF list responses expose top-level `items` and
 * `page_info` next to a parallel top-level `data` array. `{data: {items}}`
 * is not the live shape and must not be unwrapped here.
 */
function isListEnvelope(j) {
  return Array.isArray(j?.items) && typeof j?.page_info?.total === "number";
}

function jsonKeys(j) {
  return j != null && typeof j === "object" && !Array.isArray(j) ? Object.keys(j) : [];
}

function isObject(j) {
  return j != null && typeof j === "object" && !Array.isArray(j);
}

function classifyReadEnvelope(route, j) {
  if (isListEnvelope(j)) {
    return { valid: true, note: "list envelope ok — top-level items array + page_info.total present" };
  }
  if (Array.isArray(j?.items) && isObject(j?.page_info) && "next_page_token" in j.page_info) {
    return { valid: true, note: "list envelope ok — top-level items array + page_info.next_page_token present" };
  }
  if (Array.isArray(j?.data) && typeof j?.page_info?.total === "number") {
    return { valid: true, note: "list envelope ok — top-level data array + page_info.total present" };
  }
  if (route === "/bff/alerts" && Array.isArray(j?.alerts) && isObject(j?.summary) && typeof j?.meta?.snapshot_at === "string") {
    return { valid: true, note: "alert summary DTO ok — alerts array + summary + meta.snapshot_at present" };
  }
  if (Array.isArray(j?.items) && typeof j?.count === "number") {
    return { valid: true, note: "v5 list DTO ok — top-level items array + count present" };
  }
  if (Array.isArray(j?.items) && typeof j?.meta?.snapshot_at === "string") {
    return { valid: true, note: "list DTO ok — top-level items array + meta.snapshot_at present" };
  }
  return {
    valid: false,
    note: `read envelope false: keys=${jsonKeys(j).join(",") || "none"} items=${Array.isArray(j?.items) ? "array" : "absent"} data=${Array.isArray(j?.data) ? "array" : typeof j?.data} page_info.total=${typeof j?.page_info?.total}`,
  };
}

/**
 * isMeResponse — validates the /bff/me endpoint.
 *
 * /bff/me is a non-list identity endpoint. BFF currently returns
 * {data: {user, tenant, capabilities, ...}, meta: {...}}. Accept the same
 * identity fields at the root as a flat fallback, but require the frontend
 * MeResponse essentials instead of treating any id-like field as sufficient.
 */
function isMeResponse(j) {
  const candidate = j?.data ?? j;
  const hasUser =
    candidate?.user != null ||
    candidate?.current_user != null ||
    candidate?.currentUser != null ||
    typeof candidate?.user_id === "string" ||
    typeof candidate?.id === "string" ||
    typeof candidate?.email === "string" ||
    typeof candidate?.sub === "string";
  const hasTenant = candidate?.tenant != null || typeof candidate?.tenant_id === "string";
  const hasCapabilities = Array.isArray(candidate?.capabilities);
  return (
    candidate != null &&
    typeof candidate === "object" &&
    !Array.isArray(candidate) &&
    hasUser &&
    hasTenant &&
    hasCapabilities
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
  const detail = j.detail;
  return (
    typeof j.error === "string" ||
    typeof j.code === "string" ||
    typeof detail === "string" ||
    Array.isArray(detail) ||
    (typeof j.error === "object" && j.error != null && typeof j.error.code === "string") ||
    (typeof detail === "object" && detail != null && typeof detail.error?.code === "string") ||
    (typeof detail === "object" && detail != null && typeof detail.code === "string")
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
  { route: "/bff/approvals/approval-dev/decide", method: "POST", body: "invalid-smoke-payload" },
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
      note: error ? error.slice(0, 80) : valid ? "MeResponse user/tenant/capabilities present" : `unexpected shape or status ${status}`,
    });
  }

  // List endpoints — use isListEnvelope (top-level items + page_info.total)
  for (const route of LIST_ENDPOINTS) {
    const { status, json, error } = await fetchEndpoint(route);
    const envelope = classifyReadEnvelope(route, json);
    const valid = !error && status === 200 && envelope.valid;
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
        ? envelope.note
        : `status=${status} ${envelope.note}`,
    });
  }

  // Write/precondition endpoints — expected 4xx typed error envelope
  for (const { route, method, body = {} } of WRITE_ENDPOINTS) {
    const { status, json, error } = await fetchEndpoint(route, { method, body });
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
        ? "typed/validation error envelope ok"
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
    "## Envelope note (FE-INT-GATE-A11)",
    "",
    "The FE-INT-GATE-A11 target list envelope is `{data: [...], items: [...], page_info: {next_page_token, total}, meta: {...}}`.",
    "`data` and `items` are parallel top-level arrays; `isListEnvelope` checks top-level `items` plus `page_info.total`.",
    "During the live cutover, the smoke also accepts explicit route DTO variants already served by BFF:",
    "`{items, page_info, meta}`, `{data, page_info, meta}`, alert summary DTOs, and v5 `{items, count}` / `{items, meta}` lists.",
    "It does not unwrap or accept `{data: {items: [...]}}` for list endpoints.",
    "Non-list endpoints (e.g. `/bff/me`) use a separate `isMeResponse` validator that accepts",
    "wrapped `{data: {user, tenant, capabilities, ...}}` or the same fields at the flat root.",
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
