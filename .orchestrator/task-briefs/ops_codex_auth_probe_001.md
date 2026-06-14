# Task Brief: OPS-CODEX-AUTH-PROBE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add codex auth-ready probe + sticky revoked-token gate
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Next: Review approved: auth probe logic correct, sticky revoke gate verified, all 9 new tests pass, no regressions from task changes. Returning to Codex for closeout.

## Summary
根因修復:codex/codex2 派工時沒有 auth 探測,token 被撤仍能搶單(只在 worker 跑下去才爆 401)。① 在 .orchestrator/provider_permissions.py 新增 codex_auth_ready(provider_id,env) 真探測(不可用會謊報的 codex login status;改看 auth.json token 過期/refresh 結果或極輕量真實呼叫,401/revoked 判 False),並在 codex_provider_report 寫入 auth_ready。② supervisor 的 reconcile_provider_auth_recovery:把 refresh-token-revoked 當 sticky 終局狀態,auth pause 只能由一次成功的真探測解除,不要 900s 計時器自動放行。

## Owner closeout evidence
- 2026-06-14T17:49:03Z: PR #1586 merged into dev at `920bfdee2916fd45e533c52a2ba9d1870c08ae98`; required GitHub checks passed.
- Re-read reviewer approval from central `ai-status.json`: Claude2 approved auth probe logic, sticky revoked-token gate, and regression coverage.
- Verification:
  - `python3 -m py_compile .orchestrator/provider_permissions.py .orchestrator/supervisor.py .orchestrator/test_provider_permissions.py .orchestrator/test_supervisor.py`
  - `python3 .orchestrator/test_provider_permissions.py ProviderPermissionsTest.test_codex_auth_ready_false_on_revoked_refresh_token ProviderPermissionsTest.test_codex_probe_ready_rejects_login_status_output ProviderPermissionsTest.test_provider_capabilities_marks_codex_revoked_token_auth_down`
  - `python3 .orchestrator/test_supervisor.py DetectWorkerFailureTests.test_mark_revoked_auth_pause_is_sticky_until_probe DetectWorkerFailureTests.test_expire_provider_dispatch_pauses_keeps_revoked_auth_pause DetectWorkerFailureTests.test_sticky_revoked_auth_recovery_requires_live_probe_success WorkerOsDuplicateGuardTests.test_block_reason_blocks_auth_down_provider`
