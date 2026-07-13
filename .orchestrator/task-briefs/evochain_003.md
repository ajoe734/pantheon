# Task Brief: EVOCHAIN-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Postmortem publisher on incident resolution
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex
- Next: Review changes required: (1) control/prod evolution defaults to JSON while incidents/postmortems use Postgres, so the ID-only published endpoint cannot find canonical records; wire a deployable owner-safe canonical read/link path and add rendered-compose plus cross-service coverage. (2) Replace post-write in-memory retries with the canonical durable outbox/inbox or equivalent replayable delivery state; current crash/final-failure path can permanently lose resolved/published events. (3) Harden published-event dedupe: do not accept an arbitrary postmortem backlink or caller decision_id unless bridge key, target, cluster, and linked postmortem match; add unrelated-decision regression tests. (4) Surface bridge precondition failures instead of returning 200, and update the task artifact/acceptance evidence to match the final endpoint and bridge contract. Focused unit checks pass, but they mock the cross-service hop and do not prove control/prod delivery.

## Summary
補上 postmortem 事件鏈缺的呼叫端：incident resolve/close 時產生 postmortem record，經 services/evolution/postmortem_bridge.on_postmortem_published 轉成 proposal，並經 POST /api/evolution/proposals 入庫。bridge 本身保持純函式不動。
