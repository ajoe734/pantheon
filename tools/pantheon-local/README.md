# Pantheon Local Automation

這個資料夾保留的是較早期的本機 helper 骨架，現在已經不再承載 Windows GUI automation。

目前這個 repo 的正式本機協調入口是：

- `.orchestrator/`
- `.llm-inbox/`
- `.github/agents/`

也就是現在的方向是：

- `WSL` 內 watcher / supervisor / approval broker
- `Claude CLI`、`Copilot CLI`、`Gemini CLI`、`Codex CLI` 之類正式入口
- 共享檔案作為 source of truth
- 不再維護視窗切換、clipboard、SendKeys 這條 Windows agent 路線

如果你要重用目前可運作的版本，請直接看：

- `docs/agent-orchestrator.md`
- `docs/provider-capabilities.md`
- `docs/provider-permissions.md`

這個資料夾暫時只保留早期 watcher / queue schema 歷史參考，Windows runner 已移除。
