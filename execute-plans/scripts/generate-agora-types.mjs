#!/usr/bin/env node
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = path.resolve(SCRIPT_DIR, "..");
const DEFAULT_OUTPUT_DIR = path.join(FRONTEND_ROOT, "src", "lib", "bff-v1", "agora");
const BUNDLE_INDEX_REL = path.join("services", "control-plane", "specs", "agora", "bundle_index.json");

const METHODS = new Set(["get", "post", "put", "patch", "delete"]);

function usage() {
  return [
    "Usage: node scripts/generate-agora-types.mjs [--check] [--pantheon-root <path>] [--output-dir <path>]",
    "",
    "Generates src/lib/bff-v1/agora/types.ts and contract-snapshot.json from",
    "the Pantheon AG-XR-001 Agora v1 OpenAPI/schema bundle.",
  ].join("\n");
}

function parseArgs(argv) {
  const args = {
    check: false,
    pantheonRoot: "",
    outputDir: DEFAULT_OUTPUT_DIR,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--check") {
      args.check = true;
    } else if (arg === "--pantheon-root") {
      args.pantheonRoot = argv[index + 1] || "";
      index += 1;
    } else if (arg === "--output-dir") {
      args.outputDir = path.resolve(argv[index + 1] || "");
      index += 1;
    } else if (arg === "--help" || arg === "-h") {
      console.log(usage());
      process.exit(0);
    } else {
      throw new Error(`Unknown argument: ${arg}\n\n${usage()}`);
    }
  }
  return args;
}

function pathExists(filePath) {
  try {
    fs.accessSync(filePath, fs.constants.R_OK);
    return true;
  } catch {
    return false;
  }
}

export function findPantheonRoot(explicitRoot = "") {
  const candidates = [
    explicitRoot,
    process.env.PANTHEON_CONTRACT_ROOT,
    process.env.PANTHEON_REPO_ROOT,
    path.resolve(SCRIPT_DIR, "../.."),
    path.resolve(SCRIPT_DIR, "../pantheon-contracts"),
    path.resolve(process.cwd(), "pantheon-contracts"),
    path.resolve(process.cwd(), "..", "pantheon"),
  ].filter(Boolean);

  for (const candidate of candidates) {
    const root = path.resolve(candidate);
    if (pathExists(path.join(root, BUNDLE_INDEX_REL))) {
      return root;
    }
  }

  throw new Error(
    [
      "Could not locate Pantheon Agora bundle_index.json.",
      "Pass --pantheon-root <path> or set PANTHEON_CONTRACT_ROOT.",
      `Expected relative path: ${BUNDLE_INDEX_REL}`,
    ].join(" "),
  );
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function sha256File(filePath) {
  return crypto.createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
}

function sortObject(value) {
  if (Array.isArray(value)) {
    return value.map(sortObject);
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, entry]) => [key, sortObject(entry)]),
    );
  }
  return value;
}

function stableJson(value) {
  return `${JSON.stringify(sortObject(value), null, 2)}\n`;
}

function verifyBundleDigests(pantheonRoot, bundleIndex) {
  const mismatches = [];
  for (const [relativePath, expected] of Object.entries(bundleIndex.files || {})) {
    const filePath = path.join(pantheonRoot, "services", "control-plane", relativePath);
    if (!pathExists(filePath)) {
      mismatches.push(`${relativePath}: missing`);
      continue;
    }
    const actual = sha256File(filePath);
    if (actual !== expected) {
      mismatches.push(`${relativePath}: expected ${expected}, actual ${actual}`);
    }
  }
  if (mismatches.length) {
    throw new Error(`Agora bundle_index.json is stale:\n${mismatches.map((line) => `  - ${line}`).join("\n")}`);
  }
}

function parseOpenApiOperations(openApiText) {
  const operations = [];
  let inPaths = false;
  let currentPath = "";
  let currentOperation = null;

  function finishOperation() {
    if (currentOperation?.operationId) {
      operations.push(currentOperation);
    }
    currentOperation = null;
  }

  for (const line of openApiText.split(/\r?\n/)) {
    if (line === "paths:") {
      inPaths = true;
      continue;
    }
    if (!inPaths) {
      continue;
    }

    const pathMatch = line.match(/^  (\/[^:]+):\s*$/);
    if (pathMatch) {
      finishOperation();
      currentPath = pathMatch[1];
      continue;
    }

    const methodMatch = line.match(/^    ([a-z]+):\s*$/);
    if (methodMatch && METHODS.has(methodMatch[1])) {
      finishOperation();
      currentOperation = {
        method: methodMatch[1].toUpperCase(),
        path: currentPath,
        operationId: "",
        tags: [],
      };
      continue;
    }

    if (!currentOperation) {
      continue;
    }

    const operationMatch = line.match(/^\s{6}operationId:\s*"?([^"#]+?)"?\s*$/);
    if (operationMatch) {
      currentOperation.operationId = operationMatch[1].trim();
      continue;
    }

    const inlineTagsMatch = line.match(/^\s{6}tags:\s*\[(.*)]\s*$/);
    if (inlineTagsMatch) {
      currentOperation.tags = inlineTagsMatch[1]
        .split(",")
        .map((tag) => tag.trim().replace(/^["']|["']$/g, ""))
        .filter(Boolean);
    }
  }

  finishOperation();
  return operations;
}

function pascalCase(value) {
  return String(value || "GeneratedType")
    .replace(/\.schema\.json$/u, "")
    .replace(/(^|[^A-Za-z0-9]+)([A-Za-z0-9])/gu, (_match, _prefix, letter) => letter.toUpperCase())
    .replace(/[^A-Za-z0-9]/gu, "");
}

function propertyName(name) {
  return /^[A-Za-z_$][A-Za-z0-9_$]*$/u.test(name) ? name : JSON.stringify(name);
}

function literal(value) {
  return JSON.stringify(value);
}

function indent(level) {
  return "  ".repeat(level);
}

function union(values) {
  return values.length ? values.join(" | ") : "never";
}

function arrayType(schema, level) {
  const itemType = tsType(schema.items || {}, level);
  return `Array<${itemType}>`;
}

function objectType(schema, level) {
  const properties = schema.properties || {};
  const required = new Set(schema.required || []);
  const entries = Object.entries(properties);
  const additionalProperties = schema.additionalProperties;

  if (!entries.length) {
    if (additionalProperties && typeof additionalProperties === "object") {
      return `Record<string, ${tsType(additionalProperties, level)}>`;
    }
    return "Record<string, unknown>";
  }

  const lines = ["{"];
  for (const [name, propertySchema] of entries) {
    const optional = required.has(name) ? "" : "?";
    lines.push(`${indent(level + 1)}${propertyName(name)}${optional}: ${tsType(propertySchema, level + 1)};`);
  }
  if (additionalProperties === true || (additionalProperties && typeof additionalProperties === "object")) {
    lines.push(`${indent(level + 1)}[key: string]: unknown;`);
  }
  lines.push(`${indent(level)}}`);
  return lines.join("\n");
}

function tsType(schema, level = 0) {
  if (!schema || typeof schema !== "object") {
    return "unknown";
  }
  if (Object.prototype.hasOwnProperty.call(schema, "const")) {
    return literal(schema.const);
  }
  if (Array.isArray(schema.enum)) {
    return union(schema.enum.map(literal));
  }
  if (Array.isArray(schema.oneOf)) {
    return union(schema.oneOf.map((entry) => tsType(entry, level)));
  }
  if (Array.isArray(schema.anyOf)) {
    return union(schema.anyOf.map((entry) => tsType(entry, level)));
  }
  if (Array.isArray(schema.allOf)) {
    return schema.allOf.map((entry) => tsType(entry, level)).join(" & ");
  }

  const type = Array.isArray(schema.type) ? schema.type : [schema.type || (schema.properties ? "object" : "unknown")];
  const mapped = type.map((entry) => {
    if (entry === "string") return "string";
    if (entry === "number" || entry === "integer") return "number";
    if (entry === "boolean") return "boolean";
    if (entry === "null") return "null";
    if (entry === "array") return arrayType(schema, level);
    if (entry === "object") return objectType(schema, level);
    return "unknown";
  });
  return union([...new Set(mapped)]);
}

function schemaTypeName(schema, relativePath) {
  return schema.title || pascalCase(path.basename(relativePath));
}

function renderConstArray(name, values) {
  return [`export const ${name} = [`, ...values.map((value) => `  ${stableJson(value).trim()},`), "] as const;", ""].join(
    "\n",
  );
}

function renderTypes({ snapshot, schemas, capabilities, operations }) {
  const schemaEntries = schemas.map(({ relativePath, schema }) => ({
    relativePath,
    name: schemaTypeName(schema, relativePath),
    schema,
  }));
  const schemaNames = schemaEntries.map((entry) => entry.name);
  const capabilityNames = capabilities.map((entry) => entry.name);

  const lines = [
    "/* eslint-disable */",
    "/**",
    " * GENERATED FILE - DO NOT EDIT BY HAND.",
    " *",
    " * Source: Pantheon AG-XR-001 Agora v1 OpenAPI/schema bundle.",
    " * Regenerate with: node scripts/generate-agora-types.mjs",
    " */",
    "",
    `export const AGORA_V1_CONTRACT_SNAPSHOT = ${stableJson(snapshot).trim()} as const;`,
    "",
    "export type AgoraV1ContractFile = keyof typeof AGORA_V1_CONTRACT_SNAPSHOT.files;",
    "",
    renderConstArray(
      "AGORA_V1_CAPABILITIES",
      capabilities.map((capability) => ({
        name: capability.name,
        schemas: capability.schemas || [],
        bff_route_families: capability.bff_route_families || [],
        bff_path_prefixes: capability.bff_path_prefixes || [],
        auth_level: capability.auth_level || "operator",
      })),
    ).trimEnd(),
    "",
    "export type AgoraCapability = typeof AGORA_V1_CAPABILITIES[number];",
    `export type AgoraCapabilityName = ${union(capabilityNames.map(literal))};`,
    "",
    renderConstArray("AGORA_V1_OPERATIONS", operations).trimEnd(),
    "",
    "export type AgoraRoute = typeof AGORA_V1_OPERATIONS[number];",
    'export type AgoraHttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";',
    'export type AgoraOperationId = typeof AGORA_V1_OPERATIONS[number]["operationId"];',
    "",
  ];

  for (const entry of schemaEntries) {
    const body = tsType(entry.schema, 0);
    if (body.startsWith("{")) {
      lines.push(`export interface ${entry.name} ${body}`);
    } else {
      lines.push(`export type ${entry.name} = ${body};`);
    }
    lines.push("");
  }

  lines.push("export interface AgoraSchemaMap {");
  for (const entry of schemaEntries) {
    lines.push(`  ${entry.name}: ${entry.name};`);
  }
  lines.push("}");
  lines.push("");
  lines.push(`export type AgoraSchemaName = ${union(schemaNames.map(literal))};`);
  lines.push("export type AgoraSchema = AgoraSchemaMap[keyof AgoraSchemaMap];");
  lines.push("");

  return `${lines.join("\n")}`;
}

export function buildAgoraArtifacts(options = {}) {
  const pantheonRoot = findPantheonRoot(options.pantheonRoot);
  const outputDir = path.resolve(options.outputDir || DEFAULT_OUTPUT_DIR);
  const bundleIndexPath = path.join(pantheonRoot, BUNDLE_INDEX_REL);
  const bundleIndex = readJson(bundleIndexPath);
  verifyBundleDigests(pantheonRoot, bundleIndex);

  const schemaRelativePaths = Object.keys(bundleIndex.files || {}).filter((entry) =>
    entry.startsWith("specs/agora/") && entry.endsWith(".schema.json"),
  );
  const schemas = schemaRelativePaths.map((relativePath) => ({
    relativePath,
    schema: readJson(path.join(pantheonRoot, "services", "control-plane", relativePath)),
  }));
  const manifest = readJson(path.join(pantheonRoot, "services", "control-plane", "specs", "agora", "capability_manifest.json"));
  const openApiText = fs.readFileSync(
    path.join(pantheonRoot, "services", "control-plane", "openapi", "agora_v1.openapi.yaml"),
    "utf8",
  );
  const operations = parseOpenApiOperations(openApiText);
  const capabilities = manifest.capabilities || [];

  const snapshot = {
    contract_name: "pantheon-agora-v1",
    contract_version: bundleIndex.bundle_version,
    frozen_by: bundleIndex.frozen_by,
    source_bundle: "services/control-plane/specs/agora/bundle_index.json",
    schema_count: schemas.length,
    capability_count: capabilities.length,
    operation_count: operations.length,
    files: bundleIndex.files || {},
  };

  const snapshotText = stableJson(snapshot);
  const typesText = renderTypes({ snapshot, schemas, capabilities, operations });

  return {
    pantheonRoot,
    outputDir,
    typesPath: path.join(outputDir, "types.ts"),
    snapshotPath: path.join(outputDir, "contract-snapshot.json"),
    snapshot,
    snapshotText,
    typesText,
    schemas,
    capabilities,
    operations,
  };
}

function compareFile(filePath, expectedText) {
  if (!pathExists(filePath)) {
    return [`missing generated file: ${filePath}`];
  }
  const actualText = fs.readFileSync(filePath, "utf8");
  if (actualText === expectedText) {
    return [];
  }
  return [`generated file is stale: ${filePath}`];
}

function writeIfChanged(filePath, text) {
  if (pathExists(filePath) && fs.readFileSync(filePath, "utf8") === text) {
    return false;
  }
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, text);
  return true;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const artifacts = buildAgoraArtifacts(args);

  if (args.check) {
    const failures = [
      ...compareFile(artifacts.typesPath, artifacts.typesText),
      ...compareFile(artifacts.snapshotPath, artifacts.snapshotText),
    ];
    if (failures.length) {
      throw new Error(`${failures.join("\n")}\nRun: node execute-plans/scripts/generate-agora-types.mjs`);
    }
    console.log(
      `Agora generated types are current: ${artifacts.schemas.length} schemas, ${artifacts.operations.length} operations.`,
    );
    return;
  }

  const wroteTypes = writeIfChanged(artifacts.typesPath, artifacts.typesText);
  const wroteSnapshot = writeIfChanged(artifacts.snapshotPath, artifacts.snapshotText);
  console.log(
    `Agora types generated: ${artifacts.schemas.length} schemas, ${artifacts.operations.length} operations, ${artifacts.capabilities.length} capabilities.`,
  );
  console.log(`${wroteTypes ? "updated" : "current"} ${path.relative(process.cwd(), artifacts.typesPath)}`);
  console.log(`${wroteSnapshot ? "updated" : "current"} ${path.relative(process.cwd(), artifacts.snapshotPath)}`);
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  main().catch((error) => {
    console.error(error.message || error);
    process.exit(1);
  });
}
