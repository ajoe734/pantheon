# Task Brief: LOOP-PROD-AGORA-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Implement six deferred Strategy Workshop operations
- Status: review_approved
- Owner: Codex2
- Reviewer: Claude
- Next: Independent review APPROVED: all six live operations verified against canonical adapters with mandatory readback id-match; negative tests confirmed zero canonical calls on stale-etag/wrong-tenant/missing-MFA/missing-approval; idempotent replay, failure-compensation, and no-live-capital boundaries verified in code; test counts independently reproduced matching evidence.json. Returning to owner Codex2 for closeout.

## Summary
實作 v1.5 六個目前故意 501 的 operations：GET/POST versions、select version、POST research-runs、POST consultations、POST conclude；全部走 canonical store/command 並更新 OpenAPI/bundle/compat manifest。
