# Task Brief: OCLAW-PMEM-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: OpenClaw persona agent reconciliation
- Status: review
- Owner: Codex
- Reviewer: Claude
- Next: Reviewer verification complete: PR #3288 (evidence-only, no runtime code changes) + underlying PR #3003 implementation (commit 4ebd260a5, merged 875f770f0) independently re-checked. Re-ran 'pytest integrations/openclaw/test_persona_agent_sync.py services/control-plane/bff/test_bff_strategy_persona_contract.py -q' -> 37 passed; py_compile clean on persona_agent_sync.py/openclaw-sync-persona-agents.py/bff main.py. All 4 acceptance criteria map to passing tests (repair_action drift handling, SOUL Memory-section parity, agent create/update). Live model=openclaw/{persona_id} evidence has honest dispatch/operator provenance disclosure, not fabricated. Verdict: approvable, but AI_NAME=Claude ai-status.sh approve was denied by the auto-mode classifier as self-approval (Codex-owner/Claude-reviewer distinct-lane pattern is still flagged). Formal review_approved transition needs a human or a different reviewer identity to run the approve command; see support/reviews/OCLAW-PMEM-002-review-claude.md (uncommitted, local) for the full writeup.

## Summary
把 general persona create/update 接到 shared OpenClaw reconciler；既有 agent 要能同步 identity/workspace/model/SOUL，並消除 deploy script 與 library 的 SOUL drift。
