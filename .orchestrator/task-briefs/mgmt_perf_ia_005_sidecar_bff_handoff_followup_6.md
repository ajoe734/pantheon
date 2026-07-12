# Task Brief: MGMT-PERF-IA-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-6

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Prepare MGMT-PERF-IA-005 BFF and frontend handoff packet
- Status: review
- Owner: Codex
- Reviewer: Antigravity
- Next: Chair reassigned review from Claude to Antigravity: Claude owns parent MGMT-PERF-IA-005, and this sidecar review is classifier-blocked as self-approval (Claude reviewer + automated-pipeline owner Codex on a parent-owned lane) — the same pattern already confirmed and resolved this way on sibling tasks FOLLOWUP-2/3/4/5. Antigravity must provide the required independent approval. Merged `origin/dev` into this task branch (48 commits behind, 2 ahead, no conflicts) and re-confirmed all 9 cited BFF routes still register in services/control-plane/bff/main.py after the merge (recommendations GET 44332, submit POST 42745, promotion-reviews GET 42853/42941, decisions POST 42984, formula GET 43996, rebalances GET 24395/24550, apply POST 24517 — status_code=202 fail-closed unchanged). No canonical/runtime/registry/governance file touched by this reassignment.

## Summary
平行支援 MGMT-PERF-IA-005，先整理 BFF query gap、operator journey 與前端 handoff materials，不改 canonical truth。
