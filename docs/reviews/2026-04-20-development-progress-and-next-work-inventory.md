# 2026-04-20 Development Progress And Next Work Inventory

## 目的

這份文件整理四件事：

1. 以 **完整系統開發藍圖** 為基準，Pantheon 現在做到哪裡。
2. 目前 repo truth 下，哪些開發其實已完成。
3. 哪些部分仍未完成。
4. 哪些工作現在就可以繼續進行，不必再等新的高階藍圖。

---

## 先講結論

截至目前，Pantheon 的情況不是「整個藍圖還沒做完」，而是：

1. **canonical blueprint backlog 基本已完成**
2. **真正剩下的多數是 productization / UI loop / doc rebaseline / truth sync**
3. **少數項目仍卡在 architecture / ratification**
4. **還有一批可以立刻繼續做的前端、closeout、handoff activation、OSS next-wave 工作**

---

## 1. 完整系統藍圖完成度

### 1.1 Canonical blueprint backlog

根據 [docs/reviews/2026-04-16-full-blueprint-gap-analysis.md](/home/edna/code/pantheon/docs/reviews/2026-04-16-full-blueprint-gap-analysis.md:1)：

- `DEVELOPMENT_WORKBREAKDOWN.md` 定義的 `28/28` canonical backlog rows 都已有 archive，且皆為 `done`
- Phase5 convergence materialized 的 `42/42` execution tasks 也都已 archive `done`

這代表：

- 從「藍圖 row 有沒有 materialize / execute」這個角度看，主藍圖不是缺做
- 真正還沒關完的是 **delivery closure、frontend loops、truth drift、與少數後續擴展波次**

### 1.2 Already landed baselines

根據 [WORKBENCH_DELIVERY_BACKLOG.md](/home/edna/code/pantheon/WORKBENCH_DELIVERY_BACKLOG.md:1)：

下列基線已不屬於 remaining backlog：

- `F-042`
- `PKT-001` 到 `PKT-014`

也就是：

- Operator Console Wave 1 / 2 baseline
- Governance baseline
- Incident baseline
- Persona Wave 1 baseline
- PKT-003 的 baseline surfaces

都已經落成，不再是系統主藍圖的缺口。

---

## 2. 現行開發進度總盤點

### 2.1 已完成且已可視為 backend / route 落地的工作

以下 workbench backend slices 依 archive truth 已完成：

- `AUTO-IMPL-EW04-001`
- `AUTO-IMPL-RW02-001`
- `AUTO-IMPL-RW04-001`
- `AUTO-IMPL-CW01-001`
- `AUTO-IMPL-TW01-001`
- `AUTO-IMPL-TW03-001`
- `AUTO-IMPL-TW04-001`
- `AUTO-HARDEN-RW01-001`
- `AUTO-HARDEN-RW03-001`
- `AUTO-HARDEN-CW03-001`
- `AUTO-HARDEN-KW01-001`
- `AUTO-REBASE-BACKLOG-001`
- `AUTO-REBASE-LOVABLE-SA-001`
- `AUTO-TEST-PROMOTION-001`
- `AUTO-TEST-LINREAD-001`
- `AUTO-TEST-ROUTER-001`
- `AUTO-TEST-PERSONA-001`
- `AUTO-TECHDEBT-PYDANTIC-001`

換句話說，目前 repo truth 裡，以下模組已不該再被當成「純 backend implementation 尚未開始」：

- `EW-04`
- `RW-01`
- `RW-02`
- `RW-03`
- `RW-04`
- `CW-01`
- `CW-03`
- `KW-01`
- `TW-01`
- `TW-03`
- `TW-04`

### 2.2 前端協作進度

根據 [current-work.md](/home/edna/code/pantheon/current-work.md:1)：

- Lovable-ready packets: `33`
- waiting for Lovable/front-end: `7`
- UI-done returned: `26`
- frontend feedback returned: `26`
- open BFF gaps: `0`

這代表前端 lane 的主阻塞，已經不再是大量 BFF gap，而是：

- 某些 feature 還沒開始前端實作
- 很多已回傳的 loop 還差 closeout / record-layer completion

### 2.3 OSS 生態進度

根據 [docs/reviews/2026-04-16-oss-ecosystem-gap-analysis.md](/home/edna/code/pantheon/docs/reviews/2026-04-16-oss-ecosystem-gap-analysis.md:1)：

目前 OSS maturity 可分為：

- Fully integrated / governed:
  - `OpenClaw`
  - `DSPy`
  - `imitation`
  - `MLflow`
  - `vectorbt`
  - `statsmodels`
  - `QuantLib`
- Activation-ready but not fully integrated:
  - `Qlib`
  - `TRL`
  - `FinRL`
  - `RLlib`
  - `Ray Tune`
  - `W&B`

所以 OSS 這一側的主藍圖 row 雖已完成，但成熟度仍未全部走到 runnable governed backend。

---

## 3. 尚未完成開發的部分

這裡分成五類看，避免把不同性質的 unfinished work 混在一起。

---

### 3.1 尚未完成的前端實作 loops

目前 `waiting_for_lovable` 的 feature 有：

- `CW-01-consult-request`
- `EW-05-mutation-review`
- `KW-01-institutional-memory`
- `PKT-003-inspiration-graph`
- `RW-01-research-ticket`
- `RW-02-search`
- `TW-01-teaching-dialog`

這些都屬於 **現在就可以做的前端 / Lovable implementation**。

---

### 3.2 尚未完成的 delivery closeout / loop closeout

目前有大量 feature 已到 `frontend_feedback_reviewed`，但還沒完成 closure record：

- `F-042`
- `PKT-001` 到 `PKT-014` 中多數 front-returned surfaces
- `PKT-consultation-workbench`
- `PKT-knowledge-workbench`

這些已不是大規模產品開發缺口，而是：

- review disposition finalize
- closeout bookkeeping
- canonical record sync

---

### 3.3 已完成 backend，但尚未完成 handoff activation / truth rebaseline 的模組

這一類很重要，因為它們最容易被 backlog 誤判成「還沒做 backend」。

#### 已做完 backend、但前端 handoff 尚未完整打開

- `RW-04`
  - route family 已 live
  - handoff bundle 與 coordination bundle 已補齊，現已可直接交給 frontend lane
- `TW-03`
  - preview route family 已 live
  - 但 frontend handoff bundle 與 coordination truth 仍待補齊
- `TW-04`
  - replay route family 已 live
  - handoff bundle 已發布；剩餘工作是把 backlog / SA 等文件保持在同一套 live truth

#### 已 live、但仍有 module-gate 的模組

- `CW-03`
  - committee routes 與 sponsor-decision authority 已 live
  - 但正式 production handoff 仍受 `CW-01` / `CW-02` upstream gate 影響

---

### 3.4 尚未完成、且目前仍受 architecture / ratification 阻塞的部分

這些不適合直接丟 implementation worker 硬做：

- Global canonical conventions pack
- `LIN-002` lineage ownership
- `control-plane/persona` boundary
- `control-plane/router` enforcement ownership

以及需要 ratification 的模組：

- `RW-05`
- `CW-02`
- `CW-04`
- `TW-02`
- `KW-02`
- `KW-03`
- `KW-04`
- `KW-05`

細節已整理在：

[2026-04-20-system-design-open-questions-for-architecture-team.md](/home/edna/code/pantheon/docs/reviews/2026-04-20-system-design-open-questions-for-architecture-team.md:1)

---

### 3.5 尚未完成的 OSS next-wave 開發

依 OSS gap analysis，目前仍可視為 unfinished 的 OSS work 有：

- `OSS-NEXT-003` RL path activation gate closure
- `OSS-NEXT-004` W&B defer / reopen packet decision
- `OSS-NEXT-008` governed-path regression refresh

其中 `W&B` 這條現在的 truth 不是「進 backend implementation」，而是先把 defer gate 與
reopen packet 條件鎖定清楚；最早 eligible reopen date 仍是 `2026-05-15`（MLflow
30-day governed history gate）。

---

## 4. 現在就可以繼續進行的開發工作

這裡只列 **不必再等新高階藍圖** 的工作。

---

### 4.1 第一優先：直接啟動前端實作的工作

這些 feature 已 ready for Lovable/front-end，現在就可以繼續：

1. `CW-01-consult-request`
2. `EW-05-mutation-review`
3. `KW-01-institutional-memory`
4. `PKT-003-inspiration-graph`
5. `RW-01-research-ticket`
6. `RW-02-search`
7. `RW-04-experiment-launch`
8. `TW-01-teaching-dialog`
9. `TW-04-teaching-replay`

---

### 4.2 第二優先：關閉已回傳 loop 的 closeout 工作

這批不是新功能開發，而是把已完成的前端 loop 正式收斂成 canonical done truth：

- finalize review disposition
- finalize closure record
- sync canonical closeout bookkeeping

適合集中批次處理。

---

### 4.3 第三優先：做 handoff activation / doc truth rebaseline

這一批非常值得做，因為它能把「已做完但被文件寫成沒做」的模組解鎖：

1. `EW-04`
   - 已完成 route-live rebaseline；剩餘工作以 closeout / record sync 為主
2. `RW-04`
   - 已補前端 handoff / coordination bundle，可直接交給 frontend lane
3. `TW-03`
   - 補前端 handoff / coordination bundle
4. `TW-04`
   - handoff 已補齊；剩餘是持續維持 docs 與 coordination truth 對齊
5. backlog / SA / packet family rebaseline
   - 修正仍停留在舊「pending BFF implementation」說法的文件，避免把已 live 模組誤標成 blocked

---

### 4.4 第四優先：在 architecture 回答前就能先做的 support work

雖然部分模組仍卡 architecture，但還是可以先做：

- readiness drift audit
- packet / backlog / overview truth alignment
- test scaffolding
- handoff bundle skeleton
- upstream dependency map

適用模組：

- `RW-05`
- `CW-02`
- `CW-04`
- `TW-02`
- `KW-02` 到 `KW-05`

注意：這些只適合做 support / ratification prep，不適合直接實作 production route。

---

### 4.5 第五優先：OSS next-wave

如果下一輪要擴 research / learning ecosystem，可直接往下做：

1. `RL` path activation decision and first lane
2. `W&B` defer closeout and reopen-packet definition
3. `statsmodels` governed-path regression refresh
4. `QuantLib` governed-path regression refresh / CI matrix wiring
5. 其他 governed-path regression refresh

---

## 5. 建議的下一輪執行順序

若目標是最快把「系統開發完成度」往前推，我建議順序是：

1. 完成 7 個 `waiting_for_lovable` front-end loops
2. 批次關閉 `frontend_feedback_reviewed` closeout
3. 完成 `TW-03` handoff activation，並持續收斂 `EW-04` / `RW-04` / `TW-04` 的 doc truth rebaseline
4. 等 architecture team 回覆後，再切 `RW-05` / `CW-02` / `CW-04` / `TW-02` / `KW-02~05`
5. 平行規劃 OSS next-wave

---

## 一句話總結

Pantheon 現在的主問題不是「完整系統藍圖還沒實作」，而是：

- 大藍圖已經 materialize
- 剩餘工作主要集中在前端 loops、closeout、truth rebaseline、少數 architecture-blocked 模組，以及 OSS 下一波成熟化

其中最值得立刻往下做的，是前端等待中的 7 個 feature、已回傳 loops 的 closeout，以及 backend 已落地但文件還沒跟上的 handoff activation 工作。
