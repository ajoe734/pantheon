# EVOCHAIN-001 — Threshold-breach producer (telemetry -> incidents)

Status: implemented, addressed Codex round-1/round-2/round-3 reviews,
pending re-review. The deployed lineage-resolution blocker (round-2 point 1,
reaffirmed round-3 point 1) is a confirmed platform gap this task cannot
close by itself; it is now materialized as its own dependency —
`docs/decisions/LIN-003-live-lineage-write-path.md` — and this task remains
blocked on it for that one acceptance criterion. See "Round-3 review fixes"
below for what changed and why point 1 stays open.

Owner: Claude
Reviewer: Codex
Wave: 0
Depends on: none

Source gap spec: `docs/04/pantheon_evolution_journal_producer_gap_2026-07-13/EVOLUTION_JOURNAL_PRODUCER_GAP.md`
Execution packet: `docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/INDEX.md`

## Problem

`services/incidents/consumer.py` (`ThresholdTelemetryIncidentConsumer`) is a
complete adapter with zero callers. Nothing evaluates live paper telemetry
against governance thresholds and posts breach payloads, so the incident ->
postmortem -> evolution -> journal chain never fires from real data.

## What shipped

- `services/evolution/threshold_sweep_worker.py` — the producer. Reads
  per-binding/per-persona paper performance summaries from the telemetry
  read path (`GET {telemetry}/api/telemetry/runtime-summaries`, the same
  summaries the performance console reads — see
  `services/control-plane/bff/read_store.py` `_HTTP_DATASETS["telemetry_summaries"]`),
  evaluates them against live-config thresholds shaped like the governance
  `ThresholdSnapshot` schema (`services/control-plane/governance/evolution_decision.py`),
  admits a schema-valid derived telemetry event through the real telemetry
  ingest route (`POST {telemetry}/api/telemetry/ingest`), and only then POSTs
  the breach to `POST {incidents}/api/incidents/consume-threshold`
  (`services/incidents/consumer.py::ThresholdTelemetryIncidentConsumer`).
  Talks to both services over HTTP only — no cross-service Python imports,
  per the Incident service's own write-authority rule.
- `services/evolution/config/threshold_sweep_thresholds.json` — live config:
  the threshold list (`metric_name`, `signal_type`, `policy_source`,
  `summary_field`, `ratio_baseline_key`, `telemetry_event_type`,
  `comparator`, `threshold_value`, `window`, `enabled`). Ships with two
  entries derived from `EVOLUTION_REVIEW_AND_THRESHOLDS.md` section 7.1:
  `rolling_drawdown_multiple` (`enabled: true`) and `rolling_pnl_floor`
  (`enabled: false` — see "PnL floor is off by default" below).
- `services/evolution/config/threshold_sweep_baselines.json` — live config:
  per-`artifact_id` research-approved baseline values (e.g.
  `expected_drawdown`), used to turn a raw runtime-summary metric into a
  unit-consistent multiple before comparison. Ships empty.
- `docker-compose.yml` — new `evolution-threshold-sweep-producer` service.
  Not gated behind a profile (default-on, like `reconciliation-drift-svc`).
  Bind-mounts `services/evolution/config` read-only so operators can retune
  threshold/baseline values by editing the host files and restarting the one
  service — no image rebuild. Own interval env
  (`EVOCHAIN_THRESHOLD_SWEEP_INTERVAL_SECONDS`, default `86400`); does not
  touch `EVOLUTION_SCHEDULER_INTERVAL_SECONDS` or any other existing cadence.
- `services/evolution/test_threshold_sweep_worker.py` — 43 tests.

## Round-1 review fixes (Codex, PR #3509, 2026-07-13)

Codex requested changes on 4 points; each is addressed below.

### 1. Generated threshold event was not canonical/ingested (422 on the real route)

The original synthetic `tel-threshold-sweep-*` envelope did not satisfy
`services/telemetry/telemetry_event.schema.json`: `event_type` wasn't in the
schema's enum, `event_id` wasn't a real UUID, `runtime_binding_id`/
`deployment_plan_id` aren't schema property names (schema wants `binding_id`/
`plan_id`), and required `execution_mode`/`target` fields were missing, plus
an undeclared `description` field violated `additionalProperties: false`.

Fixed:

- The derived `telemetry_event` envelope built in `evaluate_breaches()` is
  now schema-valid: real `uuid5`-derived UUID `event_id`/`trace_id`, a
  `telemetry_event_type` sourced from live config (validated at load time
  against the schema's `event_type` enum — see `_TELEMETRY_EVENT_TYPES`),
  schema-correct field names, and a `target.strategy_id`.
- Before the incident is POSTed, `run_tick()` now admits this derived event
  through the real telemetry ingest route
  (`default_admit_telemetry_event` -> `POST {telemetry}/api/telemetry/ingest`).
  If telemetry rejects it, the candidate incident is skipped entirely
  (fail-closed) instead of being posted with unadmitted evidence.
- `test_threshold_sweep_worker.py::test_derived_telemetry_event_is_schema_valid_and_ingest_admissible`
  proves the derived event passes both `TelemetryIngestService._validate_event`
  (schema) and `_validate_evidence_contract` (TEL-001A evidence checks) using
  the real schema file and the real ingest service class — not a mock.
  `test_original_synthetic_envelope_shape_would_have_failed_ingest` is a
  regression guard proving the pre-fix shape would have failed the same
  checks.
- `test_consume_threshold_route_passes_real_canonical_reference_validator`
  hits the real `/api/incidents/consume-threshold` FastAPI route with the
  real (unmocked) `CanonicalReferenceValidator` — not the accept-all fake the
  rest of `services/incidents/test_main_routes.py` uses — using injected fake
  `binding_lookup`/`telemetry_lookup` doubles shaped exactly like what the
  canonical `RuntimeBinding` store and telemetry lineage projection would
  return for this binding/event (same pattern as
  `services/incident/test_reference_validation.py`). This proves the
  worker's payload is reference-shape-consistent with the real validator's
  matching rules (identity fields, artifact ref, trace ids all line up) —
  which is the part this task owns.

  **Known platform gap (out of scope for this task):** the default
  `CanonicalReferenceValidator()`'s telemetry lookup resolves through
  `LineageReadService`, which is loaded once at startup from the static
  LIN-001A benchmark corpus (`services/registry/lineage/
  lin001a_benchmark_corpus.json`) — it is not a live index of ingested
  telemetry events. No producer in this codebase (this one included) can
  currently get a freshly-ingested `telemetry_event_id` to resolve through
  the *default* unmocked validator, because nothing writes newly-ingested
  events into that lineage graph. Every existing route test that exercises
  `consume-threshold`/`consume-drift-report` in
  `services/incidents/test_main_routes.py` works around this the same way:
  the `clean_store` autouse fixture monkeypatches `reference_validator` to
  an accept-all fake. Closing this for real (wiring telemetry ingest to a
  live lineage index, or standing up a dynamic lineage service) is a
  separate, cross-cutting platform task — recommended as a LIN-003-style
  follow-up — not something a single producer task should special-case.

### 2. Drawdown units did not match the governed threshold

The runtime summary's `drawdown`/`drawdown_pct` field is a raw metric
(telemetry projects it straight from `metrics.drawdown_pct`, see
`services/telemetry/runtime_summary.py`), not a "current vs. research-expected
baseline" ratio. Comparing it directly to `1.25` conflated a raw value with a
multiple. `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §7.1 requires drawdown to
exceed the *research-expected interval* by 1.25x — that needs a real
per-artifact baseline, which does not exist anywhere else in this codebase
today (checked: no `expected_drawdown`/`max_drawdown`/baseline registry
exists in `services/registry*`, `services/control-plane/governance`, or
`services/research/*`).

Fixed:

- Added `services/evolution/config/threshold_sweep_baselines.json`: a live
  config mapping `artifact_id -> {ratio_baseline_key: value}` for
  research-approved baselines. Ships **empty** — no artifact has an approved
  baseline yet.
- `threshold_sweep_thresholds.json`'s `rolling_drawdown_multiple` entry
  declares `"ratio_baseline_key": "expected_drawdown"`. In
  `evaluate_breaches()`, the raw `summary_field` value is divided by the
  matching artifact's baseline to produce a unit-consistent multiple, which
  is what gets compared to `threshold_value` (and what is recorded in
  `threshold_snapshot.observed_value`; `raw_observed_value` keeps the
  untouched raw metric for evidence/audit).
- Fail-closed: an artifact with no baseline entry (i.e. every artifact,
  today) is skipped with a diagnostic instead of a fabricated comparison —
  the drawdown-multiple threshold will not fire until an operator/researcher
  populates a real baseline for that artifact_id.
- Tests now seed the raw metric through the **real**
  `RuntimeSummaryProjectionStore` (`test_evaluate_breaches_detects_drawdown_breach_from_real_projection`)
  instead of hand-baking `drawdown=1.42` as if the multiple already existed,
  and `test_evaluate_breaches_missing_baseline_is_diagnostic_only_fail_closed`
  covers the no-baseline-registered fail-closed path.

### 3. Scope and freshness were not fail-closed

`evaluate_breaches()` previously accepted summaries from every deployment
stage and ignored staleness/degraded state.

Fixed: `evaluate_breaches()` now, per summary:

- skips (diagnostic-only) any summary whose `deployment_stage != "paper"` —
  this task's declared scope is the paper performance sweep; canary/live/
  frozen each have their own deployment-stage-specific governance path.
- skips (diagnostic-only) any summary that is stale/degraded/ambiguous:
  `summary.get("staleness")` set, `state == "degraded"`, or
  `connectivity_status in {"degraded", "disconnected"}` (all produced by
  `RuntimeSummaryProjectionStore._apply_staleness`).
- Covered by `test_evaluate_breaches_skips_non_paper_stage` and
  `test_evaluate_breaches_skips_stale_summary` (the latter drives a real
  `RuntimeSummaryProjectionStore` heartbeat + `now=` far in the future to
  produce a genuinely stale projection, not a hand-set flag).

### 4. Unapproved `-500` PnL placeholder was active in a default-on service

Fixed: `threshold_sweep_thresholds.json`'s `rolling_pnl_floor` entry now
ships with `"enabled": false`. `load_thresholds()` drops any entry with
`enabled: false` (or missing) before the worker ever sees it — the compose
service stays default-on (only the fail-closed, baseline-gated drawdown
threshold is active), but the unapproved absolute PnL number cannot fire
until an operator flips `enabled: true` with a governance-approved value.
Covered by `test_load_thresholds_drops_disabled_entries` and
`test_default_config_file_loads_only_enabled_thresholds`.

## Round-2 review fixes (Codex, PR #3509, 2026-07-13)

Codex requested changes on 5 points at `ef1e5b3bd`; each is addressed below.

### 1. Deployed telemetry -> incidents route still 422s (confirmed platform gap, out of scope)

Investigated further per the review's instruction to either close this or
formally keep the task blocked on it. Traced the exact mechanism:
`services/incidents/main.py`'s module-level `reference_validator =
CanonicalReferenceValidator()` resolves telemetry lineage through
`_TelemetryLineageLookup`, which in local-corpus mode (no
`PANTHEON_TELEMETRY_URL`) queries `LineageReadService`
(`services/telemetry/lineage_read/service.py`). That service is built once
at telemetry process `startup()`
(`services/telemetry/main.py::_build_lineage_service()`) by loading the
static LIN-001A benchmark corpus JSON file into an in-memory graph via
`CorpusLoader.load()`. Nothing in `TelemetryIngestService.ingest()` (or
anywhere else in this codebase) adds a node/edge to that graph for a
freshly-ingested event — the ingest path and the lineage graph are two
disconnected subsystems. This is confirmed structural, not a timing/race
issue: no amount of polling or waiting after `POST /api/telemetry/ingest`
would ever make the derived event resolve, because there is no write path
into the graph at all today.

Closing this for real means wiring `TelemetryIngestService.ingest()` (or an
equivalent live index) to register accepted events/bindings into
`LineageReadService`'s graph — a change to
`services/telemetry/lineage_read/` and `services/incident/
reference_validation.py`, both shared by every current and future incident
producer (not just this one), reviewed and shipped as their own
BP5-SVC-010/LIN-002 deliverables. Per the round-1 review author's own
framing, this is "a separate, cross-cutting platform task... not something
a single producer task should special-case" — attempting it inside
EVOCHAIN-001 would mean rewriting a shared, independently-reviewed
subsystem outside this task's declared scope (`services/evolution`,
`services/incidents`, `docker-compose.yml`) with no producer-side way to
verify the fix is architecturally correct for every future producer.

What changed instead: added
`test_consume_threshold_route_422s_against_default_deployed_reference_validator`,
which hits the real route with the real DEFAULT (no injected fakes)
`reference_validator` — the "default deployed lookup path" the review
asked for — and pins today's actual behavior (`422`,
`reference_errors` in the response). This converts "documented but
untested" into "tested," so a future fix to the platform gap is a visible,
deliberate change to this test, not a silent regression. `run_tick`'s own
handling of a non-201/200 `post_incident` response (never fabricates an
incident, always fail-closed with a diagnostic — see "Fail-closed
behavior" above) already covers the runtime consequence of this gap
correctly: the worker will not create real incidents against the default
validator today, and it will not crash or misreport success either.

**This point remains open pending a separate platform task** (recommend a
LIN-003-style follow-up: wire live-ingested telemetry into a queryable
lineage index, or otherwise replace the static-corpus default) before any
threshold/drift producer's incidents can be expected to pass the *default*
`CanonicalReferenceValidator()` in production.

### 2. Freshness was fail-open for ambiguous and old metric data

Fixed two distinct gaps:

- **Missing heartbeat read as healthy.** `RuntimeSummaryProjectionStore`
  only sets `staleness`/`state`/`connectivity_status` once a heartbeat event
  has been projected; a summary that never received one carries none of
  those markers, so the old `_is_stale_or_degraded()` (which only rejected
  explicit bad markers) accepted it. Fixed: `_is_stale_or_degraded()` now
  requires the affirmative presence of `last_heartbeat_at` first. Covered by
  `test_evaluate_breaches_missing_heartbeat_is_diagnostic_only_fail_closed`.
- **A fresh heartbeat masked an old metric.** The read model had no
  per-metric as-of time, so a drawdown value set by a long-past
  `drawdown_snapshot` event looked exactly as "fresh" as a value set a
  moment ago, as long as some other event (e.g. a heartbeat) had advanced
  the summary's overall `last_event_at`. Fixed at the source:
  `services/telemetry/runtime_summary.py::project_event()` now stamps each
  projected metric field with its own `f"{field}_at"` as-of time (the
  event's own `created_at`, additive — no existing field changes meaning).
  `evaluate_breaches()` gained a `now`/`metric_max_age_seconds` parameter
  (default 2 days, `EVOCHAIN_THRESHOLD_SWEEP_METRIC_MAX_AGE_SECONDS` env
  override) and skips a metric whose as-of time is missing, unparseable, in
  the future, or older than that window — regardless of how fresh the
  summary's heartbeat is. `run_tick()` threads its own tick `now` through.
  Covered by
  `test_evaluate_breaches_old_metric_with_fresh_heartbeat_is_diagnostic_only_fail_closed`
  (12-day-old drawdown + a heartbeat seconds old -> diagnostic, no breach).

### 3. The route test wrote the default persistent incident store

`test_consume_threshold_route_passes_real_canonical_reference_validator`
imported `services.incidents.main.store` (bound to the real
`/tmp/pantheon/incidents/incidents.json`) and only cleared its in-memory
dicts after the test, which does not undo the file write the route's
`store.create_incident()` already performed. Fixed: the test now injects a
fresh `IncidentStore(path=None)` (in-memory only; `_save()` no-ops when
`path` is `None`) via `monkeypatch.setattr("services.incidents.main.store",
...)` for the duration of the test, so it never touches disk. The new
round-2 default-validator test uses the same pattern. Verified manually
(`docker compose config --quiet` run alongside the full suite) that
`/tmp/pantheon/incidents/incidents.json` does not exist after running these
tests.

### 4. `dedupe_key` was not recorded in the `IncidentCase`'s canonical evidence

`services/incidents/consumer.py::_threshold_notes()` built `evidence_summary`
from a fixed set of `threshold_snapshot` fields and silently dropped `note`
(where the worker records `dedupe_key=...`). Fixed: `_threshold_notes()` now
appends `threshold.get("note")` verbatim when present, so the audit trail
that explains why a rerun deduped instead of opening a second incident
survives into `IncidentCase.evidence_summary`. Covered by
`test_threshold_consumer_preserves_dedupe_key_note_in_evidence_summary`
(`services/incidents/test_main_routes.py`) and a `dedupe_key=` assertion
added to
`test_payload_accepted_by_real_consumer_and_idempotent_on_rerun`.

### 5. Malformed live config could raise `TypeError` and restart-loop the worker

`load_thresholds()` used `entry.get("enabled", False)` (truthy check, so the
JSON string `"false"` — truthy in Python — would activate an entry) and
performed `in` membership checks against `_COMPARATORS`/
`_TELEMETRY_EVENT_TYPES` and later `dict.get(ratio_baseline_key)` without
first confirming those values were hashable strings; an unhashable JSON
value (a list/object) would raise `TypeError` instead of being dropped,
propagating out of `run_tick()` uncaught. Fixed: `load_thresholds()` now
validates `metric_name`/`signal_type`/`policy_source`/`summary_field` are
strings, `comparator` is a string enum member (checked before the `in`
lookup, so a non-string never reaches it), `telemetry_event_type` likewise,
`ratio_baseline_key`/`window` are `None` or `str`, `threshold_value` is a
finite (`math.isfinite`) non-bool `int`/`float`, and `enabled` is the
literal `True` (not merely truthy) before an entry is accepted. Covered by
`test_load_thresholds_drops_truthy_non_bool_enabled`,
`test_load_thresholds_drops_unhashable_comparator_without_raising`,
`test_load_thresholds_drops_unhashable_telemetry_event_type_without_raising`,
`test_load_thresholds_drops_unhashable_ratio_baseline_key_without_raising`,
and `test_load_thresholds_drops_non_finite_threshold_value`.

## Round-3 review fixes (Codex, PR #3509, 2026-07-13)

Codex requested changes on 4 points at `52edbe771`; each is addressed below.

### 1. Default compose path still cannot create an IncidentCase (confirmed platform gap, materialized as LIN-003)

Investigated further per the review's explicit instruction: either close
this or materialize the lineage-repair dependency and keep the task
blocked. Went one level deeper than the round-2 diagnosis: even wiring
`TelemetryIngestService.ingest()` to register a freshly-ingested event node
would not be enough, because the static LIN-001A corpus's 4
`runtime_bindings` are fixed demo IDs (none `paper` stage, none matching any
real dev-deployed persona) — the *entire* upstream deployment-lineage chain
(runtime_binding -> deployment_plan -> capital_pool -> persona_binding ->
candidate_artifact -> ...) for a genuinely running binding is absent from
the graph today, not just the terminal telemetry-event node. Closing this
means live-wiring writes from `services/telemetry` **and**
`services/runtime-manager` **and** `services/control-plane/governance` — a
multi-service change shared by every current and future telemetry-derived
incident producer, exactly the kind of cross-cutting lineage migration work
`docs/decisions/LIN-002-lineage-ownership.md`'s "Phase 0: transitional
coexistence" already anticipated as a separate initiative.

Materialized this as `docs/decisions/LIN-003-live-lineage-write-path.md`
with the exact exit evidence the review asked for (`telemetry ingest 202 ->
incident 201 -> same replay 200` against the *default* unmocked validator
and a live-deployed binding, no fake lookups). EVOCHAIN-001 remains blocked
on LIN-003 for this one acceptance criterion; the pinning test
(`test_consume_threshold_route_422s_against_default_deployed_reference_validator`)
is unchanged and still accurately describes today's behavior.

### 2. The producer laundered stale source metrics into fresh ones

`evaluate_breaches()` cites the observed metric in its derived
`drawdown_snapshot`/`pnl_snapshot` telemetry event so the event is
schema/evidence-valid before being admitted through ingest. But
`RuntimeSummaryProjectionStore.project_event()` re-projected *any* admitted
event's `metrics` back into the summary, including this derived echo —
re-stamping the metric's own `f"{field}_at"` as-of time to "now" every time
the worker cited it. That defeated the round-2 staleness fix: a genuinely
abandoned metric could never age past `metric_max_age_seconds`, because the
worker's own breach evidence kept refreshing it, reproducing a candidate
every day forever under fresh heartbeats alone.

Fixed by marking the derived event with
`metadata.derived_from_threshold_evaluation: true` (schema-legal;
`metadata` is `additionalProperties: true`) and having `project_event()`
skip the metric-projection step entirely when that marker is present, so a
threshold-derived echo can never refresh the freshness signal it was itself
evaluated against. Covered by
`test_derived_threshold_evidence_does_not_refresh_stale_metric_across_days`
(`services/evolution/test_threshold_sweep_worker.py`): seeds one genuine
drawdown observation on day 0 plus a fresh heartbeat every day for a week
with no new real drawdown value, and asserts candidates stop from day 3
onward (`metric_max_age_seconds` default 2 days) instead of firing every
day.

### 3. Retry could make incident evidence disagree with the admitted telemetry event

The dedupe-key-derived `event_id` is stable for a binding/metric/day, but
`created_at` and the observed metric were rebuilt from the live summary on
every call to `evaluate_breaches()`. If telemetry ingest succeeded (202,
durably keeping that first payload per its own dedup-by-event_id contract)
but the incident POST then failed, a later retry recomputed a *new* payload
for the *same* `event_id` and posted that to incidents — which could
diverge from what telemetry actually stored, since incidents only checks
"does an incident already exist for this ID," not "does this payload match
what telemetry has."

Fixed with a small durable per-event-id evidence record
(`EVOCHAIN_THRESHOLD_SWEEP_STATE_PATH`, default
`/tmp/pantheon/evolution/threshold_sweep_state.json`, same durability class
as the incident store's own `/tmp` state): the first time an `event_id` is
admitted through telemetry ingest (202), `run_tick()` freezes that exact
`telemetry_event`/`threshold_snapshot` payload; any later tick for the same
`event_id` (same dedupe window) reuses the frozen payload verbatim instead
of recomputing from a possibly-drifted live summary, so the content posted
to incidents can never diverge from what telemetry already admitted.
Entries for a prior dedupe window are pruned on load. Covered by
`test_run_tick_retry_reuses_frozen_evidence_when_incident_post_previously_failed`.

### 4. Fail-closed loading/orchestration still had uncaught inputs

Two gaps:

- A JSON integer with no fixed size (e.g. `10**1000`) is valid JSON and a
  valid Python int, but `math.isfinite()` raises `OverflowError` converting
  it to a float rather than returning `False`; `load_thresholds()` now
  catches that and drops the entry fail-closed, same as any other malformed
  value. Covered by
  `test_load_thresholds_drops_huge_integer_threshold_value_without_raising`.
- A 2xx response with a malformed JSON body raised `json.JSONDecodeError`
  (a `ValueError` subclass) out of `default_admit_telemetry_event`/
  `default_post_incident`, uncaught by `run_tick()`'s `except` clauses,
  contradicting its documented "never raises" contract. Both call sites now
  also catch `ValueError`. Covered by
  `test_run_tick_fails_closed_when_telemetry_ingest_returns_malformed_json`
  and `test_run_tick_fails_closed_when_post_incident_returns_malformed_json`.

Also fixed in passing: `test_run_tick_creates_then_dedupes_on_rerun_via_real_consumer`
now injects an isolated `state_path` (`tmp_path`) instead of relying on
`DEFAULT_STATE_PATH`, so it cannot leak a write into the developer/runtime
-shared state file (same class of hazard the round-2 review flagged for the
incident store).

## Idempotency

Dedupe key: `(binding_id, metric_name, threshold window, UTC day bucket)`.
The worker hashes this key into a deterministic telemetry `event_id` (a real
`uuid5`, RFC4122-format string). The incidents consumer already derives
`incident_id` deterministically from `event_id` + `metric_name`
(`services/incidents/consumer.py::_incident_id`), so a rerun within the same
day for the same binding/metric resolves to the same `incident_id` and the
consumer's existing-incident check returns `created=False` instead of
duplicating. The dedupe key is also written into the incident's
`threshold_snapshot.note` (`dedupe_key=...`) for operator traceability.
Verified in `test_payload_accepted_by_real_consumer_and_idempotent_on_rerun`
and `test_run_tick_creates_then_dedupes_on_rerun_via_real_consumer`.

## Fail-closed behavior

Nothing is ever fabricated as a breach:

- live config missing/unreadable/malformed -> `load_thresholds`/
  `load_baselines` return `[]`/`{}`, `run_tick` logs a diagnostic and skips
  the tick (or the affected threshold).
- a threshold entry with an unknown comparator, an unknown/non-enum
  `telemetry_event_type`, or `enabled: false` is dropped at load time.
- telemetry unreachable -> `run_tick` logs a diagnostic and skips the tick
  (never calls `post_incident`).
- a runtime summary missing any required identity field, on a non-paper
  stage, or stale/degraded is skipped with a diagnostic.
- a threshold's `summary_field` missing or non-numeric on a summary is
  skipped with a diagnostic.
- a threshold that needs a per-artifact baseline and has none registered is
  skipped with a diagnostic.
- telemetry ingest rejects the derived event -> the candidate incident is
  skipped entirely; the worker never cites unadmitted evidence.

Verified in `test_load_thresholds_missing_file_fails_closed`,
`test_load_thresholds_malformed_json_fails_closed`,
`test_load_thresholds_drops_unknown_comparator`,
`test_load_thresholds_drops_unknown_telemetry_event_type`,
`test_load_thresholds_drops_disabled_entries`,
`test_load_baselines_missing_file_fails_closed`,
`test_load_baselines_malformed_json_fails_closed`,
`test_evaluate_breaches_missing_identity_field_is_diagnostic_only`,
`test_evaluate_breaches_missing_metric_field_is_diagnostic_only`,
`test_evaluate_breaches_non_numeric_metric_is_diagnostic_only`,
`test_evaluate_breaches_missing_baseline_is_diagnostic_only_fail_closed`,
`test_evaluate_breaches_skips_non_paper_stage`,
`test_evaluate_breaches_skips_stale_summary`,
`test_run_tick_fails_closed_when_no_thresholds_configured`,
`test_run_tick_fails_closed_when_telemetry_fetch_errors`,
`test_run_tick_fails_closed_when_telemetry_ingest_rejects_derived_event`.

## Local validation

```sh
python3 -m pytest services/evolution/test_threshold_sweep_worker.py -q
# 43 passed (round-3: +5 new tests for the fixes below)

python3 -m pytest services/evolution -q
# 167 passed (no regression in the rest of the evolution service)

python3 -m pytest services/incidents -q
# 50 passed (no regression)

python3 -m pytest services/incident -q
# 118 passed (no regression in the INC-001 domain layer)

python3 -m pytest services/telemetry -q
# 223 passed (round-3: derived-echo metadata-marker check in
# runtime_summary.py is additive; no existing assertion touches it)

docker compose config --quiet
# passed

docker compose config --services | grep evolution-threshold-sweep-producer
# evolution-threshold-sweep-producer

ls /tmp/pantheon/incidents/incidents.json
# No such file or directory — confirms the round-2 test-isolation fix (§3)
# leaves the shared persistent incident store untouched.

ls /tmp/pantheon/evolution/threshold_sweep_state.json
# No such file or directory — the round-3 retry-evidence state file (§3) is
# only ever written by tests through an isolated tmp_path, never the default.
```

## Acceptance mapping

| Acceptance criterion | Where |
|---|---|
| producer evaluates live paper telemetry aggregates against governance-schema thresholds from live config | `threshold_sweep_worker.load_thresholds` + `evaluate_breaches`, config in `services/evolution/config/threshold_sweep_thresholds.json` + `threshold_sweep_baselines.json` |
| breach POSTs canonical payload accepted by `ThresholdTelemetryIncidentConsumer` and creates an `IncidentCase` | proven end-to-end against the real consumer/store and the real route, and reference-shape-consistent with the real `CanonicalReferenceValidator`'s matching rules given canonical lineage/binding data. **Not yet proven against the *default* deployed `CanonicalReferenceValidator()`** — that always 422s today due to the confirmed platform gap materialized as `docs/decisions/LIN-003-live-lineage-write-path.md` (round-2 point 1, reaffirmed round-3 point 1 / "Residual risk" below), which this task cannot close by itself |
| re-runs do not duplicate open incidents for the same binding/metric/window (dedupe key recorded) | deterministic `event_id`/`incident_id`; `dedupe_key` in `threshold_snapshot.note`, preserved into the created `IncidentCase.evidence_summary` (round-2 fix §4) |
| missing or ambiguous telemetry emits diagnostics and produces no incident | fail-closed paths above, including stage/staleness/baseline gates |
| compose service ships with `EVOCHAIN_THRESHOLD_SWEEP_INTERVAL_SECONDS` default 86400 and its own logs | `docker-compose.yml` `evolution-threshold-sweep-producer`; `main()` prints one JSON line per tick to stdout |

## Residual risk

- No artifact has an approved `expected_drawdown` baseline yet
  (`threshold_sweep_baselines.json` ships empty), so the drawdown-multiple
  threshold — while now unit-correct and fail-closed — will not fire on any
  real artifact until Research/Ops populates one. Owner: Research/Ops.
- The `rolling_pnl_floor` threshold is `enabled: false` pending a
  governance-approved absolute PnL number (the v1 threshold spec only
  documents the drawdown multiplier). Owner: Human/Ops. To activate: set
  `enabled: true` and an approved `threshold_value` in the bind-mounted
  config, no code change needed.
- **Platform gap, not owned by this task (confirmed structural across two
  independent investigations, round-2 and round-3):**
  `CanonicalReferenceValidator`'s telemetry lineage lookup resolves against
  a static LIN-001A benchmark corpus loaded once at telemetry `startup()`,
  not a live index of ingested events or real deployment lineage, and
  nothing in this codebase writes either a freshly-ingested event or a
  real, live-deployed binding's upstream deployment chain into that graph —
  no amount of polling/waiting after ingest resolves it. So no producer's
  freshly-cited `telemetry_event_id` resolves through the *default*
  validator today; `POST /api/incidents/consume-threshold` 422s for every
  real breach against the default deployed config (pinned by
  `test_consume_threshold_route_422s_against_default_deployed_reference_validator`).
  See "Round-2 review fixes" §1 and "Round-3 review fixes" §1 above.
  Materialized as `docs/decisions/LIN-003-live-lineage-write-path.md`;
  **EVOCHAIN-001 is blocked on LIN-003 landing** for this one acceptance
  criterion before relying on `CanonicalReferenceValidator()`'s default
  construction in production for any threshold/drift producer. EVOCHAIN-010
  (producer-chain live verifier) depends on both.
- This task does not enable the daily sweep scheduler
  (`evolution-daily-sweep-scheduler` is still profile-gated) or deploy to
  dev; that is EVOCHAIN-002 and EVOCHAIN-011 respectively.
