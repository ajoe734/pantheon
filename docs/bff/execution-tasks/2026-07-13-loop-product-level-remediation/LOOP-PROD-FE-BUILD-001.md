# LOOP-PROD-FE-BUILD-001 — Warning-free, budgeted live/strict product build

Status: ready for fleet dispatch after dependencies are done

Canonical catalog: `tasks.json`

Canonical contract SHA-256: `17fd1ae20159229715f2f5b3d036fe0648e0c9a07489cc61268227f73d513d50`
The catalog acceptance, proof, and dispatch arrays are machine-authoritative;
the prose sections below are explanatory renderings.

Source addendum:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/REMEDIATION_GAP_ADDENDUM_2026-07-13.md`

## Assignment

| Field | Value |
| --- | --- |
| Owner | Codex |
| Reviewer | Codex2 |
| Wave | 3 |
| Fleet lane | `fe-product-build-quality` |
| Repository | `execute-plans` |
| Merge target | `dev` |
| Current maturity | build succeeds with invalid CSS, circular chunk, and oversized chunk warnings |
| Target maturity | product-level |

## Product outcome

最後的 live/strict/safe-write execute-plans build 必須 warning-free、chunk
graph 穩定且有明確 budget；桌面與手機 hosted proof 不得出現 chunk-load、
console、CORS、BFF、accessibility 或 strict-performance 例外。

## Dependencies

- `LOOP-PROD-FE-001`
- `LOOP-PROD-FE-EVID-001`
- `LOOP-PROD-AGORA-003`
- `LOOP-PROD-TJ-002`
- `LOOP-PROD-MAI-002`

## Loop scope

- `bff_health_monitoring`

## Declared artifacts

- `execute-plans/src`
- `execute-plans/vite.config.ts`
- `execute-plans/scripts/verify-product-build.mjs`
- `execute-plans/e2e/product-build-quality.spec.ts`
- `execute-plans/docs/04/loop-product-level/LOOP-PROD-FE-BUILD-001`

## Acceptance

- invalid CSS is removed and a parser regression rejects recurrence
- `runActionSafe` and related BFF modules have no circular static/dynamic chunk relationship
- explicit initial and lazy chunk budgets are justified, machine-enforced, and met without hiding warnings by raising a global limit
- exact `VITE_BFF_MODE=live`, strict fallback, real/stub writes false, and public viewer boundary build emits no unexpected warning
- clean checkout unit, lint, type/build, protected evidence, and deploy-safety gates pass
- hosted 1440px and 390px paths have zero serious/critical axe findings and no unexpected console, CORS, BFF, chunk-load, or unhandled rejection
- keyboard, focus, reduced-motion, cold/warm navigation, cache refresh, rollback, and `FE_INT_GATE_PERF_STRICT=1` pass
- exact bundle manifest, sizes, hashes, FE/BFF identities, review, and residual budgets are archived

## Required proof

- clean-environment full test/lint/build logs
- machine chunk graph and budget report
- hosted desktop/mobile DOM/network/performance/accessibility evidence
- rollback and stale-cache/chunk negative drill
- merged PR, merge SHA, checks, reviewer verdict, and checksummed evidence

Reviewer approval must set `review_file` under:

`execute-plans/docs/04/loop-product-level/LOOP-PROD-FE-BUILD-001/`

## Non-goals

- No panel-only closure
- No seed fixture as live proof
- No approval gate bypass
- No synthetic receipt as terminal execution proof
- No live-capital or live-broker side effect

## Dispatch and closeout rules

- do not suppress Vite/CSS warnings or inflate the budget without measured justification
- qualify the final feature-bearing branch, not an earlier isolated build
- keep credentials out of bundle, source maps, storage, logs, and evidence
- reviewer validates cold-cache and rollback behavior against exact hosted identity
