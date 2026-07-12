# Task Brief: PINT-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: BFF context eligibility and interaction commands
- Status: review_approved
- Owner: Codex
- Reviewer: Claude
- Next: Independently re-verified: router wiring into create_agora_router matches sibling routers, workshop_store field usage matches strategy_workshop/store.py, fail-closed tenant_mismatch fix in 0481cf650 is correct (removed the resolved.tenant_id fallback that let unscoped personas pass). Ran pytest services/control-plane/bff/tests/test_agora_persona_interactions.py services/control-plane/bff/tests/test_agora_router.py -q: 22 passed. Acceptance criteria met (idempotent context resolution, eligibility reasons, fail-closed permissions).

## Summary
Implement BFF context resolution participant eligibility and typed interaction submission.
