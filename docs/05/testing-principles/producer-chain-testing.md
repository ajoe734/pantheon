# Producer-Chain Testing — 測生產接線,不是測終端狀態

> 給所有對 Pantheon 做端到端 / 業務流程驗證的人(human 或 fleet worker)。
> 測試任務 brief 可直接引用本檔。

## TL;DR(貼牆上)

> **別測「頁面有沒有料」,要測「那條把料生出來的鏈,每一跳有沒有接通」。**
> 看到空,不准收工——先證明:鏈頭觸發了、每一跳跑了、引擎接電了。
> 尤其盯那些「**回 200 卻沒做事**」的沉默接縫(stub handler / 沒註冊的 cron / `transport=None` / 假探活 / 讀時合成)。

## 漏洞的本質

驗證者常斷言**終端狀態(名詞)**:`/bff/X` 有沒有資料、ref 解不解析、執行半圈跑了沒。
該斷言的是**生產接線(動詞)**:那個「建立人格 / 提交研究 / 觸發部署」的動作,有沒有**真的觸發**規格上那條 producer 鏈。

> 你斷言的是名詞(資料在不在),你該斷言的是動詞(動作有沒有讓鏈跑起來)。
> **Test the verb, not the noun.**

## 為什麼「讀 output」永遠抓不到這類 bug

「終端是空的」是個**歧義訊號**,至少對應四種完全不同的根因,而只讀終端狀態**無法區分**它們:

| 看到的現象 | 根因 | 性質 | 常被誤判成 |
|---|---|---|---|
| ledger 空 | A. producer 跑了但缺輸入(如沒行情) | ops / 資料 | — |
| ledger 空 | B. producer 跑了但失敗 | bug | A |
| ledger 空 | C. **producer 鏈根本沒接**(stub / cron 沒註冊 / transport=None) | **結構缺口** | A |
| ledger 空 | D. **引擎沒接電**(auth 死 / service 沒啟動 / 假探活) | **結構缺口** | A |

C 和 D 在任何「讀 output」的測試下,長得跟最無害的 A 一模一樣。於是結構性缺口被偽裝成「等資料就好」的 ops 缺口,放著爛很久。

## 怎麼測 — 三條可操作規則

### 規則 1:每個讀面,先寫出它的「生產鏈」再測
對每個 `/bff/X` / 每個頁面,逼自己回答:
> **「這格資料,規格上是**誰**、被**什麼動作**、經過**幾跳**,寫進來的?」**

寫不出這條鏈 = 你還沒資格說它「該有資料」。
鏈寫出來後,**測鏈頭的動作 + 每一跳的副作用**,不是讀鏈尾。

- ❌ 讀 `/bff/ooda/packets` 有沒有料。
- ✅ **POST 建一個 persona**,然後逐跳斷言:cron 有註冊 → OpenClaw 有被呼叫 → packet store 多一筆(且帶真實指紋)。

### 規則 2:「空」是必須被解釋的失敗,不是中性結果
**硬規則:終端為 0 時不准結案**,直到你回答完:
1. 鏈頭觸發了嗎?(動作真的發出了?)
2. 每一跳跑了嗎?(逐跳看 log / 副作用)
3. 引擎接電了嗎?(auth / cron / service / transport 的健康)

把空當 PASS 是最大的錯。空是 symptom,要追到 A/B/C/D 其中一個具體根因才算測完。

### 規則 3:專測「接縫」,尤其是沉默的接縫
跨步驟流程的 bug 幾乎都在 step N 交棒給 step N+1 的縫上。最毒的是「**回 200 但沒做事**」的縫,它通過每一個 output 測試:

- **stub handler** — `POST /personas` 回 201 但沒 wire loop。
- **沒註冊的 cron** — catalog 有定義 ≠ 有人 `register()`。
- **`transport=None` / `dry_run=True` 預設** — client 永遠不打真後端。
- **假探活** — auth/credential 死了但 `login status` / readiness 說 ok。
- **讀時合成 / fixture seed** — 資料是 read 時造的或測試夾具塞的,不是真 producer 寫的。
- **mock-only 綠燈** — 單元測試 mock 掉 transport,從沒打過真 endpoint。

**測接縫的方法 = 端到端「副作用斷言」+ 來源證明:**
- (a) 斷言真實後端**真的被打到**(看它的 log / 連線 / audit)。
- (b) 斷言資料帶**真實 producer 指紋**(trace_id、上游時間戳、source marker),不是讀時合成或夾具。

> (b) 同時就是「禁止造假」的偵測器。

## Worked example:OpenClaw assistant channel 的 404(2026-06-16)

**情境**:要讓 management assistant 與 persona OODA-loop 都透過 OpenClaw agent 跑。PR #1714「wire OpenClaw agent as assistant」merge,review 批註「107 tests green」。

**output 測試會說「通過」**:provider 存在、BFF 路由 `openclaw` provider 存在、單元測試全綠。

**生產接線測試抓到的真相**:
- provider 實際打 `POST /api/agents/main/invoke` → 上游 gateway 回 **404**,該 endpoint **不存在**。
- gateway 的 agent 協定是 **WebSocket RPC**(`ws://...:18789` + token),根本不是 REST。
- 107 個測試全綠,是因為 **HTTP transport 被 mock 掉** —— 從沒打過真 gateway(規則 3 的 mock-only 綠燈)。
- 而且 `PANTHEON_ASSISTANT_PROVIDER` 部署值仍是 `codex_cli`,根本還沒切到 openclaw(沒爆只是因為沒切)。

**對應規則**:
- 規則 1 — 沒人寫出「assistant prompt → adapter → gateway 用什麼協定」這條鏈;假設了一個不存在的 REST hop。
- 規則 3 — 典型「mock-only 綠燈」+「假探活」:測試與 readiness 都沒打真 endpoint。

**怎樣本來就會抓到**:一條「真的打 deployed gateway、拿到真實模型回覆」的 live smoke(就算 gate 在有 gateway 時才跑),`POST /api/agents/main/invoke` 立刻回 404,一翻兩瞪眼。

**更廣的教訓**:同一缺口先前讓「dev OODA loop 不閉合」被誤判 11 天 —— 看到 loop-runs/ooda-packets 空,歸因「沒 producer / rescue placeholder / 讀模型脫節」(描述對),卻停在「資料不在」,沒往上追規格本來就有的 `persona 建立 → cron 註冊 → OpenClaw → packet` 接線該存在卻沒接(根因 C)。

## Checklist(收工前自問)

- [ ] 我有沒有為這個讀面寫出完整 producer 鏈(誰、什麼動作、幾跳)?
- [ ] 我是觸發鏈頭動作來測,還是只讀鏈尾狀態?
- [ ] 終端是空的話,我能不能說出它落在 A/B/C/D 哪一個根因?
- [ ] 每個 step→step 接縫,我有沒有斷言「真的被打到」+「帶真實指紋」?
- [ ] 有沒有任何綠燈其實是 mock / dry_run / stub / 讀時合成造出來的假綠?
- [ ] 引擎本體(auth / cron 註冊 / service 啟動 / transport)我查過健康了嗎,還是只信探活?
