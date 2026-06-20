# AG-FE-DB-004 Sidecar Review Packet

| Field | Value |
|---|---|
| Task ID | `AG-FE-DB-004-SIDECAR-REVIEW` |
| Helper kind | `review_packet` |
| Parent task | `AG-FE-DB-004` - Recipe proposal/change log/version rollback |
| Parent owner / reviewer | `Codex` / `Claude` |
| Prepared by | `Codex2` |
| Reviewer | `Codex` |
| Date | `2026-06-20` |
| Mutates canonical truth | `false` |
| Status | Ready for sidecar review |

## Purpose

This packet supports `AG-FE-DB-004` by collecting reviewer-facing evidence for
the dashboard recipe proposal preview, immutable change log, and rollback UI
slice. It is support-only. It does not change L1 canonical truth, schema truth,
OpenAPI truth, BFF runtime code, frontend runtime code, registry behavior, or
governance implementation.

The parent task is already `review_approved` by `Claude` according to
`AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DB-004`. This sidecar does
not replace that parent review; it packages the evidence and caveats for
`Codex` to review as the sidecar reviewer.

## Sources Inspected

| Source | Evidence used |
|---|---|
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DB-004` | Parent status is `review_approved`; review notes say the two dashboard components are complete, OpenAPI envelopes are correct, 6/6 dashboard tests passed, and `build` passed. |
| `support/sidecars/AG-FE-DB-004/AG-FE-DB-004-SIDECAR-ACCEPTANCE.md` | Prior acceptance guardrails for v1.1 route/type truth, `previous_version`, conflict handling, append-only rollback, and support-only boundaries. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DB-004-SIDECAR-ACCEPTANCE` | Acceptance packet is archived `done`; PR #1861 merged, task commit `4284db3f04d9dd330439868349973f5b18cc8253`. |
| `git show --format=fuller --no-patch 5a8728a3` | Parent implementation commit and trailers. |
| `git show --stat --oneline 515214cc` | Parent merge commit for PR #1862 into `dev`. |
| `execute-plans/src/agora/dashboard/DashboardProposalPreview.tsx` | Proposal preview behavior, generated type import, accept envelope, concurrency notice, scope guard, and widget renderer composition. |
| `execute-plans/src/agora/dashboard/DashboardChangeLog.tsx` | Version history rendering, rollback action envelope, historical-version guard, and concurrency notice. |
| `execute-plans/src/agora/dashboard/*.test.tsx` | Focused tests for proposal accept, scope mismatch, conflict display, immutable history, rollback body, and rollback conflict display. |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Generated `DashboardRecipeV2` includes `previous_version`; no camelCase compatibility alias is needed. |
| `services/control-plane/specs/agora/v2/dashboard_recipe_v2.schema.json` | Schema contains `previous_version` and not `previousVersionId`. |

## Parent Delivery Evidence

`AG-FE-DB-004` landed in parent implementation commit
`5a8728a3531c4751322b7febdd2edad3ceff8446` and merged via PR #1862 at
`515214cc48541fbe335553c2595976f144b9bb36`.

Parent commit scope:

- `execute-plans/src/agora/dashboard/DashboardProposalPreview.tsx`
- `execute-plans/src/agora/dashboard/DashboardProposalPreview.test.tsx`
- `execute-plans/src/agora/dashboard/DashboardChangeLog.tsx`
- `execute-plans/src/agora/dashboard/DashboardChangeLog.test.tsx`

Parent commit trailers state:

- Owned layer: Agora dashboard proposal preview, immutable change log, rollback
  action envelope, and focused dashboard tests.
- Not changing: BFF route helpers, backend persistence, schema/OpenAPI
  contracts, dashboard page wiring, live fetch/write integration, or widget
  revision drawer behavior.
- Composes with: `AG-FE-DB-001`, `AG-FE-DB-003`, and `AG-BE-DB-001`.

## Review Findings

| Gate | Result | Evidence |
|---|---|---|
| Support-only sidecar boundary | PASS | This packet adds only `support/sidecars/AG-FE-DB-004/AG-FE-DB-004-SIDECAR-REVIEW.md`; no runtime, canonical, schema, OpenAPI, or frontend implementation files are changed by this sidecar. |
| Generated type usage | PASS | `DashboardProposalPreview.tsx` imports `DashboardRecipeV2` from `@/lib/bff-v1/agora/types`; `DashboardChangeLog.tsx` derives status/generated_by from that generated type. |
| `previous_version` guardrail | PASS | Tests and implementation use `previous_version`; `rg -n "previousVersionId|previousVersion" execute-plans/src/agora execute-plans/src/lib/bff-v1/agora services/control-plane/specs/agora/v2/dashboard_recipe_v2.schema.json` found no matches. |
| Proposal preview | PASS | The component renders before/after recipe stats, field deltas, and widget previews through `WidgetRenderer`; accept is enabled only for matching scope, proposal status, ETag, and an `onAccept` handler. |
| Accept envelope | PASS | Accept emits `recipe_id`, headers `If-Match` and `Idempotency-Key`, and body `expected_version` plus optional `note`; focused test asserts the exact envelope. |
| Conflict display | PASS | Proposal and rollback notices surface `expected_version`, `current_version`, `current_etag`, and `latest_href`; focused tests assert the conflict details. |
| Change log | PASS | The component renders version, status, reason, generated_by, created timestamp, and content hash without any delete/rewrite action. |
| Rollback envelope | PASS | Rollback can target only a selected historical version and emits `expected_version`, `target_version`, optional `reason`, `If-Match`, and `Idempotency-Key`; focused test asserts the exact envelope. |
| Raw route calls in components | PASS for component boundary | `rg -n "fetch\\(" execute-plans/src/agora/dashboard` found no matches. The components are presentational/action-envelope surfaces and leave live route execution to their caller. |
| Runtime/governance boundary | PASS | No broker, order, RuntimeBinding, management, or capital-binding route appears in the checked dashboard component files. |

## Caveats For Parent Owner

1. `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DB-004` points
   `review_file` at `.orchestrator/task-briefs/ag_fe_db_004_review.md`, but
   that file is not present on current `dev`. Parent review truth is still
   visible in `ai-status` review notes and PR #1862 evidence, but the missing
   review artifact path should be cleaned up or made durable during parent
   closeout if the parent owner wants a file-backed review record.
2. The parent implementation intentionally did not add BFF route helpers,
   dashboard page wiring, or live fetch/write integration. The checked
   components emit correct envelopes and accept supplied data/callbacks. If
   `AG-FE-DB-004` is later interpreted as end-to-end route wiring, that should
   be a follow-up to compose with the route helper layer rather than a defect
   in this support packet.
3. `makeIdempotencyKey` is duplicated in the two dashboard components. The
   parent review notes already mark extracting it to a shared utility as a
   non-blocking follow-up.
4. `npm ci` was needed in this worktree because `execute-plans/node_modules`
   was absent. It installed ignored local dependencies and reported existing
   npm audit findings; no dependency files were changed for this sidecar.

## Validation Run

| Command | Result |
|---|---|
| `npm --prefix execute-plans ci` | PASS; installed ignored local dependencies. Reported existing audit findings but changed no tracked files. |
| `npm --prefix execute-plans test -- src/agora/dashboard/DashboardProposalPreview.test.tsx src/agora/dashboard/DashboardChangeLog.test.tsx` | PASS; 2 test files, 6 tests. |
| `node execute-plans/scripts/generate-agora-types.mjs --check --pantheon-root .` | PASS; generated types current, 17 schemas and 96 operations. |
| `python3 scripts/agora_schema_bundle.py --verify` | PASS; frozen v1 Agora bundle entries verified. |
| `jq -e '.properties.previous_version and (.properties.previousVersionId \| not)' services/control-plane/specs/agora/v2/dashboard_recipe_v2.schema.json` | PASS; schema uses `previous_version` and has no `previousVersionId`. |
| `rg -n "previousVersionId\|previousVersion" execute-plans/src/agora execute-plans/src/lib/bff-v1/agora services/control-plane/specs/agora/v2/dashboard_recipe_v2.schema.json` | PASS; no matches. |
| `rg -n "fetch\\(" execute-plans/src/agora/dashboard` | PASS; no matches. |

## Reviewer Handoff

To `Codex`, sidecar reviewer:

- Verify that this packet accurately reflects parent PR #1862 and commit
  `5a8728a3531c4751322b7febdd2edad3ceff8446`.
- Confirm the component-layer review boundaries are acceptable: the sidecar
  checks data rendering, envelopes, conflict display, and rollback selection,
  but it does not claim live route wiring beyond emitted action contracts.
- Confirm the caveat about missing parent `review_file` is acceptable for this
  support-only packet or ask the parent owner to make that review record
  durable during `AG-FE-DB-004` closeout.

Suggested reviewer command:

```bash
AI_NAME=Codex REVIEW_FILE=support/sidecars/AG-FE-DB-004/AG-FE-DB-004-SIDECAR-REVIEW.md ./scripts/ai-status.sh approve AG-FE-DB-004-SIDECAR-REVIEW "Review approved: AG-FE-DB-004 sidecar review packet accurately summarizes parent PR #1862 evidence, dashboard component envelopes, conflict handling, rollback behavior, validation commands, support-only boundary, and non-blocking caveats."
```

Prepared by `Codex2` for the `AG-FE-DB-004-SIDECAR-REVIEW` support slice.
