# Review: APP-003-CW04-IMPL-001

Reviewer: Claude
Date: 2026-04-22
Task: Implement CW-04 Red-team Memo route family

## Verdict: Approved

## Scope Verified

1. `services/control-plane/bff/main.py:7211` `GET /api/v1/consult/memos` and `:7253` `GET /api/v1/consult/memos/{memo_id}` are wired against the ratified contract (`docs/bff/CW-04-redteam-memo.md`).
2. `services/control-plane/bff/main.py:1228-1383` provide collection / per-memo surface state, staleness, governance gating, and status-filter validation. Surface degradation rules match the contract: `degraded` keeps last-known content but blocks CTA; `unavailable` hides summary, recommendations, and evidence refs.
3. `services/control-plane/bff/main.py:1294-1324` `_cw04_allowed_actions` enforces all seven gating conditions (lifecycle, valid target, governance authority role, no active review, not suppressed/withdrawn, supported target type, surface ok). `canInitiateGovernanceReview` is the only signal exposed.
4. `services/control-plane/bff/read_store.py:9455-9576` provide backend-owned summary and detail projections, including `session_to_memo_mapping` (mapping_id, source_session_id, transcript_id, transcript_version, memo_id, memo_type, created_by, evidence_refs, mapping_status, created_at) and normalized evidence link objects (`id`, `evidence_type`, `artifact_ref`, `description`, `link`).
5. `services/control-plane/bff/read_store.py:2240-2362` seed two memo records (one published with deployment_plan target, one draft superseding) covering reviewer-with-CTA, draft-blocked, and active-review-blocked paths.
6. `services/control-plane/bff/read_store.py:451-457` register `consult_memos` against `PANTHEON_BFF_CONSULT_MEMO_STORE` with snapshot fallback.

## Test Coverage

`test_cw04_redteam_memo_contract.py` (7 tests, all passing locally):
- list envelope + degraded snapshot surface
- detail backend-owned shape + governance CTA for reviewer
- detail hides CTA for non-governance operator
- draft never allows CTA
- detail hides CTA when active governance review exists
- detail keeps last-known content when surface degraded, blocks CTA
- detail hides content when surface unavailable, blocks CTA

Regression check on adjacent CW workbench surfaces: `test_pkt015_consultation_workbench_contract.py`, `test_cw01_consult_request_contract.py`, `test_cw02_debate_transcript_contract.py`, `test_cw03_committee_board_contract.py` — 28 tests pass.

## Acceptance Check

| Criterion | Status |
|---|---|
| Consult memo list and detail routes are implemented | ✅ |
| Governance handoff fields and allowedActions match the ratified contract | ✅ |
| Tests cover memo lifecycle mapping and degraded behavior | ✅ |

## Notes

All three acceptance items are met against the ratified contract. Returning to Codex for finalization.
