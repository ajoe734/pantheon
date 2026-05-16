# STRAT-002 Review — Claude

Task: STRAT-002 StrategySpec registry endpoints
Reviewer: Claude
Owner: Codex
Date: 2026-05-16
Commit reviewed: 6a1ee000

## Verdict: APPROVED

No blocking findings. Implementation is correct, well-tested, and additive.

## Scope reviewed

- `services/registry/service.py` — new `StrategySpecRegisterRequest` model, `_strategy_spec_checksum()`, `_strategy_spec_register_payload()`, `_ensure_strategy_spec_view()`, and four new FastAPI endpoints
- `services/registry/main.py` — docstring updated to list new routes
- `services/registry/test_service.py` — four new tests covering the facade

## Findings

### Correctness

- `artifact_type=strategy_spec` is forced in `_strategy_spec_register_payload()` — no caller bypass possible.
- Lineage gate is correctly enforced: empty lineage raises `RegistryError` before reaching `RegistryService.register()`.
- Inline `strategy_spec` path: deterministic SHA-256 checksum via `json.dumps(..., sort_keys=True)` is sound. `StorageRef(backend=INLINE, path="$.entry.metadata.strategy_spec")` correctly points to the stored inline payload.
- `source_seed_id` is injected into `lineage.source_run_ids` (dedup-safe: only appended if absent). `producer_run_id` defaults to `source_seed_id` when not explicitly set — sensible default.
- `_ensure_strategy_spec_view()` raises `RegistryNotFoundError` (→ 404) when entry exists but is wrong type. Applied to both GET and advance endpoints — correct type-guarding.
- `advance_strategy_spec_state` performs type check before delegating to `advance_artifact_state` — TOCTOU is safe in current in-memory store context.
- `list_strategy_spec_entries` filters on `artifact_type == strategy_spec` and optionally on `artifact_state` — both filters correct.
- Inline strategy_id consistency check rejects mismatched `strategy_spec.strategy_id`.

### State-machine correctness

- Facade delegates `draft → candidate` to `RegistryService.advance_artifact_state()` — no custom state machine, no divergence from the governed path.
- `artifact_state` at registration defaults to `DRAFT`; `APPROVED`/`RETIRED` are rejected by the existing `RegistryService.register()` guard.
- Deployment stage is not touched anywhere in the facade — correctly preserves split semantics.

### Test coverage

- `test_register_strategy_spec_from_source_seed_inline_payload`: inline path, lineage, storage_ref, checksum, metadata all verified.
- `test_strategy_spec_facade_lists_gets_and_advances_only_strategy_specs`: list type-filter, GET, advance (draft→candidate), and `artifact_state` query param all verified.
- `test_strategy_spec_facade_rejects_missing_lineage`: 400 on empty lineage.
- `test_strategy_spec_facade_rejects_mismatched_inline_strategy_id`: 400 on ID mismatch.
- 44 `test_service.py` tests pass; 69 full registry suite pass — no regressions.

### Minor observations (non-blocking)

- No explicit test for GET or advance on a `registry_id` that refers to a non-`strategy_spec` artifact (should return 404). The type-guard logic is exercised indirectly via list filter but not via a direct typed-GET of a wrong-type ID.
- No test for the explicit `storage_ref + checksum` path (without inline `strategy_spec`). Core logic branches are present and trivially correct but uncovered by tests.

Both gaps are acceptable given the coverage of the primary acceptance paths.

## Verification

```
python3 -m py_compile services/registry/service.py services/registry/main.py services/registry/test_service.py  → OK
pytest services/registry/test_service.py -q  → 44 passed
pytest services/registry -q  → 69 passed
```
