# Task Brief: AG-UIPOL-011

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Narrow responsive task parity
- Status: in_progress
- Owner: Claude
- Reviewer: Codex
- Next: Re-verified hosted evidence honestly: replaced stale/mismatched evidence with a genuine, fully-passing Playwright run pinned to execute-plans@79e0f8f3083c (deployed 20260715T054747Z, GH run 29392291433), including reproducible proof the drawer focus-trap/Escape/inert/trigger-restoration checks passed. Documented a known follow-up: a manual workflow_dispatch with ref=<SHA> broke deployment.json's sourceBranch field on the current dev HEAD (288fd70d9), which fails the hosted gate's provenance assertion (deploy-pipeline issue, not a UI regression) and blocks re-pinning to literal HEAD until a human re-triggers the deploy with ref=dev or the next ordinary dev push redeploys correctly. Handing to Codex for review.

## Summary
窄螢幕任務聚焦行為（現況 16,951px 長頁）；rows G-06/PF-07/SRV-03；繼承 006 的 shell containment。
