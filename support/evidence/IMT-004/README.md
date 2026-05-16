# IMT-004 Evidence: Behavior Policy Artifact Type Registration

Task: `IMT-004`
Owner: `Codex`
Reviewer: `Claude`

## Scope

Registered `behavior_policy` as a first-class governed registry artifact type for the Human Trader imitation loop.

Implemented:

- `services/registry/contract.md` documents `behavior_policy` as a behavior-cloned trader policy candidate.
- `services/registry/registry_entry_schema.json` accepts `artifact_type=behavior_policy`.
- `services/registry/models.py` exposes `ArtifactType.BEHAVIOR_POLICY`.
- `services/learning/imitation/adapter.py` emits registry-ready behavior-policy envelopes with canonical `artifact_state=draft` and legacy `lifecycle_state=draft` compatibility.
- `services/research/imitation/*` preference/correction target schemas and models can reference `behavior_policy` artifacts.
- Integration notes for the governed imitation adapter now name the behavior-policy output boundary.

## Governance Boundary

`behavior_policy` artifacts do not get live authority from registration.

They follow the same registry state machine as other governed artifacts:

- initial state: `draft`
- promotion path: `draft -> candidate -> approved`
- deployment stage remains `none` until a separate deployment/runtime authority projects a stage
- imitation metadata continues to mark direct live influence as false

## Verification

```bash
python3 -m json.tool services/registry/registry_entry_schema.json >/dev/null
python3 -m json.tool services/research/imitation/preference_example.schema.json >/dev/null
python3 -m json.tool services/research/imitation/correction_trace.schema.json >/dev/null
python3 -m py_compile services/registry/models.py services/registry/service.py services/registry/test_service.py services/learning/imitation/adapter.py services/learning/imitation/test_adapter.py services/research/imitation/preference_models.py services/research/imitation/test_preference_models.py
pytest services/registry/test_service.py -q
pytest services/learning/imitation/test_adapter.py -q
pytest services/research/imitation/test_preference_models.py -q
python3 services/learning/imitation/smoke_test.py
pytest services/research/imitation -q
python3 services/research/imitation/smoke_test.py
```

Results:

- registry service tests: 45 passed
- learning imitation adapter tests: 3 passed
- research imitation preference/correction focused tests: 17 passed
- research imitation package tests: 51 passed
- learning imitation smoke test: passed
- research imitation smoke test: passed
