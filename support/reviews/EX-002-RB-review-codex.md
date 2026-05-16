# EX-002-RB Review

Reviewer: `Codex`
Owner: `Claude`
Date: `2026-05-16`
Disposition: `reopen`

## Finding

1. `ArtifactLoader._validate_metadata()` validates the new `deployment_stage` field but does not enforce the canonical `artifact_state=approved` load rule. The canonical registry contract says runtime loading requires `artifact_state=approved` and that `candidate`, `retired`, `none`, and `frozen` must be rejected for new execution loads (`services/registry/contract.md:193`). In the current implementation, metadata with `artifact_state="candidate"` and `deployment_stage="paper"` is accepted for `ExecutionMode.PAPER` because the loader only compares the stage at `services/execution/artifact_loader.py:285`. That leaves the migration partially complete: the loader has moved from `promotion_state` to `deployment_stage`, but it still does not consume the other half of the new split model.

## Verification

Passed:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/execution/test_artifact_loader.py -v
```

Result: `15 passed in 8.10s`

Passed:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 services/execution/smoke_test_artifact_loader.py
```

Result: `EX-001 smoke test passed`

Passed:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/registry/ -v
```

Result: `65 passed in 46.27s`

Additional reviewer probe:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
import hashlib, json
from services.execution.artifact_loader import ArtifactLoader, ExecutionMode, ArtifactLoadError
from services.execution.test_artifact_loader import FakeObjectStore
payload = b'{"weights":[1,2,3]}'
projection = ArtifactLoader.build_projection('strat-001', '1.2.3')
metadata = {
    'registry_id': 'reg-strat-001-1.2.3',
    'strategy_id': 'strat-001',
    'version': '1.2.3',
    'artifact_type': 'model_artifact',
    'artifact_state': 'candidate',
    'deployment_stage': 'paper',
    'checksum': 'sha256:' + hashlib.sha256(payload).hexdigest(),
    'lineage': {'source_run_ids': ['train-run-001']},
    'created_at': '2026-04-06T12:00:00Z',
}
loader = ArtifactLoader(FakeObjectStore({projection.metadata_key: json.dumps(metadata), projection.artifact_key: payload}))
try:
    loader.load('strat-001', '1.2.3', ExecutionMode.PAPER)
except ArtifactLoadError as exc:
    print('rejected', exc)
else:
    print('accepted')
PY
```

Result: `accepted`

## Required Fix

Update the loader and regression coverage so new split-envelope metadata cannot load unless `artifact_state == "approved"` for executable stages. Keep the legacy fallback intact for pre-migration object store records that only contain `promotion_state`, but reject inconsistent new metadata such as `artifact_state=candidate` with `deployment_stage=paper`.
