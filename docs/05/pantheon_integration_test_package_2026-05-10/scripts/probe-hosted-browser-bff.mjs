#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";

const FE_BASE = process.env.PANTHEON_FE_BASE_URL || "https://pantheon-dev.lovable.app";
const BFF_BASE = process.env.PANTHEON_BFF_BASE_URL || "https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io";
const OLD_BFF_URL = process.env.PANTHEON_OLD_BFF_URL || "pantheon-ai-system-front-dev";
const OUT_DIR = process.env.PANTHEON_AUDIT_OUT_DIR || ".lovable/audits";

let chromium;
try {
  ({ chromium } = await import("@playwright/test"));
} catch {
  console.error("Missing @playwright/test. Install with: npm install -D @playwright/test && npx playwright install chromium");
  process.exit(2);
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();

const requests = [];
const responses = [];
const failed = [];
const consoleErrors = [];

page.on("request", req => {
  const url = req.url();
  if (url.startsWith(BFF_BASE)) requests.push({ method: req.method(), url });
});
page.on("response", res => {
  const url = res.url();
  if (url.startsWith(BFF_BASE)) responses.push({ status: res.status(), method: res.request().method(), url });
});
page.on("requestfailed", req => {
  const url = req.url();
  if (url.startsWith(BFF_BASE)) failed.push({ method: req.method(), url, failure: req.failure()?.errorText });
});
page.on("console", msg => {
  if (msg.type() === "error") consoleErrors.push(msg.text());
});

await page.goto(`${FE_BASE}/management`, { waitUntil: "networkidle", timeout: 60000 });
const html = await page.content();
const scripts = await page.locator("script[src]").evaluateAll(nodes => nodes.map(n => n.src));
let bundleText = "";
for (const s of scripts) {
  try {
    const res = await fetch(s);
    bundleText += await res.text();
  } catch {}
}

await browser.close();

const containsBff = bundleText.includes(BFF_BASE) || html.includes(BFF_BASE);
const containsOld = bundleText.includes(OLD_BFF_URL) || html.includes(OLD_BFF_URL);
const pass = containsBff && !containsOld && requests.length > 0 && responses.length === requests.length && failed.length === 0;

const now = new Date().toISOString().slice(0, 10);
const md = [
  `# Hosted Browser BFF Probe`,
  ``,
  `Date: ${new Date().toISOString()}`,
  `FE: ${FE_BASE}`,
  `BFF: ${BFF_BASE}`,
  ``,
  `## Summary`,
  ``,
  `- contains intended BFF URL: ${containsBff}`,
  `- contains old BFF URL: ${containsOld}`,
  `- request count: ${requests.length}`,
  `- response count: ${responses.length}`,
  `- failed count: ${failed.length}`,
  `- pass: ${pass}`,
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
