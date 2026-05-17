# Wave Cadence 調整提案

> 文件版本：v1.0
> 日期：2026-05-17
> 作者：Claude (主席輪值)
> 性質：operational proposal (L2)，不動 L1 canonical
> 目標讀者：人類 operator + chair 輪值 + supervisor/orchestrator 設定維護者

---

## 1. 觀察到的問題

依 `docs/conventions/GIT_WORKFLOW.md` § 2，wave cadence 設計為：

```
Mon 09:00 open · Fri 12:00 freeze · Fri 17:00 close
ISO week-aligned, id = <YYYY>-W<NN>
```

但實際在 2026-05-17 觀察到的 wave history（節錄自 `current-work.md`）：

```
2026-05-17 13:23:05  open   wave/2026-W21  by Codex
2026-05-17 13:42:47  close  wave/2026-W21  by Codex
2026-05-17 13:46:59  open   wave/2026-W22  by Codex
2026-05-17 13:47:39  close  wave/2026-W22  by Codex
2026-05-17 15:02:39  open   wave/2026-W25  by Codex
```

問題清單：

1. **W21 只開了 19 分鐘就 close**，W22 只開 40 秒，違反「Mon open / Fri close」一週節奏
2. **跳號**：W23 / W24 被略過，從 W22 直接跳到 W25
3. **同一個 actor Codex** 在短時間連續 open/close 多個 wave，違反「chair 輪值才能 open wave」的設計
4. **沒有 freeze 階段** — 所有 wave 都是 `open` 直接 `close`，沒經過 Fri 12 freeze 的「不接新 task」窗口

## 2. 推測原因

- W21/W22 可能是 migration cutover 期間自動 fire 的測試 cycle（W21 13:23 開 = wave-migration 完成幾分鐘後）
- 跳號到 W25 是因為 wave id 想對齊「真實當週」 — 但 ISO week 計算可能出錯
- 缺乏 freeze 階段顯示自動 close 邏輯目前沒實作

## 3. 三個調整選項

### 選項 A：嚴格周節奏（保守、最貼近原設計）

```
Mon 09:00 open  →  Fri 12:00 freeze (停接新 task)  →  Fri 17:00 close  →  切 publish/v<YYYY>.<NN>.0
```

- 強制 wave 至少 4 個工作日
- chair 在 freeze 後只審查、不收新 task
- 適合：人類想看清楚的、有 daily review 節奏的、release 規律性高的場景

### 選項 B：彈性短周期（適合 AI 加速）

```
24 hour cycle: 任何 chair 在 idle 時可 close 並 open 新 wave
```

- 不嚴守周界，依工作量啟動
- 一天可能 1 個 wave，也可能 2 天 1 個
- 仍要過 freeze（即使只有 30 分鐘）
- 適合：AI worker dispatch 比人類快很多的情況；現況 21 個 task 7 小時做完，1 週節奏太長

### 選項 C：雙軌（推薦）

```
人類視角：以 ISO 週為單位看 publish/v<YYYY>.<NN>.0 release branch
AI dispatch 視角：以 「task 完成 batch」為單位 cycle wave
   wave-close 條件 = (所有 todo 都 done OR 過了 ISO 週末 17:00)
   wave-open 條件 = 上一個 wave 已 close 至少 60 分鐘
```

- ISO 週對齊發佈，但內部 cycle 跟著工作速度走
- 強制 60 分鐘 cool-down 防止亂跳號（W21 → W22 那種 40 秒 cycle）
- 跳號禁止：必須連續，W22 close 後下一個必為 W23
- 適合：當前 AI 速度 + 人類審查的混合節奏

## 4. 建議

**採用選項 C**，並補三條規則：

1. **wave-open 守門**：`scripts/ai-status.sh wave open` 拒絕跳號（W22 後不能直接開 W25）
2. **freeze 階段強制**：close 前必須先 freeze 至少 30 分鐘，凍結期間不接新 task assignment
3. **chair 輪值記錄**：wave open/close 的 actor 必須跟當前 chair-review 的 baton owner 一致；非 baton owner 強行 open 拒絕

## 5. 落地工作切片

如果採用，可拆成 3 個 task 派工：

| Task | 內容 |
|---|---|
| OPS-WAVE-001 | 在 `scripts/ai_status.py command_wave` 加入跳號 / cool-down / chair-owner 三項守門 |
| OPS-WAVE-002 | 補實 freeze 階段：wave 狀態加 `frozen` 並在該狀態下拒絕 `assign` 命令 |
| OPS-WAVE-003 | chair-review skill 加一段 wave-health check：每次 review 看 wave_state.history 是否有違規 cycle |

每條都是純 `.orchestrator/` + `scripts/` 修改，互不衝突，可平行。

## 6. 不採用的代價

如果繼續現況（W21/W22 1 分鐘 cycle、跳號），會發生：

- chair-review 報告失真（每天可能跨多個 wave，retrospective 切不出來）
- publish/v* tag 失序（W21 / W22 / W25 但沒 W23 / W24，下游消費者疑惑）
- audit trace 斷裂（task 跨 3 個 wave 但只有 30 分鐘，evidence chain 難重組）
- chair 輪值機制被繞過（任何 actor 都能 wave open/close）

## 7. 不影響範圍

- 不動 L1 canonical
- 不動 task lifecycle（todo → in_progress → review → review_approved → done 不變）
- 不動 capability lane 分配
- 不動 commit trailer 規則

## 8. 你的決定點

- [ ] 採用選項 A / B / C？或要我多寫一個變體？
- [ ] 如採用 C，是否同意把 OPS-WAVE-001/002/003 派出去？
- [ ] freeze 階段最短時長要設多久？（提案 30 分鐘）
- [ ] cool-down 60 分鐘合理嗎？或要更長？
