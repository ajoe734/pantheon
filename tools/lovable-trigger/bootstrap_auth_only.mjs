#!/usr/bin/env node

import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { chromium } from "playwright";

const DEFAULT_PROJECT_URL =
  "https://lovable.dev/projects/140c41d5-9cd8-4d6b-ba02-66d5941d0dbe";
const DEFAULT_WAIT_MS = 10 * 60 * 1000;

function parseArgs(argv) {
  const options = {
    projectUrl: DEFAULT_PROJECT_URL,
    output: path.resolve(process.cwd(), "storage-state.json"),
    profileDir: path.resolve(process.cwd(), ".lovable-profile"),
    waitMs: DEFAULT_WAIT_MS,
  };

  for (let i = 2; i < argv.length; i += 1) {
    const arg = argv[i];
    switch (arg) {
      case "--project-url":
        options.projectUrl = argv[++i];
        break;
      case "--output":
        options.output = path.resolve(process.cwd(), argv[++i]);
        break;
      case "--profile-dir":
        options.profileDir = path.resolve(process.cwd(), argv[++i]);
        break;
      case "--wait-ms":
        options.waitMs = Number(argv[++i]);
        break;
      default:
        throw new Error(`Unknown argument: ${arg}`);
    }
  }

  return options;
}

async function main() {
  const options = parseArgs(process.argv);

  await fs.mkdir(options.profileDir, { recursive: true });
  await fs.mkdir(path.dirname(options.output), { recursive: true });

  const context = await chromium.launchPersistentContext(options.profileDir, {
    headless: false,
    viewport: { width: 1600, height: 1000 },
  });

  try {
    const page = context.pages()[0] || (await context.newPage());
    console.log(`Opening Lovable project: ${options.projectUrl}`);
    console.log("Please log in on this desktop browser window if prompted.");
    await page.goto(options.projectUrl, {
      waitUntil: "domcontentloaded",
      timeout: 60000,
    });

    const startedAt = Date.now();
    while (Date.now() - startedAt < options.waitMs) {
      if (page.url().startsWith(options.projectUrl)) {
        await context.storageState({ path: options.output });
        console.log(`Authenticated session exported to: ${options.output}`);
        return;
      }
      await page.waitForTimeout(1000);
    }

    throw new Error(
      `Timed out after ${options.waitMs} ms waiting for the Lovable project page after login.`,
    );
  } finally {
    await context.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error.message || String(error));
  process.exit(1);
});
