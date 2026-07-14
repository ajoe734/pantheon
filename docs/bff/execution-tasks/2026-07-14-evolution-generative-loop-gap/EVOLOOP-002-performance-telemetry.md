# EVOLOOP-002 — Real performance telemetry supply

Status: implementation in progress; post-change validation and hosted acceptance pending

Owner: Codex2  
Reviewer: Claude  
Branch: `task/EVOLOOP-002`  
Merge target: `dev`

## Decision and scope

EVOLOOP-002 owns the per-binding paper-runtime performance supply:

```text
fill ledger + authoritative source-ingest marks
  -> portfolio value / PnL
  -> 20-day rolling drawdown
  -> separate pnl_snapshot and drawdown_snapshot events
  -> telemetry runtime-summary fields with independent as-of timestamps
  -> threshold-sweep input
```

This task supersedes the discussed but never dispatched `EVOCHAIN-012`, as
ruled in `.orchestrator/task-briefs/evochain_001_upstream_decision.md`. It does
not choose threshold values, approve artifact baselines, or enable
`rolling_pnl_floor`; those governance changes belong to `EVOLOOP-005`.

Canonical constraints come from:

- `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`: telemetry events remain the
  authoritative normalized evidence; runtime summaries are derived read
  models.
- `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md`: runtime producers send
  schema-valid events through telemetry ingest; they do not write the
  canonical store directly.
- `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md`: delivery is at-least-once, so
  consumers must tolerate duplicates and out-of-order arrivals.
- `docs/04/pantheon_evolution_generative_loop_gap_2026-07-14/EVOLUTION_GENERATIVE_LOOP_GAP.md`:
  missing marks must fail closed and must never be replaced by fabricated
  performance.

## Root cause of the zero-performance supply

Human/Ops live evidence captured on 2026-07-14 showed 14 paper runtime
summaries with `pnl=0.0` and `drawdown=None`; one binding,
`rb-31bd3cf07cc`, reported 7,325 executed trades. Code-path inspection found
that this was a supply defect, not evidence of genuinely flat performance:

1. The paper algorithm kept cash and holdings only in process memory and
   valued holdings with `Security.Price`.
2. A security began at a default price, and a simulated fill updated both the
   position and the same in-process price. With no independent market mark,
   cash plus holdings at the fill price exactly offset initial cash, producing
   `0.0` by construction.
3. The runtime emitted that process-local result as a `pnl_snapshot` even when
   no authoritative mark had been received. Synthetic paper prices could also
   make an internally generated value look like market evidence.
4. No component computed or emitted `drawdown_snapshot` events.
5. Worker restart discarded the cash/holding ledger, while the telemetry
   projection retained historical trade counts. This explains how a summary
   could show thousands of fills beside a reset PnL series.
6. Two adjacent accounting/projection defects could further corrupt the
   series: the Taiwan simulated-fill path did not consistently apply signed
   quantity and cash movement, and bracket-order log events could be counted
   as executed fills.

The correction is therefore at the valuation boundary: retain a fill-derived
ledger, accept only provenance-bearing market observations as marks, and emit
nothing when the valuation is incomplete.

## Valuation contract

For initial cash \(C_0\), signed fill quantity \(q_i\) (buy positive, sell
negative), fill price \(p_i\), open quantity \(Q_s\), and authoritative mark
\(M_{s,t}\):

```text
cash_t            = C_0 - sum(q_i * p_i)
position_value_t  = sum(Q_s * M_s,t)
portfolio_value_t = cash_t + position_value_t
pnl_t             = portfolio_value_t - C_0
```

The same equations cover long, short, partial-close, and flat books. Every
non-zero position must have one finite, positive, unambiguous mark. A partial
valuation is forbidden: if even one open symbol cannot be marked, the runtime
publishes a diagnostic and emits neither performance snapshot.

An accepted mark must:

- originate from a `source_type=market` source record in a normalized/indexed
  state;
- come from a price/quote/OHLCV/spot market dataset;
- include a finite positive price, canonical instrument identity, source
  reference, and market-observation timestamp;
- be no older than the configured freshness ceiling (task default: 172,800
  seconds) and no farther in the future than the clock-skew tolerance;
- resolve to exactly one instrument. A base-symbol collision across venues
  fails closed rather than selecting one arbitrarily.

Source-record availability time or ingest `created_at` is not a substitute for
market-observation time. Signal/fill prices and opt-in synthetic prices may
support execution simulation, but are not authoritative marks unless the
signal carries validated market source and observation provenance.

For an open book, the atomic valuation `as_of` is the oldest observation time
among all marks used in that valuation. This prevents a multi-symbol portfolio
from appearing fresher than its least-fresh component. For a flat book after
fills, `as_of` is the last fill time because no market mark contributes to its
cash-only value.

## Drawdown contract

Drawdown uses the same ordered portfolio-value samples as PnL. For valuation
time \(t\):

```text
peak_t     = max(portfolio_value_u), for t - 20 days <= u <= t
drawdown_t = (peak_t - portfolio_value_t) / peak_t
```

`drawdown_pct` is a fractional ratio, not percentage points: `0.18` means
18%. Its governed value domain is `[0, 1]`. A non-positive high-water mark or
otherwise invalid equity series must fail closed rather than create an
unbounded ratio. Duplicate samples and observations older than the latest
accepted per-binding `as_of` do not advance the rolling window. The runtime
window is 20 calendar days, not merely the last 20 polling iterations.

## Event and summary contract

Performance is represented by two distinct canonical events:

| Event | Primary metric | Required observation stamp |
|---|---|---|
| `pnl_snapshot` | `metrics.pnl` | top-level `pnl_as_of` |
| `drawdown_snapshot` | `metrics.drawdown_pct` | top-level `drawdown_as_of` |

The PnL event must not carry drawdown as another primary metric, and the
drawdown event must not carry PnL as another primary metric. They may carry
the same non-primary valuation evidence, such as `portfolio_value`,
`initial_cash`, `fill_count`, mark count, and mark-source references. Both
events retain the complete telemetry binding/runtime/artifact/plan envelope
required by `services/telemetry/telemetry_event.schema.json`.

`created_at` records when the event was emitted. `pnl_as_of` and
`drawdown_as_of` record when the underlying market valuation was true; the two
concepts must not be conflated. The runtime-summary projector stores each
metric's value, binding id, and independent `*_at` timestamp. A late event may
advance one metric only when its own explicit as-of is newer or equal; it must
not regress the other metric. Existing events without explicit per-field
timestamps retain `created_at` only as a backward-compatibility fallback.

The projected numeric fields are the inputs consumed by threshold sweep.
EVOLOOP-002 must prove the sweep evaluates supplied values instead of skipping
them as missing/stale, but this task does not alter the governed threshold or
baseline configuration.

## Fail-closed diagnostics

When performance cannot be valued, no `pnl_snapshot` or
`drawdown_snapshot` is emitted. The runtime heartbeat exposes structured
`performance_telemetry` diagnostics so absence is observable rather than
silently becoming zero. Diagnostic classes include:

- no fills yet;
- source-ingest URL unconfigured, unavailable, timed out, or malformed;
- missing, ambiguous, non-market, stale, future, non-positive, or invalid
  marks;
- invalid/corrupt persisted cash ledger;
- flat book with no recoverable last-fill observation time;
- unchanged/duplicate performance sample;
- telemetry emit failure.

Diagnostics must include the attempted time, status/code, requested and
missing symbols where applicable, configured freshness ceiling, and available
source/ledger context. They must not synthesize `0`, `null`, or an artificial
breach as a replacement snapshot.

## Restart continuity

Each binding owns a durable `paper_performance_ledger.v1` state file containing
initial cash, current cash, open holdings, fill count, last-fill time, and
execution prices. Fill accounting is persisted atomically after each fill and
restored before valuation. Restored execution prices are hints only and are
explicitly non-authoritative until refreshed from source-ingest.

A corrupt or incompatible state file blocks snapshots and produces a
diagnostic; the runtime must not silently reset the ledger and publish a new
series under the old binding identity. Fleet workers therefore require a
binding-specific state path on durable runtime storage.

This mechanism prevents future restart discontinuity. It does not claim to
reconstruct the exact historical cash ledger for bindings that accumulated
fills before this state format existed. The 7,325-fill legacy binding requires
an explicit replay/backfill-or-reset decision before its old trade count can be
treated as continuous with the new performance series.

## Implementation surfaces

The task-scoped implementation is expected to converge in these surfaces; the
final closeout must reconcile this list with the merged diff:

- `services/execution/lean_runtime/performance_telemetry.py`
- `services/execution/lean_runtime/paper_runtime.py`
- `services/execution/lean_runtime/executor.py`
- `services/execution/runtime-manager/paper_fleet_reconciler.py`
- `services/telemetry/telemetry_event.schema.json`
- `services/telemetry/runtime_summary.py`
- paper-runtime, runtime-summary, ingest-contract, and threshold-sweep tests
- runtime compose configuration for source-ingest access and durable
  per-binding ledger paths

## Validation checklist

All items below are pending post-change validation; no pending row is evidence
of acceptance.

- [ ] Unit tests prove long, short, partial-close, flat-book, and fill-derived
  cash/PnL arithmetic.
- [ ] Mark-provider tests accept normalized fresh market observations and
  reject non-market datasets, stale/future data, missing symbols, malformed
  values, and venue alias collisions.
- [ ] Missing or partial marks produce heartbeat diagnostics and zero
  performance events.
- [ ] The drawdown window spans 20 calendar days, uses fractional units, and
  suppresses duplicate and out-of-order samples.
- [ ] A process restart restores the ledger, does not treat restored execution
  prices as marks, and continues the same performance series after fresh
  marks arrive.
- [ ] Both event types independently validate against the canonical telemetry
  schema and pass through telemetry ingest.
- [ ] Runtime-summary projection preserves independent `pnl_at` and
  `drawdown_at` values and rejects per-field timestamp regression.
- [ ] Bracket-order logs do not increment executed-fill counters; Taiwan buy
  and sell fills update signed holdings and cash consistently.
- [ ] Threshold sweep evaluates real numeric/fresh projected PnL and drawdown
  instead of reporting missing/stale input.
- [ ] Relevant execution, telemetry, evolution, JSON-schema, and compose
  validation suites pass on the final branch.
- [ ] Hosted dev exposes moving numeric `pnl` and `drawdown` with per-field
  as-of stamps for active marked bindings.
- [ ] Hosted missing/stale-mark proof shows diagnostics and no snapshot.

## Live and hosted evidence status

| Evidence | Status | Notes |
|---|---|---|
| 2026-07-14 upstream live diagnosis | Recorded, pre-fix | Human/Ops observed 14/14 summaries at `pnl=0.0`, 14/14 at `drawdown=None`, including `rb-31bd3cf07cc` with 7,325 trades. This establishes the defect only; it is not post-fix acceptance. |
| Public dev BFF health | Reachable during investigation | Health reachability does not prove performance telemetry correctness. |
| Direct source-ingest/telemetry inspection | Pending | Internal endpoints were not reachable from the worker during the current evidence attempt. |
| Dev VM inspection/deployment identity | Blocked for current attempt | Local `gcloud` credentials require interactive re-authentication; no deployment or hosted commit identity is claimed here. |
| Post-change hosted performance proof | Pending | Requires a merged/deployed candidate plus fresh authoritative source-ingest marks. |

Until the last row passes, the hosted acceptance condition remains open even
if local tests pass.

## Residual ownership

| Residual | Owner / consuming task | Required disposition |
|---|---|---|
| Source-ingest currently has no proven fresh marks for the active portfolio symbols | Source-ingest/Data owner, coordinated by Human/Ops | Restore/verify fresh normalized market observations before hosted performance acceptance; do not relax the freshness gate or substitute synthetic data. |
| Deploy candidate and archive exact hosted FE/BFF/service commit evidence | `EVOLOOP-009` / Human/Ops | Deploy the merged task commit, verify the hosted manifest/service identity, then archive positive and fail-closed curl evidence. |
| Approve `expected_drawdown`, calibrate `rolling_pnl_floor`, and enable governed thresholds | `EVOLOOP-005` | Use observed real telemetry, record `policy_source`, and keep empty/missing baselines fail closed until approval. |
| Decide treatment of pre-ledger historical bindings such as the 7,325-fill runtime | Human/Ops with Execution/Telemetry owners | Choose an auditable replay/backfill or an explicit series reset; never infer historical cash from the retained trade count alone. |

EVOLOOP-002 is complete only after implementation, post-change validation,
review, merge, and hosted evidence establish both the moving-metric path and
the missing-mark fail-closed path.
