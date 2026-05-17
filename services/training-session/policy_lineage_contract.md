# Trainer Commit Persona Policy Lineage Contract

Task: `TRN-005`

`policy_lineage.py` owns the narrow side effect that records trainer commit
lineage into the lineage-read store. The training-session service remains the
writer for trainer replay decisions; this module gives that commit path a
stable edge contract.

## Public API

```python
record_commit(
    session_id: str,
    event_ids: Sequence[str],
    persona_id: str,
    *,
    store: LineageReadStore | None = None,
    persona_policy_resolver: PersonaPolicyResolver | None = None,
    commit_at: str | None = None,
    policy_artifact_id: str | None = None,
) -> list[dict[str, Any]]
```

Behavior:

- validates `session_id`, `persona_id`, and a non-empty `event_ids` sequence;
- resolves the target persona policy artifact from `policy_artifact_id` or the
  injected `PersonaPolicyResolver`;
- raises `UnknownPersonaPolicyError` before writing any edge when no target
  persona policy artifact exists;
- writes one normalized lineage-read edge per teaching event id.

## Edge Shape

Every committed teaching event produces one edge with:

- `schema_version = trainer_policy_lineage_edge.v1`
- `semantic_edge_id = persona_policy_artifact.trainer_commit`
- `source = trainer_session`
- `producer = <session_id>`
- `target = persona_policy_artifact`
- `target_id = <persona policy artifact id>`
- `persona_id`, `session_id`, `teaching_event_id`, `teaching_event_ids`
- `commit_at` and `recorded_at`

`edge_id` is stable for `(session_id, target_id, teaching_event_id)` and the
JSONL lineage-read store treats byte-equivalent replays as idempotent.

## TRN-004 Wiring

`main.py` calls `record_commit(...)` only on replay `commit`.

- The event ids come from the replay's existing teaching events.
- The target policy artifact is the TRN-004 `persona_policy_ref`.
- The replay artifacts and decision event `artifact_refs` include
  `policy_lineage_edge_ids` and `policy_lineage_store_ref`.
- Replay `discard` does not write persona policy lineage edges.

## Local Store

The default local lineage-read store is:

```text
${TRAINING_SESSION_LINEAGE_READ_STORE_PATH}
```

If unset, it falls back to:

```text
${TRAINING_SESSION_DATA_DIR:-/tmp/pantheon/training-session}/policy_lineage_edges.jsonl
```

This is an append/replay v1 adapter for local and test execution. A future
lineage-read service client only needs to implement `append_edge(edge)`.

## Verification

Focused command:

```bash
pytest -q services/training-session/test_policy_lineage.py
pytest -q services/training-session/tests/test_http_service.py
```
