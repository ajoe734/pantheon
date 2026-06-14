# Task Brief: OPS-CODEX-AUTH-PROBE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add codex auth-ready probe + sticky revoked-token gate
- Status: todo
- Owner: Codex
- Reviewer: Claude2
- Next: Auto-reassigned ownership from Claude to Codex after repeated Claude terminal: {"type":"assistant","message":{"id":"262e3ab3-8b29-4829-8a08-50e2c58c39ff","container":null,"model":"<synthetic>","role":"assistant","stop_details":null,"stop_reason":"stop_sequenc. Task returned to todo until Codex starts a fresh run.

## Summary
根因修復:codex/codex2 派工時沒有 auth 探測,token 被撤仍能搶單(只在 worker 跑下去才爆 401)。① 在 .orchestrator/provider_permissions.py 新增 codex_auth_ready(provider_id,env) 真探測(不可用會謊報的 codex login status;改看 auth.json token 過期/refresh 結果或極輕量真實呼叫,401/revoked 判 False),並在 codex_provider_report 寫入 auth_ready。② supervisor 的 reconcile_provider_auth_recovery:把 refresh-token-revoked 當 sticky 終局狀態,auth pause 只能由一次成功的真探測解除,不要 900s 計時器自動放行。
