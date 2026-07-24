# Task Brief: AG-COMPAT-001-FE

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Generate Agora frontend types and bind runtime identity
- Status: review_approved
- Owner: Claude2
- Reviewer: Codex2
- Next: Owner closeout complete; task finalized as done with the machine-readable frontend handoff published for AG-COMPAT-002-GATE.

## Closeout Record (2026-07-23)

- Delivery merged into execute-plans `dev` through PR #515, PR #517, PR #519 (runtime merge `c76a838342b08331849f994d8f756155d2e3b961`), and PR #520 (final handoff merge `8fb854ef4036271ffb74147206a5e2cbf50ffdce`); at owner-finalize time execute-plans `origin/dev` HEAD equals `8fb854ef4036271ffb74147206a5e2cbf50ffdce`.
- Review approved by Codex2 (2026-07-23T09:02:15Z): recomputed bundle `b1d488c3...`, OpenAPI `36d1be5b...`, generated types `5ef7b575...` matching the machine handoff; clean-checkout validation covered contract drift 7/7 tests, task-scoped tsc, 38 Agora files/560 tests, strict live production build; PR #520 trailer/generated/smoke/integration checks all passed (integration run 29992032076).
- Machine-readable frontend handoff for `AG-COMPAT-002-GATE`: `docs/contracts/agora/frontend-generation-output.v1_13.json` (execute-plans) pins `contract_family: agora.v1.13`, runtime commit `c76a838342b08331849f994d8f756155d2e3b961`, generated-from contract commit `9e909de182f9f2379d23e8e6b81eefec29ffbce7`, `bundle_index_sha256 b1d488c3b35aa1c691e5b464362ac5a2fdd1efc442249e15be9bb143f379f870`, `openapi_sha256 36d1be5bc033ea1a55610f3f523fc478704fdfad1f06fec620e741bed9bf6f86`, `generated_types_sha256 5ef7b5752304b65f041f2e5a724f0e16f28d4a1db36484eefc2cf0c8b77c092e` — all non-zero and reproducible.
- Owner finalize verification (2026-07-23):
  - `git merge-base --is-ancestor` confirmed runtime merge `c76a8383` and final handoff merge `8fb854ef` are ancestors of execute-plans `origin/dev`, and backend contract commit `9e909de1` is an ancestor of Pantheon `origin/dev`
  - `PANTHEON_CONTRACT_ROOT=<pantheon-worktree> npm run contract:drift` (execute-plans clean checkout at `8fb854ef`) -> `Agora bundle aligned: 49 schemas, 156 routes, 75 sha256 entries`; the drift check itself re-validates every handoff identity field, including a byte-level recomputation of `generated_types_sha256` from `src/lib/bff-v1/agora/contract-snapshot.json` and `src/lib/bff-v1/agora/types.ts`
  - `npx vitest run src/lib/bff-v1/__tests__/contract-drift.test.ts` -> 7 passed, 0 failed
- CI enforcement: `scripts/contract-drift-check.mjs` fails when committed generated output differs from regeneration, and the real-client typecheck gate from PR #519 is active; generation is deterministic (two regenerations byte-identical per reviewer evidence).
- Boundary: the dev compatibility manifest acceptance and deployment gating remain owned by `AG-COMPAT-002-GATE`; this task supplies the frontend identity/hash evidence only. No Pantheon source was copied into execute-plans and no generated file was hand-edited.

## Summary
由 exact backend contract 生成 FE types/client，CI 驗證無 drift，輸出 non-zero runtime/contract/type hashes 給 final manifest gate。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
