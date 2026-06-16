# Task Brief: OPS-DEV-DEPLOY-FLEET-DISRUPTION

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Dev deploy kills paper-runtime fleet (exit137) — scope deploys / auto-recover
- Status: todo
- Owner: Claude
- Reviewer: Claude2
- Next: Assignment created

## Summary
2026-06-16 在 dev VM 跑 nonprod-deploy(environment=dev, component=auto) 重建 root stack 時,全部 15 個 per-binding paper-runtime 容器被 exit 137 殺掉(整個 paper 艦隊停擺),且多個服務(telemetry/persona/router/evolution/incidents/...)留在 Created 沒啟動;需手動逐一 docker start 才恢復。請查明 137 是 OOM(VM 記憶體壓力)還是 compose recreate 把它們 SIGKILL 後 abort,並根治:擇一或組合 (a)dev 例行 BFF 部署改為只重建 operator-bff(別動 paper 艦隊與其他服務);(b)加 swap/記憶體 headroom;(c)讓 runtime-manager/paper-fleet-reconciler 在 runtime 被殺後自動 reconcile 重啟(目前不會)。背景:這次是手動觸發 BFF redeploy 想把永久修復套到運行容器時發生。
