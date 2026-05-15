# Review: SVC-SEARCH-RETRIEVAL-AND-CUTOFF-SIDECAR-BFF-HANDOFF

**Reviewer**: Claude
**Owner**: Claude2
**Review Date**: 2026-04-30
**Outcome**: APPROVED

## Reviewer Checklist Results

- [x] Packet is support-only — no canonical truth, runtime, or frontend implementation files modified.
- [x] Parent task status snapshot (`SVC-SEARCH-RETRIEVAL-AND-CUTOFF: todo`) matches `ai-status.json`.
- [x] Upstream dependency commits confirmed:
  - `SVC-SEARCH-INDEXING-PIPELINE`: commit `8c3bec0` ✓ (git log confirmed)
  - `SVC-SEARCH-DURABLE-COMPAT-QUARANTINE`: commit `80e2a5a` ✓ (git log confirmed)
- [x] BFF route `GET /api/v1/research/search` confirmed at `services/control-plane/bff/main.py:8487`.
- [x] `_rw02_*` helper functions confirmed in `main.py`.
- [x] `list_research_search_results`, `_rw02_search_service_payload`, `get_last_governed_search_refs` confirmed in `read_store.py`.
- [x] Search service route table (Section 5) largely matches `services/search/main.py` routes.
  - Minor gap: `/api/search/index/reload` (POST, line 348) is present in `main.py` but omitted from Section 5.
  - Assessment: not material to BFF handoff purpose; route is internal-only and the guard rail ("do not add browser calls to search service internals") covers it.
- [x] Compat quarantine history references match `ai-task-archive/tasks/SVC-SEARCH-DURABLE-COMPAT-QUARANTINE.json` (status: done).
- [x] Frontend guard rails do not expose compat paths or internal search service routes.
- [x] Post-parent-task impact table (Section 10) does not over-promise parent task scope; all five items are posture/enforcement changes only with no frontend change required.

## Summary

The BFF handoff packet is accurate and complete for its stated purpose. All major claims verified
against source files and git history. The single omission (`/api/search/index/reload` from the
route inventory) is non-material — it is an internal pipeline route that the packet's guard rails
already prohibit from browser exposure.

The artifact correctly:
- Documents the current BFF search surface (`GET /api/v1/research/search`) and its response model.
- Identifies the BFF query gap matrix, operator journey, and frontend screen regions.
- Sets clear guard rails against direct search service calls, client-side re-sorting, and
  compat path exposure.
- Scopes the post-parent-task impact table appropriately without pre-empting the parent task.

No changes required. Approved for finalization.
