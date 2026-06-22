# Task Brief: AG-E2E-SW-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Winner-branch workshop E2E acceptance
- Status: in_progress
- Owner: Codex
- Reviewer: Claude
- Next: Upstream gates verified done. Owner implementation adds the task-scoped
  winner-branch workshop E2E artifact and replaces vacuous no-order invariants
  with schema-backed assertions before handoff to Claude.

## Summary
依 SD §11/§24.3 寫 winner-branch(賣家節點)複雜描述→可驗證 StrategySpec Draft 的 E2E 驗收。 【有疑問一定要提出,不要自己亂做】動工前先讀完引用的設計稿(SD 對應章節 + docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/ + canonical services/control-plane/specs/agora/*.schema.json / openapi/agora_v1.openapi.yaml / capability_manifest.json)。只要遇到任何疑問、不確定、設計稿沒寫到、與既有 code 對不上、依賴不清、無法重現或衝突,一律 STOP,用 blocker(或向 reviewer handoff)把問題具體寫出來並等待澄清,絕對不可自行臆測、補洞、繞過或先做再說。可動工的部分必須與引用 spec/schema 逐欄位一致:不得自創 schema/欄位/評分/widget/route、不得擴張 capability allowlist、不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。

## Resolved upstream gates

- `AG-BE-SW-003`: archived `done`; completeness/NBQ skill available.
- `AG-FE-SW-002`: archived `done`; completeness rail/card contract available.
- `AG-XR-OPENAPI-004`: archived `done`; v1.3 OpenAPI/capability/schema bundle
  and hash chain merged.

## Implementation scope

- Add `services/control-plane/tests/agora/test_winner_branch_workshop_e2e.py`
  for complex winner-branch workshop evidence -> StrategySpec draft ->
  StrategyCompleteness map acceptance.
- Tighten existing v1.3 E2E/isolation acceptance tests by replacing vacuous
  no-order checks with concrete schema-backed fixtures.
