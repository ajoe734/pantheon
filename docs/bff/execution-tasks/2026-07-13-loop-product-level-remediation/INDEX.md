# Loop Product-Level Remediation Execution Packet — 2026-07-13

Status: blocked on external `LOOP-PROD-RUNTIME-BOOT-001`; only catalog
validation is currently authoritative

Primary planning baseline:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/LOOP_PRODUCT_LEVEL_REMEDIATION_PLAN_2026-07-13.md`

Additive execution audit:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/REMEDIATION_GAP_ADDENDUM_2026-07-13.md`

Machine task catalog: [tasks.json](tasks.json)

Dispatcher:
`scripts/dispatch_loop_product_level_remediation_2026-07-13.py`

Dispatcher tests:
`scripts/test_dispatch_loop_product_level_remediation_2026_07_13.py`

Planning authority delivery:

- PR `#3576` merged to `dev` as
  `a27e60c04f7f250e37876ee40338cb193f6de910`.
- Claude independently reviewed exact head
  `95f5a34f8efb2768af1b7b5b8ea894ad23b65c52`, re-ran catalog validation,
  zero-write dry-run, loop coverage, and the full dispatcher suite, and issued
  an `APPROVE` verdict.
- Verified catalog digest:
  `44a893162da5779fc64292a70ba59fb7237eb4102ffb65f8e3ad3b64a8f31357`;
  verified dispatcher result: `172 passed`.
- This approval closes planning authority only. Product delivery remains
  blocked on the external runtime bootstrap and every downstream fleet task's
  own evidence, review, deployment, and product-level acceptance gates.

## Addendum Gating & Batching Rationale

The remaining 10 addendum tasks (ATTEST, AUTH-BOOT, AUTH-OPS, BROWSER-AUTH, CLOSE-002, DELIVERY, FE-BUILD, FE-EVID, FLEET, SIGNOFF) are deliberately gated and cannot be dispatched (applied) onto the live board until the prerequisite bootstrap task `LOOP-PROD-RUNTIME-BOOT-001` transitions to `done`. This strict dependency ensures that all subsequent productization tasks run with the proper runtime task audit lock protocol in place, failing closed otherwise to preserve system integrity.

### Addendum convergence audit

The 11 addendum tasks were checked individually against
`CONVERGENCE-EVOCHAIN-EVOLOOP-2026-07-14.md`. Only the final additive
program closeout overlaps an existing EVOCHAIN/EVOLOOP delivery. The other ten
tasks own runtime-lock, delivery-provenance, auth, evidence, build, fleet, or
sign-off controls and have no overlap with the ruling's evolution dispatch,
artifact, promote, signal, performance-telemetry, verifier, or thin-slice
closeout scope.

| Addendum task | Convergence result |
| --- | --- |
| `LOOP-PROD-RUNTIME-BOOT-001` | Checked; no EVOCHAIN/EVOLOOP overlap |
| `LOOP-PROD-ATTEST-001` | Checked; no EVOCHAIN/EVOLOOP overlap |
| `LOOP-PROD-AUTH-BOOT-001` | Checked; no EVOCHAIN/EVOLOOP overlap |
| `LOOP-PROD-AUTH-OPS-001` | Checked; no EVOCHAIN/EVOLOOP overlap |
| `LOOP-PROD-BROWSER-AUTH-001` | Checked; no EVOCHAIN/EVOLOOP overlap |
| `LOOP-PROD-DELIVERY-001` | Checked; no EVOCHAIN/EVOLOOP overlap |
| `LOOP-PROD-FE-BUILD-001` | Checked; no EVOCHAIN/EVOLOOP overlap |
| `LOOP-PROD-FE-EVID-001` | Checked; no EVOCHAIN/EVOLOOP overlap |
| `LOOP-PROD-FLEET-001` | Checked; no EVOCHAIN/EVOLOOP overlap |
| `LOOP-PROD-SIGNOFF-001` | Checked; no EVOCHAIN/EVOLOOP overlap |
| `LOOP-PROD-CLOSE-002` | Consumes `EVOCHAIN-011`, thin-slice deploy/closeout evidence from `EVOLOOP-009`, and persona-learning feedback from `EVOLOOP-011`; does not recreate those scopes |

Correction PR `#3659` merged as
`af441973540f7cba267dd299cec549c5b22e7b39` and produced catalog digest
`04f94c0c4b2fc9773083624d7fd100f6c3ea2f617dcee2068def61927ffe1644`.
The exact corrected catalog in this follow-up validates as 48 tasks with digest
`44a893162da5779fc64292a70ba59fb7237eb4102ffb65f8e3ad3b64a8f31357`;
the dispatcher suite collects and passes 172 tests. Product materialization
remains gated as described above.

## Product contract

This packet contains 48 primary execution tasks plus one external pre-dispatch
bootstrap task (49 execution tasks total). It is a build-and-proof DAG,
not a checklist that can be closed from component tests. The program remains
active until the twelve canonical L1 loops plus the Per-Persona OODA composite
overlay have default runtime ownership, real canonical effects or explicit
terminal failure, restart-safe recovery, authoritative operator truth, and the
hosted product evidence required by the master plan.

Only `done` satisfies a dependency. A task that is blocked, cancelled,
superseded, submitted, merged-but-not-deployed, registry-only, fixture-only, or
missing its terminal downstream readback does not open the next frontier.

## Planner and fleet authority

The planner/controller for this program may author and archive plans, generate
and dispatch task packets, monitor state, and issue acceptance or rejection
verdicts. It must not implement any task's declared product artifacts. Product
implementation belongs only to a supervisor-admitted fleet worker in a clean
task worktree bound to the exact task, run, provider, worker slot, declared
scope, expected branch, remote, and merge target.

Owner and reviewer labels are insufficient by themselves. The execution record
must prove distinct admitted runtime identities and a formal exact-head review.
A planner review, self-authored trailer, same-session subagent note, draft PR,
local diff, or unmerged worktree is not independent review or completion proof.
Such work is input that the admitted fleet may audit, adopt, rewrite, or
discard. `LOOP-PROD-DELIVERY-001` makes this boundary fail closed before later
additive implementation is admitted.

## Audited baseline

The 2026-07-13 audit established these starting facts:

- 37 archived `LOOP-AUTO-*` tasks were done, while live loop health exposed
  zero controller records, zero live loops, and zero reconciled loops.
- Eleven canonical rows were still reported `api-only`; Capital Pool
  Execution was `manual`.
- deployment consumed receipts without a real apply callback, paper execution
  lacked a first-class signal producer, teaching could pass on stub data,
  consultation could manufacture a successful memo, evolution could manufacture
  `SUBMITTED`, and loop-run/Journey truth depended on manual/backfill paths.
- the hosted execute-plans release used unsafe write flags; a failed deploy
  switched the live symlink before probes and did not roll back.
- the BFF did not expose an exact git SHA/image identity, hosted auth still
  admitted a broad development bearer posture, and several frontend features
  were not on the exact deployed branch.
- Pantheon PR `#3557` was implemented by the planner without a canonical task
  or independent review, activated a BFF-only browser-auth restriction, and
  hit `AUTH_PUBLIC_BROWSER_ENVIRONMENT_FORBIDDEN` before route RBAC on the
  observed permissive deployment; this does not prove missing viewer grants.
  execute-plans PR
  `#323` then changed the frontend independently; Pantheon PRs `#3587` and
  `#3588` duplicated the same revert. These are incident inputs, not accepted
  delivery evidence.
- Strategy Workshop still had six intentionally fail-closed 501 operations.
  Honest unavailability is preferable to a fake success, but it is not product
  completion.

Every task must re-audit current `origin/dev` before editing because active
PPL, TJ, PINT, EVOCHAIN, SSE, and reconciliation work may advance after this
baseline.

## Program gates

| Gate | Fail-closed admission |
| --- | --- |
| G0 | 12 canonical loops + OODA overlay, unique IDs, valid one-repo routing, acyclic dependencies, explicit existing-task convergence, planner/fleet separation, canonical task/run/worktree/scope provenance, and distinct formal review |
| G1 | strict scoped dev auth, no browser bearer/secret, complete viewer route matrix, safe writes false, exact-SHA paired FE/BFF gate-before-deploy, one cutover lease, candidate probe, and two-sided rollback |
| G2 | default deployment owner, durable trigger, real canonical effect, terminal target readback |
| G3 | duplicate/lease/timeout/DLQ/replay and worker/BFF/DB/full-stack recovery; controller truth, not registry metadata |
| G4 | Knowledge, Execution, Human Interaction, and Management Repair target-dev paths |
| G5 | authenticated desktop/mobile, strict performance, accessibility, SSE recovery, degraded/error, RBAC/tenant/MFA/two-person matrix |
| G6 | protected signed attestations with checksums only as in-envelope content digests, canonical fleet delivery provenance, exact PR/merge/deploy identities, distinct-runtime formal review, protected Human/Ops verdict, evidence-derived maturity, zero blocking risk |

## Primary DAG

### Wave 0 — Safety, identity, truth, and evidence enforcement

| Task | Owner / reviewer | Repo | True dependencies | Outcome |
| --- | --- | --- | --- | --- |
| [LOOP-PROD-000](LOOP-PROD-000.md) | Codex / Codex2 | `pantheon` | none | Canonical loop inventory and OODA overlay truth |
| [LOOP-PROD-001](LOOP-PROD-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-000` | Durable controller truth substrate |
| [LOOP-PROD-002](LOOP-PROD-002.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-000`<br>`LOOP-PROD-001` | Product evidence schema and anti-false-close gate |
| [LOOP-PROD-AUTH-001](LOOP-PROD-AUTH-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-002` | Strict dev auth cutover and exact BFF build identity; not browser activation authority |
| [LOOP-PROD-FE-001](LOOP-PROD-FE-001.md) | Codex / Codex2 | `execute-plans` | `LOOP-PROD-002`<br>`LOOP-PROD-AUTH-001` | Gate-before-deploy safe execute-plans release |
| [LOOP-PROD-REC-001](LOOP-PROD-REC-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-001`<br>`LOOP-PROD-002` | Full-stack loop recovery and fault-injection harness |

### Wave 1 — Canonical loop owners and real side effects

| Task | Owner / reviewer | Repo | True dependencies | Outcome |
| --- | --- | --- | --- | --- |
| [LOOP-PROD-SRC-001](LOOP-PROD-SRC-001.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-001`<br>`LOOP-PROD-002`<br>`LOOP-PROD-REC-001`<br>`LOOP-PROD-AUTH-001` | Source requirement reconciler and default scheduler |
| [LOOP-PROD-DIST-001](LOOP-PROD-DIST-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-SRC-001` | Durable Strategy Distillation event consumer |
| [LOOP-PROD-ALPHA-001](LOOP-PROD-ALPHA-001.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-DIST-001` | Durable Alpha Replication and revalidation worker |
| [LOOP-PROD-TEACH-001](LOOP-PROD-TEACH-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-SRC-001`<br>`LOOP-PROD-REC-001`<br>`LOOP-PROD-ALPHA-001` | Fail-closed Persona Teaching on authoritative data |
| [LOOP-PROD-AGORA-001](LOOP-PROD-AGORA-001.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-001`<br>`LOOP-PROD-002`<br>`LOOP-PROD-REC-001`<br>`LOOP-PROD-TEACH-001`<br>`LOOP-PROD-AUTH-001`<br>`AG-GAP-014` | Durable Agora evidence, dataset, and handoff worker |
| [LOOP-PROD-CONS-001](LOOP-PROD-CONS-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-001`<br>`LOOP-PROD-002`<br>`LOOP-PROD-REC-001`<br>`LOOP-PROD-AGORA-001` | Real-participant Consultation workflow |
| [LOOP-PROD-AGORA-002](LOOP-PROD-AGORA-002.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-CONS-001`<br>`LOOP-PROD-ALPHA-001`<br>`AG-GAP-005`<br>`LOOP-PROD-ATTEST-001` | Implement six deferred Strategy Workshop operations |
| [LOOP-PROD-AGORA-003](LOOP-PROD-AGORA-003.md) | Codex2 / Codex | `execute-plans` | `LOOP-PROD-AGORA-002`<br>`LOOP-PROD-FE-001`<br>`AG-GAP-013`<br>`AG-GAP-014`<br>`OPS-EP-DEV-MAIN-RECONCILE-001` | Hosted Strategy Workshop generated client and actions |
| [LOOP-PROD-IMIT-001](LOOP-PROD-IMIT-001.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-AGORA-001`<br>`LOOP-PROD-TEACH-001`<br>`LOOP-PROD-REC-001`<br>`LOOP-PROD-CONS-001`<br>`LOOP-PROD-AGORA-002` | Default Human Imitation and shadow evaluation chain |
| [LOOP-PROD-DEP-001](LOOP-PROD-DEP-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-001`<br>`LOOP-PROD-002`<br>`LOOP-PROD-REC-001`<br>`LOOP-PROD-IMIT-001` | Canonical deployment dispatcher and RuntimeBinding readback |
| [LOOP-PROD-CAP-001](LOOP-PROD-CAP-001.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-DEP-001`<br>`LOOP-PROD-REC-001`<br>`TJ-E2E-014` | First-class bounded paper DecisionSignalProducer |
| [LOOP-PROD-TEL-001](LOOP-PROD-TEL-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-001`<br>`LOOP-PROD-002`<br>`LOOP-PROD-REC-001`<br>`LOOP-PROD-CAP-001` | Default telemetry reconciliation and incident chain |
| [LOOP-PROD-TEL-002](LOOP-PROD-TEL-002.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-CAP-001`<br>`LOOP-PROD-TEL-001`<br>`TJ-E2E-014` | Canonical loop-run and Trade Journey lifecycle projector |
| [LOOP-PROD-EVO-001](LOOP-PROD-EVO-001.md) | Codex2 / Codex | `pantheon` | `EVOCHAIN-011`<br>`LOOP-PROD-DEP-001`<br>`LOOP-PROD-TEL-001`<br>`LOOP-PROD-REC-001`<br>`LOOP-PROD-TEL-002` | Real Evolution target-plane dispatcher |
| [LOOP-PROD-BFF-001](LOOP-PROD-BFF-001.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-001`<br>`LOOP-PROD-TEL-001`<br>`LOOP-PROD-AUTH-001`<br>`LOOP-PROD-REC-001`<br>`LOOP-PROD-EVO-001` | Authoritative BFF health monitoring and loop-health projection |
| [LOOP-PROD-OODA-001](LOOP-PROD-OODA-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-000`<br>`LOOP-PROD-001`<br>`LOOP-PROD-002`<br>`LOOP-PROD-REC-001`<br>`OPENCLAW-CRON-WRITE-SCOPE`<br>`OPENCLAW-PERSONA-CRON-BACKFILL`<br>`OPENCLAW-OODA-PACKET-CLOSURE`<br>`LOOP-PROD-BFF-001` | Per-Persona OODA schedule reconciliation and product proof |

### Wave 2 — Cross-loop product paths and target-dev verifiers

| Task | Owner / reviewer | Repo | True dependencies | Outcome |
| --- | --- | --- | --- | --- |
| [LOOP-PROD-PER-001](LOOP-PROD-PER-001.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-DEP-001`<br>`LOOP-PROD-CAP-001`<br>`LOOP-PROD-OODA-001`<br>`PPL-ALLOC-010`<br>`PPL-ALLOC-011` | Persona provisioning through binding and first-evaluation readback |
| [LOOP-PROD-TJ-001](LOOP-PROD-TJ-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-DEP-001`<br>`LOOP-PROD-CAP-001`<br>`LOOP-PROD-TEL-002`<br>`LOOP-PROD-EVO-001`<br>`LOOP-PROD-AUTH-001`<br>`TJ-E2E-014`<br>`LOOP-PROD-PER-001` | Canonical Trade Journey governed action backend |
| [LOOP-PROD-TJ-002](LOOP-PROD-TJ-002.md) | Codex / Codex2 | `execute-plans` | `LOOP-PROD-TJ-001`<br>`LOOP-PROD-FE-001`<br>`MGMT-SSE-001`<br>`OPS-EP-DEV-MAIN-RECONCILE-001`<br>`LOOP-PROD-AGORA-003` | Hosted Trade Journey action controls |
| [LOOP-PROD-MAI-001](LOOP-PROD-MAI-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-AUTH-001`<br>`LOOP-PROD-001`<br>`LOOP-PROD-002`<br>`LOOP-PROD-REC-001`<br>`LOOP-PROD-TJ-001`<br>`LOOP-PROD-WORKER-001`<br>`LOOP-PROD-BROWSER-AUTH-001` | Hosted Management AI repair and dev-bridge backend proof |
| [LOOP-PROD-MAI-002](LOOP-PROD-MAI-002.md) | Codex / Codex2 | `execute-plans` | `LOOP-PROD-MAI-001`<br>`LOOP-PROD-FE-001`<br>`MGMT-SSE-001`<br>`OPS-EP-DEV-MAIN-RECONCILE-001`<br>`LOOP-PROD-TJ-002` | Hosted Management AI repair product UI |
| [LOOP-PROD-VERIFY-KNOW-001](LOOP-PROD-VERIFY-KNOW-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-SRC-001`<br>`LOOP-PROD-DIST-001`<br>`LOOP-PROD-ALPHA-001`<br>`LOOP-PROD-TEACH-001`<br>`LOOP-PROD-AGORA-001`<br>`LOOP-PROD-AGORA-002`<br>`LOOP-PROD-AGORA-003`<br>`LOOP-PROD-CONS-001`<br>`LOOP-PROD-IMIT-001`<br>`LOOP-PROD-BFF-001` | Target-dev Knowledge spine product verifier |
| [LOOP-PROD-VERIFY-EXEC-001](LOOP-PROD-VERIFY-EXEC-001.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-PER-001`<br>`LOOP-PROD-DEP-001`<br>`LOOP-PROD-CAP-001`<br>`LOOP-PROD-TEL-001`<br>`LOOP-PROD-TEL-002`<br>`LOOP-PROD-EVO-001`<br>`LOOP-PROD-BFF-001`<br>`PPL-ALLOC-012` | Target-dev Execution spine product verifier |
| [LOOP-PROD-VERIFY-HUMAN-001](LOOP-PROD-VERIFY-HUMAN-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-TEACH-001`<br>`LOOP-PROD-AGORA-001`<br>`LOOP-PROD-AGORA-003`<br>`LOOP-PROD-CONS-001`<br>`LOOP-PROD-IMIT-001`<br>`PINT-010-R2` | Target-dev Human interaction and learning verifier |
| [LOOP-PROD-VERIFY-OODA-001](LOOP-PROD-VERIFY-OODA-001.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-OODA-001`<br>`LOOP-PROD-VERIFY-KNOW-001`<br>`LOOP-PROD-VERIFY-EXEC-001`<br>`LOOP-PROD-MAI-001` | Multi-persona OODA overlay product verifier |

### Wave 3 — Existing product-program convergence

| Task | Owner / reviewer | Repo | True dependencies | Outcome |
| --- | --- | --- | --- | --- |
| [LOOP-PROD-PPL-001](LOOP-PROD-PPL-001.md) | Codex2 / Codex | `pantheon` | `PPL-ALLOC-009`<br>`PPL-ALLOC-010`<br>`PPL-ALLOC-011`<br>`PPL-ALLOC-012`<br>`PPL-ALLOC-013`<br>`LOOP-PROD-PER-001`<br>`LOOP-PROD-VERIFY-EXEC-001`<br>`LOOP-PROD-FE-001` | Persona promotion and allocation product closeout |
| [LOOP-PROD-TJ-003](LOOP-PROD-TJ-003.md) | Codex / Codex2 | `pantheon` | `TJ-E2E-012`<br>`TJ-E2E-014`<br>`LOOP-PROD-TJ-001`<br>`LOOP-PROD-TJ-002`<br>`LOOP-PROD-VERIFY-EXEC-001` | Trade Journey superseding product closeout |
| [LOOP-PROD-PINT-001](LOOP-PROD-PINT-001.md) | Codex2 / Codex | `pantheon` | `OPS-EP-DEV-MAIN-RECONCILE-001`<br>`PINT-010-R2`<br>`LOOP-PROD-AGORA-003`<br>`LOOP-PROD-VERIFY-HUMAN-001`<br>`LOOP-PROD-FE-001` | Persona Interaction reconciled hosted product closeout |
| [LOOP-PROD-MAI-003](LOOP-PROD-MAI-003.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-MAI-001`<br>`LOOP-PROD-MAI-002`<br>`LOOP-PROD-VERIFY-OODA-001` | Management AI/OpenClaw product closeout |

### Wave 4 — Baseline global product checkpoint

| Task | Owner / reviewer | Repo | True dependencies | Outcome |
| --- | --- | --- | --- | --- |
| [LOOP-PROD-CLOSE-001](LOOP-PROD-CLOSE-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-002`<br>`LOOP-PROD-AUTH-001`<br>`LOOP-PROD-FE-001`<br>`LOOP-PROD-REC-001`<br>`LOOP-PROD-VERIFY-KNOW-001`<br>`LOOP-PROD-VERIFY-EXEC-001`<br>`LOOP-PROD-VERIFY-HUMAN-001`<br>`LOOP-PROD-VERIFY-OODA-001`<br>`LOOP-PROD-PPL-001`<br>`LOOP-PROD-TJ-003`<br>`LOOP-PROD-PINT-001`<br>`LOOP-PROD-MAI-003` | Baseline 12-loop plus OODA product checkpoint only |

`LOOP-PROD-CLOSE-001` is retained because the baseline record may already be
live. It is a checkpoint, not the final program verdict after this addendum.

### Addendum Wave 0 — Fleet delivery authority and authorized auth bootstrap

| Task | Owner / reviewer | Repo | True dependencies | Outcome |
| --- | --- | --- | --- | --- |
| [LOOP-PROD-DELIVERY-001](LOOP-PROD-DELIVERY-001.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-002` | Fleet-only delivery provenance and independent review admission |
| [LOOP-PROD-AUTH-BOOT-001](LOOP-PROD-AUTH-BOOT-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-002`<br>`LOOP-PROD-DELIVERY-001` | Authorized dev auth credential bootstrap |

### Addendum Wave 1 — Worker, lease, browser auth, fairness, attestation, and auth operations

| Task | Owner / reviewer | Repo | True dependencies | Outcome |
| --- | --- | --- | --- | --- |
| [LOOP-PROD-WORKER-001](LOOP-PROD-WORKER-001.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-001`<br>`LOOP-PROD-002`<br>`LOOP-PROD-DELIVERY-001` | Exact-CAS worker outcome and forced termination integrity |
| [LOOP-PROD-LEASE-001](LOOP-PROD-LEASE-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-AUTH-001`<br>`LOOP-PROD-WORKER-001` | Protected shared-dev mutation lease and payload isolation |
| [LOOP-PROD-FLEET-001](LOOP-PROD-FLEET-001.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-WORKER-001` | Fair, quota-aware, starvation-bounded fleet admission |
| [LOOP-PROD-ATTEST-001](LOOP-PROD-ATTEST-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-002`<br>`LOOP-PROD-WORKER-001`<br>`LOOP-PROD-LEASE-001` | Protected product attestation trust root |
| [LOOP-PROD-AUTH-OPS-001](LOOP-PROD-AUTH-OPS-001.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-AUTH-001`<br>`LOOP-PROD-LEASE-001`<br>`LOOP-PROD-ATTEST-001` | Governed dev credential and privileged-capability lifecycle |
| [LOOP-PROD-BROWSER-AUTH-001](LOOP-PROD-BROWSER-AUTH-001.md) | Codex2 / Codex | `pantheon` | `LOOP-PROD-AUTH-BOOT-001`<br>`LOOP-PROD-AUTH-001`<br>`LOOP-PROD-FE-001`<br>`LOOP-PROD-DELIVERY-001`<br>`LOOP-PROD-LEASE-001`<br>`LOOP-PROD-AUTH-OPS-001` | Coordinated credential-free browser auth cutover and paired rollback |

### Addendum Wave 3 — Final execute-plans evidence and build qualification

| Task | Owner / reviewer | Repo | True dependencies | Outcome |
| --- | --- | --- | --- | --- |
| [LOOP-PROD-FE-EVID-001](LOOP-PROD-FE-EVID-001.md) | Codex2 / Codex | `execute-plans` | `LOOP-PROD-FE-001`<br>`LOOP-PROD-ATTEST-001`<br>`LOOP-PROD-AGORA-003`<br>`LOOP-PROD-TJ-002`<br>`LOOP-PROD-MAI-002` | Fail-closed protected-attestation consumer |
| [LOOP-PROD-FE-BUILD-001](LOOP-PROD-FE-BUILD-001.md) | Codex / Codex2 | `execute-plans` | `LOOP-PROD-FE-001`<br>`LOOP-PROD-FE-EVID-001`<br>`LOOP-PROD-AGORA-003`<br>`LOOP-PROD-TJ-002`<br>`LOOP-PROD-MAI-002` | Warning-free, budgeted live/strict product build |

### Addendum Wave 4 — Protected Human/Ops completion guard

| Task | Owner / reviewer | Repo | True dependencies | Outcome |
| --- | --- | --- | --- | --- |
| [LOOP-PROD-SIGNOFF-001](LOOP-PROD-SIGNOFF-001.md) | Codex / Codex2 | `pantheon` | `LOOP-PROD-CLOSE-001`<br>`LOOP-PROD-WORKER-001`<br>`LOOP-PROD-ATTEST-001` | Protected Human/Ops completion-verdict enforcement |

### Wave 5 — Additive final program closeout

| Task | Owner / reviewer | Repo | True dependencies | Outcome |
| --- | --- | --- | --- | --- |
| [LOOP-PROD-CLOSE-002](LOOP-PROD-CLOSE-002.md) | Codex2 / Codex | `pantheon` | `EVOCHAIN-011`<br>`EVOLOOP-009`<br>`EVOLOOP-011`<br>`LOOP-PROD-CLOSE-001`<br>`LOOP-PROD-DELIVERY-001`<br>`LOOP-PROD-WORKER-001`<br>`LOOP-PROD-LEASE-001`<br>`LOOP-PROD-BROWSER-AUTH-001`<br>`LOOP-PROD-FLEET-001`<br>`LOOP-PROD-ATTEST-001`<br>`LOOP-PROD-AUTH-OPS-001`<br>`LOOP-PROD-FE-EVID-001`<br>`LOOP-PROD-FE-BUILD-001`<br>`LOOP-PROD-SIGNOFF-001` | Sole final verdict for the 48-task primary catalog after bootstrap evidence |

Wave 1 deliberately carries additional delivery dependencies between tasks
that share the root `docker-compose.yml` integration surface. Those edges
serialize canonical deployment ownership while unrelated execute-plans and
contract work can still proceed in parallel; they are not claims that one
domain algorithm semantically depends on another.

## Existing work consumed, not recreated

The catalog validates these external dependency IDs against active supervisor
state or the archive at live dispatch time:

- `AG-GAP-005`
- `AG-GAP-013`
- `AG-GAP-014`
- `EVOCHAIN-011`
- `EVOLOOP-009`
- `EVOLOOP-011`
- `MGMT-SSE-001`
- `OPENCLAW-CRON-WRITE-SCOPE`
- `OPENCLAW-PERSONA-CRON-BACKFILL`
- `OPENCLAW-OODA-PACKET-CLOSURE`
- `OPS-EP-DEV-MAIN-RECONCILE-001`
- `PINT-010-R2`
- `PPL-ALLOC-009`
- `PPL-ALLOC-010`
- `PPL-ALLOC-011`
- `PPL-ALLOC-012`
- `PPL-ALLOC-013`
- `TJ-E2E-012`
- `TJ-E2E-014`

In particular:

- `PPL-ALLOC-009..013` converge through `LOOP-PROD-PPL-001`.
- `TJ-E2E-012` remains historical closeout evidence; `TJ-E2E-014` and the
  new producer/projector/action work converge through `LOOP-PROD-TJ-003`.
- `OPS-EP-DEV-MAIN-RECONCILE-001` and `PINT-010-R2` remain the sole PINT
  implementation convergence path.
- `EVOCHAIN-011` must complete before the new target-plane evolution
  dispatcher can start.
- `EVOLOOP-009` supplies the thin-slice deploy-and-closeout evidence consumed
  by `LOOP-PROD-CLOSE-002`; it is not re-dispatched here.
- `EVOLOOP-011` supplies persona-learning feedback consumed by the global OODA
  closeout; it is not the EVOLOOP deploy-and-closeout task.
- completed OpenClaw cron/write-scope/packet tasks are substrate for the OODA
  overlay task, not product-level restart and controller-health proof.
- `AG-GAP-005` preserved the six unavailable operations honestly; the new
  Agora tasks implement them. `AG-GAP-013/014` evidence is consumed rather
  than re-dispatched.
- Pantheon PR `#3554`, `#3558`, and `#3572`, execute-plans PR `#310` and
  `#311`, and any associated local worktrees are non-authoritative draft
  inputs. Their admitted fleet owners must audit exact heads and may adopt,
  rewrite, or discard them.
- Pantheon PR `#3557`, reverts `#3587/#3588`, and execute-plans PR `#323` are
  incident fixtures. A merge, revert, or deployment from that sequence is not
  proof that browser authorization, delivery provenance, or paired rollout is
  complete.

## Repository routing

Every task owns exactly one repository:

- Pantheon artifacts are ordinary repo-relative paths.
- execute-plans artifacts use the exact `execute-plans/` slash prefix and
  merge to execute-plans `dev`.
- no task mixes Pantheon and execute-plans artifacts. Backend, frontend, and
  evidence-aggregation closeout are separate tasks.

Do not use the legacy `execute-plans:` colon form; it routes completion to
the wrong repository and can create phantom-done evidence.

## Dispatch safety

The dispatcher must be merged before live use. It:

- validates every required field, task document, owner/reviewer, loop metadata,
  one-repo routing, dependency existence, and DAG acyclicity;
- requires the exact twelve canonical L1 loop IDs plus the OODA composite
  overlay, exact inventory/final-authority union, and at least one non-close
  product-level task for every loop;
- validates the immutable planner/fleet/reviewer authority contract and binds
  its digest into every newly materialized task;
- rejects a frozen planning wave;
- requires the external runtime-lock bootstrap capability before any
  authoritative dry-run/apply, then serializes every canonical status writer
  with the stable task-state lock and holds runtime admission serialization
  before the task-state transaction;
- fails closed when any migration/additive target is queued, running,
  approval-suspended, execution-admitted, or backed by unavailable/malformed
  live runtime state;
- checks both active and archived task IDs;
- never resurrects an archived terminal ID;
- preserves non-migration baseline records in full; applies only the v5
  allowlist to `AGORA-002`, `MAI-001`, and checkpoint-only `CLOSE-001`, and
  accepts additive collisions only when program, catalog, immutable contract,
  provenance, and completion role are exact;
- never rewrites `agents[].current_task_ids`, status, or next action; the live
  supervisor alone owns capacity and frontier activation;
- performs an atomic fsync + replace of `ai-status.json`;
- commits assignment/migration audit events through an idempotent transactional
  outbox so a crash cannot permanently lose or duplicate activity records;
- supports catalog validation and mutation-free dry run.

Validate locally:

```sh
python3 scripts/dispatch_loop_product_level_remediation_2026-07-13.py --validate-only
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon \
  python3 scripts/dispatch_loop_product_level_remediation_2026-07-13.py --dry-run  # blocked until bootstrap done

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider \
  scripts/test_dispatch_loop_product_level_remediation_2026_07_13.py
```

After the external bootstrap task is merged, deployed, and its exact
capability read back, run an authoritative dry-run and obtain independent
review before any apply. Until then, do not mutate the canonical live status
root. After that gate:

```sh
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon \
  python3 scripts/dispatch_loop_product_level_remediation_2026-07-13.py --apply

PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon \
  python3 scripts/ai_status.py sync
```

Do not bulk-materialize this DAG through the Management AI DevTaskPacket bridge
until its multi-value delimiter and partial-failure replay defects are fixed.

## Universal closeout rule

Every primary task must archive a redacted, append-only, checksummed
`evidence.json` and the reviewer must set `review_file` to it. Evidence must
identify the exact branch, PR, required checks, merge SHA, deployment SHA/image,
request/receipt/post-state correlation, duplicate/recovery/rollback/security
results, and residual risks. User-visible tasks additionally require hosted
1440px desktop and 390px mobile DOM/network evidence, axe serious/critical
zero, keyboard/focus/reduced-motion, `FE_INT_GATE_PERF_STRICT=1`, and no
unexpected console, CORS, chunk, or BFF errors.

An unkeyed evidence checksum proves content integrity only. Program acceptance
also requires the protected controller attestation: an asymmetric signature or
platform-protected keyed identity over the bound manifest and its digests.

The baseline `LOOP-PROD-CLOSE-001` checkpoint cannot declare program
completion. The final `LOOP-PROD-CLOSE-002` task requires an independent
Human/Ops verdict and zero unresolved blocking product risk across all 48
primary tasks, with the external runtime-lock bootstrap evidence accepted
tasks.
