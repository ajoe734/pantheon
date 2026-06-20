# Review: AG-XR-002 — Cross-repo generated types and drift CI

Reviewer: Claude
Reviewed at: 2026-06-20
PR: #1770 (merged into dev at 87cc05d3, all CI checks PASSED)

## Acceptance Criteria Verdict

| Criterion | Result |
|---|---|
| types.ts is auto-generated from schema and can be regenerated | PASS |
| drift check fails when schema changes but types not regenerated | PASS |
| CI job green in consistent state | PASS |

## Artifact Review

### execute-plans/src/lib/bff-v1/agora/types.ts

- Header is correct: `/* eslint-disable */` + "GENERATED FILE - DO NOT EDIT BY HAND" + regeneration command
- `AGORA_V1_CONTRACT_SNAPSHOT` embeds all 15 bundle file SHA-256 digests (13 schema files + 1 OpenAPI yaml + 1 capability manifest), matching `contract-snapshot.json` exactly
- `AGORA_V1_CAPABILITIES` (7 capabilities) and `AGORA_V1_OPERATIONS` (61 operations) are typed `as const` readonly arrays
- All 13 JSON schemas are rendered as TypeScript interfaces with correct optional/required, enum unions, nested object types, and `no_order_route_proof` literal fields
- `AgoraSchemaMap`, `AgoraSchemaName`, `AgoraSchema` union types are complete
- Snapshot counts (schema_count: 13, capability_count: 7, operation_count: 61) match content

### execute-plans/src/lib/bff-v1/agora/contract-snapshot.json

- Consistent with AGORA_V1_CONTRACT_SNAPSHOT embedded in types.ts
- Same 15 digest entries, same contract_version "1.0", same frozen_by "AG-XR-001"

### execute-plans/scripts/contract-drift-check.mjs

- Imports `buildAgoraArtifacts` from `generate-agora-types.mjs` — reuses the same generation path for comparison; no divergent logic
- Computes expected output fresh from source files, then compares against on-disk `types.ts` and `contract-snapshot.json`
- Exits 1 with actionable error (file name + first differing line) when stale or missing
- Exits 0 with digest/schema/operation summary on clean check
- `--check` flag is accepted but is a no-op (the script is always in check-only mode); CI step `node execute-plans/scripts/contract-drift-check.mjs --check` works correctly

### execute-plans/scripts/generate-agora-types.mjs

- `findPantheonRoot` tries explicit path, `PANTHEON_CONTRACT_ROOT`, `PANTHEON_REPO_ROOT`, then `SCRIPT_DIR/../..` (= repo root in CI), plus external paths; robust discovery
- `verifyBundleDigests` validates source file integrity before generation — no silent stale-input problem
- `stableJson` (sorted keys) ensures deterministic snapshot output
- Line-based OpenAPI YAML parser covers the standard format; extracts operationId, method, path, and tags correctly for 61 operations
- `buildAgoraArtifacts` is exported and shared by both generator and drift checker — single source of truth for what "correct" output looks like

### CI integration (.github/workflows/branch-ci.yml)

- `generated-files` job runs `node execute-plans/scripts/contract-drift-check.mjs --check`
- `smoke` job requires `generated-files` to pass — drift failure blocks PR merge
- PR #1770 CI result: Commit trailers ✓, Runtime mirror guard ✓, Smoke acceptance ✓ — all green

## Notes

No required changes. The implementation is clean and minimal. The drift guard correctly enforces the cross-repo contract version alignment required by SD §24/§23.5.
