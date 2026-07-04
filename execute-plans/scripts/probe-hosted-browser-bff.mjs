#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";

const FE_BASE = (process.env.PANTHEON_FE_BASE_URL || "https://pantheon-dev.lovable.app").replace(/\/+$/, "");
const BFF_BASE = (process.env.PANTHEON_BFF_BASE_URL || "https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io").replace(/\/+$/, "");
const OLD_BFF_URL = process.env.PANTHEON_OLD_BFF_URL || "pantheon-ai-system-front-dev";
const TRADING_ROOM_PATH = process.env.PANTHEON_TRADING_ROOM_PATH || "/agora/trading-room";
const OUT_DIR = process.env.PANTHEON_AUDIT_OUT_DIR || ".lovable/audits";

let chromium;
try {
  ({ chromium } = await import("@playwright/test"));
} catch {
  console.error("Missing @playwright/test. Install with: npm install -D @playwright/test && npx playwright install chromium");
  process.exit(2);
}

function absoluteFeUrl(pathname) {
  return new URL(pathname, `${FE_BASE}/`).toString();
}

function isBffRequest(url) {
  if (url.startsWith(BFF_BASE)) return true;
  try {
    return new URL(url).pathname.startsWith("/bff/");
  } catch {
    return false;
  }
}

function requestPath(url) {
  try {
    const parsed = new URL(url);
    if (url.startsWith(BFF_BASE)) return `${parsed.pathname}${parsed.search}`;
    return `${parsed.origin}${parsed.pathname}${parsed.search}`;
  } catch {
    return url.replace(BFF_BASE, "");
  }
}

function countOccurrences(haystack, needle) {
  if (!needle) return 0;
  return haystack.split(needle).length - 1;
}

function headerValue(headers, name) {
  const lower = name.toLowerCase();
  return headers[lower] || headers[name] || "";
}

async function headProbe(url) {
  try {
    const res = await fetch(url, { method: "HEAD", redirect: "follow", cache: "no-store" });
    return {
      url,
      ok: res.ok,
      status: res.status,
      cacheControl: res.headers.get("cache-control") || "",
      etag: res.headers.get("etag") || "",
      lastModified: res.headers.get("last-modified") || "",
    };
  } catch (error) {
    return {
      url,
      ok: false,
      status: 0,
      cacheControl: "",
      etag: "",
      lastModified: "",
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

async function deploymentProbe(url) {
  try {
    const res = await fetch(url, { cache: "no-store" });
    const text = await res.text();
    let json = {};
    try {
      json = text ? JSON.parse(text) : {};
    } catch {}
    const deploymentId =
      json.deployment_id ||
      json.deploymentId ||
      json.frontend_sha ||
      json.frontendSha ||
      json.commit ||
      json.sha ||
      json.git_sha ||
      json.version ||
      "";
    return {
      url,
      ok: res.ok,
      status: res.status,
      cacheControl: res.headers.get("cache-control") || "",
      deploymentId: deploymentId ? String(deploymentId) : "",
      keys: Object.keys(json).sort().join(", "),
    };
  } catch (error) {
    return {
      url,
      ok: false,
      status: 0,
      cacheControl: "",
      deploymentId: "",
      keys: "",
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

const targetUrl = absoluteFeUrl(TRADING_ROOM_PATH);
const deploymentUrl = absoluteFeUrl("/deployment.json");

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();

const requests = [];
const responses = [];
const failed = [];
const consoleErrors = [];

page.on("request", req => {
  const url = req.url();
  if (isBffRequest(url)) requests.push({ method: req.method(), url });
});
page.on("response", res => {
  const url = res.url();
  if (!isBffRequest(url)) return;
  const headers = res.headers();
  responses.push({
    status: res.status(),
    method: res.request().method(),
    url,
    requestId: headerValue(headers, "x-request-id"),
    correlationId: headerValue(headers, "x-correlation-id"),
  });
});
page.on("requestfailed", req => {
  const url = req.url();
  if (isBffRequest(url)) failed.push({ method: req.method(), url, failure: req.failure()?.errorText });
});
page.on("console", msg => {
  if (msg.type() === "error") consoleErrors.push(msg.text());
});

let gotoError = "";
try {
  await page.goto(targetUrl, { waitUntil: "networkidle", timeout: 60000 });
} catch (error) {
  gotoError = error instanceof Error ? error.message : String(error);
}

const html = await page.content().catch(() => "");
const pageText = await page.locator("body").innerText({ timeout: 5000 }).catch(() => "");
const scripts = await page.locator("script[src]").evaluateAll(nodes => nodes.map(n => n.src)).catch(() => []);

const errorDiagnostics = await page.locator("[data-testid='trading-room-error']").evaluate(el => ({
  status: el.getAttribute("data-bff-status") || "",
  code: el.getAttribute("data-bff-code") || "",
  requestId: el.getAttribute("data-request-id") || "",
  correlationId: el.getAttribute("data-correlation-id") || "",
  text: (el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 600),
})).catch(() => null);

let bundleText = "";
for (const scriptUrl of scripts) {
  try {
    const res = await fetch(scriptUrl, { cache: "no-store" });
    bundleText += await res.text();
  } catch {}
}

const assetUrl = scripts.find(scriptUrl => {
  try {
    return new URL(scriptUrl).pathname.startsWith("/assets/");
  } catch {
    return false;
  }
});
const shellHeaders = await headProbe(targetUrl);
const deployment = await deploymentProbe(deploymentUrl);
const assetHeaders = assetUrl ? await headProbe(assetUrl) : null;

await browser.close();

const bundleAndHtml = `${bundleText}\n${html}`;
const containsBff = bundleAndHtml.includes(BFF_BASE);
const oldHitCount = countOccurrences(bundleAndHtml, OLD_BFF_URL);
const containsOld = oldHitCount > 0;
const unauthorized = responses.filter(r => r.status === 401 || r.status === 403);
const tradingRoomRequestCount = requests.filter(r => {
  try {
    return new URL(r.url).pathname === "/bff/agora/trading-room";
  } catch {
    return false;
  }
}).length;

const genericFailureText = /Failed to load Trading Room\.?/i.test(pageText);
const hasDiagnosticErrorState = Boolean(
  errorDiagnostics &&
  (errorDiagnostics.status || errorDiagnostics.code || errorDiagnostics.requestId || errorDiagnostics.correlationId),
);
const hasTextDiagnostics = /(HTTP\s+\d{3}|Network failure|Request ID|Correlation)/i.test(pageText);
const genericOnlyTradingRoomFailure =
  genericFailureText &&
  !hasDiagnosticErrorState &&
  !hasTextDiagnostics;

const shellNoStore = /no-store/i.test(shellHeaders.cacheControl);
const deploymentNoStore = /no-store/i.test(deployment.cacheControl);
const assetImmutable = assetHeaders ? /immutable/i.test(assetHeaders.cacheControl) : false;
const cachePolicyOk = shellNoStore && deploymentNoStore && Boolean(assetHeaders) && assetImmutable;

const pass =
  containsBff &&
  !containsOld &&
  tradingRoomRequestCount > 0 &&
  requests.length > 0 &&
  responses.length === requests.length &&
  failed.length === 0 &&
  unauthorized.length === 0 &&
  !genericOnlyTradingRoomFailure &&
  cachePolicyOk;

const now = new Date().toISOString().slice(0, 10);
const md = [
  `# Hosted Browser BFF Probe`,
  ``,
  `Date: ${new Date().toISOString()}`,
  `FE: ${FE_BASE}`,
  `BFF: ${BFF_BASE}`,
  `Target: ${targetUrl}`,
  ``,
  `## Summary`,
  ``,
  `- contains intended BFF URL: ${containsBff}`,
  `- contains old BFF URL: ${containsOld}`,
  `- old BFF URL hit count: ${oldHitCount}`,
  `- request count: ${requests.length}`,
  `- response count: ${responses.length}`,
  `- failed count: ${failed.length}`,
  `- trading room request count: ${tradingRoomRequestCount}`,
  `- unauthorized/forbidden response count: ${unauthorized.length}`,
  `- generic-only Trading Room failure: ${genericOnlyTradingRoomFailure}`,
  `- cache policy ok: ${cachePolicyOk}`,
  `- pass: ${pass}`,
  ``,
  `## Deployment`,
  ``,
  `- deployment status: ${deployment.status}`,
  `- deployment id: ${deployment.deploymentId || "<missing>"}`,
  `- deployment keys: ${deployment.keys || "<none>"}`,
  `- deployment Cache-Control: ${deployment.cacheControl || "<missing>"}`,
  deployment.error ? `- deployment error: ${deployment.error}` : ``,
  ``,
  `## Cache headers`,
  ``,
  `| Resource | Status | Cache-Control | ETag | Last-Modified |`,
  `|---|---:|---|---|---|`,
  `| shell ${TRADING_ROOM_PATH} | ${shellHeaders.status} | ${shellHeaders.cacheControl || "<missing>"} | ${shellHeaders.etag || ""} | ${shellHeaders.lastModified || ""} |`,
  `| deployment.json | ${deployment.status} | ${deployment.cacheControl || "<missing>"} |  |  |`,
  assetHeaders
    ? `| asset ${requestPath(assetHeaders.url)} | ${assetHeaders.status} | ${assetHeaders.cacheControl || "<missing>"} | ${assetHeaders.etag || ""} | ${assetHeaders.lastModified || ""} |`
    : `| asset | 0 | <missing> |  |  |`,
  shellHeaders.error ? `\nShell header error: ${shellHeaders.error}` : ``,
  assetHeaders?.error ? `\nAsset header error: ${assetHeaders.error}` : ``,
  ``,
  `## Trading Room error diagnostics`,
  ``,
  errorDiagnostics
    ? [
        `- status: ${errorDiagnostics.status || "<missing>"}`,
        `- code: ${errorDiagnostics.code || "<missing>"}`,
        `- request id: ${errorDiagnostics.requestId || "<missing>"}`,
        `- correlation id: ${errorDiagnostics.correlationId || "<missing>"}`,
        `- text: ${errorDiagnostics.text || "<empty>"}`,
      ].join("\n")
    : "None",
  gotoError ? `\nNavigation error: ${gotoError}` : ``,
  ``,
  `## Responses`,
  ``,
  `| Status | Method | URL | Request ID | Correlation ID |`,
  `|---:|---|---|---|---|`,
  ...responses.map(r => `| ${r.status} | ${r.method} | ${requestPath(r.url)} | ${r.requestId || ""} | ${r.correlationId || ""} |`),
  ``,
  `## Failed`,
  ``,
  failed.length ? failed.map(f => `- ${f.method} ${f.url}: ${f.failure}`).join("\n") : "None",
  ``,
  `## Console errors`,
  ``,
  consoleErrors.length ? consoleErrors.slice(0, 20).map(e => `- ${e}`).join("\n") : "None",
].filter(line => line !== undefined).join("\n");

fs.mkdirSync(OUT_DIR, { recursive: true });
const out = path.join(OUT_DIR, `hosted-browser-bff-probe-${now}.md`);
fs.writeFileSync(out, md, "utf8");
console.log(md);
if (!pass) process.exitCode = 1;
