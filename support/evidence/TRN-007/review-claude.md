# TRN-007 Review — Claude

Reviewer: Claude
Date: 2026-05-17
Status: **APPROVED**

## Acceptance Criteria Verification

| Criterion | Result |
|---|---|
| `export_session(session_id, target_format)` supports `{bc, trl, preference}` | PASS |
| BC output matches `services/research/imitation` trajectory schema | PASS |
| TRL output matches IMT-008 `prompt/chosen/rejected` pair shape | PASS |
| 1 fixture export test per format | PASS (3 tests) |
| `pytest -q exit 0` | PASS (5 passed) |

## Verification Commands

```
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/training-session/test_trace_export.py -q
=> 5 passed in 0.90s

PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/training-session/trace_export.py services/training-session/test_trace_export.py
=> OK

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/training-session/test_trace_export.py services/research/imitation/test_dataset_builder.py services/research/imitation/test_preference_models.py -q
=> 46 passed in 9.09s (no regressions)
```

## Findings

No blocking issues.

- `export_session()` correctly dispatches to `_export_bc()`, `_export_preference()`, `_export_trl()`.
- BC path validates output through `DatasetBuildRequest.from_dict()` + `build_dataset(require_feedback_event_ids=True)`.
- Preference path validates every emitted example/trace through IMT-002 schema validators.
- TRL path produces stable JSON `prompt/chosen/rejected` pairs; null sides use documented `__null__` sentinel.
- `target.promotion_state` is checked against `{candidate, paper}` fail-closed.
- Governance metadata: `research_only=True`, `direct_live_influence=False` ✓.
- TRN-001 schemas untouched ✓.
- Import layout handles both repo-root package import (service tests) and service-dir execution correctly.
- `trace_export_contract.md` clearly documents source stream, BC/preference/TRL shapes, and governance boundary.
