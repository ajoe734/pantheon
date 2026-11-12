# Persona portfolio combination source acceptance

Task: `PERSONA-PORTFOLIO-COMBINATION-20261112`, owner Codex, reviewer Claude.

The new `persona_portfolio_v1` strategy logic is carried by the existing
StrategyArtifact / `execution_bundle`. Each member embeds its exact artifact,
version and checksum, risk weight and virtual-only flag. The envelope checksum
also binds the combination rule, limits, expression policy and lineage. The
normal registration API creates draft/candidate entries only; approval,
DeploymentPlan and RuntimeBinding authorization remain required. No runtime
reads mutable pool membership to choose an executed strategy.

Paper evaluates every envelope underlying inside the same binding and queue.
It prepares every underlying decision before enqueuing any, so missing member
data or a missing underlying snapshot cannot produce a partial evaluated pool.
The existing instrument expression, risk gate, account ledger and capital pool
single-runtime enforcement remain in use. Source-owned latest stored snapshots
remain the production input; inline `market_inputs` are explicit recorded replay
inputs, with no data or member fallback.

The registry evaluator nets `direction × strength × risk_weight` with decimal
weights and clips to `max_net_strength`. A legacy directional view whose
strength is `None` represents unit strength. A full-weight singleton returns
the original member view verbatim. Factor members evaluate their own history;
aggregate persona holding state is never supplied as multi-member state.

Virtual books observe the exact same underlying close, source reference and bar
time for all members, before instrument expression or execution. Previous-bar
view exposure earns the current close-to-close return, then the current view
sets the next exposure. They never place orders. Trial members with zero
execution weight continue to accrue virtual observations. Member return series
use equal notional across the member's underlyings and only intervals observed
for every underlying; missing marks are withheld rather than filled with zero.
Per-underlying details accompany each member's total return, sample Sharpe and
maximum drawdown. Daily Sharpe uses 252 periods/year; fewer than two observations
or zero variance yields an explicit undefined Sharpe. These are gross underlying
view metrics, separate from the persona's instrument/account performance.

The books persist atomically in a sibling `.member-books.json` file beside the
existing lifecycle outbox. Binding, envelope checksum, member and underlying
scope every series. Duplicate observations, including older replayed decisions,
do not add returns; conflicting or previously unseen out-of-order observations
fail closed. Runtime snapshots, heartbeats and the telemetry runtime projection
expose the member metrics alongside account performance. Binding rollover clears
the projection's previous member metrics.

## Recorded market evidence

Four original TWSE `STOCK_DAY` JSON responses are committed here: 2330 and 2317,
October and November 2024 (19 + 21 trading days per instrument). Retrieval URLs
and SHA-256 hashes are in `evidence.json`. Values are parsed directly from the
recorded responses; no generated OHLCV or interpolated prices enter parity.

The parity test traverses all 39 evaluable bars per underlying (78 decisions),
comparing paper net directions/actions and strengths with the vectorbt evaluator
bar for bar, then runs the real vectorbt backend. Separate integration exercises
the producer queue and PaperRuntimeService, observes all three virtual members
across both underlyings with exactly offset execution views, proves no account
fills, restarts the runtime, and projects member metrics through telemetry.

## Scope and remaining delivery

This is source acceptance, not hosted activation or a claim that a pool has been
approved/deployed. Publishing a new approved immutable envelope whenever pool
membership or weights change belongs to wave 2. Intraday virtual-book
annualisation remains for the ordered intraday task; this implementation accepts
daily virtual marks only. Canonical review and supervisor integration must finish
before owner closeout can mark the task done.
