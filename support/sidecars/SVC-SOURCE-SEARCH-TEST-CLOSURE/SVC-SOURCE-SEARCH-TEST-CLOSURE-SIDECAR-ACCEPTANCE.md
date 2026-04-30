# SVC-SOURCE-SEARCH-TEST-CLOSURE Sidecar Acceptance Packet

- Task: `SVC-SOURCE-SEARCH-TEST-CLOSURE-SIDECAR-ACCEPTANCE`
- Parent task: `SVC-SOURCE-SEARCH-TEST-CLOSURE`
- Helper kind: `acceptance_packet`
- Sidecar owner: `Codex`
- Sidecar reviewer: `Claude`
- Parent owner: `Codex`
- Parent reviewer: `Codex2`
- Prepared: 2026-04-30
- Review approved: 2026-04-30 by `Claude`
- Scope: support artifact only; no L1 canonical truth, core contract truth, runtime, registry, or governance implementation changes.

## Purpose

This packet gives the parent owner a focused acceptance checklist and dependency
map for closing the source/search test gap:

1. Incremental search indexing must count only new or updated knowledge objects
   while preserving removal counts and snapshot watermarks.
2. The SD-03 `SourceConnector` JSON schema must accept the current canonical
   `SourceConnector.to_dict()` payload, or the serialization path must filter
   non-contract fields before validation.
3. Focused source/search tests and source-search posture checks must remain
   green.

This sidecar does not approve the parent task, does not claim parent closeout,
and does not own any mainline implementation changes. Parent ownership decides
whether to absorb the currently observed candidate changes into the main task.

## Sources Read

- `AI_COLLABORATION_GUIDE.md`
- `.orchestrator/task-briefs/svc_source_search_test_closure_sidecar_acceptance.md`
- `.orchestrator/skills/task-closeout-finalization.md`
- `ai-status.json`
- `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/planning-session.json` (targeted search for this scope only)
- `services/search/index_pipeline.py`
- `services/search/test_index_pipeline.py`
- `services/search/tests/test_contracts.py`
- `services/source_ingestion/connectors/base.py`
- `docs/contracts/source_connector.schema.json`
- `scripts/smoke_source_search_prod_posture.py`
- `services/test_source_search_posture.py`
- `services/source_ingestion/test_compose_activation.py`
- `services/search/tests/test_service_activation_contract.py`
- Specific task archive snapshots for source/search predecessor baselines.
- `.orchestrator/chair-reviews/SVC-SOURCE-SEARCH-TEST-CLOSURE-SIDECAR-ACCEPTANCE-review.md`

I did not read `current-work.md` or the full `ai-activity-log.jsonl`.

## Current Task Snapshot

| Item | State |
|---|---|
| Parent task | `SVC-SOURCE-SEARCH-TEST-CLOSURE` |
| Parent status | `todo` |
| Parent owner / reviewer | `Codex` / `Codex2` |
| Parent declared dependencies | none |
| Parent scope | Mutates canonical implementation/contracts under `services/search`, `services/source_ingestion`, and `docs/contracts` |
| Sidecar scope | Support packet only |
| Sidecar artifact | `support/sidecars/SVC-SOURCE-SEARCH-TEST-CLOSURE/SVC-SOURCE-SEARCH-TEST-CLOSURE-SIDECAR-ACCEPTANCE.md` |
| Sidecar review | Approved by `Claude`; all six review checks passed |

## Non-Scope Guardrails

- Do not edit L1 canonical architecture or policy from this sidecar.
- Do not treat this packet as the SD-03 contract source of truth.
- Do not modify parent runtime, registry, governance, service code, schema files,
  or tests from this sidecar.
- Do not close or approve the parent task based only on this packet.
- Do not attribute currently dirty parent-scope files to this sidecar. They are
  observed implementation candidates for the parent owner to accept, replace, or
  discard under the parent task.

## Dependency Map

The active parent task has no `depends_on` entries in `ai-status.json`.
The following completed baselines are the practical dependency context for
reviewing and absorbing this closure:

| Baseline | Status | Why it matters |
|---|---|---|
| `SVC-SOURCE-CONNECTOR-FRAMEWORK` | done | Introduced current `SourceConnector` model serialization, including `schema_version`, policy summaries, source metadata, secret-ref handling, and no-inline-secret guardrails. |
| `SVC-SEARCH-INDEXING-PIPELINE` | done | Established pipeline snapshots, `incremental_count`, `indexed_watermarks`, `indexed_object_ids`, retention, and HTTP refresh/freshness endpoints. |
| `SVC-SOURCE-SEARCH-AUTONOMOUS-CONNECTOR-INDEXER` | done | Added scheduled connector execution and materialized index baseline. The closure should not regress scheduled ingest, watermarks, DLQ/replay, or materialized index paths. |
| `SVC-SOURCE-SEARCH-PROD-HARDENING` | done | Added production posture enforcement and `scripts/smoke_source_search_prod_posture.py`; closure should keep posture contracts green. |
| `SVC-SOURCE-SEARCH-OPS-BFF` | done | Added BFF source/search ops surface and idempotent command clients. Parent changes should remain compatible with these ops routes. |

## Observed Candidate Mainline Delta

The current worktree already contains parent-scope edits in these files. This
sidecar did not create or modify them:

| File | Observed change | Parent acceptance relevance |
|---|---|---|
| `services/search/index_pipeline.py` | Incremental selection now includes brand-new object ids even if their effective timestamp is older than the last pipeline run timestamp. Existing ids are selected only when effective time is missing, last run time is missing, or effective time is at/after the previous run. | Prevents old unchanged objects from inflating `incremental_count` while still indexing newly discovered ids. |
| `services/search/test_index_pipeline.py` | `test_pipeline_incremental_only_new_objects` now asserts `incremental_count == 1` instead of `>= 1`. | Converts the previous loose check into the acceptance contract: only the new object is incremental in that fixture. |
| `docs/contracts/source_connector.schema.json` | Schema now requires `schema_version: source_connector.v2` and accepts `auth_policy`, `rate_limit_policy`, `license_policy`, and `source_metadata` objects. | Aligns schema validation with `SourceConnector.to_dict()` as currently emitted by the source connector framework. |

The focused diff stat for these files was:

```text
docs/contracts/source_connector.schema.json | 7 ++++++-
services/search/index_pipeline.py           | 9 +++++++--
services/search/test_index_pipeline.py      | 4 ++--
3 files changed, 15 insertions(+), 5 deletions(-)
```

## Parent Acceptance Checklist

### 1. Incremental pipeline counts only new or updated objects

Required checks:

- First run with no prior snapshot remains a full rebuild.
- Second run with unchanged prior ids is not a full rebuild.
- `incremental_count` counts brand-new ids even when the object's effective
  timestamp predates the previous pipeline run timestamp.
- Existing unchanged ids below the previous run timestamp are not counted.
- Objects with `updated_at`, `indexed_at`, or `created_at` at/after the previous
  run timestamp are counted as updated.
- `removed_count` remains based on previous snapshot ids missing from the current
  repository, not on incremental object selection.
- `indexed_object_ids` remains the sorted full current-id set after every run.

Suggested reviewer focus:

- Inspect `IncrementalIndexPipeline.run()` and confirm the incremental predicate
  is id-aware as well as timestamp-aware.
- Keep `test_pipeline_incremental_only_new_objects` exact (`== 1`) so the old
  overcount cannot silently return.

### 2. SD-03 schema accepts canonical `SourceConnector` payloads

Required checks:

- `SourceConnector.to_dict()` validates against `docs/contracts/source_connector.schema.json`.
- The schema includes `schema_version: source_connector.v2` if the model emits it.
- The schema either accepts the current policy objects (`auth_policy`,
  `rate_limit_policy`, `license_policy`, `source_metadata`) or the serialization
  path filters those fields before schema validation.
- `additionalProperties: false` remains meaningful. Do not loosen this to hide
  future contract drift.
- No inline secret material is introduced. Secret-bearing fields must remain
  references governed by `SourceConnector` validation.

Suggested reviewer focus:

- Keep `services/search/tests/test_contracts.py::test_sd03_contract_schemas_accept_model_payloads`
  validating `connector.to_dict()` directly, unless the parent intentionally
  introduces a named contract-filtering function and tests that function.

### 3. Focused parent tests pass

Required command:

```bash
python3 -m pytest -q services/search/test_index_pipeline.py services/search/tests/test_contracts.py
```

Observed in this sidecar run:

```text
27 passed in 8.35s
```

### 4. Source-search production posture remains green

Minimum static/contract checks:

```bash
python3 -m pytest -q services/test_source_search_posture.py services/source_ingestion/test_compose_activation.py services/search/tests/test_service_activation_contract.py
docker compose config -q
```

Observed in this sidecar run:

```text
9 passed in 3.19s
docker compose config -q -> exit 0
```

Live smoke requirement:

```bash
scripts/smoke_source_search_prod_posture.py
```

This sidecar did not run the live smoke because it requires running
source-ingest and search services at `SOURCE_INGEST_URL` and `SEARCH_URL` with
production posture configuration. Parent closeout should either run it against
the target stack or explicitly record why the parent reviewer accepted the
static posture-contract checks instead.

## Owner Finalization Verification

Owner closeout reran the support-packet verification on 2026-04-30:

```bash
git diff --check -- support/sidecars/SVC-SOURCE-SEARCH-TEST-CLOSURE/SVC-SOURCE-SEARCH-TEST-CLOSURE-SIDECAR-ACCEPTANCE.md
python3 -m pytest -q services/search/test_index_pipeline.py services/search/tests/test_contracts.py
python3 -m pytest -q services/test_source_search_posture.py services/source_ingestion/test_compose_activation.py services/search/tests/test_service_activation_contract.py
docker compose config -q
```

Observed owner-closeout results:

```text
git diff --check -> exit 0
27 passed in 8.43s
9 passed in 2.95s
docker compose config -q -> exit 0
```

## Absorption Plan for Parent Owner

1. Restart `SVC-SOURCE-SEARCH-TEST-CLOSURE` under parent owner `Codex`.
2. Decide whether the observed three-file candidate delta is acceptable as the
   parent implementation.
3. If accepted, stage and commit those parent-scope implementation/schema/test
   changes under the parent task, not this sidecar.
4. Run the focused parent tests and posture checks listed above.
5. Hand the parent to `Claude` for review with exact verification output and any
   live-smoke exception called out.

## Sidecar Review Checklist

Reviewer `Claude` verified only this sidecar packet:

| Check | Expected |
|---|---|
| Support artifact only | This file is the only sidecar-owned artifact. |
| Canonical truth untouched | No L1 policy, contract truth, runtime, registry, or governance implementation is changed by this sidecar. |
| Parent dependency map usable | Completed source/search predecessor baselines are identified. |
| Acceptance checklist actionable | Parent owner can use it to absorb or reject candidate deltas. |
| Verification commands recorded | Focused pytest, posture contract tests, and compose config results are listed. |
| Live-smoke limitation explicit | The packet does not falsely claim a production-posture live smoke was run. |

## Handoff Note

This packet passed sidecar review and is ready for owner finalization through
the normal `review_approved -> done` closeout path with a task-scoped
support-artifact commit. Parent owner `Codex` remains responsible for deciding
whether to absorb the observed parent-scope changes into
`SVC-SOURCE-SEARCH-TEST-CLOSURE`.
