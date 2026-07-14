# Task Brief: EVOLOOP-006

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Promote pipeline: registry to LEAN binding
- Status: review
- Owner: Codex2
- Reviewer: Claude
- Next: Review complete: PR #3629 (implementation) and PR #3633 (live dev evidence) both examined in full, including a deep sub-agent pass over the new same-stage 'replace' transition and the loosened rollback-target check in deployment_plan.py. All 4 acceptance criteria verified met: service-APIs-only promote of rescue binding rb-abb82fd -> rb-9d952e, runtime_id runtime-tw-equity-paper matched across RuntimeBinding/fleet/readyz/proc-environ at every stage, exact rollback to artifact-tw-equity-session-v1@1.0.0 (rb-1e1182) then re-promote to rb-f13ece, and no direct store edits (invariants + code confirm). Fail-closed behavior, replay/idempotency guards, and secret handling all checked out; one non-blocking note that the runtime-manager cutover bypass has a brief double-active-binding window auto-recovered by the replay matcher, worth revisiting before canary/live. approve was denied by the auto-mode classifier as self-approval (same automated-worker system authored the code and drove this review) — needs a human to run 'ai_status.py approve EVOLOOP-006' before owner Codex2 can do the formal done closeout.

## Summary
跑通 promote 管線:registry artifact → deployment plan → 以管線(非手動改 store)替換一個 rescue 佔位 binding 成 pipeline-managed binding。遵守 RuntimeBinding 契約(runtime_id 必須等於容器 PANTHEON_RUNTIME_ID;參照 paper-binding-rescue runbook)。rollback 路徑要文件化並實測(re-bind 前一個 artifact)。
