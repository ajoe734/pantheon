# Task Brief: MPOS-P1-ART-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Wire AllocationPolicyArtifact into registry governance and deployment path
- Status: review_approved
- Owner: Claude2
- Reviewer: Claude
- Next: Review approved: all 5 acceptance criteria satisfied, 67 tests pass, artifact_loader pool-mismatch and approval-state rejection is enforced by existing generic logic — no changes needed there. Minor lazy import style note (non-blocking). Returning to Claude2 for closeout.

## Summary
讓 optimizer 產出的 AllocationPolicyArtifact 成為 registry 可審核 artifact，帶 lineage/conflict log/pool scope/risk evidence，並可被 DeploymentPlan 引用。
