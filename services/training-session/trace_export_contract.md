# Trainer Trace Export Contract

Task: TRN-007
Module: `services/training-session/trace_export.py`

## Purpose

`trace_export.py` projects a trainer `TeachingEvent` stream into imitation
training data. It is a downstream bridge only: it does not change
`TeachingSession`, `TeachingEvent`, registry state, runtime bindings, broker
state, or live execution behavior.

## Entry Point

```python
export_session(session_id: str, target_format: str, *, data_dir=None, store=None) -> dict
```

Supported `target_format` values:

- `bc`
- `trl`
- `preference`

The default store is `TrainingSessionStore` from `TRAINING_SESSION_DATA_DIR`
or `/tmp/pantheon/training-session`. Tests and services may inject `store`.

## Source Stream

The exporter reads append-only events with `store.list_event_log(session_id)`.
If the event log is empty or partial, it also considers `session["events"]`.
Events are de-duplicated by `event_id` and sorted by `sequence_number`,
timestamp, then `event_id`.

The exporter accepts both service-native TeachingEvent fields and BFF replay
projection aliases such as `emitted_at`, top-level `patch_delta`, and
top-level `artifact_refs`.

## BC Output

`target_format="bc"` returns the trajectory dataset shape consumed by
`services/research/imitation/dataset_builder.py`:

- `dataset_id`
- `strategy_id`
- `source_dataset_refs`
- optional `source_strategy_spec_id`
- `sessions[]`

Each session row contains:

- `trajectory_id`
- `actor_id`
- `actor_role`
- `decision`
- `target`
- `steps[]`

The output is validated with `DatasetBuildRequest.from_dict()` and
`build_dataset(..., require_feedback_event_ids=True)`.

## Preference Output

`target_format="preference"` returns:

- `preference_examples[]`: IMT-002 `PreferenceExample` payloads
- `correction_traces[]`: IMT-002 `CorrectionTrace` payloads for edit events
- `metadata`

Mappings:

- `control_patch`, `correction`, `patch_proposed` -> edit preference plus
  correction trace
- `commit` -> approve preference
- `discard` -> reject preference

Every emitted example and correction trace is validated through the IMT-002
schema-backed models.

## TRL Output

`target_format="trl"` returns:

- `pairs[]`
- `metadata.pair_shape = "prompt/chosen/rejected"`

Each pair has the deterministic DPO-style fields:

- `prompt`: stable JSON context prefixed with `pantheon_trainer_preference`
- `chosen`: stable JSON string for the preferred artifact
- `rejected`: stable JSON string for the non-preferred artifact

Null approve/reject sides use:

```json
{"artifact_id": "__null__", "is_null": true}
```

This matches the pending IMT-008 bridge contract direction and the local TRL
DPO adapter convention of serializing `prompt`, `chosen`, and `rejected`.

## Governance

The exporter is research-only and fail-closed:

- `target.promotion_state` must be `candidate` or `paper`
- every BC step must include `feedback_event_id`
- unsupported formats raise `TraceExportError`
- sessions without exportable events raise `TraceExportError`
- no live execution, registry mutation, or persona policy mutation occurs
