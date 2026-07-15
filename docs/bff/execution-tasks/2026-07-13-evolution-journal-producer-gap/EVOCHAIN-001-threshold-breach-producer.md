# EVOCHAIN-001 — Threshold-breach producer (telemetry -> incidents)

Status: reviewed and approved (Codex, round-9 re-review) on PR #3620
(`task/EVOCHAIN-001` -> `dev`); closeout in progress. Review points from
rounds 1-9 have all been resolved (see below, kept as historical record of
each round's finding and fix — a point marked "fixed" in an earlier round's
section was true at that round's review time; round-9 found further gaps in
some of the same areas, addressed in its own section below). Round-9 fixed 6
further points found during re-review of that same PR, and the round-9 fixes
themselves were independently re-verified as correct at closeout time. LIN-003
has successfully landed, adding the live telemetry lineage write path and
resolving the default-validator platform blocker (the default
CanonicalReferenceValidator now returns 201 for a live-ingested breach event).
All tests pass locally (135 across the worker, incidents, and incidents
replay suites, re-run after merging `origin/dev` to resolve a second BEHIND
state) and compose volume mounting persists the sweep state.

Owner: Claude
Reviewer: Codex
Wave: 0
Depends on: none

Source gap spec: `docs/04/pantheon_evolution_journal_producer_gap_2026-07-13/EVOLUTION_JOURNAL_PRODUCER_GAP.md`
Execution packet: `docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/INDEX.md`
PR: `ajoe734/pantheon#3620`, branch `task/EVOCHAIN-001` -> `dev`. Scope: this
task touches `services/evolution`, `services/incidents`, and
`docker-compose.yml` (plus this doc); as of round-9 it also adds one
optional, additive field (`threshold_identity`, default `None`) to the
shared `IncidentCase` domain object and `PostmortemEvidenceCollector` in
`services/incident/incident.py` and `services/incident/evidence_collector.py`
— needed to harden the `incident_id` collision guard (round-9 §4) without
regex-parsing free-text evidence. It does not touch any other service.

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
- `services/evolution/test_threshold_sweep_worker.py` — 74 tests (round-9;
  see "Local validation" below for the current count history).

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

**Historical: this section documents the round-2 (2026-07-13) investigation
and the gap's status as of that review round.** The "remains open" framing
below was accurate at round-2 time; it was superseded once LIN-003 landed
(see round-3 §1 and the Status header above) — the default deployed
`CanonicalReferenceValidator` now returns 201 for a live-ingested breach
event.

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

**(Historical, as of round-2) This point remains open pending a separate
platform task** (recommend a LIN-003-style follow-up: wire live-ingested
telemetry into a queryable lineage index, or otherwise replace the
static-corpus default) before any threshold/drift producer's incidents can
be expected to pass the *default* `CanonicalReferenceValidator()` in
production. **Resolved as of round-3/LIN-003 landing** — see round-3 §1
above and the Status header.

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
and a live-deployed binding, no fake lookups). LIN-003 has now landed, and its
own full-stack test (`TestLiveLineageWritePathFullStackHTTPRoute`) verifies the
live end-to-end wiring. The EVOCHAIN-001 unit test
`test_consume_threshold_route_succeeds_against_default_reference_validator`
verifies validator structure/schema compatibility under mock-patched lookup
helpers, rather than replicating the full-stack lineage integration test.

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

## Round-6 review fixes (Antigravity, 2026-07-14)

Review comments on 3 main points:
1. **Duplicate Telemetry Event ID Mismatch Rejection**:
   - `TelemetryIngestService` now keeps a `dict` for `_seen_event_ids` mapping `event_id` to the originally accepted event payload.
   - Upon duplicate event retry, we run full schema and evidence validation on the incoming event.
   - We reject the retry (fail-closed, return `False`) if any key content fields (except transient metadata like `created_at`) mismatch the originally accepted event payload.
   - Lineage repair uses the immutable originally accepted event payload instead of trusting the incoming replay body.
   - Verified by `test_telemetry_duplicate_retry_rejects_content_mismatch_and_preserves_canonical`.
2. **Robust WAL Loading**:
   - `_load_pending_evidence()` now raises `ValueError` if the state file exists but is unreadable/malformed/non-UTF-8.
   - `run_tick` catches this error, records an explicit fail-closed diagnostic, and exits early to ensure we never recompute a different payload under the same deterministic `event_id`.
   - Structurally invalid records within the state JSON are safely ignored per-record and do not raise.
   - Pending undelivered records in the WAL are retried independently of whether new thresholds config successfully loads or runtime summaries fetch successfully.
   - Verified by `test_wal_loading_unreadable_or_malformed_fails_closed`, `test_wal_loading_ignores_structurally_invalid_records`, and `test_pending_undelivered_records_retry_independently_of_config_and_fetch_success`.
3. **No Metric Carryover on Rollover**:
   - `RuntimeSummaryProjectionStore` resets all metrics, positions, and as-of timestamps when a binding rollover is detected (the incoming event has a different `binding_id` than the summary on record).
   - In addition, every projected metric is stamped with a `f"{field}_binding_id"` provenance key.
   - `evaluate_breaches()` checks metric provenance against the current binding ID of the summary and skips evaluation if they do not match.
   - Verified by `test_runtime_summary_projection_store_reset_on_binding_rollover` and `test_evaluate_breaches_validates_metric_provenance`.

## Round-7 review fixes (Codex, PR #3612 merge-time review, 2026-07-14)

Codex requested changes on 7 points at `4a5c9102` (comment 4965409421,
re-confirmed unaddressed at `d8df8b940`, comment 4965680921), plus one
additional finding surfaced during round-7 re-verification. PR #3612 had
already merged, so these land as a new task-branch commit rather than a
merge-only handoff.

### 1. Blocker — corrupt undelivered WAL entries could lose frozen evidence

`_load_pending_evidence()` silently dropped a structurally invalid record
(missing inner `event_id`, wrong types, etc.) instead of reserving its key.
Because the dedupe key hashes deterministically to that same `event_id`, a
later tick with a matching live candidate treated the corrupt record as if
it had never existed: it recomputed a fresh payload from whatever the live
summary said *now* and admitted/posted it under the corrupt record's old id,
silently discarding the original frozen evidence.

Fixed: `_load_pending_evidence()` now returns `(valid, quarantined_event_ids)`.
A structurally invalid record's `event_id` is reserved in the quarantine set
instead of being forgotten. `run_tick()` refuses to admit/post any new
candidate whose `event_id` matches a quarantined key — it fails that
candidate closed (`errors += 1`, explicit diagnostic) rather than
recomputing. Covered by
`test_run_tick_quarantines_corrupt_wal_record_instead_of_recomputing_under_same_event_id`,
which drives a real candidate to the exact deterministic `event_id` of a
corrupt WAL record and asserts `admit_telemetry_event`/`post_incident` are
never called and the on-disk WAL is untouched.

### 2. High — missing metric provenance was still fail-open

The rollover guard only rejected a *present-but-mismatched*
`<field>_binding_id`; a summary with the provenance marker missing entirely
(not just wrong) fell through to a real candidate. Fixed: a missing
`<field>_binding_id` is now diagnostic-only, the same as a mismatched one.
Covered by
`test_evaluate_breaches_missing_metric_provenance_is_diagnostic_only_fail_closed`.

### 3. High — `run_tick()` still violated its "never raises" contract

`http.client.IncompleteRead` (raised by `urllib` on a truncated HTTP
response body) is not a subclass of `OSError`/`ValueError`/`URLError`, so it
escaped the fetch/admit/post exception whitelists. Fixed: all three HTTP
call sites (`fetch_summaries`, `admit_telemetry_event`, `post_incident`) now
also catch `http.client.HTTPException`. Covered by
`test_run_tick_fails_closed_when_telemetry_fetch_raises_incomplete_read`.
The pre-existing `test_run_tick_never_raises_on_corrupt_wal_records` was
also non-hermetic (it relied on the default `fetch_summaries`, making a real
network request to `http://telemetry.test`); it now injects an explicit
in-test stub.

### 4. High — the canonical ThresholdSnapshot boundary remained incomplete

`build_incident_from_threshold_payload()` only validated non-empty
`metric_name`/`policy_source`. `evolution_decision.schema.json`'s
`threshold_snapshots[]` also requires `signal_type` (from the governance
enum), `comparator` (from the governance enum), `observed_value`, and
`threshold_value` — a payload that dropped any of those still created an
`IncidentCase`. Fixed: `build_incident_from_threshold_payload()` now
validates the full required-field boundary at the authoritative consumer
entry point (both the direct-consumer path and the HTTP route go through
this same function). Covered by
`test_consume_threshold_route_rejects_missing_signal_type`,
`test_consume_threshold_route_rejects_unknown_signal_type`,
`test_consume_threshold_route_rejects_missing_comparator`,
`test_consume_threshold_route_rejects_missing_observed_value`, and
`test_consume_threshold_route_rejects_missing_threshold_value`.

### 5. High — incidents route tests deleted the default persistent store

The `clean_store` autouse fixture in `services/incidents/test_main_routes.py`
cleared and unlinked the module-level `store`'s default path
(`/tmp/pantheon/incidents/incidents.json`) before/after every test, mutating
developer/runtime-shared state on every test run instead of using an
isolated store. Fixed: the fixture now injects a fresh in-memory
`IncidentStore(path=None)` via `monkeypatch.setattr` for both
`services.incidents.main.store` (what the route handlers read) and this test
module's own imported `store` name (what test assertions read), so no test
in this file touches disk. Verified manually that
`/tmp/pantheon/incidents/incidents.json` does not exist after running the
full suite.

### 6. Medium — the dedupe tuple encoding was collision-prone

`f"{binding_id}:{metric_name}:{window_label}"` colon-joins fields without
escaping: distinct tuples that only differ in *where* a colon falls (e.g.
`metric_name="a:b", window="c"` vs `metric_name="a", window="b:c"`) produced
the identical joined string and therefore the identical `event_id`, silently
suppressing one candidate as a false duplicate of the other. Fixed: the
dedupe key is now a canonical JSON array encoding of
`[binding_id, metric_name, window_label]` (each element independently
quoted/escaped, so the encoding is injective for this fixed-arity tuple).
Covered by
`test_evaluate_breaches_dedupe_key_is_not_collision_prone_across_colon_boundaries`.

### 7. Delivery hygiene

- Compose declared `EVOCHAIN_THRESHOLD_SWEEP_METRIC_MAX_AGE_SECONDS` nowhere,
  even though `main()` reads it — an operator override in the host
  environment never reached the container. Fixed: added to
  `evolution-threshold-sweep-producer`'s `environment:` block in
  `docker-compose.yml`, default `172800` (matches the worker's own
  `_DEFAULT_METRIC_MAX_AGE_SECONDS`). Covered by a new compose contract test,
  `test_threshold_sweep_producer_forwards_metric_max_age_env_in_root_compose`
  (`services/evolution/test_compose_activation.py`).
- `git diff --check` flagged trailing whitespace and extra blank lines at
  EOF in the worker and its test file; cleaned up.
- This document's test-count evidence was stale (claimed 58, actual 62 at
  round-7 review time); the "Local validation" section below is refreshed
  with the current counts.

### Additional round-7 finding — heartbeat freshness was fail-open for ambiguous timestamps

`_is_stale_or_degraded()` only checked `last_heartbeat_at` for truthiness,
not whether it actually parsed as a valid timestamp or lay in the past.
A malformed value (e.g. `"not-a-date"`) or a value in the future both read
as "fresh" and let a summary through to real breach evaluation. Fixed:
`_is_stale_or_degraded()` now takes the tick's `now` and requires
`last_heartbeat_at` to parse and to not be later than `now`; either failure
is treated as ambiguous/fail-closed the same as an explicit staleness
marker. Covered by
`test_evaluate_breaches_skips_summary_with_unparseable_heartbeat_timestamp`
and `test_evaluate_breaches_skips_summary_with_future_heartbeat_timestamp`.

## Round-8 review fixes (Codex, PR #3620 merge-time review, GitHub review 4691486924, 2026-07-14)

Codex requested changes on 4 points; each is addressed below.

### 1. Blocker — WAL quarantine was not durable

`_load_pending_evidence()` (round-7 fix) correctly reserved a corrupt
record's `event_id` in an in-memory `quarantined_event_ids` set, but
`_save_pending_evidence()` was always called with only the currently-valid
`pending` dict. Every prune (§2), new-candidate write-ahead-log save (§4), or
post-delivery save (§5) therefore serialized `pending` alone and silently
deleted the quarantined record from disk — the very next unrelated save
after quarantine, not just a hypothetical later one. A later tick then
reloaded a WAL with no record at all under that `event_id` and recomputed
and posted a fresh payload under the same deterministic id, exactly the
evidence-loss failure round-7 §1 was meant to close.

Fixed: `_load_pending_evidence()` now returns `(valid, quarantined)`, where
`quarantined` maps `event_id -> raw record as read from disk` (not just the
id). A new `_full_state(pending, quarantined)` helper unions both back
together, and every `_save_pending_evidence()` call site in `run_tick()`
(prune, write-ahead log, both post-delivery saves) now writes
`_full_state(...)` instead of `pending` alone, so a quarantine tombstone
survives every subsequent save until the WAL is hand-repaired. Covered by
`test_run_tick_persists_quarantine_tombstone_across_prune_and_delivery_saves`,
a two-tick regression: tick 1 quarantines one corrupt record and, in the same
tick, delivers an unrelated new candidate (triggering the exact
prune/WAL/post-delivery saves that used to drop the tombstone); asserts the
tombstone is still on disk unchanged afterward. Tick 2 re-runs and asserts
the quarantined `event_id` is still refused (never admitted/posted) rather
than recomputed.

### 2. High — the incidents replay suite still deleted the configured persistent store

`services/incidents/tests/test_incident_replay_suite.py`'s `clean_store`
fixture imported the module-level `services.incidents.main.store` (bound to
the real `/tmp/pantheon/incidents/incidents.json` unless
`INCIDENTS_DATA_DIR` is overridden) and cleared/unlinked it before and after
every test — the same class of hazard round-7 §5 already fixed in
`test_main_routes.py`, but this second suite had not been converted. With an
isolated `INCIDENTS_DATA_DIR`, this suite's own 17 tests still passed while
leaving `incidents.json` deleted from disk afterward.

Fixed: same pattern as `test_main_routes.py`'s `clean_store` — inject a
fresh in-memory `IncidentStore(path=None)` via `monkeypatch.setattr` for both
`services.incidents.main.store` (what the route handlers read) and this test
module's own imported `store` name (what test assertions read), and drop the
disk-deletion `_reset()` helper entirely. Verified manually that
`/tmp/pantheon/incidents/incidents.json` is untouched after running this
suite.

### 3. Medium — canonical ThresholdSnapshot validation was fail-open for `breached`

`evolution_decision.schema.json`'s `threshold_snapshots[].breached` is
`type: boolean`, but `consumer.py::_is_breached()` coerced any truthy
non-bool value (e.g. the string `"yes"`) into `True`, so a malformed
producer payload — never actually validated as breached by the schema's own
rules — could still open an `IncidentCase`.

Fixed: `_is_breached()` now raises `IncidentConsumerError` for any present
`breached` value that is not an actual `bool` (a missing key still defaults
to `True`, unchanged). Covered by
`test_consume_threshold_route_rejects_non_boolean_breached`
(`services/incidents/test_main_routes.py`).

### 4. Medium — dedupe trusted caller-controlled `incident_id` without identity equivalence

`consumer.py::_incident_id()` accepts any producer-supplied explicit
`incident_id` verbatim, and `ThresholdTelemetryIncidentConsumer.consume()`
looked an existing incident up by that id alone, returning it as "the"
duplicate (`created=False`) with no check that the new payload was actually
about the same breach. A second payload reusing the same explicit id but
describing a different `event_id`/`binding_id`/`metric_name` therefore
silently discarded its own breach and reported success against an unrelated
incident.

Fixed: added `_require_same_incident_identity(existing, incident)`, called
at both `get_incident()` lookup sites in `consume()` (the direct dedupe
check and the create-race fallback). It requires the looked-up incident and
the newly built one to share `binding_id`, `runtime_id`, and at least one
`telemetry_event_id` before treating the lookup as a genuine duplicate;
otherwise it raises `IncidentConsumerError` describing the collision instead
of silently returning the unrelated incident. Covered by
`test_consume_threshold_route_rejects_explicit_incident_id_collision_across_identities`,
which reproduces the exact scenario: two fixture payloads sharing one
explicit `incident_id` but different `event_id`/`binding_id`/`metric_name`;
asserts the second is rejected (422) and the first incident is left
unmodified.

### Delivery hygiene

- Added `test_threshold_sweep_producer_compose_shape_matches_acceptance_criteria`
  (`services/evolution/test_compose_activation.py`): a complete compose-shape
  assertion for `evolution-threshold-sweep-producer` (default-on, build,
  command, restart policy, telemetry/incidents URLs, the
  `EVOCHAIN_THRESHOLD_SWEEP_INTERVAL_SECONDS:-86400` default, the read-only
  config bind mount, the named `evolution-data` state volume, and
  `depends_on` health gates), not just the metric-max-age env forward the
  round-7 test already covered.
- Refreshed this document's stale round count/status header, PR/branch/scope
  reference, and the "Local validation" test counts below (round-8: worker
  file 69 tests, +1 over round-7's 68).
- Corrected a stale residual-risk claim: `evolution-daily-sweep-scheduler`
  was described below as still profile-gated, but EVOCHAIN-002 (merged, PR
  #3516) already removed that gate — see the corrected "Residual risk"
  section.

## Round-9 review fixes (Codex, PR #3620 review, GitHub review comment, 2026-07-14)

Codex requested changes on 6 points; each is addressed below.

### 1. Blocker — the evidence WAL was neither crash-durable nor serialized

`_save_pending_evidence()` wrote a temp file and called `os.replace()`, but
never flushed/fsynced the temp file's data, fsynced the parent directory, or
locked the read-modify-write cycle — materially weaker than
`services/incident/incident.py`'s `IncidentStore` and
`services/foundation/reliable_delivery.py`'s `AtomicJsonRecordStore`. A
host/volume crash right after a save could resurrect the previous WAL
contents even though `run_tick()` already treated the write as durable
authorization to admit telemetry and post an incident; overlapping worker
instances could also last-writer-win away each other's pending/delivered
record.

Fixed: `_save_pending_evidence()` now flushes and `os.fsync()`s the temp
file's file descriptor, then `os.fsync()`s the parent directory's file
descriptor after `os.replace()` — the same durability pattern as
`AtomicJsonRecordStore._write_unlocked`. A new `_wal_lock()` context manager
(`fcntl.flock`, exclusive) now wraps the *entire* WAL transaction in
`run_tick()` — from the initial load through every prune/write-ahead-log/
post-delivery save — so two overlapping worker instances serialize into
sequential single-writer transactions instead of racing on the same file.
Covered by `test_save_pending_evidence_fsyncs_temp_file_and_parent_directory`
(spies on `os.fsync`, asserts exactly 2 calls — file + directory — and no
leftover temp file) and `test_wal_lock_serializes_concurrent_holders` (two
threads racing on the same lock path; asserts the second never enters while
the first still holds it).

### 2. High — a malformed `delivered=true` WAL record fabricated a dedupe

`_load_pending_evidence()` only validated `event_type`/`metrics` integrity
for *undelivered* records. A record with `delivered: true`, a matching
inner/outer `event_id`, and no real `event_type`/`metrics` was accepted as
valid; a genuine candidate recomputing that same deterministic `event_id`
was then silently counted as an already-delivered dedupe instead of being
admitted/posted.

Fixed: the `event_type`/`metrics` integrity check now runs unconditionally,
regardless of `delivered` state — a malformed record is quarantined either
way, which correctly routes a colliding fresh candidate into the existing
"fail-closed: corrupt/unreadable prior WAL record" path instead of a silent
dedupe. Covered by
`test_run_tick_quarantines_malformed_delivered_record_instead_of_fabricating_dedupe`.

### 3. High — conflicting live thresholds were order-dependent instead of fail-closed

`run_tick()` kept whichever duplicate `(metric_name, window)` definition
loaded first and dropped the rest with a warning; with two conflicting
threshold entries for the same observed value, whether an incident opened
depended on live-config JSON key order.

Fixed: entries are now grouped by `(metric_name, window)` identity.
Byte-identical duplicates are coalesced (safe, harmless repetition);
anything else is a genuine conflict, and the *entire* identity is disabled
fail-closed with a diagnostic, regardless of ordering. Covered by
`test_run_tick_disables_conflicting_threshold_entries_fail_closed`, which
asserts zero candidates under both orderings of the same conflicting pair.

### 4. High — the round-8 `incident_id` collision guard remained bypassable and omitted metric identity

`_require_same_incident_identity()` compared `binding_id`, `runtime_id`, and
*any* shared telemetry id (a full-set intersection of
`telemetry_event_ids`). This missed the threshold's own metric/window/policy
identity (so an explicit-id collision with only `metric_name` changed still
passed), and a caller could inject an old event id as a supplemental
top-level `telemetry_event_ids` entry while changing the real primary event,
satisfying "shared evidence" without actually being the same breach.

Fixed: added an optional `IncidentCase.threshold_identity` field
(`services/incident/incident.py`), a JSON-array-encoded
`(metric_name, window, policy_source)` string set by
`build_incident_from_threshold_payload` via a new `_threshold_identity()`
helper and threaded through `PostmortemEvidenceCollector.create_incident()`.
`_require_same_incident_identity()` now requires: same `binding_id`, same
`runtime_id`, the same *canonical primary* telemetry event id
(`telemetry_event_ids[0]` — reliable because `_event_ids()` always adds the
primary id first into an order-preserving dict — not an arbitrary
intersection), and matching `threshold_identity` (an incident with no
recorded `threshold_identity` can never be proven to be the same breach, so
it fails closed rather than matching by default). Covered by
`test_consume_threshold_route_rejects_explicit_incident_id_collision_across_metric_only`
and
`test_consume_threshold_route_rejects_explicit_incident_id_collision_via_supplemental_id_injection`.

### 5. Medium — canonical ThresholdSnapshot validation was still partial

`evolution_decision.schema.json` declares optional `window`/`note` as
`type: string`, but `consumer.py` accepted lists/objects and stringified
them into evidence via `_threshold_notes()`.

Fixed: `build_incident_from_threshold_payload` now raises
`IncidentConsumerError` if `window` or `note` is present and not a string.
Covered by `test_consume_threshold_route_rejects_non_string_window` and
`test_consume_threshold_route_rejects_non_string_note`.

### Delivery hygiene

- Replaced the destructive local-validation recipe below (which ran
  `rm -f /tmp/pantheon/incidents/incidents.json` against the real
  developer/runtime-shared store) with an isolated `INCIDENTS_DATA_DIR`
  sentinel-directory proof that never touches the shared path.
- Merged current `dev` (`origin/dev` was 22 commits ahead of this branch's
  prior head at round-9 review time) and reran the full local validation
  below against the merged tree.
- Refreshed the stale "43 tests"/"70 tests" claims above and in "Local
  validation" below to the current round-9 counts.

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

Counts below are post-merge (`origin/dev` merged into this branch again at
round-9 review time to resolve a second BEHIND state — `dev` had advanced 37
commits with unrelated work, merged cleanly with no conflicts).

```sh
python3 -m pytest services/evolution/test_threshold_sweep_worker.py -q
# 74 passed (round-9: +4 new tests — malformed-delivered-record quarantine,
# conflicting-threshold fail-closed disable, WAL fsync spy, WAL lock mutual
# exclusion; remainder unchanged from round-8 plus unrelated dev-merge tests)

python3 -m pytest services/evolution -q
# 217 passed (round-9: +4 in the worker file above; remainder is unrelated
# work merged in from dev — no regression)

python3 -m pytest services/incidents -q
# 68 passed (round-9: +5 new tests — metric-only incident_id collision,
# supplemental-ID-injection collision, non-string window rejection,
# non-string note rejection, and the isolated replay-suite run below counted
# once via the combined `services/incidents` package; remainder is unrelated
# work merged in from dev — no regression)

python3 -m pytest services/incidents/tests/test_incident_replay_suite.py -q
# 17 passed (unchanged from round-8; no regression)

python3 -m pytest services/incident -q
# 119 passed (no regression in the INC-001 domain layer; threshold_identity
# is an optional additive field, default None, no existing test depends on it)

docker compose config --quiet
# passed

docker compose config --services | grep evolution-threshold-sweep-producer
# evolution-threshold-sweep-producer

git diff --check origin/dev...HEAD -- services/evolution/threshold_sweep_worker.py services/evolution/test_threshold_sweep_worker.py services/incidents/consumer.py services/incidents/test_main_routes.py services/incident/incident.py services/incident/evidence_collector.py
# no output

# Isolated-store proof (round-9: replaces the round-8 recipe's
# `rm -f /tmp/pantheon/incidents/incidents.json`, which deleted the same
# developer/runtime-shared store the isolation fix is meant to protect).
# Points INCIDENTS_DATA_DIR at a throwaway sentinel directory instead, so the
# real shared store is never touched, and confirms it stays untouched:
SENTINEL_DIR=$(mktemp -d)
INCIDENTS_DATA_DIR="$SENTINEL_DIR" python3 -m pytest services/incidents/test_main_routes.py services/incidents/tests/test_incident_replay_suite.py -q
# 60 passed
ls /tmp/pantheon/incidents/incidents.json
# No such file or directory — the shared path was never created or touched;
# both test_main_routes.py's and the replay suite's `clean_store` fixtures
# inject an in-memory IncidentStore(path=None), so neither ever writes
# through INCIDENTS_DATA_DIR for the incident store itself.
```

## Acceptance mapping

| Acceptance criterion | Where |
|---|---|
| producer evaluates live paper telemetry aggregates against governance-schema thresholds from live config | `threshold_sweep_worker.load_thresholds` + `evaluate_breaches`, config in `services/evolution/config/threshold_sweep_thresholds.json` + `threshold_sweep_baselines.json` |
| breach POSTs canonical payload accepted by `ThresholdTelemetryIncidentConsumer` and creates an `IncidentCase` | proven end-to-end against the real consumer/store and the real route, and verified to be schema- and structure-compatible with the default `CanonicalReferenceValidator`'s matching rules under lookup mock-patching. (The actual end-to-end integration and live wiring under LIN-003 is verified by `services/telemetry/test_lineage_write_path.py::TestLiveLineageWritePathFullStackHTTPRoute`). |
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
- **Platform gap resolved (tested separately):**
  The lineage platform gap has been closed. LIN-003 has landed, wiring the live
  telemetry ingest write path to the lineage query engine, so the default unmocked
  `CanonicalReferenceValidator` now successfully validates freshly ingested events in production.
  The live end-to-end integration is verified by telemetry's full-stack integration test
  (`services/telemetry/test_lineage_write_path.py::TestLiveLineageWritePathFullStackHTTPRoute`).
  The EVOCHAIN-001 unit test `test_consume_threshold_route_succeeds_against_default_reference_validator`
  verifies validator structure compatibility under mock-patched lookups, but does not itself
  exercise the live end-to-end wiring.
- **Scheduler activation resolved:** this task never enabled the daily sweep
  scheduler itself, but the claim that `evolution-daily-sweep-scheduler` is
  still profile-gated is now stale — EVOCHAIN-002 (merged, PR #3516) removed
  the `profiles: ["evolution-daily-sweep-scheduler"]` gate, so it now ships
  default-on in `docker-compose.yml`, same as this task's own
  `evolution-threshold-sweep-producer`. Covered by
  `test_daily_sweep_scheduler_is_enabled_by_default_in_root_compose`
  (`services/evolution/test_compose_activation.py`).
- Deploying this stack to the shared `dev` environment is still separate
  follow-up work (EVOCHAIN-011), not part of this task's scope.
