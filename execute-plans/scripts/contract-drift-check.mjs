#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { buildAgoraArtifacts } from "./generate-agora-types.mjs";

function usage() {
  return [
    "Usage: node scripts/contract-drift-check.mjs [--pantheon-root <path>]",
    "",
    "Fails when the checked-in execute-plans Agora v1 generated snapshot/types",
    "do not match the Pantheon AG-XR-001 OpenAPI/schema bundle digests.",
  ].join("\n");
}

function parseArgs(argv) {
  const args = { pantheonRoot: "" };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--check") {
      continue;
    }
    if (arg === "--pantheon-root") {
      args.pantheonRoot = argv[index + 1] || "";
      index += 1;
      continue;
    }
    if (arg === "--help" || arg === "-h") {
      console.log(usage());
      process.exit(0);
    }
    throw new Error(`Unknown argument: ${arg}\n\n${usage()}`);
  }
  return args;
}

function readText(filePath) {
  try {
    return fs.readFileSync(filePath, "utf8");
  } catch {
    return "";
  }
}

function firstDifferenceLine(actual, expected) {
  const actualLines = actual.split(/\r?\n/u);
  const expectedLines = expected.split(/\r?\n/u);
  const maxLength = Math.max(actualLines.length, expectedLines.length);
  for (let index = 0; index < maxLength; index += 1) {
    if (actualLines[index] !== expectedLines[index]) {
      return index + 1;
    }
  }
  return 0;
}

function checkGeneratedFile(filePath, expectedText) {
  const actualText = readText(filePath);
  if (!actualText) {
    return [`missing ${filePath}`];
  }
  if (actualText === expectedText) {
    return [];
  }
  const line = firstDifferenceLine(actualText, expectedText);
  return [`stale ${filePath}${line ? ` at line ${line}` : ""}`];
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const artifacts = buildAgoraArtifacts({ pantheonRoot: args.pantheonRoot });
  const failures = [
    ...checkGeneratedFile(artifacts.snapshotPath, artifacts.snapshotText),
    ...checkGeneratedFile(artifacts.typesPath, artifacts.typesText),
  ];

  if (failures.length) {
    console.error("Agora contract drift detected:");
    for (const failure of failures) {
      console.error(`  - ${failure}`);
    }
    console.error("Regenerate with: node execute-plans/scripts/generate-agora-types.mjs");
    process.exit(1);
  }

  console.log(
    [
      "Agora contract drift check passed:",
      `${Object.keys(artifacts.snapshot.files).length} bundle digests,`,
      `${artifacts.snapshot.schema_count} schemas,`,
      `${artifacts.snapshot.operation_count} OpenAPI operations.`,
      `snapshot=${path.relative(process.cwd(), artifacts.snapshotPath)}`,
    ].join(" "),
  );
}

main().catch((error) => {
  console.error(error.message || error);
  process.exit(1);
});
