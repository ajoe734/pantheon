# Task Brief: EVOCHAIN-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Enable evolution daily sweep on dev
- Status: review_approved
- Owner: Codex
- Reviewer: Claude
- Next: Review approved: PR #3516 verified — default activation, proposal-only safety, disable procedure, and proof-gate coverage all confirmed

## Summary
解除（或以 committed override 取代）docker-compose.yml 中 evolution-daily-sweep-scheduler 的 profiles gate，讓 dev `docker compose up -d` 預設啟動 daily sweep。用 scheduler tick log 以及既有 open seed incident 被掃成 decision proposal 作為證據。interval 沿用既有 env 預設，不改 cadence 設計。
