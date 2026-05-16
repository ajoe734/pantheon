# IMT-002 Evidence

## Scope

Implemented schema-backed `PreferenceExample` and `CorrectionTrace` contracts
under `services/research/imitation/`.

These contracts capture governed human preference examples and edit correction
traces for research imitation / preference-learning data preparation. They are
validation-only contracts: they do not write registry state, launch training,
promote artifacts, or grant runtime authority.

## Deliverables

- `services/research/imitation/preference_example.schema.json` -
  Draft-7 `PreferenceExample` schema with approve/edit/reject pair semantics.
- `services/research/imitation/correction_trace.schema.json` -
  Draft-7 `CorrectionTrace` schema for append-only before/after correction
  records and edit operations.
- `services/research/imitation/preference_models.py` -
  frozen schema-backed dataclasses, payload validators, and edit-example to
  correction-trace cross-validation.
- `services/research/imitation/test_preference_models.py` -
  focused schema/model tests.
- `services/research/imitation/__init__.py` -
  package exports for the new IMT-002 contracts.

## Governance Invariants

| Invariant | Enforcement |
|---|---|
| Human-only learning signal | `actor_role` is limited to `operator` / `approver`. |
| Training-state boundary | target `promotion_state` is limited to `candidate` / `paper`. |
| Governed linkage required | target must include `registry_id`, `(artifact_version + artifact_type)`, or `(lineage_ref + artifact_type)`. |
| Pair semantics are explicit | approve requires `chosen_artifact`; reject requires `rejected_artifact`; edit requires both plus `correction_trace_id`. |
| Correction traces are non-noop | `operations` must be non-empty and `before_artifact` must differ from `after_artifact`. |
| Cross-object lineage is checkable | `validate_preference_example_against_correction_trace()` verifies trace id, strategy id, feedback event id, actor id, and before/after artifacts. |

## Verification

```bash
python3 -m py_compile services/research/imitation/preference_models.py services/research/imitation/test_preference_models.py
python3 -m json.tool services/research/imitation/preference_example.schema.json
python3 -m json.tool services/research/imitation/correction_trace.schema.json
python3 -m pytest services/research/imitation/test_preference_models.py -q
python3 -m pytest services/research/imitation -q
```

Results:

- py_compile passed
- both JSON schema files parse cleanly
- `test_preference_models.py`: 16 passed
- `services/research/imitation`: 40 passed

## Commit

Task-scoped implementation commit includes this evidence packet.
