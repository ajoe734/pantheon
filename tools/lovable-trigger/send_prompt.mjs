#!/usr/bin/env node

import fs from "node:fs";
import fsp from "node:fs/promises";
import { fileURLToPath } from "node:url";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { chromium } from "playwright-core";

const DEFAULT_PROFILE_DIR = path.join(
  os.homedir(),
  ".cache",
  "pantheon-lovable-trigger",
  "profile",
);
const DEFAULT_WAIT_MS = 5 * 60 * 1000;
const DEFAULT_SUBMIT_WAIT_MS = 60 * 1000;
const DEFAULT_COOLDOWN_MS = 45 * 1000;
const DEFAULT_CHAT_RESPONSE_WAIT_MS = 15 * 1000;
const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));

function defaultHeadlessForCommand(command) {
  if (command === "bootstrap") {
    return false;
  }
  if (process.platform === "linux" && !process.env.DISPLAY) {
    return true;
  }
  return false;
}

function usage(exitCode = 1) {
  const msg = `
Usage:
  node send_prompt.mjs bootstrap --repo <repo-path> [--project-url <url>] [--profile-dir <dir>] [--wait-ms <ms>]
  node send_prompt.mjs send --repo <repo-path> --prompt-file <file> [--project-url <url>] [--profile-dir <dir>] [--storage-state <file>] [--pointer-prompt] [--cooldown-ms <ms>] [--headless|--headed]
  node send_prompt.mjs batch --repo <repo-path> --prompt-file <file>... [--project-url <url>] [--profile-dir <dir>] [--storage-state <file>] [--pointer-prompt] [--cooldown-ms <ms>] [--headless|--headed]

Examples:
  node send_prompt.mjs bootstrap --repo /home/edna/code/front-ai-trading-system
  node send_prompt.mjs send --repo /home/edna/code/front-ai-trading-system --prompt-file /home/edna/code/front-ai-trading-system/docs/lovable/2026-04-24-route-live-activation-prompt.md
`;
  console.error(msg.trim());
  process.exit(exitCode);
}

function parseArgs(argv) {
  const [, , command, ...rest] = argv;
  if (!command) {
    usage();
  }

  const options = {
    command,
    promptFiles: [],
    headless: defaultHeadlessForCommand(command),
    repo: null,
    profileDir: DEFAULT_PROFILE_DIR,
    waitMs: DEFAULT_WAIT_MS,
    cooldownMs: DEFAULT_COOLDOWN_MS,
    requireEcho: true,
    pointerPrompt: false,
  };

  for (let i = 0; i < rest.length; i += 1) {
    const arg = rest[i];
    switch (arg) {
      case "--repo":
        options.repo = rest[++i];
        break;
      case "--project-url":
        options.projectUrl = rest[++i];
        break;
      case "--prompt-file":
        options.promptFiles.push(rest[++i]);
        break;
      case "--profile-dir":
        options.profileDir = rest[++i];
        break;
      case "--storage-state":
        options.storageState = rest[++i];
        break;
      case "--wait-ms":
        options.waitMs = Number(rest[++i]);
        break;
      case "--cooldown-ms":
        options.cooldownMs = Number(rest[++i]);
        break;
      case "--require-echo":
        options.requireEcho = true;
        break;
      case "--pointer-prompt":
        options.pointerPrompt = true;
        break;
      case "--headless":
        options.headless = true;
        break;
      case "--headed":
        options.headless = false;
        break;
      default:
        console.error(`Unknown argument: ${arg}`);
        usage();
    }
  }

  if (!["bootstrap", "send", "batch"].includes(command)) {
    console.error(`Unknown command: ${command}`);
    usage();
  }
  if (!options.repo) {
    console.error("--repo is required");
    usage();
  }
  if ((command === "send" || command === "batch") && options.promptFiles.length === 0) {
    console.error(`${command} requires at least one --prompt-file`);
    usage();
  }
  return options;
}

function ensureAbsolute(fileOrDir) {
  return path.isAbsolute(fileOrDir) ? fileOrDir : path.resolve(process.cwd(), fileOrDir);
}

function findChromiumExecutable() {
  const explicit = process.env.LOVABLE_CHROMIUM_PATH;
  if (explicit && fs.existsSync(explicit)) {
    return explicit;
  }

  const cacheRoot = path.join(os.homedir(), ".cache", "ms-playwright");
  if (!fs.existsSync(cacheRoot)) {
    throw new Error(
      "No Playwright Chromium cache found. Set LOVABLE_CHROMIUM_PATH or install a Playwright browser first.",
    );
  }

  const candidates = fs
    .readdirSync(cacheRoot)
    .filter((name) => name.startsWith("chromium-"))
    .sort()
    .reverse()
    .map((name) => path.join(cacheRoot, name, "chrome-linux", "chrome"))
    .filter((candidate) => fs.existsSync(candidate));

  if (candidates.length === 0) {
    throw new Error(
      "Could not find a Chromium executable under ~/.cache/ms-playwright. Set LOVABLE_CHROMIUM_PATH explicitly.",
    );
  }

  return candidates[0];
}

async function discoverProjectUrl(repoPath) {
  const readmePath = path.join(repoPath, "README.md");
  const text = await fsp.readFile(readmePath, "utf8");
  const match = text.match(/https:\/\/lovable\.dev\/projects\/[0-9a-f-]+/i);
  if (!match) {
    throw new Error(
      `Could not discover a Lovable project URL from ${readmePath}. Pass --project-url explicitly.`,
    );
  }
  return match[0];
}

async function extractPrompt(promptFile) {
  const raw = await fsp.readFile(promptFile, "utf8");
  const fenced = raw.match(/```(?:text)?\n([\s\S]*?)```/);
  if (fenced) {
    return fenced[1].trim();
  }
  return raw.trim();
}

function buildPointerPrompt(repoPath, promptFile) {
  const relativePromptPath = path.relative(repoPath, promptFile).split(path.sep).join("/");
  return [
    `Please execute the repo-local instructions in \`${relativePromptPath}\`.`,
    `Read that file first, implement it, and write back \`ui-done\`, \`frontend-feedback\`, and the full feedback bundle with a truthful \`source_commit\`.`,
  ].join("\n");
}

async function launchContext({ profileDir, headless, storageState = null }) {
  const executablePath = findChromiumExecutable();
  await fsp.mkdir(profileDir, { recursive: true });
  return chromium.launchPersistentContext(profileDir, {
    executablePath,
    headless,
    viewport: { width: 1600, height: 1000 },
    storageState,
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
}

async function waitForProjectPage(page, projectUrl, waitMs) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < waitMs) {
    if (page.url().startsWith(projectUrl)) {
      return true;
    }
    await page.waitForTimeout(1000);
  }
  return false;
}

async function gotoProject(page, projectUrl) {
  await page.goto(projectUrl, { waitUntil: "domcontentloaded", timeout: 60000 });
}

async function saveStorageState(context, profileDir) {
  const storageStatePath = path.join(profileDir, "storage-state.json");
  await context.storageState({ path: storageStatePath });
  return storageStatePath;
}

async function closeContext(context) {
  await context.close();
}

async function findComposer(page) {
  const locator = page.locator('[aria-label="Chat input"]').first();
  await locator.waitFor({ state: "visible", timeout: 20000 });
  return locator;
}

async function waitForComposerReady(page, waitMs = 20000) {
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

async function waitForComposerIdle(page, waitMs = DEFAULT_SUBMIT_WAIT_MS) {
  const startedAt = Date.now();
  const sendButton = await findSendButton(page);

  while (Date.now() - startedAt < waitMs) {
    const bodyText = await page.evaluate(() => document.body?.innerText || "");
    if (/verification required/i.test(bodyText)) {
      throw new Error("Lovable is blocking the composer with a verification gate.");
    }
    if (await sendButton.isEnabled()) {
      return;
    }
    await page.waitForTimeout(1000);
  }

  throw new Error("Timed out waiting for the Lovable composer to become ready for the next prompt.");
}

async function findSendButton(page) {
  const selectors = [
    () => page.locator("button").filter({ hasText: /^Send message$/i }).last(),
    () => page.getByRole("button", { name: /^Send message$/i }).first(),
    () => page.getByRole("button", { name: /Send message/i }).first(),
    () => page.locator('[aria-label="Send message"]').first(),
    () => page.locator('[role="button"]').filter({ hasText: /^Send message$/i }).last(),
  ];

  for (const buildLocator of selectors) {
    const locator = buildLocator();
    try {
      await locator.waitFor({ state: "visible", timeout: 4000 });
      return locator;
    } catch {
      // try next
    }
  }

  throw new Error("Could not find the Lovable send button.");
}

async function readComposerValue(locator) {
  try {
    return await locator.inputValue();
  } catch {
    return (
      (await locator.evaluate((el) => {
        if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) {
          return el.value;
        }
        return el.textContent || "";
      })) || ""
    );
  }
}

async function attemptButtonSubmit(page) {
  const locator = await findSendButton(page);
  if (await locator.isEnabled()) {
    await locator.click({ timeout: 3000, force: true });
    return true;
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

function buildPromptSignature(prompt) {
  const normalized = prompt
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (normalized.length === 0) {
    return "";
  }
  return normalized[0].replace(/\s+/g, " ").trim().slice(0, 120);
}

function normalizeForMatch(text) {
  return String(text || "")
    .replace(/`/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

async function waitForSubmissionEvidence(page, composer, prompt, waitMs, requireEcho) {
  const excerpt = buildPromptSignature(prompt);
  const normalizedExcerpt = normalizeForMatch(excerpt);
  const startedAt = Date.now();

  while (Date.now() - startedAt < waitMs) {
    const content = await page.evaluate(() => document.body?.innerText || "");
    const normalizedContent = normalizeForMatch(content);
    const composerValue = await readComposerValue(composer);
    if (/verification required/i.test(content)) {
      return "Lovable requested verification before the submission became durable";
    }
    if (normalizedExcerpt && normalizedContent.includes(normalizedExcerpt)) {
      if (/verification required/i.test(content)) {
        return "prompt echoed into conversation; Lovable requested verification";
      }
      return "prompt echoed into conversation";
    }
    if (
      !requireEcho &&
      (composerValue.trim().length === 0 ||
        !normalizeForMatch(composerValue).includes(normalizedExcerpt.slice(0, 20)))
    ) {
      return "composer cleared after submission";
    }
    if (!requireEcho && /(thinking|building|generating|working)/i.test(content)) {
      return "Lovable entered a working state";
    }
    await page.waitForTimeout(1000);
  }

  throw new Error(
    requireEcho
      ? `Timed out waiting for the prompt signature to appear in the conversation: ${excerpt}`
      : "Timed out waiting for Lovable to acknowledge the prompt. The composer never cleared and no conversation evidence appeared.",
  );
}

async function captureScreenshot(screenshotDir, screenshotPrefix, page) {
  await fsp.mkdir(screenshotDir, { recursive: true });
  const screenshotPath = path.join(
    screenshotDir,
    `${screenshotPrefix}-${new Date().toISOString().replace(/[:.]/g, "-")}.png`,
  );
  await page.screenshot({ path: screenshotPath, fullPage: true });
  return screenshotPath;
}

async function verifySubmissionPersists(page, prompt, waitMs = 30000) {
  const excerpt = normalizeForMatch(buildPromptSignature(prompt));
  if (!excerpt) {
    return "no excerpt available for persistence verification";
  }

  await page.reload({ waitUntil: "domcontentloaded", timeout: 60000 });
  await waitForComposerReady(page, waitMs);
  await waitForComposerIdle(page, waitMs);
  const bodyText = await page.evaluate(() => document.body?.innerText || "");
  if (normalizeForMatch(bodyText).includes(excerpt)) {
    return "prompt persisted after reload";
  }
  throw new Error(
    `Prompt appeared locally but did not persist after reload: ${excerpt}`,
  );
}

async function submitPrompt(page, prompt, screenshotDir, screenshotPrefix, requireEcho) {
  await waitForComposerReady(page);
  await waitForComposerIdle(page);
  const excerpt = buildPromptSignature(prompt);
  const normalizedExcerpt = normalizeForMatch(excerpt);
  const existingConversation = await page.evaluate(() => document.body?.innerText || "");
  if (normalizedExcerpt && normalizeForMatch(existingConversation).includes(normalizedExcerpt)) {
    const screenshotPath = await captureScreenshot(screenshotDir, screenshotPrefix, page);
    return {
      evidence: "prompt already present in conversation",
      screenshotPath,
    };
  }

  const composer = await findComposer(page);
  await composer.click();
  await page.keyboard.press("Control+A").catch(() => {});
  await page.keyboard.press("Meta+A").catch(() => {});
  await page.keyboard.press("Backspace").catch(() => {});
  await page.keyboard.insertText(prompt);

  const composerValue = await readComposerValue(composer);
  if (
    !normalizeForMatch(composerValue).includes(
      normalizedExcerpt.slice(0, Math.min(40, normalizedExcerpt.length)),
    )
  ) {
    throw new Error("Typed prompt was not reflected in the Lovable chat input.");
  }

  const chatPostPromise = observeChatPost(page);
  let submitted = await attemptButtonSubmit(page);
  if (!submitted) {
    const keyChords = ["Control+Enter", "Meta+Enter", "Enter"];
    for (const chord of keyChords) {
      try {
        await composer.press(chord);
        submitted = true;
        break;
      } catch {
        // try next
      }
    }
  }

  if (!submitted) {
    throw new Error("Could not submit the prompt: no send button or working keyboard shortcut was found.");
  }

  const evidence = await waitForSubmissionEvidence(
    page,
    composer,
    prompt,
    DEFAULT_SUBMIT_WAIT_MS,
    requireEcho,
  );
  if (/requested verification/i.test(evidence)) {
    const screenshotPath = await captureScreenshot(screenshotDir, screenshotPrefix, page);
    throw new Error(
      `Lovable accepted the prompt locally but blocked durable submission with a verification gate. The tool stopped after capturing evidence. Screenshot: ${screenshotPath}`,
    );
  }

  const chatPost = await chatPostPromise;
  const persistence = await verifySubmissionPersists(page, prompt);
  const chatEvidence = chatPost
    ? `chat POST returned ${chatPost.status}`
    : "no chat POST response was observed by Playwright";

  const screenshotPath = await captureScreenshot(screenshotDir, screenshotPrefix, page);
  return { evidence: `${chatEvidence}; ${evidence}; ${persistence}`, screenshotPath };
}

function buildAuthError(storageState) {
  const authHint = storageState
    ? `The imported storage-state file (${storageState}) is not authenticated or has expired.`
    : "The persistent Lovable session is not authenticated.";
  return [
    authHint,
    "Run `node send_prompt.mjs bootstrap --repo <repo>` from a visible desktop session first,",
    "or point --profile-dir / --storage-state at an already-authenticated session export.",
  ].join(" ");
}

async function prepareProjectSession(page, projectUrl, storageState, waitMs = DEFAULT_SUBMIT_WAIT_MS) {
  await gotoProject(page, projectUrl);
  if (page.url().includes("/login")) {
    throw new Error(buildAuthError(storageState));
  }
  await waitForComposerReady(page, waitMs);
  await waitForComposerIdle(page, waitMs);
}

async function resolvePrompt(repo, promptFile, pointerPrompt) {
  return pointerPrompt ? buildPointerPrompt(repo, promptFile) : extractPrompt(promptFile);
}

async function dispatchPromptFile(page, {
  promptFile,
  repo,
  pointerPrompt,
  requireEcho,
  screenshotDir,
}) {
  const prompt = await resolvePrompt(repo, promptFile, pointerPrompt);
  return submitPrompt(
    page,
    prompt,
    screenshotDir,
    path.basename(promptFile, path.extname(promptFile)),
    requireEcho,
  );
}

async function runBootstrap(options) {
  if (!process.env.DISPLAY && options.headless === false) {
    console.error(
      "No DISPLAY is set. Bootstrap login needs a visible desktop session so you can complete Lovable authentication.",
    );
    console.error(
      "Run this from a desktop shell, or bootstrap once on another machine and reuse the same profile directory.",
    );
    process.exit(2);
  }

  const repo = ensureAbsolute(options.repo);
  const projectUrl = options.projectUrl || (await discoverProjectUrl(repo));
  const profileDir = ensureAbsolute(options.profileDir);
  const context = await launchContext({ profileDir, headless: false });
  const page = context.pages()[0] || (await context.newPage());

  console.log(`Opening ${projectUrl}`);
  await gotoProject(page, projectUrl);
  if (page.url().includes("/login")) {
    console.log("Lovable login is required. Complete authentication in the opened browser window.");
  }

  const ready = await waitForProjectPage(page, projectUrl, options.waitMs);
  if (!ready) {
    await context.close();
    throw new Error(
      `Timed out after ${options.waitMs} ms waiting for the authenticated Lovable project page.`,
    );
  }

  const storageStatePath = await saveStorageState(context, profileDir);
  console.log(`Authenticated project session detected.`);
  console.log(`Profile dir: ${profileDir}`);
  console.log(`Storage state snapshot: ${storageStatePath}`);
  await context.close();
}

async function runSend(options) {
  const repo = ensureAbsolute(options.repo);
  const promptFile = ensureAbsolute(options.promptFiles[0]);
  const projectUrl = options.projectUrl || (await discoverProjectUrl(repo));
  const profileDir = ensureAbsolute(options.profileDir);
  const storageState = options.storageState ? ensureAbsolute(options.storageState) : null;
  const screenshotDir = path.join(SCRIPT_DIR, "screenshots");
  const context = await launchContext({
    profileDir,
    headless: options.headless,
    storageState,
  });
  const page = context.pages()[0] || (await context.newPage());

  try {
    await prepareProjectSession(page, projectUrl, storageState, options.waitMs);
    const { evidence, screenshotPath } = await dispatchPromptFile(page, {
      promptFile,
      repo,
      pointerPrompt: options.pointerPrompt,
      requireEcho: options.requireEcho,
      screenshotDir,
    });
    const savedStatePath = await saveStorageState(context, profileDir);
    console.log(JSON.stringify({
      ok: true,
      projectUrl,
      promptFile,
      evidence,
      screenshotPath,
      storageState: savedStatePath,
    }, null, 2));
  } finally {
    await closeContext(context);
  }
}

async function runBatch(options) {
  const repo = ensureAbsolute(options.repo);
  const promptFiles = options.promptFiles.map(ensureAbsolute);
  const projectUrl = options.projectUrl || (await discoverProjectUrl(repo));
  const profileDir = ensureAbsolute(options.profileDir);
  const storageState = options.storageState ? ensureAbsolute(options.storageState) : null;
  const screenshotDir = path.join(SCRIPT_DIR, "screenshots");
  const context = await launchContext({
    profileDir,
    headless: options.headless,
    storageState,
  });
  const page = context.pages()[0] || (await context.newPage());

  try {
    await prepareProjectSession(page, projectUrl, storageState, options.waitMs);

    const results = [];
    for (let index = 0; index < promptFiles.length; index += 1) {
      const promptFile = promptFiles[index];
      console.log(`Dispatching ${promptFile}`);
      const { evidence, screenshotPath } = await dispatchPromptFile(page, {
        promptFile,
        repo,
        pointerPrompt: options.pointerPrompt,
        requireEcho: options.requireEcho,
        screenshotDir,
      });
      const result = {
        promptFile,
        evidence,
        screenshotPath,
      };
      results.push(result);
      console.log(JSON.stringify(result, null, 2));

      if (index < promptFiles.length - 1 && options.cooldownMs > 0) {
        console.log(
          `Cooling down for ${options.cooldownMs} ms before dispatching the next prompt.`,
        );
        await page.waitForTimeout(options.cooldownMs);
      }
    }

    const savedStatePath = await saveStorageState(context, profileDir);
    console.log(JSON.stringify({
      ok: true,
      projectUrl,
      promptFiles,
      results,
      storageState: savedStatePath,
    }, null, 2));
  } finally {
    await closeContext(context);
  }
}

async function main() {
  const options = parseArgs(process.argv);
  switch (options.command) {
    case "bootstrap":
      await runBootstrap(options);
      break;
    case "send":
      await runSend(options);
      break;
    case "batch":
      await runBatch(options);
      break;
    default:
      usage();
  }
}

main().catch((error) => {
  console.error(error.stack || error.message || String(error));
  process.exit(1);
});
