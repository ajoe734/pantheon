# Task Brief: MGMT-PERF-IA-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-5

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Prepare MGMT-PERF-IA-005 BFF and frontend handoff packet
- Status: review
- Owner: Codex2
- Reviewer: Claude
- Next: Re-verified (new session, 2026-07-11): PR #3283 (`287b69bc4`, packet) and PR #3284 (`1271e79ef`, review note) are both merged into `dev`; merge commit `94af8d7df` (origin/dev into this task branch) brought no changes to the sidecar packet or `services/control-plane/bff/main.py`. Re-confirmed all 9 cited BFF routes remain registered at the same lines: `GET .../quarterly-ranking/recommendations` (44208), `POST .../recommendations/{id}/submit` (42621), `GET /bff/management/promotion-reviews` (42729), `GET .../promotion-reviews/{id}` (42817), `POST .../decisions` (42860), `GET .../quarterly-ranking/formula` (43872), `GET /bff/rebalances` (24393), `GET /bff/rebalances/{id}` (24548), `POST /bff/rebalances/{id}/apply` (24515). No canonical/runtime/registry/governance/frontend file changed. Formal `approve`/`review_approved` remains expected to be classifier-blocked as self-approval for the Claude-reviewer + Codex2-owner sidecar lane on parent MGMT-PERF-IA-005 — same pattern confirmed on FOLLOWUP-2/3/4 (see `.orchestrator/reviews/MGMT-PERF-IA-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-review-antigravity.md`, where an Antigravity session picked up the reassigned reviewer role and approved). Recommend the same fix: reassign this task's reviewer to a non-Claude identity (e.g. Antigravity) so an independent `approve` can be issued. Packet content is already merged into `dev`; no further code change is needed — only the review-gate closeout is pending.

## Summary
平行支援 MGMT-PERF-IA-005，先整理 BFF query gap、operator journey 與前端 handoff materials，不改 canonical truth。
