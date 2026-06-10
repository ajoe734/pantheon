# Review: ASST-KERNEL-001 — Implement assistant context-pack schema and BFF route

**Reviewer:** Claude  
**Date:** 2026-05-31  
**Task commit:** 11f65217abbb723b25d8aca7150c3f0b85e11ec0  
**Merge commit:** cb4eb3acc0429f5d6ed0e5a94b064d54f257f2c3 (PR #670)  
**Verdict:** APPROVED

## Acceptance Criteria Verification

1. **context pack route builds structured snapshot** — PASS  
   `POST /bff/assistant/sessions/{session_id}/context` is wired in `main.py` via `_include_assistant_routes()`. The route calls `compose_context_pack` which returns a fully-shaped `AssistantContextPack` with `context_pack_id`, `session_id`, `mode`, `actor`, `snapshot_at`, `frontend`, `backend`, `internal_debug`, `sources`, `redaction`, and `omitted_sources`.

2. **includes frontend route and selected entity** — PASS  
   `_merged_frontend_context` correctly merges top-level `request.route`/`request.selected_entity`/`request.focus` with `request.frontend`, producing a canonical `AssistantFrontendContext`. Test confirms `frontend.route == "/agora/ask"` and `frontend.selected_entity.entity_id == "job_123"`.

3. **includes source refs, timestamps, and staleness metadata** — PASS  
   Each collected source gets an `AssistantSourceRef` with `source_id`, `href`, `snapshot_at`, `status`, and `staleness`. `_source_staleness` emits fresh/stale/unavailable states with `served_from` and `last_known_at`. Test asserts all sources carry `snapshot_at`, `href`, and `staleness.status in {fresh, stale, unavailable}`.

4. **composes allowlisted BFF surfaces only** — PASS  
   `ALLOWLISTED_SOURCES` frozenset in `context_composer.py` is the gate. Non-allowlisted sources are added to `omitted_sources` with `reason="not_allowlisted"` without raising. Test confirms `database_credentials` is omitted with correct reason/message structure.

5. **user mode rejects kernel-only sources** — PASS  
   `_enforce_mode_policy` raises `AssistantContextPolicyError` when `mode == USER` and any `KERNEL_ONLY_SOURCES` appear in the requested list. Route translates this to HTTP 403 with `ErrorCode.FORBIDDEN`, `precondition_failed="assistant_context_mode_policy"`, and `denied_sources`. Test asserts the 403 shape exactly.

6. **tests cover allowlist, missing sources, and staleness** — PASS  
   Three test cases cover the full surface:
   - `test_assistant_context_pack_builds_structured_snapshot`: happy path, all sources, redaction active.
   - `test_assistant_context_pack_omits_non_allowlisted_sources_and_marks_staleness`: non-allowlist omission and `BFF_READ_SURFACE_STATE=stale` degraded staleness.
   - `test_assistant_context_pack_user_mode_rejects_kernel_only_sources`: user-mode policy enforcement.

## Test Run

```
pytest tests/test_assistant_context_pack.py -q
3 passed in 2.05s
```

## Implementation Notes

- Model layer (`models.py`): clean Pydantic v2 with `by_alias` support; `AssistantBaseModel` has `extra="allow"` which is appropriate for an evolving context pack.
- Composer (`context_composer.py`): clean separation between policy enforcement, source collection, and payload assembly. Source aliases (`SOURCE_ALIASES`) normalize user-facing names.
- Route factory (`routes.py`): dependency-injected `build_context_pack`, `extract_identity`, `require_read_role` — no static BFF coupling.
- `sanitized_logs` is in `ALLOWLISTED_SOURCES` and `KERNEL_ONLY_SOURCES` but has no collector registered in `_assistant_collect_source` yet; it would be omitted with `reason="collector_unavailable"`. This is acceptable for this task scope — the collector will come with a later task.

## Decision

All acceptance criteria pass. Implementation is correct, well-tested, and appropriately scoped. **Approved for owner closeout.**
