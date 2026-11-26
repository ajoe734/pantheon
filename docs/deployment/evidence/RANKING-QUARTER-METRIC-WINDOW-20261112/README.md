# RANKING-QUARTER-METRIC-WINDOW-20261112

Owner: Codex2. Assigned independent reviewer: Claude. Source anchor:
`7d4565c43437d99c3f9f54fda4be52e9da824626`.

## Implemented source

The runtime projection retains deduplicated `quarter_equity.v1` owner valuations
from accepted PnL events, including equity, fixed initial funding, cumulative PnL,
fill count, real mark references and event/runtime/binding identity. The
execution valuation contract rejects missing marks and unreconciled funding.
Failed valuation heartbeats remain explicit unknown observations. Replays and
out-of-order arrivals retain the earlier decline; binding rollover clears the
prior generation, and tenant partitions remain separate.

The real quarterly ranking route reduces only observations in `[start, end)`
using UTC instants. A ledger opened inside the quarter uses its actual initial
funding; an older ledger requires an actual observation exactly at quarter open.
PnL is the difference between endpoint cumulative values, never their sum.
Maximum drawdown is computed over the retained quarter equity series, including
the owner opening balance. Multiple runtime ledgers require a synchronized grid
and matching currency; no missing marks are carried forward. Funding changes,
conflicts, missing opening equity or missing series yield null PnL/drawdown,
null score/rank/allocation input, and explicit exclusion reasons.

Runtime coverage, distinct observed UTC days / quarter days, and trading sample
sufficiency are separate diagnostics. No new trading-sample threshold is
invented: sufficiency stays unknown with `no_trading_sample_threshold_declared`.
Observed-day coverage does not prove complete intraday history. Formula version,
weights, tier thresholds and the compatibility evidence-count divisor are
unchanged. Execution metrics use their own in-quarter timestamps. Snapshots bind
the item quarter bounds and metric diagnostics in the content digest.

## Verification and fixture repair

See `evidence.json` and the captured terminal logs for exact commands, results
and hashes. The expanded whole-service batch timed out (exit 124); it is not
counted as passed.

The original fixture contract reported **3 failures / 2 passes** because its
shared builder supplied only a latest telemetry tuple. Human/Ops added exactly
the missing fixture file to the canonical artifact contract at
`2026-11-12T14:02:08Z` and reopened this same task at `14:02:15Z`:

`services/control-plane/bff/test_bff_promotion_review_governance.py`

Commit `7e7380efa6e09237a7b8b20db29457b9ab473bdd` applies the preserved
23-line `proposed-fixture-repair.patch` to that file. The fixture uses the real
owner observation constructor for an explicit local three-observation Q1 ledger
and supplies the two execution metric timestamps. Existing capital, currency,
period, callers and assertions remain intact. No production admission, weights,
thresholds, capital or hosted behavior changed. The original patch and
temporary-copy diagnostic remain historical evidence; run the checked-in tests
for current validation rather than reapplying the diagnostic's patch.

Fresh bounded results at that source commit (exact commands and hashed terminal
logs are in `evidence.json`):

- Unchanged fixture contract: **5 passed**.
- Quarterly route, execution producer, projection, snapshot, Capital lineage
  and evaluator regression batch: **292 passed, 28 subtests passed**. The
  missing/wrong-period, unknown, tenant and malformed-mark negatives remain.
- Full Rankings / Telemetry suites: **592 passed, 54 subtests passed, 1 skipped**.
  No skip conditions were added or modified. These tests overlap the scoped
  batch; their totals must not be added.
- Broad fixture callers: **162 passed, 8 failed**. An isolated export of the
  exact `dev` base `ac4fd811db2c2c48a2e3cb870b3ec1fb15406d9d`, separately
  provisioned, reproduces four failure identities (**14 passed, 4 failed**).
  They concern Inbox timeout behavior, Cockpit's timeout reason, and the
  security fixture's separate latest-only portfolio telemetry returning
  `total_pnl=None`. No quarter-series assertion fails in this comparison.
- Bounded recheck of all eight failed callers: **5 passed, 3 failed**. All
  four Jobs / Alerts timing cases pass unchanged; Cockpit also passes this
  recheck. The three remaining failures reproduce on `dev`. The initial
  failing batches remain failed evidence; no assertions were relaxed.

The original 180-second expanded invocation remains **exit 124 / NOT passed**.
Fresh owner suites and the scoped source batch have completed separately;
their success does not turn that historical timeout into a passing invocation.

The fixture scope hold is resolved. This delivery uses the same task, PR #7240,
Codex2 owner and assigned independent reviewer Claude. Both declared source
dependencies remain satisfied. No additional source outside the artifact
contract was changed to repair unrelated baseline failures.

Independent review, integration and owner `done` have not occurred. The source
branch is published for preservation and reviewability only. No hosted deploy,
capital allocation, production action or hosted acceptance is claimed.
