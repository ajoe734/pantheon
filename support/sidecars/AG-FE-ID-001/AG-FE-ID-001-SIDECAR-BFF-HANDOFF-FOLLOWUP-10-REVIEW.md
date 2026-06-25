# Review: AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-10

| Field | Value |
|---|---|
| Reviewer | `Claude` |
| Owner | `Codex` |
| Review date | `2026-06-20` |
| Outcome | **Approved** |
| Review notes (zh) | 審查通過｜無需修改｜請 Codex 完成 closeout 收尾 |

## Scope Compliance

The packet correctly declares `Mutates canonical truth: false`. The artifact is
a `support/sidecars/` document only. No changes were made to L1 canonical docs,
OpenAPI, capability manifests, BFF runtime code, registry code, governance
implementation, OpenClaw adapter code, or execute-plans source. Scope is strictly
limited to the `bff_handoff_packet` helper kind as required by the sidecar
constraint rules.

The only working tree diff is the task-brief file
(`.orchestrator/task-briefs/`), which is a worker-managed artifact and does not
affect canonical truth.

## Content Review

**§1 Purpose** correctly identifies the tenth followup purpose: updating the
handoff after the branch was fast-forwarded to `origin/dev` at merge commit
`ac0d55c1`. The key delta vs FOLLOWUP-9 is accurately stated: `AG-BE-ID-002`
is now archived `done`, which means `/bff/agora/servant/ensure` is a working
backend path (not a 501 stub), while `AG-BE-ID-003` remains blocked on the
`session_type` contract gap. The contract/runtime mismatch for `/servant/ensure`
(runtime accepts no body / returns 200 for both create and reconcile vs OpenAPI
requiring `ServantEnsureRequest` body and `201` for new provisioning) is called
out clearly and correctly.

**§2 Current Task State Snapshot** is accurate as of 2026-06-20. Task statuses
verified independently:

- `AG-BE-ID-002`: archived `done` confirmed via `ai_status.py show`.
- `AG-BE-ID-003`: `blocked`, `waiting_for: Claude`, with the exact `session_type`
  disposition gap described in the next field (`intent/strategy_ref/metadata` but
  no `session_type` in `ServantSessionCreateRequest`).
- Parent `AG-FE-ID-001`: still `todo`; dependency chain is honestly maintained.
- `AG-FE-DB-004`: `review_approved` and kept correctly separate from
  servant/session readiness.

The dependency honesty rule is correctly applied: because `AG-BE-ID-003` is
blocked, the parent must not claim session readiness.

**§3 Sources Rechecked** is complete and scoped to the sidecar's deliverable.
The packet correctly avoids reading `current-work.md` and the full
`ai-activity-log.jsonl`.

**§4 Delta Since Followup-9** is the core addition and is well-structured. Each
change row supplies a concrete "Parent implication" column. Notable accuracy
points:

- Servant ensure transition from 501 stub to working path is correctly sourced
  from `AG-BE-ID-002` closeout.
- Required headers (`Idempotency-Key` and `X-Request-Id`) are called out with
  the specific 422 test coverage.
- Contract/runtime mismatch (no body / 200 vs required body / 201) is explicitly
  flagged rather than silently assuming generated operation semantics.
- `AG-BE-ID-003` `session_type` blocker is described with the specific schema
  gap that prevents implementation.
- Frontend target files remain missing — confirmed by the source scan in §6.

**§5 BFF Query Ledger** is accurate and well-differentiated. The updated
`/servant/ensure` row correctly reflects implemented and tested status. Routes
without runtime handlers (`/servant`, `/servant/reconcile`, `/servant/sessions*`)
are kept correctly in the "no frontend use until runtime lands" category. The
distinction between the v1.1 type/contract inventory and runtime implementation
completeness is maintained throughout.

**§6 Frontend Surface To Hand Off** is accurate. `AgoraApp.tsx`, `identity.ts`,
and `servant.ts` are still missing. The source scan list confirms present files
and the required parent decisions are clearly stated. The note that
`dashboard.ts` is not an identity/servant client and must not be copied blindly
is a useful guard.

**§7 Updated Minimal Status-Shell Contract** correctly revises the blocked-shell
framing from "servant backend not ready" to "servant profile ready, servant
sessions not ready" — a precise and non-regressive update reflecting the
`AG-BE-ID-002` closure. The state table and authority prohibition
(`servant_policy.execution_authority = "none"`) are correct and complete.

**§8 Operator Journey** provides two paths: the current honest journey (servant
ensure reachable, sessions unavailable) and the future blocked session journey.
Both are accurately described. The sequence correctly includes the
`Idempotency-Key` requirement for ensure calls and the 503 handling for OpenClaw
degradation.

**§9 Parent Absorption Checklist** carries forward the 13-check set from
FOLLOWUP-9 without regression. The `servant ensure truth` check is correctly
updated to reflect the new 501→200 transition. The `ensure contract/runtime
mismatch` check is a valuable new addition. The header-discipline check
accurately references the 422 test coverage.

**§10 Suggested Parent Verification** commands are correct and actionable. The
addition of an `rg` check for `ensureAgoraServant|createServantSession|
ServantProfile|ServantSessionCreateRequest|session_type` spanning the types
snapshot, OpenAPI, and servant router is appropriate given the contract/runtime
mismatch documented in §4.

**§11 Sidecar Verification** records explicit commands and results including:

- Branch confirmed correct and fast-forwarded to `origin/dev`.
- `35 passed in 20.86s` for the focused BFF/identity/servant/OpenClaw test set
  (up from 22 in FOLLOWUP-9, consistent with AG-BE-ID-002 implementation added).
- Frozen v1 schema bundle verify passed for 15 files.
- v1.1 OpenAPI YAML parse OK.
- Agora generated types check passed: 17 schemas, 96 operations.
- `git diff --check` clean for task-owned files.

All verification results are consistent with the packet's claims.

## Approval Notes

This packet is approved. It is a faithful incremental update over FOLLOWUP-9,
adding only the delta caused by: (a) `AG-BE-ID-002` closing as `done`, which
makes `/bff/agora/servant/ensure` a real implemented path; (b) the resulting
need to document the contract/runtime mismatch and updated required-header
discipline; and (c) the updated shell state framing now that servant profile can
be ensured while sessions remain blocked.

Key rules for parent (`AG-FE-ID-001`) absorption carried forward from this
review:

- `/bff/agora/servant/ensure` is implemented and tested. Parent `servant.ts`
  must supply `Idempotency-Key` and `X-Request-Id` headers, handle the observed
  200 response for both create and reconcile, and explicitly note that the v1.1
  OpenAPI declaration (required body, 201 new-create) does not match current
  runtime behavior.
- `/bff/agora/me` and `/bff/agora/capabilities` remain interim runtime routes
  not present in the generated operation inventory. Parent must use them as
  runtime truth only.
- `AG-BE-ID-003` is still blocked on `session_type` contract disposition. Parent
  must keep Ask/session/command surfaces disabled or read-only until that blocker
  clears.
- `AgoraApp.tsx`, `identity.ts`, and `servant.ts` are still missing. Parent
  implementation must create them under the strict-client and no-broad-import
  rules in §7.
- Dashboard readiness must remain strictly separate from servant/session shell
  readiness.

No changes requested. The task may close.
