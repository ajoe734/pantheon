#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.PANTHEON_BFF_BASE_URL || "https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io";
const TOKEN = process.env.PANTHEON_BFF_SMOKE_BEARER_TOKEN;
const OUT_DIR = process.env.PANTHEON_AUDIT_OUT_DIR || ".lovable/audits";

if (!TOKEN) {
  console.error("Missing PANTHEON_BFF_SMOKE_BEARER_TOKEN");
  process.exit(2);
}

const readChecks = [
  ["GET", "/bff/me", (j) => j?.user && j?.tenant && Array.isArray(j?.capabilities)],
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

function isListEnvelope(j) {
  return j && Array.isArray(j.items) && j.cursor && typeof j.pageSize === "number" && typeof j.totalCountExact === "boolean";
}

function isErrorEnvelope(j) {
  return j && j.error && typeof j.error.code === "string" && typeof j.error.correlationId === "string";
}

async function call(method, route, body) {
  const headers = {
    "Accept": "application/json",
    "Authorization": `Bearer ${TOKEN}`,
    "X-Request-Id": `req_auth_smoke_${Date.now()}_${Math.random().toString(36).slice(2)}`,
    "X-Correlation-Id": `corr_auth_smoke_${Date.now()}_${Math.random().toString(36).slice(2)}`,
    "X-BFF-Api-Version": "2026-05-07",
  };
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    headers["Idempotency-Key"] = `idk_auth_smoke_${Date.now()}_${Math.random().toString(36).slice(2)}`;
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

const rows = [];

for (const [method, route, validate] of readChecks) {
  const r = await call(method, route);
  const pass = r.status >= 200 && r.status < 300 && validate(r.json);
  rows.push({ ...r, pass, expectation: "2xx + DTO shape" });
}

for (const [method, route, body, allowedCodes] of preconditionChecks) {
  const r = await call(method, route, body);
  const pass =
    (r.status >= 200 && r.status < 300) ||
    (r.status >= 400 && isErrorEnvelope(r.json) && allowedCodes.includes(r.json.error.code));
  rows.push({ ...r, pass, expectation: `2xx command or non-2xx envelope in ${allowedCodes.join("/")}` });
}

const passed = rows.filter(r => r.pass).length;
const now = new Date().toISOString().slice(0, 10);
const md = [
  `# Authenticated BFF Live Smoke`,
  ``,
  `Date: ${new Date().toISOString()}`,
  `Target: ${BASE}`,
  ``,
  `## Summary`,
  ``,
  `Passed: ${passed}/${rows.length}`,
  ``,
  `## Results`,
  ``,
  `| Pass | Status | Method | Path | Expectation | ErrorCode |`,
  `|---|---:|---|---|---|---|`,
  ...rows.map(r => `| ${r.pass ? "✅" : "❌"} | ${r.status} | ${r.method} | ${r.route} | ${r.expectation} | ${r.json?.error?.code ?? ""} |`),
].join("\n");

fs.mkdirSync(OUT_DIR, { recursive: true });
const out = path.join(OUT_DIR, `bff-authenticated-live-smoke-${now}.md`);
fs.writeFileSync(out, md, "utf8");
console.log(md);
if (passed !== rows.length) process.exitCode = 1;
