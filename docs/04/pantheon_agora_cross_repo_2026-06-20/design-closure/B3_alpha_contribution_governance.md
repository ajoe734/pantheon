# B3 — Alpha Contribution 治理準則

> 狀態：Design Frozen v1.0  
> 阻擋解除：`AG-BE-AL-001`、`AG-E2E-AL-001`  
> 原則：Agora 交易員策略預設 private；只有取得授權、完成去識別、可重現、獨立複製與治理審核後，才可升為 desk/global Alpha candidate。

---

## 1. 狀態機

```text
private
→ proposed
→ redacted
→ replication_running
→ replicated
→ desk_candidate | global_candidate
→ approved
→ retired
```

任一狀態可轉 `rejected`；`approved` 可轉 `suspended`／`retired`。

---

## 2. State Gates

### private → proposed

需要：

- 明確 user opt-in 或權利契約允許。
- StrategySpec／artifact 有 lineage。
- 沒有 pending privacy deletion request。
- 指定 contribution scope：desk 或 global。

Approver：策略 owner／授權 owner。

### proposed → redacted

需要：

- B2 privacy pipeline 通過。
- 無 raw prompt、raw journal、private identifiers。
- 無可重建私有 Alpha 的精確參數／symbol sequence。
- IP／資料 license 可用於指定 scope。
- Redaction report 與 checksum。

Approver：Privacy Reviewer + IP/Data Rights Reviewer。

### redacted → replication_running

需要：

- 完整可重現 research package。
- 獨立資料 split／time window。
- Reproduction environment pinned。
- Data/PIT/leakage checks 通過。

Approver：Research Reviewer。

### replication_running → replicated

策略家族門檻可覆寫，預設 gate：

```text
至少 2 個 non-overlapping OOS folds
OOS 占整體歷史 >= 30%
成本後 OOS Sharpe >= 0.80
OOS 最大回撤 <= 20% 或風險政策上限
正報酬 folds >= 60%
2× 成本／滑價壓力後淨報酬不為負
參數鄰域中 >= 70% 組合保持正向或主要結論一致
無單一標的／單一事件貢獻 > 20% 總 PnL（除非策略家族明確允許）
容量 >= 預期部署資金的 2 倍
no data leakage / PIT violation
reproduction checksum matched
```

事件策略另需：

```text
至少 30 個獨立事件
事件分布跨至少 3 個年份或 regime
```

低頻策略可由 Research Governance 核准替代門檻，但需書面理由。

### replicated → desk_candidate

需要：

- Desk mandate 適合。
- 與現有 desk Alpha 的 correlation／homogeneity review。
- Capacity 足夠。
- Desk Risk Reviewer 通過。

Approver：Desk Research Lead + Desk Risk Owner。

### replicated → global_candidate

額外需要：

- 跨市場／跨期間泛化證據，或明確標示適用域。
- Institutional Privacy Review。
- Platform correlation／duplication review。
- Global Research Committee。

### candidate → approved

需要：

- Registry artifact state／evidence 完整。
- Reviewer signatures。
- Usage restrictions。
- Monitoring plan。
- Retirement conditions。

Approved Alpha 是研究模板／candidate asset，不自動取得 paper/live execution 權。

---

## 3. Approver Roles

| Gate | Roles |
|---|---|
| propose | Owner / Rights holder |
| redaction | Privacy Reviewer + IP/Data Rights Reviewer |
| replication | Independent Research Reviewer |
| desk candidate | Desk Research Lead + Desk Risk Owner |
| global candidate | Platform Research Committee + Platform Risk + Privacy |
| approved | Registry/Promotion authority per scope |

單一人不得同時擔任原作者與唯一 replication reviewer。

---

## 4. Contribution Package

```text
StrategySpec (redacted)
research hypothesis
feature/label definitions
universe rules
entry/add/reduce/exit
portfolio/risk/execution rules
source/data rights report
reproduction environment
backtest + OOS + stress artifacts
cost/capacity report
privacy redaction report
correlation/duplication report
limitations
monitoring/retirement plan
```

---

## 5. Scope

```text
private_trader
private_team
desk_shared
pantheon_global
```

Scope promotion 必須是明確 transition，不可因使用次數或績效自動升級。

---

## 6. Similarity / Duplication

候選需與既有 Alpha Registry 比較：

- Feature overlap。
- Signal correlation。
- PnL correlation。
- Universe overlap。
- Rule similarity。

若高度重複，優先建立 variation／lineage，不重複新增全新 Alpha。

Default review trigger：

```text
signal_correlation >= 0.85
or pnl_correlation >= 0.80
or rule_overlap >= 0.70
```

---

## 7. Monitoring / Retirement

Approved Alpha 需監控：

```text
OOS decay
drift
capacity
turnover/cost
crowding/correlation
data availability
legal/license changes
usage yield
```

Retirement triggers：

- 連續 3 個評估窗低於策略家族最低 gate。
- 資料／license 不再可用。
- 嚴重 leakage／privacy／rights issue。
- 被更高品質版本 supersede。
- 長期無使用且 replication yield 低。

---

## 8. UI / Agora 呈現

Agora 預設只顯示：

```text
保持私有
提議貢獻
貢獻範圍
會移除哪些私有資訊
目前審查狀態
可撤回狀態
```

不得使用暗黑模式誘導交易員分享私有 Alpha。

---

## 9. E2E Acceptance

`AG-E2E-AL-001` 必須證明：

1. 新 Agora 策略預設 `private`。
2. 未 opt-in 不會建立 contribution proposal。
3. 提議後先做 privacy/redaction，不直接進共享 Registry。
4. Replication 使用獨立資料與 reviewer。
5. 未達 gate 不能成為 desk/global candidate。
6. Approved 仍不具有 execution authority。
7. 所有 transition、approver、evidence 可追溯。

---

## 10. Definition of Done

- 狀態、gate、threshold、approver 明確。
- 私有策略預設不共享。
- Privacy、IP、replication、risk 各自有獨立 gate。
- 策略家族可覆寫 threshold，但需版本化政策。
- Alpha Registry 不接受 raw chat 或未驗證想法。
- Approved Alpha 不直接啟用 paper/live。
