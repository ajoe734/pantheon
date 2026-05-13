#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.PANTHEON_BFF_BASE_URL || process.env.VITE_BFF_BASE_URL || "https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io";
const OUT_DIR = process.env.PANTHEON_AUDIT_OUT_DIR || ".lovable/audits";
const AUTH_TOKEN = process.env.PANTHEON_BFF_SMOKE_BEARER_TOKEN || "";
const mode = process.argv.includes("--authenticated") ? "authenticated" : "anonymous";

const routes = [
  ["GET", "/health"],
  ["GET", "/healthz"],
  ["GET", "/readyz"],
  ["GET", "/openapi.json"],
  ["GET", "/bff/events/stream"],
  ["GET", "/bff/me"],
  ["POST", "/bff/auth/refresh"],
  ["POST", "/bff/logout"],
  ["POST", "/bff/actions/strategies/strategy-dev/promote"],
  ["GET", "/bff/strategies"],
  ["GET", "/bff/strategies/strategy-dev"],
  ["GET", "/bff/personas"],
  ["GET", "/bff/personas/persona-dev"],
  ["GET", "/bff/capital-pools"],
  ["GET", "/bff/capital-pools/capital-dev"],
  ["GET", "/bff/rebalances"],
  ["GET", "/bff/deployments"],
  ["GET", "/bff/evolution-programs"],
  ["GET", "/bff/jobs"],
  ["GET", "/bff/approvals"],
  ["POST", "/bff/approvals/approval-dev/decide"],
  ["POST", "/bff/approvals/batch-decide"],
  ["GET", "/bff/alerts"],
  ["POST", "/bff/alerts/alert-dev/acknowledge"],
  ["GET", "/bff/incidents"],
  ["GET", "/bff/audit"],
  ["GET", "/bff/artifacts"],
  ["GET", "/bff/runtimes"],
  ["GET", "/bff/mcp-servers"],
  ["POST", "/bff/mcp-servers/mcp-dev/import-tools"],
  ["GET", "/bff/mcp-tools"],
  ["GET", "/bff/skills"],
  ["GET", "/bff/channels"],
  ["GET", "/bff/tools"],
  ["GET", "/bff/ranking-formulas"],
  ["GET", "/bff/research-experiments"],
  ["GET", "/bff/agora/signals"],
  ["GET", "/bff/agora/inbox"],
  ["GET", "/bff/agora/journal"],
  ["GET", "/bff/agora/postmortems"],
  ["GET", "/bff/agora/ask/sessions"],
  ["GET", "/bff/v5/loop-runs"],
  ["GET", "/bff/v5/sentinel/findings"],
  ["GET", "/bff/v5/interventions"],
  ["POST", "/bff/v5/interventions/intervention-dev/decide"],
  ["GET", "/bff/v5/execution/persona-health"],
];

function bodyFor(method, route) {
  if (method === "GET") return undefined;
  if (route.includes("/decide")) return JSON.stringify({ decision: "defer", memo: "route probe noop" });
  if (route.includes("/acknowledge")) return JSON.stringify({ memo: "route probe noop" });
  if (route.includes("/import-tools")) return JSON.stringify({ schemaJson: { probe: true }, memo: "route probe noop" });
  if (route.includes("/auth/refresh") || route.includes("/logout")) return JSON.stringify({});
  if (route.includes("/actions/")) return JSON.stringify({ memo: "route probe noop", expectedVersion: 1 });
  return JSON.stringify({});
}

async function probe(method, route) {
  const headers = {
    "Accept": "application/json",
    "X-Request-Id": `req_probe_${Date.now()}_${Math.random().toString(36).slice(2)}`,
    "X-BFF-Api-Version": "2026-05-07",
  };
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    headers["Idempotency-Key"] = `idk_probe_${Date.now()}_${Math.random().toString(36).slice(2)}`;
  }
  if (mode === "authenticated" && AUTH_TOKEN) {
    headers["Authorization"] = `Bearer ${AUTH_TOKEN}`;
  }

  const url = `${BASE}${route}`;
  const started = Date.now();
  try {
    const res = await fetch(url, {
      method,
      headers,
      body: bodyFor(method, route),
      signal: AbortSignal.timeout(15000),
    });
    return { method, route, status: res.status, ms: Date.now() - started };
  } catch (err) {
    return { method, route, status: "ERR", ms: Date.now() - started, error: err instanceof Error ? err.message : String(err) };
  }
}

const results = [];
for (const [method, route] of routes) {
  results.push(await probe(method, route));
}

const counts = {};
for (const r of results) counts[r.status] = (counts[r.status] || 0) + 1;

const missing = results.filter(r => r.status === 404);
const transportErrors = results.filter(r => r.status === "ERR");
const now = new Date().toISOString().slice(0, 10);
const md = [
  `# BFF Route Probe — ${mode}`,
  ``,
  `Date: ${new Date().toISOString()}`,
  `Target: ${BASE}`,
  ``,
  `## Counts`,
  ``,
  "```json",
  JSON.stringify(counts, null, 2),
  "```",
  ``,
  `## Verdict`,
  ``,
  `- Canonical 404 count: ${missing.length}`,
  `- Transport errors: ${transportErrors.length}`,
  ``,
  `## Results`,
  ``,
  `| Status | Method | Path | ms |`,
  `|---:|---|---|---:|`,
  ...results.map(r => `| ${r.status} | ${r.method} | ${r.route} | ${r.ms} |`),
  ``,
  `## Gate`,
  ``,
  missing.length === 0
    ? `PASS: no canonical route returned 404.`
    : `FAIL: ${missing.length} canonical routes returned 404.`,
].join("\n");

fs.mkdirSync(OUT_DIR, { recursive: true });
const out = path.join(OUT_DIR, `bff-route-probe-${mode}-${now}.md`);
fs.writeFileSync(out, md, "utf8");
console.log(md);
if (missing.length || transportErrors.length) process.exitCode = 1;
