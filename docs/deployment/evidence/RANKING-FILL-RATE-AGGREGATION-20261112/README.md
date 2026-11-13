# RANKING-FILL-RATE-AGGREGATION-20261112

Owner: Codex2. Assigned reviewer: Claude. Source implementation and authorized
shared fixture repair are ready for fresh exact-head independent review in
PR #7247. No approval, merge or hosted activation is claimed.

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
The original three scoped suites completed with 293 passed; after freezing the
scenario clock and adding native-quantity coverage, the affected subset passed
9/9. These historical overlapping counts must not be added to fresh results.
The shared fixture contract now passes **5/5 in the actual task worktree**.
The manifest retains the earlier 3-failed/2-passed result as resolved historical
evidence, alongside the original baseline and isolated-patch experiments.
Fresh ranking and promotion HTTP callers pass **57/57**, including the real
runtime/restart-to-quarterly-ranking test and zero/unknown admission cases.
The broader four-suite caller run reports **150 passed, 4 failed**. All four
failures reproduce on the unchanged base `421c13abc`: three inbox/cockpit timeout
contracts and a portfolio PNL assertion (`None` versus `1.25`). Both terminal
logs are retained; these failures are not counted as passes or repaired in this
task. The 57-test run overlaps the broader run. Assertions and production gates
were not weakened or skipped.

## Authorized fixture repair

`services/control-plane/bff/test_bff_promotion_review_governance.py` defines the
shared `build_pm12_eligible_persona_records` fixture used by the three failures.
Its former scalar fill rate lacked order facts. Human/Ops added this exact
artifact to the canonical contract at 2026-11-12T14:58:30Z and reopened the same
task. Anchor commit `35e4e19f2` applies `proposed-fixture-repair.patch` in the
original branch: explicit local submitted/partial-filled test facts use the
builder's existing `fill_rate` parameter. These are unit-fixture facts only.

All six prior task source hashes remain unchanged. AST comparison verifies the
fixture builder is identical after removing only the newly added order facts;
the merged PNL owner-capital and quarter-series inputs are preserved. The scorer
AST still equals the delivery base. Production unknown/zero/partial-fill/restart
semantics and score weights remain unchanged by this repair.

The earlier artifact-scope hold is resolved, and the declared quarter dependency
is done. Fresh exact-head Claude review, required CI and native supervisor
integration remain required before owner closeout. The same PR #7247 carries
this delivery.

## Limits

No hosted deployment, trading, allocation or real-capital action occurred.
Durability is the existing telemetry projection plus runtime lifecycle/fill
outbox; reconstruction of missing legacy requests is deliberately unavailable.
Retained facts grow with actual order history, without silent truncation.
Partial quantities and source evidence are observational inputs, not a change
to scoring weights or a claim of complete historical telemetry coverage.
