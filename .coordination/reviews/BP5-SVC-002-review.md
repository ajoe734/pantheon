# BP5-SVC-002 Review

Reviewer: Codex
Date: 2026-04-15
Status: approved

## Result

No remaining findings.

The earlier API error-mapping issue is resolved:

1. `RegistryNotFoundError` is now a dedicated subclass in [services/registry/split_api.py](/home/edna/code/pantheon/services/registry/split_api.py:36), and missing-entry paths raise it consistently from `get()`, `advance_artifact_state()`, and `update_deployment_summary()`.
2. The FastAPI adapter in [services/registry/service.py](/home/edna/code/pantheon/services/registry/service.py:110) now returns `404` for missing registry entries and `400` for governed split-model validation failures on both write endpoints.
3. The regression coverage in [services/registry/test_service.py](/home/edna/code/pantheon/services/registry/test_service.py:524) now includes all four API error paths discussed in the previous review round.

## Verification

- `python3 services/registry/smoke_test.py` → `40 passed, 0 failed`
- `pytest -q services/registry/test_service.py` → `38 passed in 2.71s`
- Source inspection confirms the implemented registry semantics still match [TARGET_ARCHITECTURE.md](/home/edna/code/pantheon/TARGET_ARCHITECTURE.md:16) and [services/registry/contract.md](/home/edna/code/pantheon/services/registry/contract.md:49): `artifact_state` remains the governed lifecycle, while `deployment_stage` is exposed only as derived deployment/read-model state.
