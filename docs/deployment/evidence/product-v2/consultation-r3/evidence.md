# Task Evidence Manifest: PRODUCT-V2-CONSULTATION-R3-20260813

- Task ID: `PRODUCT-V2-CONSULTATION-R3-20260813`
- Title: Deliver governed consultation intake and terminal memo
- Owner: Antigravity2
- Reviewer: Antigravity
- Status: review_approved -> done finalization

## Summary of Delivered Functionality

1. **Production Consultation Intake Boundary**:
   - Added `POST /api/consult/intake/policy-learning-candidate` in `services/consultation/main.py`.
   - Added `intake_policy_learning_candidate` helper method to `ConsultationServiceClient` in `services/consultation/client.py`.
   - Validates that candidates land in terminal `processed` status before intake. Non-terminal candidate states (e.g. `proposed`, `claimed`, `in_progress`) are strictly rejected.

2. **DatasetVersion & Candidate Lineage Persistence**:
   - Creates and persists `ConsultRequest` records tied directly to `dataset_version_id` and full `dataset_lineage`.
   - Maintains strict lineage tracking from the original policy-learning candidate through consultation.

3. **Terminal Memo & Governance Proposal Generation**:
   - Automatically generates and publishes a `ConsultMemo` (`MemoStatus.PUBLISHED`) with evaluation findings (`action_match_rate`, `return_gap`, checksum) and explicit recommendation (`approve`, `approve_with_conditions`, or `reject`).
   - Maps committee decisions through `sponsor_decision_bridge.py` to produce deterministic `ApprovalDecisionProposal` objects with explicit sponsor persona identity and risk disposition.

4. **Replay & Duplicate Intake Protection**:
   - Enforces strict idempotency based on `candidate_id` and tenant scope.
   - If an intake request is replayed for an already-consumed candidate, the system returns the existing `ConsultRequest`, `ConsultMemo`, and proposal metadata (`replayed: True`) without creating duplicate decisions or records.

## Verification

Executed test suite:
```bash
PANTHEON_PY="$(python3 scripts/dev/provision_python_distribution.py --print-python)"
"$PANTHEON_PY" -m pytest services/consultation -v
```

Test Results:
- `services/consultation/test_policy_learning_intake.py`: All intake, lineage, memo, proposal, replay, and rejection tests passed.
- `services/consultation/`: 60/60 tests passed cleanly.
