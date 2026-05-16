# TRN-004 Evidence: trainer commit / discard / replay

## Scope

TRN-004 hardens the `training-session-svc` replay decision path for completed
trainer sessions:

- commit/discard decisions accept `Idempotency-Key` and `X-Idempotency-Key`
- same-key/same-payload retries replay the existing decision without appending
  a second `TeachingEvent`
- same-key/different-payload retries return conflict
- commit decisions stamp traceable `lineage_ref`, `lineage_edge_id`,
  `persona_policy_ref`, and `route_policy_ref` fields into replay artifacts and
  the decision event `artifact_refs`
- discard decisions stamp decision lineage while keeping `after_artifact_ref`
  empty and avoiding persona/route-policy mutation claims

## Verification

```bash
python3 -m py_compile services/training-session/main.py services/training-session/tests/test_http_service.py
python3 -m pytest services/training-session/tests/test_http_service.py -q
python3 -m pytest services/training-session/tests -q
python3 -m pytest services/control-plane/bff/test_training_session_service_client.py -q
python3 -m pytest services/control-plane/bff/test_tw04_teaching_replay_contract.py -q
```

Results:

- `py_compile` passed
- `services/training-session/tests/test_http_service.py`: 7 passed
- `services/training-session/tests`: 17 passed
- `services/control-plane/bff/test_training_session_service_client.py`: 3 passed,
  2 pre-existing `datetime.utcnow()` deprecation warnings in BFF read-store code
- `services/control-plane/bff/test_tw04_teaching_replay_contract.py`: 34 passed,
  8 pre-existing `datetime.utcnow()` deprecation warnings in BFF read-store code
