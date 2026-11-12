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

## Verification and scope hold

See `evidence.json` and the captured terminal logs for exact commands, results
and hashes. The expanded whole-service batch timed out (exit 124); it is not
counted as passed.

The additional real fixture contract reports **3 failures / 2 passes**. Its shared
builder supplies only a latest telemetry tuple, so the stricter quarter input
correctly remains unknown. The missing artifact is:

`services/control-plane/bff/test_bff_promotion_review_governance.py`

`proposed-fixture-repair.patch` is a concrete test-only repair. It supplies an
explicit local three-observation quarter series and the two execution metric
timestamps. No production returns, baselines or marks are invented. The
`fixture_diagnostic.py.txt` script applies this patch to a temporary copy and
replaces only the fixture function in memory; all **5 unchanged fixture
contract tests pass** under that proposed input. This diagnostic is not a claim
that the checked-in fixture has been repaired.

Human/Ops must add that exact file to this existing task's artifact contract
through the governed `artifact-contract` command, then `reopen` the same task.
The owner can then apply the prepared patch, rerun the fixture callers and
source checks, and hand off the exact PR/head/manifest to Claude. Artifact scope
is enforced by `.orchestrator/rewrite/task_contract.py` and only Human/Ops may
revise it (`scripts/ai_status.py::command_artifact_contract`). No new task or
re-authorization of the already completed dependencies is needed.

Independent review, integration and owner `done` have not occurred. The source
branch is published for preservation and reviewability only. No hosted deploy,
capital allocation, production action or hosted acceptance is claimed.
