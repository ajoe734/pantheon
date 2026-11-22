# Trading pulse operator facts acceptance

Task: BFF-TRADING-PULSE-OPERATOR-FACTS-20261122 (RC6 WP6.1/6.2/6.3).
Owner Codex2; independent reviewer Antigravity2. Source delivery only; hosted
acceptance belongs to TRADING-PULSE-HOSTED-ACCEPTANCE-20261122 (task E).

Run from the provisioned checkout, without PYTHONPATH:

```sh
.venv-pantheon/bin/python3 acceptance/probes/trading-pulse-operator
```

Set `ACCEPTANCE_BFF_URL`, `ACCEPTANCE_VIEWER_CLIENT_ID`,
`ACCEPTANCE_VIEWER_CLIENT_SECRET` and `ACCEPTANCE_TENANT_ID` using the existing
read-only viewer credentials. Credentials and raw source bodies are never
printed or saved. Exit 0 means verified, 1 means a contradiction, 77 means
unverifiable (missing, unavailable or moving sources); 77 is never acceptance.
The only POST authenticates the viewer. All business reads use GET.

The probe independently reads `/api/v1/personas`, `/api/v1/runtime-bindings`,
`/api/v1/telemetry/{runtime}/summary` and binding-filtered trade journeys. It
compares both pulse endpoints, distinct people/runtime denominators, source
zeros versus unknowns, fill quantity units, PnL groups and refusal provenance.
Directory/binding reads bracket the run; changing source timestamps prevent a
false pass. No fixed persona IDs or historical amounts define acceptance.
Offline tests intentionally corrupt the aggregate while leaving raw evidence
unchanged. Historical events remain historical; this probe verifies the latest
binding outcome, and task E separately verifies the known historical refusal.

## Wire contract for B and D

Existing envelopes, `runtime_rows`, scalar metric names, cards and four ranking
blocks stay present. `summary.operator_scope` is `verified`, `partial` or
`unavailable`; unknown counts are null, verified counts and by-stage counts
separate personas from runtime instances. Every business output and diagnostic
ID comes from the admitted tenant directory and non-retired bindings, excluding
`is_probe_or_test_persona` identities. A directory failure exposes no identities.

Each row and ranking carries `persona_id`, `persona_name`, `strategy_id`,
`strategy_name`, `underlying_symbol`, `tenant_id`, `performance_context`,
`latest_outcome`, and `links`. Unknown source values stay null. Links use existing
`/management/personas/{id}`, `/management/strategies/{id}` and scoped
`/management/trade-journeys/{id}?tenant_id=...&environment=...` targets. No fake
journey target is produced when its evidence is unavailable.

`performance_context` retains `ledger_fill_count`, `pnl_currency`,
`pnl_period_start_at`, `pnl_at`, `pnl_definition`, raw `fill_rate_details`,
`metric_reasons`, `metric_units`, `metric_periods`, `latest_report_at`,
`last_fill_at`, `snapshot_at`, source staleness and `source_status`.
`fill_rate_basis` names the source algorithm (`legacy_source_scalar` identifies
older scalar-only sources). Positive denominators permit a fraction; 0/452 is
0, 0/0 is null with its source reason. A binding-matched ledger count of zero
remains zero without manufacturing PnL. Sharpe reasons and zero slippage survive.

`latest_outcome` includes `status`, `occurred_at`, `reason_code`,
`submitted_to_broker`, `requested_quantity`, `required`, `available`, `currency`,
`journey_id`, `source_event_id` and `focus_stage`. It uses the existing indexed
journey projection, filtering tenant, environment, runtime, binding, persona and
snapshot. It never scans the full journey/ledger collection for each runtime.

`summary.pnl_groups` groups only equal stage, currency, exact valuation interval
and source definition; known source quarter-equity observations can supply the
valuation definition. Groups expose value, as-of, interval, covered/eligible
persona counts and covered/eligible runtime counts. Missing comparability stays
on the individual row. Legacy `total_pnl` is null across incompatible groups.
`metric_coverage` exposes each metric's available, missing and eligible counts.
`average_fill_rate` is named `arithmetic_mean_of_observed_runtime_rates` through
`fill_rate_aggregation`; raw per-runtime algorithms remain available.
Ranking items include `ranking_unit`, `comparison_scope` and ranks within that
scope. `ranking_basis=within_comparison_scope` prevents interpreting a USD/live
rank as competition with TWD/paper. Unknown PnL intervals and stale sources do
not enter the PnL ranking. Missing observations remain in block diagnostics.

## Budget and cleanup

No recurring job, writer, queue, table, cache or deployment gate is added.
Cadence is manual per release, normally 0 runs/day. Hosted scale is currently a
few admitted runtimes; at most 200 are accepted per run. Source input is capped
at 5 MiB per response, requests time out after 30 seconds, and the redacted
stdout summary contains only counts and fixed diagnostic codes (under 8 KiB).
Normal daily persistent growth is 0 bytes. The probe writes no files and keeps
credentials/source records in process memory until exit. If task E captures
stdout, retain one summary per exact FE/BFF pair under its existing 5 MiB package
cap and delete superseded temporary captures. No new storage-manifest writer is
needed.
