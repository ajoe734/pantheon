# Task Brief: MPOS-P1-RISK-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add homogeneity and correlation review to allocation gate
- Status: review_approved
- Owner: Codex
- Reviewer: Claude
- Next: Review approved: homogeneity/correlation gate meets all acceptance criteria; risk veto outranks committee; 21 tests pass. Returned to Codex for finalization.

## Summary
在 pre-LEAN allocation gate 補 homogeneity/correlation review，避免多個 persona 同時堆疊高度相關或重複 exposure。

## Owner Closeout
- Implementation PR: #1261, merged to `dev` at `ec3c4682bb49f23bcbdecfee2fb5b7f8900a8b9e`.
- Implementation commit: `900259235d536a3353c253447a99cf4091515375`.
- Reviewer approval: `.orchestrator/task-briefs/mpos_p1_risk_002_review.md`.
- Final verification: `python3 -m pytest services/optimizer-svc/test_allocation_conflict_classifier.py services/optimizer-svc/test_portfolio_synthesis.py services/capital/test_risk_policy.py -v` (21 passed).
- Scope remains implementation-level; no canonical architecture docs were changed.
