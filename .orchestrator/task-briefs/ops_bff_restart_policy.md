# Task Brief: OPS-BFF-RESTART-POLICY

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: operator-bff + critical services: restart unless-stopped (auto-recover)
- Status: review_approved
- Owner: Claude2
- Reviewer: Claude
- Next: Supervisor resumed OPS-BFF-RESTART-POLICY for finalize after successful dispatch.

## Summary
operator-bff(及其他關鍵 control-plane 服務)在 docker-compose 的 restart policy 是 `no`,一崩就永久躺平(2026-06-15 502 約16分直到手動重啟)。改成 restart: unless-stopped(或 on-failure),讓暫態崩潰自癒。
