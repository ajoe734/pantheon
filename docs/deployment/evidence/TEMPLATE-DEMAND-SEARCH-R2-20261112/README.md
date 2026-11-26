# Demand-driven template search

The persona cycle already calls `HttpPoolCyclePorts.reconcile_demands`. The
research owner's authenticated reconcile route records at most one active
Research run per open demand. The existing Research worker evaluates at most
one new registered template per demand run. It reads that persona's
explicit `metadata.market_scope.symbols` from the persona owner and templates
from the registry owner. Missing scope, credentials or governed data produces
a durable unavailable reason. Satisfied and expired demands do not search.

The existing governed retrain dataset loader supplies the daily market data.
This uses `RESEARCH_RETRAIN_CANONICAL_DATASET_PATH` /
`RESEARCH_RETRAIN_THRESHOLD_POLICY_PATH` (or their training-session fallbacks),
`RESEARCH_PERSONA_SERVICE_TOKEN[_FILE]`, `RESEARCH_REGISTRY_SERVICE_TOKEN[_FILE]`,
`PERSONA_API_URL` and `PANTHEON_REGISTRY_URL`. No source path, persona universe,
market observation or baseline is synthesized when these are unavailable.

The compiler preserves the registered time-series signal and lifts literal
constants into declared, fixed-range controls. The deterministic trading rule
is long for a positive signal and flat otherwise, using ten shares as in the
canonical replication artifact default. It does not optimize a threshold on
out-of-sample bars. Cross-sectional, retired, cross-tenant, checksum-mismatched
and missing-input templates are excluded. The current bar authority supports
observed price-volume fields; in particular it does not infer VWAP from OHLC.
The template evaluator and registry interpreter are unchanged.

`alpha_replication.admission.evaluate_template_candidate` delegates to the
existing `SharedReplicationGate.evaluate_candidate`; the gate computes the
real backtest and binds it to its authoritative shared backtest run store.
Thresholds, statutory costs, 70/30 split and walk-forward rules are unchanged.
The shared family counter key is `template:<origin.library>:<family>`, shared
across demands and frequencies. The lifetime limit is 500; an exhausted family
is added to `tried_families` on the demand. A compiled attempt that returns
before the numerical trial check still consumes one trial. Missing inputs
consume none.

Each terminal attempt is persisted before proceeding. Candidate identities
include tenant, demand, template content, source, universe, dataset and compiler
parameters. Repeating a reconcile with identical inputs reuses the receipt.
A file lock serializes competing template searches; the existing shared gate
stores protect other backtest and counter writers. An abrupt crash after a
trial increment but before its receipt can conservatively consume an extra
trial on retry; it cannot reset the lifetime budget. The persona reconcile
HTTP timeout remains 15 seconds. Reconcile records runs without waiting for
owner reads, compilation or backtesting. Runs share the existing Research
active-run limit and worker, startup resume, retry, cancellation and terminal
task/run handling. A concurrent cycle reuses the active run; a full Research
run budget defers the demand until the next normal persona cycle. No second
queue, thread framework, dispatcher service, scheduler or API is introduced.
When owner credentials have no configured environment value or file, demand
reconciliation records an unavailable reason without creating an unexecutable
Research task; collector query-plan proposals stay independent. Configured
credential files and actual owner responses are validated by the worker.
The internal `template_demand_gate` adapter is rejected by generic dispatch;
stub run parameters alone never grant template execution on startup or retry.
Cancellation fences prevent a late gate result from completing a canceled run.

This head contains the actual merged PR #7315 actor/tenant guards. Writes use
the verified `persona-pool-cycle` principal and tenant claim/header binding;
the existing listing read roles are preserved. The persona caller source has
no diff against the delivery base. The prior timeout exception is withdrawn.

Passing artifacts are durable paper research candidates in
`discovery.template_search.attempts`, with their demand/persona/tenant identity,
`mechanism_untested`, null support and the complete gate response. This does
not manufacture reviewed StrategySpecs, queue approvals, approved baselines,
registry approval, pool members or capital bindings. A demand stays open until
the existing member reconciliation satisfies it. External-text discovery
receipts and template receipts preserve one another.

## Recorded-data evidence and limits

`template_demand_tw_bars.json` contains 1,361 official TWSE OHLCV observations
for 2330 and the 0050 benchmark. Its provenance pins the previously recorded
`recorded_mechanism_tw.json` by SHA-256 and lists the recorded 0050 split
adjustment (pre-split prices divided by four, share volume multiplied by four).
The fixture-integrity test independently reconstructs every row from the
original STOCK_DAY fields and refuses differences. The production search does
not load either fixture.

The integration test uses the normal persona HTTP port (including its unchanged
15-second timeout), the existing dev-issued cycle JWT with strict verification,
research reconcile route, durable Research runs, existing worker, demand store,
compiler, interpreter, vectorbt protocol, default gate and backtest/counter
stores. There are no authentication dependency overrides. Owner responses and
HTTP transport are local recordings. The test holds the owner read until after
reconcile returns and repeats reconciliation while that worker is blocked,
proving one active run. It then releases and joins the actual worker, checks
terminal run/task status, and verifies receipt reuse on the next cycle.
Synthetic data and fallback-artifact builders are poisoned. The recorded KMID
candidate is legitimately **rejected**, with its
actual OOS metrics, family trial and gate criteria recorded in
`recorded-gate-result.json`; `recorded-research-runs.json` summarizes both completed
Research runs, their lifecycle events and reused candidate receipt. It references
the full 786-line output at immutable commit `c122aeaa100a362c5f340ddc14c53f6d162c4ba6`
by repository path, Git blob, byte count and SHA-256; `git show` in its
`full_output.retrieval` field reproduces the exact original bytes. Repeated gate
payloads are omitted from the summary. A successful numerical strategy admission
on these bars is not claimed. Passing-candidate persistence and family exhaustion are
separately exercised with an explicitly named gate double; its receipts are
not represented as real numerical proof. No hosted deployment, pool admission,
profitable candidate, StrategySpec approval or capital write is claimed.

`evidence.json` records the completed validation commands and their terminal
results. Independent review is still required from Codex; only the canonical
supervisor integrator may merge the approved head.
