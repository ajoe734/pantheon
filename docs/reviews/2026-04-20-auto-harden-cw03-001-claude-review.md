# Review: AUTO-HARDEN-CW03-001 — Harden CW-03 Committee Board Truth Source

**Reviewer:** Claude  
**Date:** 2026-04-20  
**Decision:** APPROVED

## Acceptance Criteria Verification

### 1. CW-03 truth source 更清楚 ✅

`read_store.py` exposes a clean service-backed truth via `_consultation_session_records()`, which resolves from the `PANTHEON_BFF_CONSULTATION_SESSION_STORE` service path when available and falls back to local snapshot otherwise. The three committee methods (`list_committees`, `get_committee`, `record_sponsor_decision`) all use this source consistently. `record_sponsor_decision` writes back to the service store on success (line 6435), so sponsor decisions persist across restarts.

### 2. Committee projection 對齊 live route ✅

`_cw03_committee_projection` (main.py:1187) maps all fields required by `docs/bff/CW-03-committee-board.md`:
- Top-level detail fields: `committee_id`, `committee_ref`, `linked_request_id`, `linked_session_id`, `started_at`, `escalation_reason`, `quorum_state`, `consensus_state`, `participant_roster`, `sponsor_assignment`, `sponsor_decision`, `sponsor_decided_at`, `sponsor_decided_by`, `synthesis_summary`, `linked_evidence`
- `allowedActions.canRecordSponsorDecision`
- `meta.snapshot_at`, `meta.surfaces.committee_board`

Routes `GET /api/v1/committees` (line 5954) and `GET /api/v1/committees/{committee_id}` (line 6003) are live. The `RecordSponsorDecision` command is wired through the operator command bus at line 8425.

### 3. 測試涵蓋 sponsor decision path ✅

All 6 CW-03 contract tests pass (`test_cw03_committee_board_contract.py`):
- `test_cw03_list_contract_returns_committee_projection` — list route projection shape
- `test_cw03_detail_contract_returns_synthesis_and_allowed_actions` — detail with full synthesis and allowed actions
- `test_cw03_record_sponsor_decision_executes_and_updates_projection` — decision persists and projection reflects new state
- `test_cw03_detail_hides_record_sponsor_decision_for_reviewer_only` — role gating (reviewer cannot act)
- `test_cw03_detail_hides_record_sponsor_decision_without_sponsor_assignment` — sponsor assignment gating
- `test_cw03_record_sponsor_decision_persists_to_service_store` — durable write-back via service store

Consultation workbench overview test (`test_pkt015_consultation_workbench_contract.py`) also passes; CW-03 is correctly marked `not_ready` pending CW-02, with summary accurately stating committee routes are live but packet handoff gated on transcript truth.

## Authority Gating

`_cw03_allowed_actions` (main.py:1162) enforces all four contract conditions:
- `canRecordSponsorDecision` is `False` when surface is unavailable (early return)
- `False` unless `sponsor_decision in (None, "")` — blocks re-recording
- `False` unless `consensus_state == "sponsor_required"`
- `False` unless explicit sponsor participant is assigned (`sponsor_participant_id`)
- `False` unless operator holds `operator`, `approver`, or `admin` role

All conditions match `docs/bff/CW-03-committee-board.md` Authority Rules exactly.

## No Issues Found

The implementation is correct and complete for the stated scope.
