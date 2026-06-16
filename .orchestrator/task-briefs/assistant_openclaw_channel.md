# Task Brief: ASSISTANT-OPENCLAW-CHANNEL

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Wire pantheon-assistant as an OpenClaw channel (route Management AI through OpenClaw agent, not codex CLI)
- Status: review_approved
- Owner: Claude
- Reviewer: Claude2
- Next: Supervisor resumed ASSISTANT-OPENCLAW-CHANNEL for finalize after successful dispatch.

## Summary
現況:Management AI 經 openclaw_ops_client.invoke_assistant_provider 打 adapter /api/openclaw-adapter/assistant/providers/codex/invoke 跑 codex CLI,沒走 OpenClaw agent。目標:assistant 變成 OpenClaw 前端渠道——訊息送進 OpenClaw gateway agent(工具/記憶/人格),回應帶回。接點:(1)openclaw-gateway-adapter 新增 provider 'openclaw',/assistant/providers/openclaw/invoke 改走 OpenClaw gateway agent(參考 services/control-plane/cron/openclaw_client.py 的 WS client,OPENCLAW_GATEWAY_URL=ws://openclaw-gateway:18789,agent=main)。(2)BFF openclaw_ops_client 預設 provider 由 codex 改為可設定 env PANTHEON_ASSISTANT_PROVIDER default openclaw,保留 codex/claude fallback。驗收:Management AI 回應經 OpenClaw agent(provider=openclaw)、adapter readiness openclaw=ready、live 對話實測非空、contract test 綠。OpenClaw model auth 已修(~/.openclaw/.env OPENAI_API_KEY,probe ok)。禁止造假。
