# SA/SD：一次性 worker 必須收齊驗證結果再結束

日期：2026-09-05。Task：OPS-WORKER-FOREGROUND-VALIDATION-PREREQUISITE-001。
本文件簽章後不可修改；新發現另記交接報告，不回寫已簽內容。

## SA：重新盤點與根因

這是 development tooling prerequisite，不是產品 runtime 或 hosted 工作。
2026-09-05 04:25:20 與 04:37:35 UTC，BFF-TEST-ARCH-001 與 Python
prerequisite 的 Claude owner 都正常結束一次性 CLI：result success、
stop_reason=end_turn、runner exit 0 / signal null；但背景驗證尚未收齊。
後續 log 記錄 background handle bz3j22zqm / b5nzmfkf4 被終止；無
TaskOutput 調用。BFF sweep 只有三個完整文件結果，第四個只有開始；
不能宣稱整批 79 files 已執行。Python 該次背景 suite 沒有完整終局結果。

這不是已證明的 lease timeout。既有 adapter 用 claude -p stream-json
執行一次性 session；CLI 本身退出會清理背景工作，worker_runner 也會在
直接子程序結束後清理殘留 process group。兩層都符合證據，不能把每一次
kill 精確歸因給其中一層。Supervisor 隨後看到 worker_process_missing，
因未收到 canonical handoff 才 recovery；exit 0 不是任務完成。

取證 runtime：20282eba2ce2304560ab7eab0cd27af824a22b8b。
既有實作 owner：adapters/claude_cli.py 的 _spawn_env 與 deliver；
templates/wakeup.txt，由 watch_events.render_wakeup_message 渲染；
worker_runner.py:904–923 保留必要清理。當前 claude／claude2 provider
runtime.env 未設定 CLAUDE_CODE_DISABLE_BACKGROUND_TASKS；未檢查繼承
程序環境，故不宣稱其環境值必定缺失。

官方資料（2026-09-05 已查證）：

- [Environment variables](https://code.claude.com/docs/en/env-vars)：
  CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1 禁止 Bash／subagent 背景參數、
  自動背景化與 Ctrl+B。會同時限制背景 subagent，非僅 Bash。
- [Background Bash commands](https://code.claude.com/docs/en/interactive-mode#background-bash-commands)：
  CLI exit 會清理背景程序，包含部分 detached descendants。

已安裝版本 2.1.260 的 binary 可找到該環境設定名稱。不可新增自製輪詢
daemon、第二個 launcher、watchdog、cron、lease 延長器來掩蓋問題。

## 相依與不重複開發

Authoritative observational snapshot event 2245 / task-state head
1e9cf4536846e016d52d4bd99b025a1b6be9678f9a8f09bdceea24c71b124f1e
包含 17 個 nonterminal tasks；逐項 exact／glob／prefix artifact 與 scoped
描述檢查未發現前四個核心 artifacts 的 active owner。
scripts/test_provision_live_supervisor_config.py 尚屬 Python prerequisite。
因此本任務 dependsOn OPS-SUPERVISOR-PYTHON-RUNTIME-PREREQUISITE-001，
待其正常封存後才開始；不得追加到已獨立審查的 PR #5599。
本任務不修改 BFF-TEST-ARCH 的測試或其產品缺口。

## SD：沿用單一政策入口

1. 在既有 wakeup.txt 增加 provider-neutral 一次性工作收尾要求：
   使用 bounded foreground batches；若工具回傳背景 handle，必須在
   final/handoff 前收集 terminal output 和 exit status；Claude 用
   TaskOutput(block=true) 等既有工具。不得 nohup／& 放走需驗證的命令，
   不得以「等待通知」當 continuation。長工作可分批 anchor／正式 handoff
   或 genuine blocker；不得把 collection／timeout／killed 計為 passed。
2. 在既有 .orchestrator/config.json 的 claude／claude2 runtime.env 設定
   CLAUDE_CODE_DISABLE_BACKGROUND_TASKS="1"，只適用 supervisor 的一次性
   CLI delivery；保留兩個身份、account、config dir、auth 與 quota 規則。
   不更改 interactive 使用者 shell、其他 providers 或產品設定。
3. 使用 _spawn_env 已有的 runtime.env 傳遞，不增加 adapter 硬編碼第二個
   policy owner。build_live_config 已以 repo config 為單一來源；驗證其
   projection 保留設定，不把 live config 再當第二個政策 overlay。
4. 這個環境設定不能攔截任意原始 shell backgrounding，故醒喚指令與
   真實 terminal evidence 仍必要。不得宣稱全面靜態禁止所有 & 命令。

## 精確 artifact contract

- .orchestrator/templates/wakeup.txt
- .orchestrator/config.json
- .orchestrator/test_watch_events.py
- .orchestrator/test_adapter_delivery_policy.py
- scripts/test_provision_live_supervisor_config.py
- docs/operations/worker-validation-completion.md
- docs/deployment/evidence/OPS-WORKER-FOREGROUND-VALIDATION-PREREQUISITE-001/evidence.json

Implementation files watch_events.py／adapters/claude_cli.py／provision helper
是讀取參考，預期不需改。若真正必須更改，先 checkpoint、owner-authenticated
blocker，再透過 Human/Ops 正式擴充 exact artifact；不得自行擴 scope。

## 驗收與交付

- 真實 wakeup renderer 的 owner/reviewer/closeout 相關渲染保留 task 身份、
  canonical runtime 指令及新的 foreground/terminal-result 要求；不用另一份
  copied template 充當測試對象。
- 實際 repository provider config 的兩個 Claude entries 都為 "1"。
  Mock spawn 測試驗證兩個 provider 的 child env 經既有 adapter 收到它；
  不能只在 test fixture 硬塞 env 然後宣稱 repository policy 已傳遞。
  Configured policy 要覆蓋 contradictory ambient 值；不洩漏 secret，不啟動
  paid provider call。其他 provider identity/env 正常，既有 tests 不退化。
- Rendered live config 保留 candidate 的設定；incumbent overlay 不得反蓋。
  不在 test 中另寫 renderer／fallback policy。
- 跑 focused wakeup、adapter、provision tests 及既有 worker cleanup tests；
  清理語意、lease authority、exit0 不等同 done 的既有邊界保持不變。
  列明 exact head、commands、exit codes、passed/skipped/failed/timeout；
  收齊 terminal outputs，不拿啟動/收集數替代測試執行。
- Clean task branch、current dev rebase、required trailers、push、PR、
  independent canonical review、required CI、existing integrator merge/archive。
  禁止手改 canonical JSON 或自己批准自己；不直接 merge／promote 本任務。
- Source delivery 與 live policy 生效分列。Merge 後由既有 exact-runtime
  promotion 流程投影設定；promotion 前通過 preflight，保留 journal、leases、
  source/integration roots。下一次真實 one-shot dispatch 才可提供 live
  spawned-env／bounded terminal-result 證據，不碰現有 CLI 程序環境。

## 回退

若 policy 造成不相容，以正常 task/PR 明確 revert 此 provider 設定與相關
指令，經既有 runtime promotion 生效；不建立第二條 delivery 路徑。
既有安全清理不得停用。此任務本身不操作 live supervisor、cron 或 hosted。
