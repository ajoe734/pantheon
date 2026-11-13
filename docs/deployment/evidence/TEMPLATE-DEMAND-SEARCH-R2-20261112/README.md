# Demand-driven template search

The persona cycle already calls `HttpPoolCyclePorts.reconcile_demands`. The
research owner's authenticated reconcile route now evaluates at most one new
registered template per open demand per invocation. It reads that persona's
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
HTTP timeout is bounded at 180 seconds to accommodate a real backtest.

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

The integration test uses the real persona HTTP port, research reconcile route,
demand store, compiler, interpreter, vectorbt protocol, default gate and durable
backtest/counter stores. Only owner reads and the HTTP transport are replaced
with recorded responses. Synthetic data and fallback-artifact builders are
poisoned. The recorded KMID candidate is legitimately **rejected**, with its
actual OOS metrics, family trial and gate criteria recorded in
`recorded-gate-result.json`. A successful numerical strategy admission on these
bars is not claimed. Passing-candidate persistence and family exhaustion are
separately exercised with an explicitly named gate double; its receipts are
not represented as real numerical proof. No hosted deployment, pool admission,
profitable candidate, StrategySpec approval or capital write is claimed.

`evidence.json` records the completed validation commands and their terminal
results. Independent review is still required from Codex; only the canonical
supervisor integrator may merge the approved head.
