# BFF-CONSOL-002 Review — Claude

**Task:** Frontend route manifest extractor (execute-plans)
**Owner:** Codex2
**Reviewer:** Claude
**Date:** 2026-05-13
**Verdict:** APPROVED

## Artifacts Reviewed

- `execute-plans/scripts/bff_route_manifest_frontend.ts` (commit 08ec785e)
- `execute-plans/contract_snapshots/frontend_routes_manifest.json`

## Acceptance Criteria Verification

| Criterion | Status | Evidence |
|---|---|---|
| manifest 涵蓋上述 6 個 caller source | PASS | `metadata.source_files` lists all 6: paths.ts, client.ts, v5.ts, agora.ts, runAction.ts, liveSse.ts; each appears as `caller_file` in entries |
| 每筆有 method/path/family/caller_file/mode | PASS | All 63 entries contain all 5 required fields |
| 動態 path segment 用 {param} normalize | PASS | `normalizePath()` converts both `${var}` template expressions and `{name}` to `{param}`; verified in entries: `/bff/actions/{param}/{param}/{param}`, `/bff/channels/{param}`, etc. |
| 腳本可在 execute-plans CI 重跑 | PASS | `--check` mode exits 0 after local re-compilation: `EXECUTE_PLANS_ROOT=... node .tmp-bff-route-manifest/bff_route_manifest_frontend.js --check` → exit 0; sorted stable output |
| Pantheon route diff 可消費此 JSON | PASS | Top-level `entries` array, each entry is a flat object with stable field order; `buildManifest()` exported for programmatic use |
| 不更動 runtime 行為 | PASS | Script is a standalone read-only AST analyzer; no imports of or writes to runtime modules |

## Implementation Notes

- **AST-based analysis** via the TypeScript compiler API is more robust than regex scraping. Handles template literals, string concatenation, property access chains, and arrow functions correctly.
- **`evalPathExpression`** recursively evaluates path builder expressions; unknown nodes fall back to `{param}`.
- **Deduplication** via `Map<entryKey, entry>` prevents identical route entries even across multiple call sites.
- **`--check` key comparison** uses `method + path + caller_file + mode` (not metadata fields), so CI won't fail just because `snapshot_date` changed.
- **`buildManifest()` is exported**, enabling BFF-CONSOL-003 route diff script to import and call it programmatically without re-parsing args.
- `EXECUTE_PLANS_ROOT` env var allows running from any working directory, which is CI-friendly.

## Local Verification Commands

```bash
# tsc type check — exit 0
npx tsc --noEmit --module NodeNext --moduleResolution NodeNext --target ES2022 --types node --skipLibCheck scripts/bff_route_manifest_frontend.ts

# compile + --check snapshot stability — exit 0
mkdir -p .tmp-bff-route-manifest
npx tsc --module NodeNext --moduleResolution NodeNext --target ES2022 --types node --skipLibCheck --outDir .tmp-bff-route-manifest scripts/bff_route_manifest_frontend.ts
EXECUTE_PLANS_ROOT=/home/lupin/code/execute-plans node .tmp-bff-route-manifest/bff_route_manifest_frontend.js --check
```

Both commands confirmed exit 0 in Claude's review environment.

## Decision

All 6 acceptance criteria met. Implementation is clean, stable, and non-invasive. Downstream consumers (BFF-CONSOL-003 diff script) can import `buildManifest()` or read the snapshot JSON directly.

**APPROVED — returning to Codex2 for finalization.**
