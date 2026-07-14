# PINT-010-R2 hosted integration evidence

Date: 2026-07-14 UTC
Owner: Codex2
Reviewer: Claude
Status: implementation, authority, durability, a current merged exact BFF/FE
pair, and both task-specific hosted gates are complete. The final integration
workflow is finishing its repository-wide tail and artifact upload; the
evidence PR and distinct Claude review remain. This task is not `done`; only
`review_approved` permits owner closeout.

## Delivery lineage

### execute-plans

| PR | Merge SHA | Evidence role |
| --- | --- | --- |
| #275 | `ff195d8166a5be5bb928b86dfb103afc706bdf9c` | Original PINT-006 source on `main` |
| #288 | `ada61156d15977bc188d7e73d4f1e04e556c2b37` | Contract-aware reconciliation onto `dev` |
| #332 | `68770f1e59f503d85126248195b0e4e173ec5b47` | Governed Persona interaction journey |
| #333 | `622b8620db1ebdecc00216ae76cceb7faf3e8827` | Authenticated proposal audit readback |
| #334 | `aa30a578be092b803d3270f427cee89883171475` | Positive `/health` deploy gate |
| #336 | `8cf10d809893c1765be1fdbcb3cc0f28d656916c` | Cross-origin hosted E2E fixture fidelity |
| #337 | `936f252e09fa3bb887c88e733e24b6941cac644e` | Tokenless bundle, viewer-negative UI, opt-in live proposal gate |
| #338 | `60461cb65038c43e427e192e0c857c4772f03ced` | Schema-valid unauthenticated proposal probe |
| #340 | `761c3013ac1c70da1dd6b20f5b0931f3eab6607d` | Canonical Pack-D `AUTH_REQUIRED` assertion and evidence field |
| #342 | `544efc8929b5a723289ea19b48240aabef1fd77d` | Latest-lens fixture compatibility and mobile Trading Room access |
| #343 | `6bea2d28c84d823993398e34ecbdd2d9a46bdf81` | Unrelated hosted fallback-card assertion |
| #344 | `cbc6877630e0af087cd4d119da6024d816e4e495` | Narrow responsive parity and viewport regressions |
| #345 | `b6a5bc9311941cf7333c5f738526868715533101` | Hosted drawer gate hardening |

PR #275 is the main-side source and is not an ancestor of the deployed `dev`
history. PR #288 is the reviewed reconciliation commit that is an ancestor of
the hosted frontend. PRs #338, #340, #342, #344, and #345 have green required
trailer, generated-file, and smoke checks. Final hosted SHA `b6a5bc93...` is a
merged descendant of #342. PR #343 changes only the unrelated
`e2e/evochain009.spec.ts` probe; #344 and #345 add later responsive coverage
while retaining the PINT journey.

### Pantheon

| PR | Merge SHA | Evidence role |
| --- | --- | --- |
| #3480 | `ca36f1209e401c7ed1953003c60295dd56b54c9f` | PINT-006 handoff evidence |
| #3589 | `4223ae4ef98c6d1b9ecc0e0286376240b2ec3bee` | Canonical approval evidence binding |
| #3594 | `39709fa4f3f418ed71f4badfccd905a9d8fc6203` | Viewer capability boundary |
| #3601 | `cef3701f0ceb84a063288f15c987d50effb2a459` | Governed proposed-action path |
| #3603 | `863112506186f5cd8ba211bebf5ee7a6fca6435b` | Auth-neutral deploy persistence smoke |
| #3604 | `7823838eb62b8635d55c5491ed456c1a09214996` | Durable revisions, idempotency, and outbox |
| #3605 | `8de9ed3b09ae1002edc74256f33b9bec1fe3b717` | Write-authority enforcement and restart-smoke repair |
| #3607 | `1d711ea7b88433c1c7450df2667ac0a761599b6e` | Residual acceptance and closeout gates |
| #3482 | `07594aaccb07ffa5b36ca80a8f99eb54b37601c1` | Pack-D errors, regression coverage, evidence, and restart recovery |
| #3628 | `9d393816acfe322a12ba1b295218f829db36ac28` | Final merged `dev` deployment identity; #3628 PR delta was task docs only |

PR #3482 merged with all required checks green. Its merge is an ancestor of
every accepted BFF deployment in this record.

## Authority and real hosted BFF proof

The final focused backend composition passed locally:

```text
pytest -q \
  services/control-plane/bff/tests/test_agora_write_authority.py \
  services/control-plane/bff/tests/test_agora_governed_proposals.py \
  services/control-plane/bff/tests/test_agora_persona_interactions.py \
  services/control-plane/bff/tests/test_agora_strategy_workshop.py \
  scripts/test_agora_workshop_restart_persistence_smoke.py

108 passed, 1 skipped, 136 warnings in 159.48s
```

The skipped case requires the separately configured external
`TEST_DATABASE_URL`. The deploy restart evidence below verifies that the dev
workshop and governance stores use Postgres. The focused restart helper also
passed independently: `5 passed`.

A task-scoped live BFF probe against earlier exact SHA
`4a27eb31fcb35c10cfb1519475a596b81e908e20` created paper-only proposal
`prop_144a08ea3a0c429caa1b545d022796bc` and recorded no bearer values:

| Check | Result |
| --- | --- |
| Unauthenticated proposal create | HTTP 401, Pack-D `AUTH_REQUIRED` |
| Viewer proposal create | HTTP 403, Pack-D `FORBIDDEN` |
| Viewer proposal modify | HTTP 403, Pack-D `FORBIDDEN` |
| Operator proposal create | HTTP 201, revision 1 |
| Operator modify | HTTP 200, revision 2 |
| Operator paper validation | HTTP 200, revision 3 |
| Authenticated readback | revisions `1, 2, 3`; audit `create, modify, validate` |
| Same-key replay | same proposal, still revision 3 |
| Execution authority | `none`; no downstream execution attempted |

Regression coverage additionally rejects viewer writes when a strict JWT grants
`agora.workshop.v1`, self-approval, cross-tenant and wrong-target approvals,
wrong proposal revision/content/validation digests, expired, superseded, or
revoked decisions, stale ETags, and same-key/different-payload collisions.
The strict capability-bearing viewer checks cover interaction, proposed-action,
proposal-create, and proposal-modify writes; the rejected modify leaves revision
1 unchanged. The shared admin mutation role has positive workshop-create and
proposal-create regressions. Persona and Servant output remains advice or a
governed proposal; it has no order, broker, capital-binding, runtime-binding,
or memory authority.

## Durability and restart evidence

Pantheon deploy run
[`29306830685`](https://github.com/ajoe734/pantheon/actions/runs/29306830685),
job `87001761085`, successfully deployed exact BFF SHA
`7c2e3673b26a277bdba4e57d009f7088efce34d0`. It verified:

- `/bff/version` returned HTTP 200 with that exact 40-character SHA and
  `source_commit_known=true`;
- `/healthz` returned HTTP 200 with `live=true`, `ready=true`, and healthy
  dependencies;
- OpenClaw and public/CORS smoke passed;
- workshop `deploy-restart-29306830685-1` and proposal
  `proposal-deploy-restart-29306830685-1` retained revisions `1,2,3` and audit
  `create,modify,validate` across a fresh BFF process;
- a completed outbox item replayed without rerunning its side effect;
- a pending recovery item left after a modeled partial failure was reclaimed
  and completed exactly once after restart;
- latest-revision idempotent replay did not append a revision or rerun work.

Four transient readiness HTTP 502 responses occurred during the intentional
restart and recovered. The run completed successfully.

Run
[`29308875940`](https://github.com/ajoe734/pantheon/actions/runs/29308875940)
then repeated the same deploy and recovery gate for exact descendant SHA
`c30bf618249f9f43604edd058b4e2ca34c892e07`. Workshop
`deploy-restart-29308875940-1` and proposal
`proposal-deploy-restart-29308875940-1` seeded
`completed_outbox=completed` and `recovery_outbox=pending`; after restart the
result was `completed_outbox=replayed` and `recovery_outbox=recovered`. Three
transient 502 responses recovered, and all workflow steps passed.

The final accepted restore is Pantheon run
[`29315706536`](https://github.com/ajoe734/pantheon/actions/runs/29315706536),
job `87030684634`. Exact input ref, checkout, resolved SHA, deploy `--sha`,
managed `dev-bff` worktree, BFF source verification, and deployment-complete
marker all agreed on merged `dev` SHA
`9d393816acfe322a12ba1b295218f829db36ac28`. Public BFF smoke passed;
`/bff/version` returned that exact SHA with `source_commit_known=true`, and
`/healthz` returned HTTP 200 with live/ready runtime-manager, governance, and
deployment dependencies.

Its extended restart gate used workshop `deploy-restart-29315706536-1` and
proposal `proposal-deploy-restart-29315706536-1`. It seeded
`completed_outbox=completed` and `recovery_outbox=pending`; after a fresh BFF
process, the result was `completed_outbox=replayed` and
`recovery_outbox=recovered`. Four expected transient HTTP 502 responses
recovered, and the job completed successfully. The OpenClaw step was correctly
skipped for `component=bff`; the immediately preceding full task deployment
`29314870187` passed its OpenClaw live smoke.

## Frontend deployment and hosted browser proof

execute-plans deploy run
[`29316287074`](https://github.com/ajoe734/execute-plans/actions/runs/29316287074),
job `87030975673`, artifact `pantheon-dev-fe-deploy-evidence` ID `8304151693`
with digest
`sha256:33ca223a7e513389b3df51da92d3feb0dc81f3a0fbb76d334fc380ee1e6fa29f`,
successfully checked out, built, installed, and served immutable frontend SHA
`b6a5bc9311941cf7333c5f738526868715533101`. Live `deployment.json` at
`20260714T075837Z` paired it with exact BFF SHA
`9d393816acfe322a12ba1b295218f829db36ac28` and recorded:

```text
VITE_BFF_MODE: live
VITE_BFF_FALLBACK: strict
VITE_BFF_REAL_WRITES: false
VITE_BFF_ALLOW_DEV_STUB_WRITES: false
VITE_BFF_EMBEDDED_BEARER_TOKEN: false
```

The deploy artifact records five intended browser requests and five responses,
zero failures, zero old-host hits, `/health` HTTP 200, and accepted tokenless
`/bff/me` HTTP 401. It also records no non-production Persona rows and no armed
or standby seed fallback. A fresh Content-Type-aware recursive read-only scan
verified 762 actual hosted JavaScript responses with zero HTTP failures and
zero literal `pantheon-dev-browser:viewer` or
`pantheon-dev-browser:operator` matches. It excluded 674 `.js`-looking strings
that the SPA host resolved to `text/html` fallback rather than miscounting
those HTTP 200 responses as JavaScript chunks. The structured result is
recorded in `PINT-010-R2-BUNDLE-SCAN.json` beside this evidence file.

The accepted deployment order is explicit: the BFF restore job completed at
`07:58:02Z`; the FE deploy recorded its exact pair at `07:58:37Z`; and the
final integration job began at `08:02:47Z`.

Against that exact hosted pair, the focused Persona suite passed both viewports
in final integration run `29316624607`:

```text
Chromium desktop: 5 passed
Pixel 5 / mobile Chromium: 5 passed
Total: 10 passed
```

The cases cover a named-Persona ask, red-team consultation with visible
disagreement, proposal modify and paper validation, viewer mutation denial
without revision change, audit readback with ETag, Trading Room context and
proposal linkage, Persona Trade Journal reflection, and a negative assertion
that no order, broker, capital, runtime, binding, or memory authority was
emitted.

The browser journey loads the real hosted asset bundle, but its deterministic
Persona mutations use a credentialed cross-origin fixture. It proves hosted UI
wiring and authority-negative requests, not durable hosted mutation by itself.
The real BFF operator/viewer and durable-readback proof is composed from the
separate live probe; this boundary is intentional.

## Final cross-repository integration gate

execute-plans run
[`29316624607`](https://github.com/ajoe734/execute-plans/actions/runs/29316624607),
job `87032047017`, was dispatched from exact frontend source
`b6a5bc9311941cf7333c5f738526868715533101` with both expected BFF SHA and
Pantheon contract ref fixed to
`9d393816acfe322a12ba1b295218f829db36ac28`. Its two opt-in hard steps passed:

| Task gate | Result |
| --- | --- |
| PINT hosted governed-proposal smoke | success, `08:13:33Z`-`08:14:24Z` |
| PINT hosted desktop/mobile E2E | success, `08:21:31Z`-`08:22:21Z`; 5 desktop + 5 mobile |

At both task-step completion checkpoints, hosted `deployment.json` remained FE
`b6a5bc93...` plus BFF `9d393816...`, with live/strict and all three
write/stub/token flags false; live `/bff/version` independently returned exact
`9d393816...`. The proposal artifact fields, exact browser first-pass/retry
summary, artifact ID, and terminal repository-wide aggregate are added below
when the workflow tail uploads them.

## Integration runs and known failures

- execute-plans deploys for #332 (`29301525217`) and #333 (`29302049975`)
  failed because a protected-route auth challenge was used as the health gate.
  PR #334 changed the required signal to positive `/health`; run `29302393447`
  passed.
- The deploy after unrelated PR #335 (`29302487605`) saw transient
  `/bff/version` unavailability. The #336 and #337 deploys subsequently passed.
- Pantheon runs `29302102989` and `29302498354` failed when `grep -q` under
  `pipefail` yielded SIGPIPE exit 141. PR #3605 made the probes consume complete
  input; `29303223159` and all later accepted restart runs passed.
- Integration runs `29302889967` and `29302946272` had the PINT cases green
  (4/4 desktop and 4/4 mobile), but unrelated aggregate failures, a stale
  generic bearer, and stale BFF SHA mean they are not exact-pair proof.
- Pantheon run `29305416126` was canceled while queued and ran no steps. Run
  `29305788872` succeeded for a task-containing branch target but was not the
  accepted final `dev` lineage.
- Integration run `29305376271` failed its unrelated aggregate with the PINT
  opt-in path skipped.
- Integration run `29308070725`, artifact ID `8301022546`, reached the live
  proposal API but expected Pack-D `UNAUTHENTICATED`; the API correctly returned
  `AUTH_REQUIRED`. PR #340 corrected the assertion.
- Delayed Pantheon run `29306967263` then deployed explicit SHA
  `183cba011d6993029b3e828dc85f13dd166f207c`, superseding the earlier live
  `7c2e...` state.
- Replacement integration run `29308481272`, artifact ID `8301203792`, failed
  its exact-SHA precondition: expected `7c2e...`, observed `183cba...`. It made
  no proposal mutation request, and desktop/mobile PINT E2E was skipped. This
  was environment drift, not a recurrence of the Pack-D assertion bug.
- Auto FE run `29308447944` was canceled, and later successful run
  `29308549121` superseded that hosted manifest.
- Integration run `29311323207`, job `87015494638`, artifact ID `8303065628`,
  passed its real proposal gate against BFF `4a27eb31...`. An automatic FE
  deploy then changed the live bundle from declared `544efc89...` to
  descendant `6bea2d28...` before the browser step. The browser step finished
  `9 passed, 1 flaky` after one successful mobile retry, while five unrelated
  aggregate checks failed or were incomplete. Because no one immutable FE
  identity spans both task steps, this run is supporting evidence only.
- Replacement run `29312299695`, artifact ID `8302695077`, was intentionally
  canceled after its exact guard observed manifest BFF `4a27eb31...` but live
  BFF `2c336434...`. Both PINT steps were skipped; that means not executed, not
  zero passing cases.
- Integration run `29313952469`, artifact ID `8304212000`, established a clean
  intermediate exact-pair window: real proposal proof passed and its focused
  browser result was `10 passed` with no retry. Unrelated run `29313232772`
  switched live BFF to unmerged `c49edeb3...` only after both PINT steps, then
  failed its own dirty-worktree probe and skipped public/restart smokes. It was
  not accepted as final.
- Follow-up task-branch deploy `29314870187` repaired that probe, but current
  merged-lineage acceptance was restored by `component=bff` run `29315706536`.
  FE run `29316287074` and final integration run `29316624607` then established
  the current merged pair and both task-specific hosted gates.

## Explicit non-claims

- No live-capital, order, broker, capital-binding, runtime-binding, or memory
  mutation was attempted or authorized.
- No hosted smoke approved a proposal. Exact binding and distinct-approver
  behavior are regression/contract proof, not a claim of a production-like dev
  approver credential.
- The dev BFF reports permissive stub auth. Strict-live frontend transport with
  no embedded bearer is not a claim that dev uses production OIDC/JWT posture.
- Fixture-backed hosted UI evidence and real hosted BFF mutation evidence are
  composed but never conflated.
- A green task-specific PINT path does not erase unrelated repository-wide
  integration failures.

## Remaining gates before review handoff

1. Let integration run `29316624607` finish its unrelated repository-wide
   tail and upload the immutable task artifact; reconcile its proposal fields,
   browser first-pass/retry summary, and aggregate without weakening either
   PINT hard gate.
2. Commit and merge this final task-scoped evidence through a Pantheon PR, then
   hand off to Claude.
3. Only distinct reviewer approval and `review_approved` permit the owner to
   run the closeout skill and mark the task `done`.
