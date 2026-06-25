# Task Brief: AG-BE-DB-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: DashboardRecipe/WidgetSpec persistence and validator
- Status: review_approved
- Owner: Claude
- Reviewer: Claude2
- Next: Review approved: all 11 §17.5 routes implemented, ETag concurrency correct, A3 §7 critical safety rules enforced. Rules 4 and 7 deferred to follow-up. Returning to owner Claude for closeout.

## Summary
依 SD §9 與 design-closure A3(widget_registry.v1.json + widget_spec.schema.json + chart_spec.schema.json)、specs/agora/dashboard_recipe.schema.json 做 recipe/widget 持久化 + §9.6 validator:widgetType 必須在 widget_registry.v1.json、dataSource 在 allowlist、不得含 raw prompt/other-user/management-only/broker/JS-HTML;§17.5 endpoint + optimistic concurrency。前後端 registry checksum 必須一致。 【有疑問一定要提出,不要自己亂做】動工前先讀完引用的設計稿(SD 對應章節 + docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/ + canonical services/control-plane/specs/agora/*.schema.json / openapi/agora_v1.openapi.yaml / capability_manifest.json)。只要遇到任何疑問、不確定、設計稿沒寫到、與既有 code 對不上、依賴不清、無法重現或衝突,一律 STOP,用 blocker(或向 reviewer handoff)把問題具體寫出來並等待澄清,絕對不可自行臆測、補洞、繞過或先做再說。可動工的部分必須與引用 spec/schema 逐欄位一致:不得自創 schema/欄位/評分/widget/route、不得擴張 capability allowlist、不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。
