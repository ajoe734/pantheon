# Task Brief: LOOP-PROD-REC-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Full-stack loop recovery and fault-injection harness
- Status: review_approved
- Owner: Antigravity
- Reviewer: Claude
- Next: Round 3 approve: all three round-2 findings (fabricated record_log, premature AC-05, hardcoded SHAs) fixed in 97009d4e7/913eaa2e1/c5010050b. Independently verified with a disposable postgres:16-alpine container: pytest scripts/test_loop_product_recovery_matrix.py + services/loop-control/test_loop_control.py (28 passed, 1 skipped) and services/control-plane/bff/test_loop_health_read_model_contract.py (11 passed). evidence.sha256 checksum matches. PR #3586 comment posted.

## Summary
建立可重複的 target-dev recovery harness，在 outbox、downstream mutation、receipt、projection 各切點注入故障，並驗證 duplicate、lease expiry、timeout、worker/BFF/DB/full-stack restart。
