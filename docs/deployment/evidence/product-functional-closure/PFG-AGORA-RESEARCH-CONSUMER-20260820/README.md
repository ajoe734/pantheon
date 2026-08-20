# Task Verification & Evidence: PFG-AGORA-RESEARCH-CONSUMER-20260820

- **Task ID:** PFG-AGORA-RESEARCH-CONSUMER-20260820
- **Title:** Drain Agora Research outbox with the existing ResearchDispatcher
- **Owner:** Antigravity
- **Reviewer:** Codex2
- **Target Repository:** `pantheon` (`ajoe734/pantheon`)
- **Merge Target:** `dev`

## Code Disposition & Implementation Summary
1. **Existing Dispatcher & Production Consumer:** `services/control-plane/bff/agora/research/dispatcher.py` implements `drain_outbox`, which queries pending queued outbox records, acquires leases via `acquire_outbox_lease`, executes allowlisted backend adapters via `execute_stage`, and updates stage/run status to completed with explicit provenance (`real`, `simulation`, `fixture`, `unavailable`).
2. **Router Integration:** `services/control-plane/bff/agora/research/router.py` invokes `dispatcher.drain_outbox` upon plan stage dispatch, ensuring queued outbox records are actively leased and executed by the single existing dispatcher.
3. **No Facade-Only Fallback:** In `router.py`, production candidate pool creation explicitly returns an empty candidate list with explicit exclusion reasons (`no_authoritative_registry_candidates_discovered`, `no_eligible_research_artifacts_match_filter`) when no real input candidates are supplied, rather than defaulting to hardcoded fixtures.
4. **Verification Evidence:**
   - Evaluated unit & integration test suites in `services/control-plane/bff/tests/test_agora_research_candidate_governed.py` and `tests/agora_product_journey/` (23 tests passing cleanly, including `test_end_to_end_outbox_consumer_dispatch`).
   - Validated end-to-end flow from plan creation -> approval -> stage dispatch outbox record creation -> leased consumer drain -> execution_status succeeded.

