#!/usr/bin/env node
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = path.resolve(SCRIPT_DIR, "..");
const DEFAULT_OUTPUT_DIR = path.join(FRONTEND_ROOT, "src", "lib", "bff-v1", "agora");
const CONTROL_PLANE_REL = path.join("services", "control-plane");
const DEFAULT_BUNDLE_INDEX_REL = path.join(CONTROL_PLANE_REL, "specs", "agora", "bundle_index.v1_1.json");
const FALLBACK_BUNDLE_INDEX_REL = path.join(CONTROL_PLANE_REL, "specs", "agora", "bundle_index.json");

const METHODS = new Set(["get", "post", "put", "patch", "delete"]);

function usage() {
  return [
    "Usage: node scripts/generate-agora-types.mjs [--check] [--pantheon-root <path>] [--output-dir <path>] [--bundle-index <path>]",
    "",
    "Generates src/lib/bff-v1/agora/types.ts and contract-snapshot.json from",
    "the Pantheon Agora OpenAPI/schema bundle. The default bundle is v1.1",
    "when present, composed with the frozen v1 base bundle.",
  ].join("\n");
}

function parseArgs(argv) {
  const args = {
    bundleIndexRel: DEFAULT_BUNDLE_INDEX_REL,
    check: false,
    pantheonRoot: "",
    outputDir: DEFAULT_OUTPUT_DIR,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--check") {
      args.check = true;
    } else if (arg === "--bundle-index") {
      args.bundleIndexRel = argv[index + 1] || "";
      index += 1;
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
    if (pathExists(path.join(root, DEFAULT_BUNDLE_INDEX_REL)) || pathExists(path.join(root, FALLBACK_BUNDLE_INDEX_REL))) {
      return root;
    }
  }

  throw new Error(
    [
      "Could not locate Pantheon Agora bundle_index.json.",
      "Pass --pantheon-root <path> or set PANTHEON_CONTRACT_ROOT.",
      `Expected relative path: ${DEFAULT_BUNDLE_INDEX_REL}`,
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
    const filePath = path.join(pantheonRoot, CONTROL_PLANE_REL, relativePath);
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

function verifyExtendedBundleDigest(pantheonRoot, bundleIndex) {
  const bundlePath = bundleIndex.extends?.bundle_path;
  const expected = bundleIndex.extends?.bundle_index_sha256;
  if (!bundlePath || !expected) {
    return;
  }
  const filePath = path.join(pantheonRoot, bundlePath);
  if (!pathExists(filePath)) {
    throw new Error(`Extended Agora base bundle is missing: ${bundlePath}`);
  }
  const actual = sha256File(filePath);
  if (actual !== expected) {
    throw new Error(`Extended Agora base bundle digest mismatch: ${bundlePath}: expected ${expected}, actual ${actual}`);
  }
}

function loadBundleChain(pantheonRoot, bundleIndexRel, seen = new Set()) {
  const normalizedRel = bundleIndexRel || DEFAULT_BUNDLE_INDEX_REL;
  if (seen.has(normalizedRel)) {
    throw new Error(`Circular Agora bundle extension detected at ${normalizedRel}`);
  }
  seen.add(normalizedRel);

  const bundlePath = path.join(pantheonRoot, normalizedRel);
  if (!pathExists(bundlePath)) {
    if (normalizedRel === DEFAULT_BUNDLE_INDEX_REL && pathExists(path.join(pantheonRoot, FALLBACK_BUNDLE_INDEX_REL))) {
      return loadBundleChain(pantheonRoot, FALLBACK_BUNDLE_INDEX_REL, seen);
    }
    throw new Error(`Agora bundle index not found: ${normalizedRel}`);
  }

  const bundleIndex = readJson(bundlePath);
  verifyBundleDigests(pantheonRoot, bundleIndex);
  verifyExtendedBundleDigest(pantheonRoot, bundleIndex);

  const basePath = bundleIndex.extends?.bundle_path;
  const baseChain = basePath ? loadBundleChain(pantheonRoot, basePath, seen) : [];
  return [...baseChain, { bundleIndex, bundleIndexRel: normalizedRel }];
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

function arrayType(schema, level, context) {
  const itemType = tsType(schema.items || {}, level, context);
  return `Array<${itemType}>`;
}

function objectType(schema, level, context) {
  const properties = schema.properties || {};
  const required = new Set(schema.required || []);
  const entries = Object.entries(properties);
  const additionalProperties = schema.additionalProperties;

  if (!entries.length) {
    if (additionalProperties && typeof additionalProperties === "object") {
      return `Record<string, ${tsType(additionalProperties, level, context)}>`;
    }
    return "Record<string, unknown>";
  }

  const lines = ["{"];
  for (const [name, propertySchema] of entries) {
    const optional = required.has(name) ? "" : "?";
    lines.push(`${indent(level + 1)}${propertyName(name)}${optional}: ${tsType(propertySchema, level + 1, context)};`);
  }
  if (additionalProperties === true || (additionalProperties && typeof additionalProperties === "object")) {
    const valueType = additionalProperties === true ? "unknown" : tsType(additionalProperties, level + 1, context);
    lines.push(`${indent(level + 1)}[key: string]: ${valueType};`);
  }
  lines.push(`${indent(level)}}`);
  return lines.join("\n");
}

function resolveJsonPointer(root, pointer) {
  if (!pointer || pointer === "#") {
    return root;
  }
  const pathParts = pointer.replace(/^#\/?/u, "").split("/").filter(Boolean);
  let current = root;
  for (const part of pathParts) {
    const key = part.replace(/~1/gu, "/").replace(/~0/gu, "~");
    current = current?.[key];
  }
  return current;
}

function refType(ref, level, context) {
  const [targetRaw, fragmentRaw = ""] = String(ref).split("#");
  const target = targetRaw || "";
  const fragment = fragmentRaw ? `#${fragmentRaw}` : "";

  if (!target && context?.currentSchema) {
    const resolved = resolveJsonPointer(context.currentSchema, fragment || "#");
    return tsType(resolved, level, context);
  }

  const targetSchema = context?.schemaByBasename?.get(path.basename(target)) || context?.schemaByPath?.get(target);
  if (!targetSchema) {
    return "unknown";
  }
  if (!fragment) {
    return context?.typeNameBySchema?.get(targetSchema) || "unknown";
  }
  return tsType(resolveJsonPointer(targetSchema, fragment), level, {
    ...context,
    currentSchema: targetSchema,
  });
}

function tsType(schema, level = 0, context = {}) {
  if (!schema || typeof schema !== "object") {
    return "unknown";
  }
  if (schema.$ref) {
    return refType(schema.$ref, level, context);
  }
  if (Object.prototype.hasOwnProperty.call(schema, "const")) {
    return literal(schema.const);
  }
  if (Array.isArray(schema.enum)) {
    return union(schema.enum.map(literal));
  }
  if (Array.isArray(schema.oneOf)) {
    return union(schema.oneOf.map((entry) => tsType(entry, level, context)));
  }
  if (Array.isArray(schema.anyOf)) {
    return union(schema.anyOf.map((entry) => tsType(entry, level, context)));
  }
  if (Array.isArray(schema.allOf)) {
    return schema.allOf.map((entry) => tsType(entry, level, context)).join(" & ");
  }

  const type = Array.isArray(schema.type) ? schema.type : [schema.type || (schema.properties ? "object" : "unknown")];
  const mapped = type.map((entry) => {
    if (entry === "string") return "string";
    if (entry === "number" || entry === "integer") return "number";
    if (entry === "boolean") return "boolean";
    if (entry === "null") return "null";
    if (entry === "array") return arrayType(schema, level, context);
    if (entry === "object") return objectType(schema, level, context);
    return "unknown";
  });
  return union([...new Set(mapped)]);
}

function schemaTypeName(schema, relativePath) {
  if (relativePath === "specs/agora/widget_spec.schema.json") {
    return "WidgetSpecV1";
  }
  return schema.title || pascalCase(path.basename(relativePath));
}

function schemaTypeAliases(relativePath, generatedName) {
  if (relativePath === "specs/agora/widget_spec.schema.json" && generatedName === "WidgetSpecV1") {
    return ["WidgetSpec"];
  }
  return [];
}

function renderConstArray(name, values) {
  return [`export const ${name} = [`, ...values.map((value) => `  ${stableJson(value).trim()},`), "] as const;", ""].join(
    "\n",
  );
}

function createSchemaContext(schemaEntries, currentSchema) {
  const schemaByPath = new Map();
  const schemaByBasename = new Map();
  const typeNameBySchema = new Map();
  for (const entry of schemaEntries) {
    schemaByPath.set(entry.relativePath, entry.schema);
    schemaByBasename.set(path.basename(entry.relativePath), entry.schema);
    typeNameBySchema.set(entry.schema, entry.name);
  }
  return { currentSchema, schemaByPath, schemaByBasename, typeNameBySchema };
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
    " * Source: Pantheon Agora OpenAPI/schema bundle.",
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
    const body = tsType(entry.schema, 0, createSchemaContext(schemaEntries, entry.schema));
    if (body.startsWith("{")) {
      lines.push(`export interface ${entry.name} ${body}`);
    } else {
      lines.push(`export type ${entry.name} = ${body};`);
    }
    lines.push("");
    for (const alias of schemaTypeAliases(entry.relativePath, entry.name)) {
      lines.push(`export type ${alias} = ${entry.name};`);
      lines.push("");
    }
  }

  lines.push("export interface AgoraSchemaMap {");
  for (const entry of schemaEntries) {
    lines.push(`  ${entry.name}: ${entry.name};`);
    for (const alias of schemaTypeAliases(entry.relativePath, entry.name)) {
      lines.push(`  ${alias}: ${alias};`);
    }
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
  const bundleChain = loadBundleChain(pantheonRoot, options.bundleIndexRel || DEFAULT_BUNDLE_INDEX_REL);
  const primaryBundle = bundleChain[bundleChain.length - 1];
  const bundleIndex = primaryBundle.bundleIndex;
  const bundleFiles = Object.assign({}, ...bundleChain.map((entry) => entry.bundleIndex.files || {}));

  const schemaRelativePaths = Object.keys(bundleFiles || {}).filter((entry) =>
    entry.startsWith("specs/agora/") && entry.endsWith(".schema.json"),
  );
  const schemas = schemaRelativePaths.map((relativePath) => ({
    relativePath,
    schema: readJson(path.join(pantheonRoot, CONTROL_PLANE_REL, relativePath)),
  }));

  const capabilityManifestPaths = [
    "specs/agora/capability_manifest.json",
    ...Object.keys(bundleFiles).filter((entry) => entry.endsWith("capability_manifest_v1_1.json")),
  ];
  const capabilityByName = new Map();
  for (const relativePath of capabilityManifestPaths) {
    const manifestPath = path.join(pantheonRoot, CONTROL_PLANE_REL, relativePath);
    if (!pathExists(manifestPath)) continue;
    const manifest = readJson(manifestPath);
    for (const capability of manifest.capabilities || []) {
      capabilityByName.set(capability.name, capability);
    }
  }
  const capabilities = [...capabilityByName.values()];

  const openApiPaths = [
    "openapi/agora_v1.openapi.yaml",
    ...(bundleIndex.bundle_version === "1.1" && pathExists(path.join(pantheonRoot, CONTROL_PLANE_REL, "openapi", "agora_v1_1.openapi.yaml"))
      ? ["openapi/agora_v1_1.openapi.yaml"]
      : []),
  ];
  const operationByKey = new Map();
  for (const relativePath of openApiPaths) {
    const openApiText = fs.readFileSync(path.join(pantheonRoot, CONTROL_PLANE_REL, relativePath), "utf8");
    for (const operation of parseOpenApiOperations(openApiText)) {
      operationByKey.set(operation.operationId || `${operation.method} ${operation.path}`, operation);
    }
  }
  const operations = [...operationByKey.values()];

  const snapshot = {
    contract_name: "pantheon-agora-v1",
    contract_version: bundleIndex.bundle_version,
    frozen_by: bundleIndex.frozen_by || bundleIndex.extends?.frozen_by || "AG-XR-001",
    source_bundle: primaryBundle.bundleIndexRel,
    extends: bundleIndex.extends,
    schema_count: schemas.length,
    capability_count: capabilities.length,
    operation_count: operations.length,
    files: bundleFiles,
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
