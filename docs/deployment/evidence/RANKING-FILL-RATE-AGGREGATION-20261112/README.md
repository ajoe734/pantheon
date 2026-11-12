# RANKING-FILL-RATE-AGGREGATION-20261112

Owner: Codex2. Assigned reviewer: Claude. Source implementation ready; shared
fixture scope repair required before independent review. No approval, merge or
hosted activation is claimed.

## Contract

`fill_rate` is the sum of absolute filled quantities divided by the sum of
absolute submitted quantities for unique orders submitted within the requested
UTC interval `[start_at, end_exclusive_at)`. Only fills inside that interval
contribute. Requests rejected before or at the paper broker count in submitted
quantity; no-op/HOLD decisions do not. Partial fills retain the original request
amount. Different order sizes weight the result by quantity, never by event
count, process counters or an average of rates. Quantities are native order
units; no price, notional, capital or synthetic unit conversion is introduced.

`fill_rate_details` exposes status, reason, numerator, denominator, unit, the
selected window and per-order runtime/binding/event sources. The non-quarter
summary exposes the retained observation start/as-of boundary. Missing requests,
zero/missing denominators, incomplete runtime inputs, conflicting retries,
malformed identities/timestamps and overfills remain unknown. An order submitted
before quarter open belongs to its original submission cohort; its later fill
is not a numerator for a quarter that has no corresponding denominator.

The runtime no longer emits `fill_rate=0` on rejection/no-op or a process-counter
rate on performance snapshots. Telemetry persists `order_execution_observations`
in its existing atomic projection store; stable order/fill identities deduplicate
out-of-order delivery, replay and restarts. Binding rollover clears old facts and
tenants remain isolated. Legacy scalar-only summaries cannot invent missing
order history. Rejection/no-op events retain their reasons.

The existing score function is bytecode-independent AST-identical to dev; see
`formula-identity.json`. Unknown fill input is withheld by the ranking admission
layer: null execution/overall score, unknown tier, no rank or allocation input,
and `unknown_fill_rate` exclusion. The known-input arithmetic, weights and formula
version are unchanged. The HTTP test uses real signal consumption, paper runtime,
emitter, lifecycle outbox, persistent telemetry and registered quarterly route.
Broker transport and unrelated equity-owner inputs are explicit local fixtures.
It proves partial fill 4/10 = 0.4, then runtime restart, reject 6 and no-op:
4/16 = 0.25 and execution score 25.0.

## Validation

The manifest records exact commands, exit codes, hashes and source identities.
The three scoped suites completed with 293 passed; after freezing the scenario
clock and adding native-quantity coverage, the affected subset passed 9/9.
These overlapping counts must not be added. The shared fixture contract is
**3 failed, 2 passed** on this delivery. It is 5/5 on the unchanged base and 5/5
with the exact proposed fixture repair applied in an isolated temporary source
copy. Assertions and production gates were not weakened or skipped.

## Required scope repair

`services/control-plane/bff/test_bff_promotion_review_governance.py` defines the
shared `build_pm12_eligible_persona_records` fixture used by the three failures.
Its current scalar fill rate lacks order facts. `proposed-fixture-repair.patch`
adds explicit local submitted/partial-filled test facts using the builder's
existing `fill_rate` parameter. The target file has not been edited in this task
worktree because it is absent from the canonical artifact contract.

The task handoff admission validates all PR files against declared artifacts
(`.orchestrator/rewrite/task_contract.py::validate_task_artifact_diff_scope`).
The governed `artifact-contract` mutation is Human/Ops-only and requires the task
to be todo/blocked (`AI_COLLABORATION_GUIDE.md`, Status Commands). This is an
artifact-scope hold, not a task-dependency hold; the declared quarter task is done.

Required continuation: Human/Ops adds exactly the shared fixture file through
`artifact-contract`, reopens this existing task, and returns it to Codex2. Apply
the committed patch, rerun the unchanged fixture contract and affected callers,
update this same PR/manifest, then hand off exact PR/head/manifest to Claude.
Supervisor integration and owner `done` follow independent approval and merge.
Do not create a replacement task or weaken the unknown-input guard.

## Limits

No hosted deployment, trading, allocation or real-capital action occurred.
Durability is the existing telemetry projection plus runtime lifecycle/fill
outbox; reconstruction of missing legacy requests is deliberately unavailable.
Retained facts grow with actual order history, without silent truncation.
Partial quantities and source evidence are observational inputs, not a change
to scoring weights or a claim of complete historical telemetry coverage.
