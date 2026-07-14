# PINT-010-R2 hosted integration evidence

Date: 2026-07-14 UTC
Owner: Codex2
Reviewer: Claude
Status: implementation evidence is ready for the final post-merge deploy pass;
the task is not `done` until the remaining gates at the end of this document
are complete and the distinct reviewer approves it.

## Delivery lineage

### execute-plans

| PR | Merge SHA | Evidence role |
| --- | --- | --- |
| #275 | `ff195d8166a5be5bb928b86dfb103afc706bdf9c` | Original PINT-006 source on `main` |
| #288 | `ada61156d15977bc188d7e73d4f1e04e556c2b37` | Contract-aware reconciliation of the PINT feature content onto `dev` |
| #332 | `68770f1e59f503d85126248195b0e4e173ec5b47` | Governed Persona interaction journey |
| #333 | `622b8620db1ebdecc00216ae76cceb7faf3e8827` | Authenticated proposal audit-readback assertion |
| #334 | `aa30a578be092b803d3270f427cee89883171475` | Positive `/health` deploy gate |
| #336 | `8cf10d809893c1765be1fdbcb3cc0f28d656916c` | Cross-origin hosted E2E fixture fidelity |
| #337 | `936f252e09fa3bb887c88e733e24b6941cac644e` | Tokenless public bundle, viewer-negative UI case, and opt-in live proposal proof |

PR #275 is the main-side source. Its merge commit is not an ancestor of the
deployed `dev` history, so this evidence does not claim that it is. PR #288 is
the reviewed reconciliation commit that is an ancestor of the deployed
frontend.

### Pantheon

| PR | Merge SHA | Evidence role |
| --- | --- | --- |
| #3480 | `ca36f1209e401c7ed1953003c60295dd56b54c9f` | PINT-006 handoff evidence |
| #3589 | `4223ae4ef98c6d1b9ecc0e0286376240b2ec3bee` | Canonical approval evidence binding |
| #3594 | `39709fa4f3f418ed71f4badfccd905a9d8fc6203` | Viewer capability boundary |
| #3601 | `cef3701f0ceb84a063288f15c987d50effb2a459` | Governed proposed-action path |
| #3603 | `863112506186f5cd8ba211bebf5ee7a6fca6435b` | Auth-neutral deploy persistence smoke |
| #3604 | `7823838eb62b8635d55c5491ed456c1a09214996` | Durable proposal revisions/idempotency/outbox |
| #3605 | `8de9ed3b09ae1002edc74256f33b9bec1fe3b717` | Agora write-authority enforcement and restart-smoke repair |
| #3607 | `1d711ea7b88433c1c7450df2667ac0a761599b6e` | Residual acceptance and closeout gates |
| #3482 | pending merge | Pack-D proposal errors, regression coverage, final evidence, and proposal restart proof |

## Authority and real hosted BFF proof

The latest focused authority/interaction composition passed locally:

```text
pytest -q \
  services/control-plane/bff/tests/test_agora_write_authority.py \
  services/control-plane/bff/tests/test_agora_governed_proposals.py \
  services/control-plane/bff/tests/test_agora_persona_interactions.py \
  services/control-plane/bff/tests/test_agora_strategy_workshop.py \
  scripts/test_agora_workshop_restart_persistence_smoke.py

108 passed, 1 skipped
```

The skipped case was the separate external-Postgres test when
`TEST_DATABASE_URL` was not set. The successful deploy below proves workshop
persistence and verifies that both dev stores are configured as Postgres; the
post-merge deploy remains the gate for durable proposal/outbox behavior.

An authenticated, task-scoped live BFF smoke ran at
`8de9ed3b09ae1002edc74256f33b9bec1fe3b717` on 2026-07-14T03:46:12Z. It used
runtime-only dev identities and recorded no bearer values:

| Check | Result |
| --- | --- |
| Viewer proposal create | HTTP 403, Pack-D `FORBIDDEN` |
| Viewer proposal modify | HTTP 403, Pack-D `FORBIDDEN` |
| Operator proposal create | HTTP 201 |
| Operator modify | HTTP 200, revision 2 |
| Operator paper validation | HTTP 200, revision 3 |
| Authenticated readback | revisions `1, 2, 3`; audit `create, modify, validate` |
| Same-key replay | same proposal, still revision 3 |
| Execution authority | `none`; no downstream execution attempted |

The probe proposal was
`prop_c564bb996a344a15818ebf9e95fb16cc`. It was paper-only, retained its human
gate, and was not approved or executed.

Focused backend regression coverage additionally rejects viewer writes even
when a strict JWT grants `agora.workshop.v1`, self-approval, cross-tenant and
wrong-target approvals, wrong proposal revision/content/validation digests,
expired/superseded decisions, stale ETags, and same-key/different-payload
collisions. Persona output remains advice or a governed proposal; it has no
order, broker, capital-binding, runtime-binding, or memory authority.

## Durability and restart evidence

Pantheon nonprod deploy run
[`29303223159`](https://github.com/ajoe734/pantheon/actions/runs/29303223159)
completed successfully for BFF SHA
`8de9ed3b09ae1002edc74256f33b9bec1fe3b717`. The run verified:

- `/bff/version` returned the exact 40-character source SHA with
  `source_commit_known=true`;
- `/healthz` reported `live=true`, `ready=true`, and healthy dependencies;
- workshop and governance stores were configured and initialized as Postgres;
- an internal auth-neutral record survived an operator-bff restart;
- public BFF and configured CORS-origin smoke passed.

PR #3482 extends that same fresh-process smoke to create a governed proposal,
append modify and validate revisions, complete one command outbox item, leave a
second recovery item pending after a modeled partial side-effect failure,
restart operator-bff, and then require revisions `1,2,3`, audit
`create,modify,validate`, a latest-revision idempotent replay, no rerun of the
completed item, and one-time recovery/completion of the pending item. Its
focused helper validation passed:

```text
5 passed
```

The post-merge deploy of this extended smoke is a remaining gate; local tests
alone are not presented as hosted restart proof.

## Exact frontend deployment and hosted browser proof

execute-plans dev deploy run
[`29304416758`](https://github.com/ajoe734/execute-plans/actions/runs/29304416758)
completed successfully after PR #337. Required branch checks also passed in
run
[`29304416763`](https://github.com/ajoe734/execute-plans/actions/runs/29304416763).

The hosted `deployment.json` at this evidence checkpoint reports:

```text
frontend commit: 936f252e09fa3bb887c88e733e24b6941cac644e
frontend source branch: dev
bff commit: 8de9ed3b09ae1002edc74256f33b9bec1fe3b717
VITE_BFF_MODE: live
VITE_BFF_FALLBACK: strict
VITE_BFF_REAL_WRITES: false
VITE_BFF_ALLOW_DEV_STUB_WRITES: false
VITE_BFF_EMBEDDED_BEARER_TOKEN: false
```

The deploy artifact records a passed browser/BFF probe: required `/health`
returned 200, optional unauthenticated `/bff/me` returned 401, five intended
requests produced five responses, old-host hits were zero, and request failures
were zero. The hosted index, entry bundle, and all 107 referenced JavaScript
assets were downloaded and scanned; the former
`pantheon-dev-browser:viewer` bundle value had zero matches.

The strict-live production build completed with 7,216 modules. Sixteen focused
deploy/auth-header tests, Bash syntax, workflow YAML, ESLint, Node syntax, and
diff checks passed.

Against the exact hosted frontend commit above, the PINT Persona suite passed:

```text
Chromium desktop: 5 passed
Pixel 5 / mobile Chromium: 5 passed
Total: 10 passed
```

Those cases cover one named-Persona ask, red-team consultation with visible
disagreement, governed proposal revision and paper validation, viewer mutation
denial without revision change, fixture-authenticated audit readback with ETag,
Trading Room context/linkage, Persona Trade Journal reflection, and the negative check
that no order/broker/capital/runtime/memory route or authority-bearing payload
was emitted.

The browser journey loads the real hosted asset bundle, but its deterministic
Persona mutations are intercepted by a credentialed cross-origin fixture. It
therefore proves hosted UI wiring and authority-negative requests, not durable
hosted mutation by itself. The real BFF operator/viewer and durable-readback
proof is the separate live probe above; this distinction is intentional.

## Known failed runs and resolution

- execute-plans deploy runs for #332 and #333 failed because the deploy probe
  treated protected-route auth challenges as the health gate. PR #334 changed
  the required signal to positive `/health`; run `29302393447` passed.
- The deploy after unrelated PR #335 (`29302487605`) failed when
  `/bff/version` was transiently unavailable. Subsequent #336 and #337 deploys
  resolved the failure and recorded exact live BFF SHAs.
- Pantheon deploy runs `29302102989` and `29302498354` failed at the original
  restart smoke because `grep -q` under `pipefail` produced SIGPIPE exit 141.
  PR #3605 made the probes consume complete input; run `29303223159` passed.
- One mobile Journal rerun saw a transient dynamic-import fetch failure. The
  same hashed chunk immediately returned HTTP 200, two repeated mobile suites
  passed 8/8, and the exact PR #337 deployment subsequently passed the expanded
  desktop/mobile suite 10/10.
- FE-BFF integration runs `29302889967` and `29302946272` had the PINT cases
  green (4/4 desktop and 4/4 mobile) but their repository-wide aggregate failed:
  other Playwright specs failed, the stored generic smoke bearer returned 401,
  and the inherited `PANTHEON_BFF_SHA` was stale. Those runs are not claimed as
  exact-pair or authenticated positive proof. PR #337 adds an opt-in
  task-specific hosted proposal and 10-case desktop/mobile workflow path; it
  must be dispatched with the final explicit BFF SHA after PR #3482 deploys.

## Explicit non-claims

- No live-capital, order, broker, capital-binding, runtime-binding, or memory
  mutation was attempted or authorized.
- The hosted smoke did not approve a proposal. Canonical exact-binding and
  distinct-approver behavior are proven by Pantheon contract/regression tests,
  not by inventing a production-like approver credential in dev.
- The current dev BFF reports permissive stub auth. The frontend proof is
  strict-live transport with no embedded bearer; it is not a claim that dev BFF
  itself uses production OIDC/JWT posture.
- A green task-specific PINT path does not erase unrelated failures in the
  repository-wide integration aggregate.

## Remaining gates before review handoff

1. Merge Pantheon PR #3482 and deploy its exact merge SHA.
2. Require the extended proposal/revision/outbox restart smoke to pass on that
   deployment.
3. Redeploy execute-plans commit
   `936f252e09fa3bb887c88e733e24b6941cac644e` so `deployment.json` pairs it with
   the final BFF SHA; verify `/bff/version` equality and tokenless assets again.
4. Dispatch the PR #337 task-specific hosted workflow with the final explicit
   BFF SHA; require proposal-endpoint unauthenticated 401 and viewer 403 Pack-D
   results, then archive its proposal and desktop/mobile artifacts.
5. Record those post-merge run URLs and exact SHAs in a final evidence update.
6. Hand off to Claude. Only after a distinct reviewer approves may the owner
   perform closeout finalization and mark the task `done`.
