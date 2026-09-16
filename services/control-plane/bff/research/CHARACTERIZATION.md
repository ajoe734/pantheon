# Experiments / Research Experiments — Current Contract Characterization

Task: ACG-BFF-EVOEXP-PREP-20260828 (design unit ACG-01-EVOEXP).
Matrix items: ACG-01-008 (MERGE), ACG-01-009 (REMOVE, follow-up task).

This document characterizes the route family exactly as it behaves today in
`services/control-plane/bff/main.py`, before any wiring change, so the
prepared router in `router.py` can be reviewed against a known-true baseline.

## Current registrations (as-is, not touched by this task)

`main.py` registers the family in four places:

- **Durable block** (`main.py:57452-57628`): `read_store.list_experiments_bff`
  / `create_experiment_bff` / `get_experiment_bff` / `get_experiment_logs`
  / `get_experiment_metrics` / `get_experiment_artifacts`. Envelope uses a
  `data` key.
- **`_GOV_BFF` overlay block** (`main.py:61191-61417`): merges
  `read_store.list_research_experiments()` with an in-process dict
  `_GOV_BFF_EXPERIMENT_OVERLAY`; writes go only to the overlay dict.
  Envelope uses an `items` key.
- **Research Experiments surface** (`main.py:61421-61472`,
  `GET /bff/research-experiments[/{id}]`): reads through the *same*
  overlay-merge helpers as the block above (`_list_bff_experiments` /
  `_get_bff_experiment`) under a different URL prefix — not a separate
  dataset, just a different envelope over the same records.
- **Generic-alias stubs** (`main.py:68158-68163`, `68517-68520`):
  `PATCH /bff/research-experiments/{id}` and
  `POST /bff/research-experiments` are the *only* registrations for those
  verb/path pairs; they echo the payload back with a generated id and never
  touch `read_store` or the overlay at all.

`_prefer_latest_bff_gap004_routes()` prunes the durable block's
registrations for the six `/bff/experiments*` paths it shares with the
overlay block, so the overlay block is what actually serves traffic today.
Note `read_store.list_experiments_bff` / `get_experiment_bff` /
`create_experiment_bff` are themselves thin projections over
`list_research_experiments` / `get_research_experiment` /
`create_research_experiment` — the "durable" and "research experiments"
data are the *same underlying store*, just two different field
projections. `research/router.py` unifies onto `list_research_experiments`
/ `get_research_experiment` directly as the one durable source for the
whole family.

## Seven normalized route shapes (the ACG-01-008 gate)

| Method | Path | Live handler today |
|---|---|---|
| GET | `/bff/experiments` | overlay (`main.py:61191`) |
| POST | `/bff/experiments` | overlay (`main.py:61221`) |
| GET | `/bff/experiments/{id}` | overlay (`main.py:61276`) |
| POST | `/bff/experiments/{id}/actions/{action_id}` | overlay (`main.py:61302`) |
| GET | `/bff/experiments/{id}/logs` | overlay (`main.py:61335`) |
| GET | `/bff/experiments/{id}/metrics` | overlay (`main.py:61363`) |
| GET | `/bff/experiments/{id}/artifacts` | overlay (`main.py:61391`) |
| GET | `/bff/research-experiments[/{id}]` | overlay-merge helper (`main.py:61421`, `61452`) |

(`PATCH`/`POST /bff/research-experiments*` generic-alias stubs are excluded
from this router's scope; they never had a real domain owner to begin
with, so folding them in is a functional decision for the cutover task,
not a mechanical port.)

## Envelope contract (what `research/router.py` preserves)

- **`GET /bff/experiments`**: `{"items": [...], "page_info": {"next_page_token": ...}, "meta": {...}}`
  (the live overlay shape).
- **`GET /bff/research-experiments`**: `{"data": [...], "items": [...], "page_info": {"next_page_token": ..., "total": N}, "meta": {...}}`
  — both keys are populated with the same list, matching the live overlay
  exactly (it is the one place both `data` and `items` coexist).
- **`POST /bff/experiments`**: flat record dict (both existing blocks
  agree).
- **`GET /bff/experiments/{id}`**, **`GET /bff/research-experiments/{id}`**:
  `{"data": {...}, "meta": {...}}`.
- **`GET .../logs`**: `{"experiment_id": ..., "logs": [...], "meta": {...}}`.
- **`GET .../metrics`**: `{"experiment_id": ..., "metrics": {...}, "meta": {...}}`.
- **`GET .../artifacts`**: `{"experiment_id": ..., "artifacts": [...], "meta": {...}}`.
- **Action**: delegated to the injected `submit_experiment_action`
  callable.

## Filters, pagination, validation

- `status` filter: comma-separated, case-insensitive set membership (the
  overlay's contract). `read_store.list_research_experiments(status=...)`
  itself only supports a single exact-case value, so this router always
  calls it with no filter and applies the CSV filter in Python, same as
  the overlay does.
- Pagination: identical opaque numeric-offset token scheme as Evolution
  Programs, `page_size` default 20, bounded `[1, 200]`.
- Sort order: `created_at` (falling back to `queued_at`) descending.
- `POST /bff/experiments` validation: `name` or `experiment_name` must be a
  non-empty string after `.strip()`, else `422 VALIDATION_FAILED` (both
  blocks agree).

## Detail-record enrichment (analysis links)

The overlay's `_get_bff_experiment` / `_bff_experiment_with_analysis_links`
enrich every experiment detail record (but not list records) with
`analysis_links` / `analysis_ids` computed from
`read_store.list_research_analyses(experiment_id=...)`. This router
reproduces that enrichment for `GET /bff/experiments/{id}`,
`GET /bff/research-experiments/{id}`, and the action/logs/metrics/artifacts
lookups (since they all resolve the experiment through the same
`_require_experiment` helper) — it is not overlay-specific behavior, it is
part of the live detail contract and is preserved for all durable records
too.

## Known gap inherited as-is (not fixed by this task)

`read_store.get_experiment_logs` / `get_experiment_metrics` /
`get_experiment_artifacts` read `logs` / `metrics` / `artifact_ref` off of
`get_research_experiment`'s *projected* detail dict
(`_project_research_experiment_detail`), but that projection does not
include a `logs` or `metrics` key and exposes `artifact_ids`/`artifact_refs`
rather than a singular `artifact_ref`. In practice these three endpoints
return empty results for any experiment that was not created directly in
the `_GOV_BFF_EXPERIMENT_OVERLAY` dict (which does set those keys
explicitly). This is already true of the current durable block
(`main.py:57562-57628`, which calls the exact same `read_store` functions)
— it is a pre-existing `read_store.py` gap, not something introduced or
fixed here, and this task's acceptance criteria forbid touching
`read_store.py`. Flagged for the cutover/follow-up task to decide whether
to extend the read_store projection.

## BFF-RESEARCH-JOBS-OWNER-BINDING-CORRECTIVE-001 (U10A) update

`read_store.py` no longer exists in the repository at all (it was removed
by an earlier corrective task); every "read_store.py" reference above is
historical context for how the route family got here, not a live import.
This task (see
`docs/operations/bff-upstream-v2-20260911/decisions/research-jobs.md`)
makes the following corrections, narrowly scoped to Research Experiments and
the separate Jobs projection (`jobs/router.py`, `ports/job_read.py`) that
this family had been silently reusing:

- **In-memory `_experiments` dict DELETED.** `DefaultResearchKnowledgeSourcePort`
  (`ports/research_knowledge_source.py`) no longer holds any experiment
  state itself. `create_research_experiment` / `cancel_research_experiment` /
  `get_research_experiment` / `list_research_experiments` delegate
  exclusively to `services.research.write_owner.ResearchWriteOwner`
  (Postgres, `research.research_experiments`). If the write owner cannot be
  resolved (missing `DATABASE_URL`/`RESEARCH_STORE_DSN`, or a DB round-trip
  failure), every one of those methods raises
  `ResearchWriteOwnerUnavailableError`, mapped by callers to HTTP 500/503 —
  never a silent empty/fake success and never a fallback to memory.
- **`get_experiment_logs` / `get_experiment_metrics` / `get_experiment_artifacts`
  are real methods now**, added directly to `ReadSurfacePorts`
  (`ports/read_surface_ports.py`), no longer probed with
  `hasattr`/`getattr` fallback chains that silently returned `[]`/`{}` when
  the method happened to be missing. They still legitimately return
  `[]`/`{}` when an experiment genuinely has no logs/metrics/artifacts yet
  (the "empty when genuinely no data" case explicitly preserved by the
  decision doc) — `get_experiment_artifacts` resolves `artifact_ids` through
  the existing RW-05 `get_research_artifact` port method rather than
  inventing a second artifact store.
- **`create_research_experiment` / `cancel_research_experiment` are
  deliberately NOT exposed on `ReadSurfacePorts` itself** — see
  `tests/test_read_surface_caller_migration.py`'s
  `RETAINED_WRITES_DEFERRED_FROM_READ_SURFACE` static regression. Callers
  reach the mutation through `ResearchRouteContext.call_mutation_port` (see
  `research/routes/common.py`) / `_resolve_research_write_port` (see
  `research/routes/experiments.py`), which follow main.py's test-time
  `_active_delegate` swap explicitly and then read
  `research_knowledge_source` off of whichever object is actually active,
  rather than reaching `read_store.research_knowledge_source` directly
  (which would silently bypass a test's swapped-in `read_store` double,
  since `research_knowledge_source` is one of the fixed sub-port names
  `ReadSurfacePorts.__getattribute__` never forwards to `_active_delegate`).
- **Experiment action dispatch is real, not faked.** `POST
  /bff/experiments/{id}/actions/{action_id}` (via `ExperimentAction`) now
  routes to `command_adapters/experiment_adapter.py::ExperimentCommandAdapter`,
  registered ahead of `EvolutionCommandAdapter` in
  `command_adapters/registry.py`. Only `cancel` performs a real mutation
  (`ResearchWriteOwner.cancel_research_experiment`, the only experiment
  mutation method that already existed); `invalidated` / `attached_to_review`
  / `archived` / `retry` fail closed with `ActionUnavailableError` because no
  owner mutation exists for them yet — implementing those is explicit
  follow-up scope, not invented here.
- **Jobs are a completely separate aggregate from this family.**
  `GET /bff/jobs*` no longer touches `research_knowledge_source` at all (it
  used to call `get_research_ticket`/`list_research_tickets`, i.e.
  `ResearchTicket`s masquerading as Jobs). It is now served by
  `ports/job_read.py::JobReadPort`, a typed read composition across the six
  qualified job sources (`job-worker-*`, `job-orchestrator-*`,
  `job-trainer-*`, `job-ingest-*`, `job-policy-*`, `job-openclaw-*`
  detail-only), dispatched by `job_id` prefix. `JobAction` routes to
  `command_adapters/job_adapter.py::JobCommandAdapter`, which fails every
  action closed with `ActionUnavailableError` in this task (U10A is
  read-plumbing/minimal-write scope; real job action execution closure is
  tracked as U10B for the orchestrator source and separate per-service
  follow-up tasks for the other four).

## What still needs main.py at wiring time (not reimplemented here)

- `submit_experiment_action`: the command-store/audit dispatch pipeline
  (`_gov_bff_action_command`) and `command_adapters/evolution_adapter.py`'s
  `_execute_experiment_or_job` (currently a stub with no real domain call —
  see that adapter's own docstring/behavior, unchanged by this task).
- `dataset_surface_status` / `snapshot_meta`: same caveat as
  `evolution/CHARACTERIZATION.md` — defaults here exist only for standalone
  testability.
- The `/bff/research-experiments/{id}` overlay handler additionally applies
  a `_research_experiments_surface_source` heuristic (distinguishing
  `bff_overlay` / missing-dataset / composed-market-persona-defaults
  sources) via `_sem_final_read_model_detail`. This router returns a plain
  `{"data": ..., "meta": ...}` envelope instead of reproducing that
  heuristic; the cutover task should decide whether that source-labeling
  behavior is still needed once the overlay dict is deleted (its main
  purpose was to describe *why* the overlay had no data, which cannot
  happen once there is no overlay).
- `POST`/`PATCH /bff/research-experiments*` generic-alias stubs are not
  ported — see "Seven normalized route shapes" above.
