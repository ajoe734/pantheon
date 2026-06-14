#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.PANTHEON_BFF_BASE_URL || "https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io";
let TOKEN = process.env.PANTHEON_BFF_ACCESS_TOKEN || process.env.PANTHEON_BFF_SMOKE_BEARER_TOKEN;
const APPROVAL_RACE_ID = process.env.PANTHEON_BFF_APPROVAL_RACE_ID || "";
const APPROVAL_RACE_TOKEN_A = process.env.PANTHEON_BFF_APPROVAL_RACE_TOKEN_A || "";
const APPROVAL_RACE_TOKEN_B = process.env.PANTHEON_BFF_APPROVAL_RACE_TOKEN_B || "";
const CLIENT_ID = process.env.PANTHEON_BFF_OIDC_CLIENT_ID;
const CLIENT_SECRET = process.env.PANTHEON_BFF_OIDC_CLIENT_SECRET;
const DEV_LOGIN_PATH = process.env.PANTHEON_BFF_DEV_LOGIN_PATH || "/bff/auth/dev-login";
const OUT_DIR = process.env.PANTHEON_AUDIT_OUT_DIR || ".lovable/audits";

async function acquireToken() {
  if (TOKEN) return TOKEN;
  if (!CLIENT_ID || !CLIENT_SECRET) return "";
  const res = await fetch(`${BASE}${DEV_LOGIN_PATH}`, {
    method: "POST",
    headers: {
      "Accept": "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      grant_type: "client_credentials",
      client_id: CLIENT_ID,
      client_secret: CLIENT_SECRET,
    }),
    signal: AbortSignal.timeout(20000),
  });
  const text = await res.text();
  let json;
  try { json = text ? JSON.parse(text) : null; } catch { json = null; }
  if (!res.ok || !json?.access_token) {
    console.error(`Failed to acquire BFF JWT from ${DEV_LOGIN_PATH}: ${res.status} ${text.slice(0, 300)}`);
    process.exit(2);
  }
  TOKEN = json.access_token;
  return TOKEN;
}

await acquireToken();

if (!TOKEN) {
  console.error("Missing PANTHEON_BFF_ACCESS_TOKEN or PANTHEON_BFF_OIDC_CLIENT_ID/CLIENT_SECRET");
  process.exit(2);
}

const readChecks = [
  ["GET", "/bff/me", isMeEnvelope],
  ["GET", "/bff/strategies", isListEnvelope],
  ["GET", "/bff/personas", isListEnvelope],
  ["GET", "/bff/capital-pools", isListEnvelope],
  ["GET", "/bff/rebalances", isListEnvelope],
  ["GET", "/bff/deployments", isListEnvelope],
  ["GET", "/bff/jobs", isListEnvelope],
  ["GET", "/bff/alerts", isListEnvelope],
  ["GET", "/bff/incidents", isListEnvelope],
  ["GET", "/bff/audit", isListEnvelope],
  ["GET", "/bff/artifacts", isListEnvelope],
  ["GET", "/bff/runtimes", isListEnvelope],
  ["GET", "/bff/mcp-servers", isListEnvelope],
  ["GET", "/bff/mcp-tools", isListEnvelope],
  ["GET", "/bff/skills", isListEnvelope],
  ["GET", "/bff/channels", isListEnvelope],
  ["GET", "/bff/tools", isListEnvelope],
  ["GET", "/bff/ranking-formulas", isListEnvelope],
  ["GET", "/bff/research-experiments", isListEnvelope],
  ["GET", "/bff/agora/signals", isListEnvelope],
  ["GET", "/bff/agora/inbox", isListEnvelope],
  ["GET", "/bff/agora/journal", isListEnvelope],
  ["GET", "/bff/agora/postmortems", isListEnvelope],
  ["GET", "/bff/agora/ask/sessions", isListEnvelope],
  ["GET", "/bff/v5/loop-runs", isListEnvelope],
  ["GET", "/bff/v5/sentinel/findings", isListEnvelope],
  ["GET", "/bff/v5/interventions", isListEnvelope],
  ["GET", "/bff/v5/execution/persona-health", isListEnvelope],
];

const preconditionChecks = [
  ["POST", "/bff/actions/strategies/strategy-dev/promote", { memo: "authenticated smoke expects precondition envelope", expectedVersion: 1 }, ["CONFIRM_TOKEN_REQUIRED", "APPROVAL_REQUIRED", "TWO_MAN_REQUIRED", "STATE_CONFLICT", "RESOURCE_NOT_FOUND", "VALIDATION_FAILED"]],
  ["POST", "/bff/approvals/approval-dev/decide", { decision: "defer", memo: "authenticated smoke noop", expectedVersion: 1 }, ["STATE_CONFLICT", "RESOURCE_NOT_FOUND", "VALIDATION_FAILED", "PERMISSION_DENIED", "CAPABILITY_MISSING"]],
  ["POST", "/bff/v5/interventions/intervention-dev/decide", { decision: "defer", memo: "authenticated smoke noop", expectedVersion: 1 }, ["STATE_CONFLICT", "RESOURCE_NOT_FOUND", "VALIDATION_FAILED", "PERMISSION_DENIED", "CAPABILITY_MISSING"]],
];

const bffAllowedErrorCodes = [
  "INSUFFICIENT_ROLE",
  "PRECONDITION_NOT_MET",
  "INVALID_PARAMS",
  "INVALID_REQUEST",
  "INVALID_STATE",
  "OBJECT_NOT_FOUND",
  "MFA_REQUIRED",
];

function isMeEnvelope(j) {
  const data = j?.data && typeof j.data === "object" ? j.data : j;
  return data?.user && data?.tenant && Array.isArray(data?.capabilities);
}

function isListEnvelope(j) {
  return j && Array.isArray(j.items) && j.cursor && typeof j.pageSize === "number" && typeof j.totalCountExact === "boolean";
}

function isErrorEnvelope(j) {
  const error = j?.error || j?.detail?.error;
  return Boolean(error && typeof error.code === "string");
}

function errorCodeFrom(j) {
  return j?.error?.code || j?.detail?.error?.code || "";
}

function bearerToken(token) {
  return String(token || "").replace(/^Bearer\s+/i, "");
}

async function callWithToken(method, route, body, token, idempotencyScope = "auth_smoke") {
  const headers = {
    "Accept": "application/json",
    "Authorization": `Bearer ${bearerToken(token)}`,
    "X-Request-Id": `req_${idempotencyScope}_${Date.now()}_${Math.random().toString(36).slice(2)}`,
    "X-Correlation-Id": `corr_${idempotencyScope}_${Date.now()}_${Math.random().toString(36).slice(2)}`,
    "X-BFF-Api-Version": "2026-05-07",
  };
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    headers["Idempotency-Key"] = `idk_${idempotencyScope}_${Date.now()}_${Math.random().toString(36).slice(2)}`;
  }
  const res = await fetch(`${BASE}${route}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(20000),
  });
  const text = await res.text();
  let json;
  try { json = text ? JSON.parse(text) : null; } catch { json = null; }
  return {
    method, route, status: res.status,
    requestId: res.headers.get("X-Request-Id"),
    correlationId: res.headers.get("X-Correlation-Id"),
    json,
  };
}

async function call(method, route, body) {
  return callWithToken(method, route, body, TOKEN);
}

function approvalRaceSafeError(r) {
  return r.status >= 400 &&
    isErrorEnvelope(r.json) &&
    [
      "RESOURCE_NOT_FOUND",
      "OBJECT_NOT_FOUND",
      "NOT_FOUND",
      "STATE_CONFLICT",
      "VERSION_CONFLICT",
      "CONFLICT",
      "VALIDATION_FAILED",
      "PRECONDITION_NOT_MET",
      "APPROVAL_ALREADY_DECIDED",
    ].includes(errorCodeFrom(r.json));
}

const rows = [];

for (const [method, route, validate] of readChecks) {
  const r = await call(method, route);
  const pass = r.status >= 200 && r.status < 300 && validate(r.json);
  rows.push({ ...r, pass, expectation: "2xx + DTO shape" });
}

for (const [method, route, body, allowedCodes] of preconditionChecks) {
  const r = await call(method, route, body);
  const effectiveAllowedCodes = new Set([...allowedCodes, ...bffAllowedErrorCodes]);
  const pass =
    (r.status >= 200 && r.status < 300) ||
    (r.status >= 400 && isErrorEnvelope(r.json) && effectiveAllowedCodes.has(errorCodeFrom(r.json)));
  rows.push({ ...r, pass, expectation: `2xx command or non-2xx envelope in ${allowedCodes.join("/")}` });
}

if (APPROVAL_RACE_ID && APPROVAL_RACE_TOKEN_A && APPROVAL_RACE_TOKEN_B && bearerToken(APPROVAL_RACE_TOKEN_A) !== bearerToken(APPROVAL_RACE_TOKEN_B)) {
  const route = `/bff/approvals/${encodeURIComponent(APPROVAL_RACE_ID)}/decide`;
  const body = { decision: "approve", memo: "authenticated live approval race probe" };
  const [a, b] = await Promise.all([
    callWithToken("POST", route, body, APPROVAL_RACE_TOKEN_A, "approval_race_a"),
    callWithToken("POST", route, body, APPROVAL_RACE_TOKEN_B, "approval_race_b"),
  ]);
  const accepted = [a, b].filter(r => r.status >= 200 && r.status < 300);
  const safeErrors = [a, b].filter(approvalRaceSafeError);
  const pass = accepted.length <= 1 &&
    ((accepted.length === 1 && safeErrors.length === 1) || safeErrors.length === 2);
  rows.push({
    method: "POST",
    route: `${route}#race`,
    status: `${a.status}/${b.status}`,
    requestId: `${a.requestId || ""}/${b.requestId || ""}`,
    correlationId: `${a.correlationId || ""}/${b.correlationId || ""}`,
    json: { error: { code: [errorCodeFrom(a.json), errorCodeFrom(b.json)].filter(Boolean).join("/") } },
    pass,
    expectation: "multi-operator race: 1 accepted + 1 safe envelope, or 2 safe envelopes",
  });
}

const passed = rows.filter(r => r.pass).length;
const now = new Date().toISOString().slice(0, 10);
const md = [
  `# Authenticated BFF Live Smoke`,
  ``,
  `Date: ${new Date().toISOString()}`,
  `Target: ${BASE}`,
  `Approval race configured: ${Boolean(APPROVAL_RACE_ID && APPROVAL_RACE_TOKEN_A && APPROVAL_RACE_TOKEN_B && bearerToken(APPROVAL_RACE_TOKEN_A) !== bearerToken(APPROVAL_RACE_TOKEN_B))}`,
  ``,
  `## Summary`,
  ``,
  `Passed: ${passed}/${rows.length}`,
  ``,
  `## Results`,
  ``,
  `| Pass | Status | Method | Path | Expectation | ErrorCode |`,
  `|---|---:|---|---|---|---|`,
  ...rows.map(r => `| ${r.pass ? "✅" : "❌"} | ${r.status} | ${r.method} | ${r.route} | ${r.expectation} | ${errorCodeFrom(r.json)} |`),
].join("\n");

fs.mkdirSync(OUT_DIR, { recursive: true });
const out = path.join(OUT_DIR, `bff-authenticated-live-smoke-${now}.md`);
fs.writeFileSync(out, md, "utf8");
console.log(md);
if (passed !== rows.length) process.exitCode = 1;
