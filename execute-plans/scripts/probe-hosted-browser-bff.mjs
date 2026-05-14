#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const FE_BASE = trimTrailingSlash(process.env.PANTHEON_FE_BASE_URL || "https://pantheon-dev.lovable.app");
const BFF_BASE = trimTrailingSlash(process.env.PANTHEON_BFF_BASE_URL || "https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io");
const OLD_BFF_URL = trimTrailingSlash(process.env.PANTHEON_OLD_BFF_URL || "https://pantheon-dev-bff.35.236.178.81.sslip.io");
const OUT_DIR = process.env.PANTHEON_AUDIT_OUT_DIR || ".lovable/audits";
const OVERALL_TIMEOUT_MS = 90_000;
const OPTIONAL_CORE_TIMEOUT_MS = 5_000;
const NAVIGATION_WAIT_UNTIL = "domcontentloaded";
const REQUIRED_CORE_BFF_PATHS = ["/bff/v5/control-room"];
const OPTIONAL_CORE_BFF_PATHS = ["/bff/me"];
const CORE_BFF_PATHS = [...OPTIONAL_CORE_BFF_PATHS, ...REQUIRED_CORE_BFF_PATHS];
const probeStartedAt = Date.now();

function currentSha() {
  const fromEnv =
    process.env.PANTHEON_PROBE_NOCACHE_SHA ||
    process.env.GITHUB_SHA ||
    process.env.VERCEL_GIT_COMMIT_SHA;
  if (fromEnv) return fromEnv.slice(0, 40);
  try {
    return execFileSync("git", ["rev-parse", "--short=12", "HEAD"], { encoding: "utf8" }).trim();
  } catch {
    return Date.now().toString(36);
  }
}

const NOCACHE_SHA = currentSha();

function withNoCache(url) {
  const parsed = new URL(url);
  parsed.searchParams.set("nocache", NOCACHE_SHA);
  return parsed.toString();
}

function trimTrailingSlash(value) {
  return value.replace(/\/+$/, "");
}

function matchesUrlNeedle(url, needle) {
  if (!needle) return false;
  const cleanNeedle = trimTrailingSlash(needle.trim());
  if (!cleanNeedle) return false;
  return cleanNeedle.startsWith("http") ? url.startsWith(cleanNeedle) : url.includes(cleanNeedle);
}

function pathnameOf(url) {
  try {
    return new URL(url).pathname;
  } catch {
    return "";
  }
}

function isCoreBffResponse(res, expectedPath) {
  const url = res.url();
  return url.startsWith(BFF_BASE) && pathnameOf(url) === expectedPath && res.request().method() === "GET";
}

function isAcceptableCoreStatus(response) {
  if (response.path === "/bff/me") return response.status >= 200 && response.status < 500;
  return response.status >= 200 && response.status < 400;
}

function isRequiredCorePath(pathname) {
  return REQUIRED_CORE_BFF_PATHS.includes(pathname);
}

function remainingTimeoutMs() {
  return Math.max(1, OVERALL_TIMEOUT_MS - (Date.now() - probeStartedAt));
}

async function waitForCoreBffResponse(page, expectedPath, timeoutMs = remainingTimeoutMs()) {
  try {
    const res = await page.waitForResponse(res => isCoreBffResponse(res, expectedPath), {
      timeout: Math.min(timeoutMs, remainingTimeoutMs()),
    });
    return {
      path: expectedPath,
      status: res.status(),
      method: res.request().method(),
      url: res.url(),
      error: "",
    };
  } catch (err) {
    return {
      path: expectedPath,
      status: 0,
      method: "GET",
      url: "",
      error: String(err).replace(/\s+/g, " ").slice(0, 240),
    };
  }
}

function textHits(label, text, needle) {
  if (!needle) return [];
  let count = 0;
  let index = text.indexOf(needle);
  while (index !== -1) {
    count += 1;
    index = text.indexOf(needle, index + needle.length);
  }
  return count ? [{ source: label, url: needle, count }] : [];
}

let chromium;
try {
  ({ chromium } = await import("@playwright/test"));
} catch {
  console.error("Missing @playwright/test. Install with: npm install -D @playwright/test && npx playwright install chromium");
  process.exit(2);
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
page.setDefaultTimeout(OVERALL_TIMEOUT_MS);
page.setDefaultNavigationTimeout(OVERALL_TIMEOUT_MS);

const requests = [];
const responses = [];
const failed = [];
const oldUrlHits = [];
const consoleErrors = [];
const coreResponses = [];
let html = "";
let bundleText = "";
const bundleFetches = [];

page.on("request", req => {
  const url = req.url();
  if (url.startsWith(BFF_BASE)) requests.push({ method: req.method(), url });
  if (matchesUrlNeedle(url, OLD_BFF_URL)) oldUrlHits.push({ source: "request", method: req.method(), url });
});
page.on("response", res => {
  const url = res.url();
  if (url.startsWith(BFF_BASE)) responses.push({ status: res.status(), method: res.request().method(), url });
  if (matchesUrlNeedle(url, OLD_BFF_URL)) oldUrlHits.push({ source: "response", status: res.status(), method: res.request().method(), url });
});
page.on("requestfailed", req => {
  const url = req.url();
  if (url.startsWith(BFF_BASE)) failed.push({ method: req.method(), url, failure: req.failure()?.errorText });
  if (matchesUrlNeedle(url, OLD_BFF_URL)) oldUrlHits.push({ source: "failed", method: req.method(), url, failure: req.failure()?.errorText });
});
page.on("console", msg => {
  if (msg.type() === "error") consoleErrors.push(msg.text());
});

const pageUrl = withNoCache(`${FE_BASE}/management`);
try {
  const requiredCoreResponsePromises = REQUIRED_CORE_BFF_PATHS.map(expectedPath =>
    waitForCoreBffResponse(page, expectedPath)
  );
  const optionalCoreResponsePromises = OPTIONAL_CORE_BFF_PATHS.map(expectedPath =>
    waitForCoreBffResponse(page, expectedPath, OPTIONAL_CORE_TIMEOUT_MS)
  );

  await page.goto(pageUrl, { waitUntil: NAVIGATION_WAIT_UNTIL, timeout: remainingTimeoutMs() });
  coreResponses.push(...await Promise.all(optionalCoreResponsePromises));
  coreResponses.push(...await Promise.all(requiredCoreResponsePromises));

  html = await page.content();
  const scripts = await page.locator("script[src]").evaluateAll(nodes => nodes.map(n => n.src));
  for (const s of scripts) {
    if (remainingTimeoutMs() <= 1) break;
    try {
      const fetchedUrl = withNoCache(s);
      const res = await fetch(fetchedUrl, { signal: AbortSignal.timeout(remainingTimeoutMs()) });
      bundleFetches.push({ source: s, fetched: fetchedUrl, status: res.status });
      bundleText += await res.text();
    } catch {}
  }
} finally {
  await browser.close();
}

const containsBff = bundleText.includes(BFF_BASE) || html.includes(BFF_BASE);
const containsOld = bundleText.includes(OLD_BFF_URL) || html.includes(OLD_BFF_URL);
oldUrlHits.push(...textHits("html", html, OLD_BFF_URL));
oldUrlHits.push(...textHits("bundle", bundleText, OLD_BFF_URL));
const oldUrlHitCount = oldUrlHits.reduce((total, hit) => total + (hit.count ?? 1), 0);
const requiredCoreResponseOk =
  REQUIRED_CORE_BFF_PATHS.every(expectedPath =>
    coreResponses.some(response => response.path === expectedPath && isAcceptableCoreStatus(response))
  );
const optionalCoreResponsesObserved =
  OPTIONAL_CORE_BFF_PATHS.every(expectedPath =>
    coreResponses.some(response => response.path === expectedPath && isAcceptableCoreStatus(response))
  );
const pass = containsBff && requiredCoreResponseOk && oldUrlHitCount === 0 && requests.length > 0 && responses.length === requests.length && failed.length === 0;

const now = new Date().toISOString().slice(0, 10);
const md = [
  `# Hosted Browser BFF Probe`,
  ``,
  `Date: ${new Date().toISOString()}`,
  `FE: ${FE_BASE}`,
  `Page URL: ${pageUrl}`,
  `BFF: ${BFF_BASE}`,
  `Old BFF: ${OLD_BFF_URL}`,
  `nocache: ${NOCACHE_SHA}`,
  `timeout ms: ${OVERALL_TIMEOUT_MS}`,
  `navigation waitUntil: ${NAVIGATION_WAIT_UNTIL}`,
  `core waitForResponse paths: ${CORE_BFF_PATHS.join(", ")}`,
  `required core waitForResponse paths: ${REQUIRED_CORE_BFF_PATHS.join(", ")}`,
  `optional core waitForResponse paths: ${OPTIONAL_CORE_BFF_PATHS.join(", ")}`,
  ``,
  `## Summary`,
  ``,
  `- contains intended BFF URL: ${containsBff}`,
  `- contains old BFF URL: ${containsOld}`,
  `- old BFF URL hit count: ${oldUrlHitCount}`,
  `- required core BFF responses complete: ${requiredCoreResponseOk}`,
  `- optional core BFF responses observed: ${optionalCoreResponsesObserved}`,
  `- request count: ${requests.length}`,
  `- response count: ${responses.length}`,
  `- failed count: ${failed.length}`,
  `- pass: ${pass}`,
  ``,
  `## Core BFF responses`,
  ``,
  `| Status | Method | Path | Required | Accepted | URL / Error |`,
  `|---:|---|---|---|---|---|`,
  ...coreResponses.map(r => `| ${r.status} | ${r.method} | ${r.path} | ${isRequiredCorePath(r.path)} | ${isAcceptableCoreStatus(r)} | ${r.url ? r.url.replace(BFF_BASE, "") : r.error} |`),
  ``,
  `## Bundle fetches`,
  ``,
  `| Status | Source | Fetched |`,
  `|---:|---|---|`,
  ...bundleFetches.map(r => `| ${r.status} | ${r.source} | ${r.fetched} |`),
  ``,
  `## Old URL hits`,
  ``,
  oldUrlHits.length
    ? oldUrlHits.map(h => `- ${h.source}${h.method ? ` ${h.method}` : ""}${h.status ? ` ${h.status}` : ""}${h.count ? ` count=${h.count}` : ""}: ${h.url}`).join("\n")
    : "None",
  ``,
  `## Responses`,
  ``,
  `| Status | Method | URL |`,
  `|---:|---|---|`,
  ...responses.map(r => `| ${r.status} | ${r.method} | ${r.url.replace(BFF_BASE, "")} |`),
  ``,
  `## Failed`,
  ``,
  failed.length ? failed.map(f => `- ${f.method} ${f.url}: ${f.failure}`).join("\n") : "None",
  ``,
  `## Console errors`,
  ``,
  consoleErrors.length ? consoleErrors.slice(0, 20).map(e => `- ${e}`).join("\n") : "None",
].join("\n");

fs.mkdirSync(OUT_DIR, { recursive: true });
const out = path.join(OUT_DIR, `hosted-browser-bff-probe-${now}.md`);
fs.writeFileSync(out, md, "utf8");
console.log(md);
if (!pass) process.exitCode = 1;
