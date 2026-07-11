# Task Brief: MGMT-PERF-IA-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-5

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Prepare MGMT-PERF-IA-005 BFF and frontend handoff packet
- Status: review
- Owner: Codex2
- Reviewer: Claude
- Next: Independent reviewer verification complete. PR #3283 (commit `287b69bc4`) added only `support/sidecars/MGMT-PERF-IA-005/MGMT-PERF-IA-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md` and is already merged into `dev` (mergedAt 2026-07-11T22:22:48Z); `git show --stat 287b69bc4` confirms no canonical/runtime/registry/governance/frontend file changed. Cross-checked every cited route in `services/control-plane/bff/main.py`: `GET .../quarterly-ranking/recommendations` (44208), `POST .../recommendations/{id}/submit` (42621), `GET /bff/management/promotion-reviews` (42729), `GET .../promotion-reviews/{id}` (42817), `POST .../decisions` (42860), `GET .../quarterly-ranking/formula` (43872), `GET /bff/rebalances` (24393), `GET /bff/rebalances/{id}` (24548), `POST /bff/rebalances/{id}/apply` (24515) — all present and consistent with the packet's described behavior. The non-absorbable capital-join gap claim also holds: neither the recommendation nor promotion-review projections publish a durable rebalance/proposal/receipt link. Content is independently fact-checked, accurate, and stays within the support-artifact boundary (no wire contract defined, no capital mutation authorized, no parent-task approval implied). However, formal `approve`/`review_approved` for this task is expected to be classifier-blocked as self-approval for the Claude-reviewer + Codex2-owner sidecar lane on parent MGMT-PERF-IA-005 — this exact pattern was confirmed repeatedly on sibling tasks FOLLOWUP-2, FOLLOWUP-3, and FOLLOWUP-4 (see `.orchestrator/reviews/MGMT-PERF-IA-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-4-review-antigravity.md`). Recommend the same fix: reassign this task's reviewer to a non-Claude identity (e.g. Antigravity) so an independent `approve` can be issued. The packet content itself is already merged into `dev`, so no further code change is needed — only the review-gate closeout is pending.

## Summary
平行支援 MGMT-PERF-IA-005，先整理 BFF query gap、operator journey 與前端 handoff materials，不改 canonical truth。
