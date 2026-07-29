# Task Brief: AG-GOV-WORKSHOP-COMPAT-DEPLOY-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate and deploy the repaired Governance–Workshop exact pair
- Status: in_progress
- Owner: Codex
- Reviewer: Claude2
- Next: Submit the complete deployment and hosted restart-persistence evidence
  for Claude2 review, then perform owner closeout after `review_approved`.

## Summary
在 Governance–Workshop 修復合併後，更新 accepted backend runtime pair、驗證既有 contract/handoff hashes 是否仍適用，透過 dev workflow strict gate 部署，並以公開 API 證明修復與 restart persistence。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Implementation record

- Accepted exact pair:
  - backend `f71c1f8ba889ba64956006ef0f9159840be6d065`
  - frontend `e4399e3ec68f882ace35d0349e6597cdd101525f`
- Accepted public frontend deployment:
  - integration gate `30003411349`
  - deploy `30067684910`
  - profile `read-only`
  - live/strict fallback with real and stub writes disabled
- Accepted strict BFF deployment and restart:
  - initial deploy `30065241892`
  - post-probe restart `30068077516`
- Hosted product proof:
  - canonical `strategy_workshop` approval created, reviewed, and approved
  - strategy and both Registry identities remained distinct and correctly linked
  - research remained `handoff_only`
  - Workshop concluded without execution or capital authority
  - all durable resources read back after governed BFF restart
- Evidence:
  - `docs/deployment/evidence/agora/ag-gov-workshop-compat-deploy-001.md`
  - `docs/deployment/evidence/agora/ag-gov-workshop-compat-deploy-001/qualification-20260724T045953Z.json`
