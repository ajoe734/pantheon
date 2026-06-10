# Task Brief: MPOS-P1-E2E-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Run approved AllocationPolicyArtifact through paper LEAN loop
- Status: review
- Owner: Claude
- Reviewer: Codex
- Next: Gap G1 closure complete. Focused task suite passes with 49 passed / 7 skipped across registry, governance, DeploymentPlan paper-run, and allocation-policy paper-run tests. Broader LEAN smoke review suite passes with 49 passed / 9 skipped after adding the same _lean_submodule_available() skip guard to services/execution/lean_runtime/test_algorithm_smoke.py. Acceptance criteria verified: AllocationPolicyArtifact->registry->ApprovalDecision->DeploymentPlan(artifact_type=allocation_policy)->RuntimeBinding(sponsor_persona+persona_capital_binding)->fills/telemetry->lineage by all 5 dimensions. LEAN paper-run tests correctly skip pending submodule init. Commits: 676915bf (anchor), 14204c76 (deployment-plan skip fix), pending reviewer guard commit. Ready for review approval.

## Summary
把已核准 AllocationPolicyArtifact 實際接到 DeploymentPlan、RuntimeBinding、paper LEAN、fills/telemetry 與 lineage 查詢。
