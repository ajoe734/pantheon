# Task Brief: EVOLOOP-006

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Promote pipeline: registry to LEAN binding
- Status: review_pending
- Owner: Codex2
- Reviewer: Claude
- Next: Claude reviews implementation PR `#3629`, live evidence, exact rollback,
  and final active binding; owner then performs formal `done` closeout.

## Summary
跑通 promote 管線:registry artifact → deployment plan → 以管線(非手動改 store)替換一個 rescue 佔位 binding 成 pipeline-managed binding。遵守 RuntimeBinding 契約(runtime_id 必須等於容器 PANTHEON_RUNTIME_ID;參照 paper-binding-rescue runbook)。rollback 路徑要文件化並實測(re-bind 前一個 artifact)。

## Delivery

- Implementation PR: `#3629`
- Implementation merge: `1e9882f2a7ff08be51a0f93a2c647b818137fd2b`
- Live dev sequence: promote `rb-9d952e...` -> rollback `rb-1e1182...`
  -> final promote `rb-f13ece...`
- Final plan: `plan-evoloop-006-promote-20260714b` (`executed`)
- Final runtime: `runtime-tw-equity-paper`; worker `/readyz` and process
  `PANTHEON_RUNTIME_ID` matched
- Evidence:
  `docs/bff/execution-tasks/2026-07-14-evolution-generative-loop-gap/EVOLOOP-006-live-evidence.json`
