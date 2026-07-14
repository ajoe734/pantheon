# PINT-010-R2 Final Evidence Ledger

Evidence captured: 2026-07-14 (UTC)  
Planning source: `docs/product/persona-interaction-and-governed-action-plan.md`  
Execution source: `docs/bff/execution-tasks/2026-07-12-persona-interaction/INDEX.md`  
Evidence state: **INCOMPLETE — frontend, paired deployment, and hosted acceptance remain PENDING**

This ledger records verified delivery evidence without advancing the task state.
It is deliberately not a `done` claim. The backend and BFF deployment lanes
below are proven; the frontend and cross-repository acceptance lanes remain
open until the placeholders in [Pending evidence](#pending-evidence) are
replaced with GitHub-visible and hosted evidence.

## Planning closure

| Evidence | Result |
| --- | --- |
| Pantheon planning PR | [#3607](https://github.com/ajoe734/pantheon/pull/3607), `PINT-010-R2: close residual planning gates` |
| Merge commit | `1d711ea7b88433c1c7450df2667ac0a761599b6e` |
| Scope | Added the residual task, explicit authority/durability/deployment/hosted lanes, and this evidence artifact requirement. |
| Runtime provenance | Documentation-only planning merge; it is not presented as the deployed BFF revision. |

## Backend implementation ledger

All rows below are merged into Pantheon `dev`.

| PR | Merge commit | Verified responsibility |
| --- | --- | --- |
| [#3589](https://github.com/ajoe734/pantheon/pull/3589) — canonical approval evidence | `4223ae4ef98c6d1b9ecc0e0286376240b2ec3bee` | Requires authoritative approval evidence and rejects self-approval. |
| [#3594](https://github.com/ajoe734/pantheon/pull/3594) — capability-safe viewer sessions | `39709fa4f3f418ed71f4badfccd905a9d8fc6203` | Keeps viewer capability projection read-only rather than treating capability presence as role elevation. |
| [#3601](https://github.com/ajoe734/pantheon/pull/3601) — governed Persona proposed actions | `cef3701f0ceb84a063288f15c987d50effb2a459` | Routes `propose_action` through governed proposal creation with no direct execution authority. |
| [#3603](https://github.com/ajoe734/pantheon/pull/3603) — auth-neutral persistence smoke | `863112506186f5cd8ba211bebf5ee7a6fca6435b` | Adds an internal seed/restart/verify persistence proof without browser or bearer-token dependence. |
| [#3604](https://github.com/ajoe734/pantheon/pull/3604) — durable Persona governance | `7823838eb62b8635d55c5491ed456c1a09214996` | Persists proposal revisions, idempotency, audit/outbox state, deterministic side effects, and exact approval binding across proposal/revision/target/digests/actor/time/expiry. |
| [#3605](https://github.com/ajoe734/pantheon/pull/3605) — Agora write authority | `8de9ed3b09ae1002edc74256f33b9bec1fe3b717` | Enforces write-role checks on Workshop, context, interaction, and proposal mutations; retains capability-scoped participant eligibility reads; adds viewer bypass/operator positive tests; fixes the deploy smoke `pipefail` probe. |

Backend acceptance established by these merges:

- Persona and Servant interaction output has `execution_authority: none`; a
  `propose_action` request creates a governed proposal rather than placing an
  order or mutating capital/binding state.
- Viewer requests cannot create a Workshop, resolve a write context, submit an
  interaction, or create/act on a governed proposal. Denials use the Pack-D
  `FORBIDDEN` envelope and role-check reason. Authorized operator regression
  tests cover the same mutation surfaces.
- Proposal modification creates a new revision. Validation and approval are
  bound to the exact proposal content and validation result; stale, mismatched,
  revoked, cross-scope, expired, and self-approval paths fail closed.
- Durable proposal/idempotency/outbox records and deterministic interaction
  side effects are backed by the production Postgres stores. The deployment
  recovery proof below confirms Workshop state survives an operator-BFF
  restart.

## Failed deployment evidence and resolution

The two failures are retained as evidence; neither is presented as a successful
deployment.

| Run | Requested/head revision | Exact failure |
| --- | --- | --- |
| [29302102989](https://github.com/ajoe734/pantheon/actions/runs/29302102989) | `7823838eb62b8635d55c5491ed456c1a09214996` | `Dev Agora workshop restart persistence smoke` failed with `Process completed with exit code 141`. |
| [29302498354](https://github.com/ajoe734/pantheon/actions/runs/29302498354) | `d1e9ba363942e0db9206e296708ee893259f88ca` | The same restart persistence step failed with `Process completed with exit code 141`. |

Root cause: the remote command enabled `set -o pipefail` and used four
`docker inspect`/`docker logs` pipelines ending in `grep -q`. Once `grep -q`
found its match, it closed the pipe early; the Docker producer received
`SIGPIPE`, making the pipeline exit `141` even though the expected backend and
startup-log values were present.

Resolution: PR #3605 replaced the four early-exit probes with full-input
consumers (`grep -F` or `grep -F -x`, redirected to `/dev/null`) and added a
static regression test that rejects `grep -q` in this smoke step. It did not
weaken the backend-value assertions.

## Successful BFF deployment and recovery proof

| Evidence | Verified result |
| --- | --- |
| Deploy run | [29303223159](https://github.com/ajoe734/pantheon/actions/runs/29303223159), conclusion `success` |
| Exact deployed commit | `8de9ed3b09ae1002edc74256f33b9bec1fe3b717` (PR #3605 merge) |
| Run SHA | `8de9ed3b09ae1002edc74256f33b9bec1fe3b717` |
| Job | `Nonprod deploy`, completed successfully in 14m58s |
| OpenClaw live smoke | `success` |
| Public BFF smoke | `success` |
| Restart persistence smoke | Step 10, `Dev Agora workshop restart persistence smoke`, `completed/success` |
| Dev BFF `/healthz` | HTTP `200`; `status: ok`, `live: true`, `ready: true`; runtime-manager, governance, and deployment dependencies reported `ok` |
| Dev BFF `/readyz` | HTTP `200`; `status: ok`, `live: true`, `ready: true` |
| Dev BFF `/bff/version` | HTTP `200`; both `source_commit_sha` and `commit` equal `8de9ed3b09ae1002edc74256f33b9bec1fe3b717`; `source_commit_known: true`; `environment: dev` |

The successful run proves the corrected inspection/log probes, seed, BFF
restart, readiness wait, and fresh-process verification all completed. The
failure-to-success chain therefore resolves the observed `exit 141` without
discarding the failed-run history.

## Frontend integration gate attempt 2: protected cancellation

`execute-plans` PR [#328](https://github.com/ajoe734/execute-plans/pull/328)
was still open when integration-gate run
[29307503475, attempt 2](https://github.com/ajoe734/execute-plans/actions/runs/29307503475/attempts/2)
ran against frontend revision
`a64cd1de7a06729f9fc6991cbd80c83c132f6d4d`. The gate resolved and recorded
the release identity as frontend
`bc2b9a41d552ecc653a287b46117ad6738ecaef2` with live BFF
`183cba011d6993029b3e828dc85f13dd166f207c`.

The following attempt-2 steps completed successfully before cancellation:

- lint, unit/integration tests, production build, and bundle-size budget;
- Agora contract drift (`38` schemas, `133` routes, and `49` SHA-256 entries
  aligned; the focused contract suite passed `7/7`);
- anonymous route probing, authenticated BFF smoke, and live dry-run write
  probing;
- management live deep validation, including the configured RBAC matrix,
  distinct-operator two-man dry-run race, and long-SSE reconnect check;
- the local frontend's browser-to-BFF probe and management route-load
  baseline; and
- the final cleanup check that the live BFF identity was still
  `183cba011d6993029b3e828dc85f13dd166f207c`.

The hosted management acceptance step was actively cancelled, and the PINT
hosted desktop/mobile proof and later Playwright steps were consequently
skipped. This was a release-integrity protection, not a program-test failure:
Pantheon deploy run
[29308875940](https://github.com/ajoe734/pantheon/actions/runs/29308875940)
had begun while the gate was running and was expected to replace the live BFF
revision, invalidating an exact FE/BFF identity claim made across the deploy
boundary. The job therefore concluded `cancelled`; its aggregate gate reported
the intentionally missing downstream evidence after cancellation. Neither
conclusion is recorded as proof of a defect in the already-green unit, build,
contract, or live-validation steps.

Run 29308875940 and the authoritative post-deploy live BFF SHA remain pending
below. A final gate must run against one stable, recorded FE/BFF pair after the
external deployment settles.

## Pending evidence

The following fields are intentionally unresolved. They must not be inferred
from local tests, the successful BFF-only deployment, an open PR, or an older
frontend deployment.

| Required evidence | Current value |
| --- | --- |
| `execute-plans` implementation PR | **PENDING — PR #328 final state, title, review/check result, merge time, and 40-character merge commit must be recorded.** |
| Frontend exact source commit | **PENDING — must be the GitHub-visible `execute-plans` commit actually built for Pantheon-owned dev hosting.** |
| Frontend build posture | **PENDING — prove `VITE_BFF_MODE=live`, dev `VITE_BFF_BASE_URL`, `VITE_BFF_FALLBACK=strict`, safe write defaults, and no embedded bearer token.** |
| Paired deployment manifest | **PENDING — record one manifest pairing frontend exact commit with BFF commit `8de9ed3b09ae1002edc74256f33b9bec1fe3b717`, or record a later exact BFF commit and its successful BFF deployment proof.** |
| Frontend deployment run | **PENDING — run URL/ID, conclusion, deployed artifact location, and exact paired commits.** |
| Cross-repository integration gate | **PENDING — attempt 2 of run 29307503475 proved unit/build/contracts/live probes/deep validation under BFF `183cba011d6993029b3e828dc85f13dd166f207c`, then was protectively cancelled before hosted proof because Pantheon deploy 29308875940 would invalidate the exact release pair. A fresh green run against the stable final pair is still required.** |
| Hosted desktop positive flow | **PENDING — authenticated Persona context, independent opinions/disagreement, governed proposal creation, revision, and validation against Pantheon-owned hosted FE and live BFF.** |
| Hosted mobile positive flow | **PENDING — same governed path at mobile viewport.** |
| Hosted viewer negatives | **PENDING — UI controls and direct API attempts prove viewer mutation denial without synthetic routing.** |
| Durable hosted readback | **PENDING — proposal revision/audit records read back after the hosted write flow and, where required, after restart.** |
| Hosted strict-live/degraded proof | **PENDING — no mock/seed fallback, no embedded token, and explicit degraded/recovery behavior.** |
| Distinct reviewer closeout | **PENDING — reviewer decision after every field above is replaced by evidence.** |

Until all required pending rows are resolved, this ledger remains incomplete
and does not authorize a `done` transition.

## Evidence exclusions and state discipline

- Pantheon PR #3482 and its `task/PINT-010-R2` worktree are not merged or
  absorbed by this ledger. They contain overlapping/stale backend
  implementation relative to the merged PR chain above. This docs-only change
  makes no request to close the PR and does not alter its state.
- This ledger does not update `ai-status`, archived task JSON, supervisor state,
  or any task lifecycle field.
- Later frontend facts must update this ledger on this docs-only branch/PR (or
  a documented successor) rather than rewriting the verified backend history.
