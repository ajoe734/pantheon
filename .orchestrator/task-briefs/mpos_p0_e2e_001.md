# Task Brief: MPOS-P0-E2E-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add minimal governed persona proposal to runtime binding E2E
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Next: Review approved: E2E test proves persona proposals are evidence-only and runtime impact requires full governed chain. Returning to Codex for final closeout.

## Summary
建立最小整合測試，證明 persona 只能提出 proposal/evidence，必須經 artifact lineage、approval、deployment plan、runtime binding、telemetry/evolution 才能形成 runtime 影響。

## Review Approval
- Reviewer: Claude2
- Approval state: review_approved
- Approval summary: E2E test proves persona proposals are evidence-only and runtime impact requires the full governed chain.

## Closeout Record
- Owner finalization: Codex
- Implementation commit: `650dc45a` (`MPOS-P0-E2E-001: add governed persona E2E`)
- Implementation PR: `#1213` merged to `dev`
- Finalization PR: `#1216`
- Verified: `python3 -m unittest services/control-plane/governance/test_paper_approval_decision.py services/control-plane/governance/test_paper_deployment_plan.py services/control-plane/governance/test_paper_runtime_binding.py services/telemetry/test_paper_telemetry_packet.py services/control-plane/governance/test_paper_evolution_decision.py services/control-plane/governance/test_persona_proposal_runtime_binding_e2e.py` (12 tests, OK)
