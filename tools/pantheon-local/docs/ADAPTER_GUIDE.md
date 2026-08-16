# Adapter Guide

這個共享工具故意不把任何單一專案的欄位格式寫死。

## 目前預設假設

- `ai-status.json` 內有 `tasks`
- 每個 task 至少有：
  - `id`
  - `title`
  - `status`
  - `owner`
  - `reviewer`
  - `next`
  - `artifacts`
  - `depends_on`
- `current-work.md` 是人類可讀摘要
- `ai-activity-log.jsonl` 是 append-only 歷史

## 如果別的專案要重用

只要換三件事：

1. 專案的 `status/current/activity` 路徑
2. `.orchestrator/config.json` 裡的 reviewer 對應 profile
3. prompt builder 需要額外塞入的上下文

## 最佳做法

- 共享核心放在 `tools/pantheon-local/`
- 每個專案只提供自己的 adapter config
- 每台機器的桌面視窗設定只放在 Windows profiles，不跟 repo 綁死
