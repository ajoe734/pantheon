#!/usr/bin/env node

import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { chromium } from "playwright";

const PROJECT_URL =
  "https://lovable.dev/projects/140c41d5-9cd8-4d6b-ba02-66d5941d0dbe";
const DEFAULT_COOLDOWN_MS = 45 * 1000;
const DEFAULT_CHAT_RESPONSE_WAIT_MS = 15 * 1000;

const POINTER_PROMPTS = [
  {
    id: "route-live-activation",
    excerpt:
      "Please execute the repo-local instructions in docs/lovable/2026-04-24-route-live-activation-prompt.md.",
    text: [
      "Please execute the repo-local instructions in docs/lovable/2026-04-24-route-live-activation-prompt.md.",
      "Read that file first, implement it, and write back ui-done, frontend-feedback, and the full feedback bundle with a truthful source_commit.",
    ].join("\n"),
  },
  {
    id: "reopened-evolution-consultation-realignment",
    excerpt:
      "Please execute the repo-local instructions in docs/lovable/2026-04-24-reopened-evolution-consultation-realignment-prompt.md.",
    text: [
      "Please execute the repo-local instructions in docs/lovable/2026-04-24-reopened-evolution-consultation-realignment-prompt.md.",
      "Read that file first, implement it, and write back ui-done, frontend-feedback, and the full feedback bundle with a truthful source_commit.",
    ].join("\n"),
  },
  {
    id: "pkt001-pkt003-followup",
    excerpt:
      "Please execute the repo-local instructions in docs/lovable/2026-04-24-pkt001-pkt003-followup-prompt.md.",
    text: [
      "Please execute the repo-local instructions in docs/lovable/2026-04-24-pkt001-pkt003-followup-prompt.md.",
      "Read that file first, implement it, and write back ui-done, frontend-feedback, and the full feedback bundle with a truthful source_commit.",
    ].join("\n"),
  },
];

function normalize(text) {
  return String(text || "")
    .replace(/`/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function parseArgs(argv) {
  const options = {
    storageState: path.resolve(process.cwd(), "storage-state.json"),
    screenshotsDir: path.resolve(process.cwd(), "screenshots"),
    projectUrl: PROJECT_URL,
    waitMs: 10 * 60 * 1000,
    cooldownMs: DEFAULT_COOLDOWN_MS,
  };

  for (let i = 2; i < argv.length; i += 1) {
    const arg = argv[i];
    switch (arg) {
      case "--storage-state":
        options.storageState = path.resolve(process.cwd(), argv[++i]);
        break;
      case "--screenshots-dir":
        options.screenshotsDir = path.resolve(process.cwd(), argv[++i]);
        break;
      case "--project-url":
        options.projectUrl = argv[++i];
        break;
      case "--wait-ms":
        options.waitMs = Number(argv[++i]);
        break;
      case "--cooldown-ms":
        options.cooldownMs = Number(argv[++i]);
        break;
      default:
        throw new Error(`Unknown argument: ${arg}`);
    }
  }

  return options;
}

async function waitForComposerReady(page, waitMs) {
  await page.waitForFunction(
    () => {
      const bodyText = document.body?.innerText || "";
      return (
        bodyText.includes("Ask Lovable...") &&
        bodyText.includes("Send message") &&
        Boolean(document.querySelector('[aria-label="Chat input"]'))
      );
    },
    { timeout: waitMs },
  );
}

async function findComposer(page) {
  const locator = page.locator('[aria-label="Chat input"]').first();
  await locator.waitFor({ state: "visible", timeout: 20000 });
  return locator;
}

async function waitForComposerIdle(page, waitMs) {
  const startedAt = Date.now();
  const sendButton = await findSendButton(page);

  while (Date.now() - startedAt < waitMs) {
    const bodyText = await page.evaluate(() => document.body?.innerText || "");
    if (/Verification required/i.test(bodyText)) {
      throw new Error("Lovable is blocking the composer with a verification gate.");
    }
    if (await sendButton.isEnabled()) {
      return;
    }
    await page.waitForTimeout(1000);
  }

  throw new Error("Timed out waiting for the Lovable composer to become ready.");
}

async function findSendButton(page) {
  const selectors = [
    () => page.locator("button").filter({ hasText: /^Send message$/i }).last(),
    () => page.getByRole("button", { name: /^Send message$/i }).first(),
    () => page.getByRole("button", { name: /Send message/i }).first(),
  ];

  for (const build of selectors) {
    const locator = build();
    try {
      await locator.waitFor({ state: "visible", timeout: 4000 });
      return locator;
    } catch {
      // try next
    }
  }
  throw new Error("Could not find the Send message button.");
}

async function captureScreenshot(page, screenshotsDir, prefix) {
  await fs.mkdir(screenshotsDir, { recursive: true });
  const screenshotPath = path.join(
    screenshotsDir,
    `${prefix}-${new Date().toISOString().replace(/[:.]/g, "-")}.png`,
  );
  await page.screenshot({ path: screenshotPath, fullPage: true });
  return screenshotPath;
}

async function waitForVerificationToClear(page, waitMs) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < waitMs) {
    const bodyText = await page.evaluate(() => document.body?.innerText || "");
    if (!/Verification required/i.test(bodyText)) {
      return true;
    }
    await page.waitForTimeout(1000);
  }
  return false;
}

async function waitForPromptToPersist(page, excerpt, waitMs) {
  const normalizedExcerpt = normalize(excerpt);
  const startedAt = Date.now();
  while (Date.now() - startedAt < waitMs) {
    const bodyText = await page.evaluate(() => document.body?.innerText || "");
    if (normalize(bodyText).includes(normalizedExcerpt)) {
      return true;
    }
    await page.waitForTimeout(1000);
  }
  return false;
}

function observeChatPost(page) {
  return page
    .waitForResponse(
      (response) => {
        const request = response.request();
        return (
          request.method() === "POST" &&
          /https:\/\/api\.lovable\.dev\/projects\/[0-9a-f-]+\/chat/i.test(response.url())
        );
      },
      { timeout: DEFAULT_CHAT_RESPONSE_WAIT_MS },
    )
    .then((response) => ({
      status: response.status(),
      url: response.url(),
    }))
    .catch(() => null);
}

async function sendPrompt(page, promptSpec, screenshotsDir, waitMs) {
  await waitForComposerReady(page, waitMs);
  await waitForComposerIdle(page, waitMs);
  const composer = await findComposer(page);
  const sendButton = await findSendButton(page);

  const bodyBefore = await page.evaluate(() => document.body?.innerText || "");
  if (normalize(bodyBefore).includes(normalize(promptSpec.excerpt))) {
    const screenshotPath = await captureScreenshot(page, screenshotsDir, promptSpec.id);
    return {
      id: promptSpec.id,
      status: "already-present",
      screenshotPath,
    };
  }

  await composer.click();
  await page.keyboard.press("Control+A").catch(() => {});
  await page.keyboard.press("Meta+A").catch(() => {});
  await page.keyboard.press("Backspace").catch(() => {});
  await page.keyboard.type(promptSpec.text);
  await page.waitForTimeout(500);

  if (!(await sendButton.isEnabled())) {
    throw new Error(`Send button is not enabled for ${promptSpec.id}.`);
  }

  const chatPostPromise = observeChatPost(page);
  await sendButton.click({ force: true });

  let bodyAfter = await page.evaluate(() => document.body?.innerText || "");
  if (/Verification required/i.test(bodyAfter)) {
    const cleared = await waitForVerificationToClear(page, waitMs);
    if (!cleared) {
      const screenshotPath = await captureScreenshot(page, screenshotsDir, promptSpec.id);
      throw new Error(
        `Verification did not clear in time for ${promptSpec.id}. Screenshot: ${screenshotPath}`,
      );
    }
  }

  const chatPost = await chatPostPromise;
  const persisted = await waitForPromptToPersist(page, promptSpec.excerpt, waitMs);
  const screenshotPath = await captureScreenshot(page, screenshotsDir, promptSpec.id);
  if (!persisted) {
    throw new Error(
      `Prompt did not appear in the conversation for ${promptSpec.id}. Screenshot: ${screenshotPath}`,
    );
  }

  await page.reload({ waitUntil: "domcontentloaded", timeout: 60000 });
  await waitForComposerReady(page, waitMs);
  await waitForComposerIdle(page, waitMs);
  const reloadedBody = await page.evaluate(() => document.body?.innerText || "");
  if (!normalize(reloadedBody).includes(normalize(promptSpec.excerpt))) {
    throw new Error(
      `Prompt for ${promptSpec.id} appeared locally but did not persist after reload. Screenshot: ${screenshotPath}`,
    );
  }

  return {
    id: promptSpec.id,
    status: "sent-and-persisted",
    evidence: chatPost
      ? `chat POST returned ${chatPost.status}`
      : "no chat POST response was observed by Playwright",
    screenshotPath,
  };
}

async function main() {
  const options = parseArgs(process.argv);
  const context = await chromium.launchPersistentContext(
    path.resolve(process.cwd(), ".lovable-send-profile"),
    {
      headless: false,
      viewport: { width: 1600, height: 1000 },
      storageState: options.storageState,
    },
  );

  try {
    const page = context.pages()[0] || (await context.newPage());
    await page.goto(options.projectUrl, {
      waitUntil: "domcontentloaded",
      timeout: 60000,
    });

    const results = [];
    for (let index = 0; index < POINTER_PROMPTS.length; index += 1) {
      const promptSpec = POINTER_PROMPTS[index];
      console.log(`Sending ${promptSpec.id}...`);
      const result = await sendPrompt(
        page,
        promptSpec,
        options.screenshotsDir,
        options.waitMs,
      );
      results.push(result);
      console.log(JSON.stringify(result, null, 2));
      if (index < POINTER_PROMPTS.length - 1 && options.cooldownMs > 0) {
        console.log(
          `Cooling down for ${options.cooldownMs} ms before dispatching the next prompt.`,
        );
        await page.waitForTimeout(options.cooldownMs);
      }
    }

    console.log(JSON.stringify({ ok: true, results }, null, 2));
  } finally {
    await context.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error.message || String(error));
  process.exit(1);
});
