# Task Brief: ASST-SKILL-005

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add provider re-auth as device-flow skill assistant.provider.reauth
- Status: review_approved
- Owner: Codex
- Reviewer: Claude
- Next: Review approved: credential isolation, kernel gate, and device-flow field restriction are all correct. Returning to Codex for closeout.

## Summary
新增 assistant.provider.reauth skill：以 service-user CODEX_HOME 執行 codex login --device-auth，安全回傳 verification_uri/user_code，背景追蹤登入完成並重新 probe readiness。

## Closeout Evidence

- Reviewed artifact: `.orchestrator/reviews/asst_skill_005_review.md`
- Merged delivery reviewed: PR #1183 / merge commit `fabc64ae954994e9dd7f0cfb5f3614a0773c13ac`
- Focused validation:
  - `git diff --check`
  - `git diff --check origin/dev...HEAD`
  - `python3 scripts/git/check_commit_trailers.py --range origin/dev..HEAD --skip-merge`
  - `python3 -m py_compile services/openclaw-gateway-adapter/assistant_codex_provider.py services/openclaw-gateway-adapter/main.py services/openclaw-gateway-adapter/tool_workflow_bridge.py services/control-plane/bff/assistant/routes.py services/control-plane/bff/openclaw_ops_client.py`
  - `python3 -m pytest services/openclaw-gateway-adapter/tests/test_assistant_codex_provider.py -q`
  - `python3 -m pytest services/openclaw-gateway-adapter/test_main.py services/openclaw-gateway-adapter/test_tool_workflow_bridge.py -q`
  - `python3 -m pytest services/control-plane/bff/tests/test_assistant_security.py::test_provider_reauth_requires_active_kernel_debug_or_repair services/control-plane/bff/tests/test_assistant_security.py::test_provider_reauth_delegates_to_openclaw_adapter -q`
  - `python3 -m pytest services/control-plane/bff/tests/test_management_nl_assistant_provider.py::test_openclaw_client_starts_provider_reauth_device_flow services/control-plane/bff/tests/test_management_nl_assistant_provider.py::test_openclaw_client_reads_provider_reauth_status -q`
