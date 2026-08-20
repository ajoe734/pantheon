# Task Verification & Evidence: PFG-AGORA-RESEARCH-CONSUMER-20260820

- **Task ID:** PFG-AGORA-RESEARCH-CONSUMER-20260820
- **Title:** Drain Agora Research outbox with the existing ResearchDispatcher
- **Owner:** Antigravity
- **Reviewer:** Codex2
- **Target Repository:** `pantheon` (`ajoe734/pantheon`)
- **Merge Target:** `dev`

## Code Disposition & Implementation Summary
1. **Existing Dispatcher:** `services/control-plane/bff/agora/research/dispatcher.py` implements outbox record creation, lease acquisition (`acquire_outbox_lease`), adapter resolution (`ALLOWLISTED_STAGE_BACKENDS`), adapter execution, ordered run progress transitions, checksum calculation (`compute_artifact_checksum`), explicit provenance tagging (`real`, `simulation`, `fixture`, `unavailable`), and artifact readback projection.
2. **Existing Store & Outbox:** `services/control-plane/bff/agora/research/store.py` (`MemoryResearchPlanStore` and `PostgresResearchPlanStore`) implements outbox persistence and atomic lease acquisition.
3. **No Facade-Only Fallback:** In `router.py`, production candidate pool creation explicitly returns an empty candidate list with explicit exclusion reasons (`no_authoritative_registry_candidates_discovered`, `no_eligible_research_artifacts_match_filter`) when no real input candidates are supplied, rather than defaulting to hardcoded fixtures.
4. **Verification Evidence:**
   - Evaluated unit & integration test suites in `tests/agora_product_journey/` (16 integration tests passing cleanly).
   - Validated idempotency, lease acquisition, recovery, isolation, and end-to-end lineage.
