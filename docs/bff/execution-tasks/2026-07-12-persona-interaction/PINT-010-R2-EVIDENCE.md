# PINT-010-R2 hosted integration evidence

Date: 2026-07-14 UTC
Owner: Codex2
Reviewer: Claude
Status: implementation, authority, durability, and hosted-UI evidence is
reconciled. The latest exact BFF publish deployment and the final paired
frontend/integration run are still in progress. This task is not `done`; only a
distinct reviewer approval permits owner closeout.

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

PR #275 is the main-side source and is not an ancestor of the deployed `dev`
history. PR #288 is the reviewed reconciliation commit that is an ancestor of
the hosted frontend. PRs #338 and #340 have green required trailer, generated
file, and smoke checks.

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

108 passed, 1 skipped, 136 warnings in 258.68s
```

The skipped case requires the separately configured external
`TEST_DATABASE_URL`. The deploy restart evidence below verifies that the dev
workshop and governance stores use Postgres. The focused restart helper also
passed independently: `5 passed`.

A task-scoped live BFF probe against exact SHA
`7c2e3673b26a277bdba4e57d009f7088efce34d0` created paper-only proposal
`prop_88fc98bceae54632ae8a2ce0b89d220a` and recorded no bearer values:

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
wrong proposal revision/content/validation digests, expired or superseded
decisions, stale ETags, and same-key/different-payload collisions. Persona and
Servant output remains advice or a governed proposal; it has no order, broker,
capital-binding, runtime-binding, or memory authority.

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

That second deployment is intentionally not called final: hourly publish run
`29309421576` is deploying the newer PINT-containing `dev` snapshot
`4a27eb31fcb35c10cfb1519475a596b81e908e20`. Final identity and restart results
will be recorded after that queued deployment settles.

## Frontend deployment and hosted browser proof

execute-plans deploy run
[`29307640351`](https://github.com/ajoe734/execute-plans/actions/runs/29307640351),
job `87004227360`, artifact `pantheon-dev-fe-deploy-evidence` ID `8300936589`,
successfully checked out, built, and installed immutable frontend SHA
`936f252e09fa3bb887c88e733e24b6941cac644e`. The workflow definition head was
`60461cb65038c43e427e192e0c857c4772f03ced`; the explicit target, built source,
installed source, and manifest frontend identity all agreed on `936f252e...`.

At that checkpoint, `deployment.json` paired FE `936f252e...` with BFF
`7c2e3673...` and recorded:

```text
VITE_BFF_MODE: live
VITE_BFF_FALLBACK: strict
VITE_BFF_REAL_WRITES: false
VITE_BFF_ALLOW_DEV_STUB_WRITES: false
VITE_BFF_EMBEDDED_BEARER_TOKEN: false
```

The deploy artifact recorded five intended browser requests and five responses,
zero failures, zero old-host hits, `/health` HTTP 200, and accepted tokenless
`/bff/me` HTTP 401. A recursive scan fetched the hosted index and 299 reachable
JavaScript chunks with zero fetch failures and zero literal
`pantheon-dev-browser:viewer` matches.

The checkpoint was later superseded, first by a delayed BFF deploy and then by
successful FE run `29308549121`. Before the current BFF deployment window, the
live manifest truth was FE
`89515d82f087bf10363b3a949868c480f2c15cda` paired with BFF
`183cba011d6993029b3e828dc85f13dd166f207c`. This record does not merge those
two historical pairs or claim either is current final truth.

Against the currently hosted FE `89515d82...`, the focused Persona suite was
re-run on both viewports:

```text
Chromium desktop: 5 passed
Pixel 5 / mobile Chromium: 5 passed
Total: 10 passed in 3.1m
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
- One mobile Journal run saw a transient chunk fetch failure. The same chunk
  immediately returned HTTP 200; repeated mobile suites passed 8/8 and the
  expanded desktop/mobile suite passed 10/10.
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
- Auto FE run `29308447944` was canceled, but later successful run
  `29308549121` superseded the hosted manifest. Final evidence therefore waits
  for one settled BFF deployment, one subsequent exact FE deployment, and one
  replacement integration run.

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

1. Let Pantheon run `29309421576` settle and verify its exact 40-character live
   BFF identity plus restart-recovery result.
2. Deploy immutable execute-plans SHA
   `89515d82f087bf10363b3a949868c480f2c15cda` after that BFF deployment and
   require `deployment.json` to pair both exact SHAs with live/strict and safe
   write defaults.
3. Dispatch the opt-in PINT workflow from that exact frontend source. Require
   unauthenticated 401 `AUTH_REQUIRED`, viewer 403 create/modify, operator
   create/modify/validate, revisions/audit/replay/authority-negative readback,
   and desktop/mobile PINT success in the uploaded artifact.
4. Commit and merge the resulting final evidence update, then hand off to
   Claude. Only `review_approved` permits the owner closeout skill and `done`.
