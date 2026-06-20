# A4 — Shadow `human_actual` Arm 資料來源與 Dev 接線規格

> 狀態：Design Frozen v1.0  
> 阻擋解除：`AG-BE-SH-002`、`AG-E2E-SH-001`  
> 原則：Agora 不直接下 live order；`human_actual` 是「交易員在決策時間點選擇的行為 arm」，其 outcome 來源必須明確標示，不能把 paper proxy 冒充真實成交。

---

## 1. 語義修正

原 schema 的 `human_actual` 保留作邏輯 arm 名稱，但新增 `execution_observation_kind`：

```ts
type HumanExecutionObservationKind =
  | "verified_broker_fill"
  | "imported_broker_fill"
  | "pantheon_paper_proxy"
  | "manual_declared_outcome"
  | "declared_no_trade";
```

UI 顯示必須依來源：

| kind | 顯示名稱 |
|---|---|
| verified_broker_fill | 交易員實際成交（已驗證） |
| imported_broker_fill | 交易員成交（匯入／待完整驗證） |
| pantheon_paper_proxy | 交易員決策（紙上代理） |
| manual_declared_outcome | 交易員手動回報結果 |
| declared_no_trade | 交易員選擇不交易 |

禁止在 `pantheon_paper_proxy` 時顯示「實際成交」。

---

## 2. Human Decision Lock

交易員每次裁示時建立 immutable decision：

```ts
type HumanDecisionLock = {
  humanDecisionId: string;
  userId: string;
  strategyId: string;
  strategyVersionId: string;
  sourceTradingEventId: string;
  decisionTime: string;
  availableDataCutoff: string;
  decision: "enter" | "add" | "reduce" | "exit" | "hold" | "reject" | "defer" | "no_trade";
  targetInstrument: string;
  requestedQuantity?: number;
  requestedWeight?: number;
  requestedPriceConstraint?: unknown;
  rationalePrivateRef?: string;
  redactedSummary?: string;
  checksum: string;
  immutable: true;
};
```

決策不得事後修改；只能建立 superseding record。

---

## 3. Outcome Source 優先序

系統依下列優先序解析 outcome：

1. `verified_broker_fill`：受治理 broker integration 回傳，account/user/decision 可對應。
2. `imported_broker_fill`：使用者匯入 broker statement／fill，通過 checksum、symbol、time、quantity 基本核對。
3. `pantheon_paper_proxy`：將 HumanDecisionLock 映射至既有 AllocationPolicyArtifact／target projection，送入 LEAN paper runtime。
4. `manual_declared_outcome`：使用者回報 off-platform 結果；僅作低信賴度分析，不作自動模型升級依據。
5. `declared_no_trade`：無 fill，仍可對照其他 shadow arms 的反事實結果。

同一 decision 若後續取得更高權威來源，不覆寫舊 outcome；建立 `supersedes_outcome_id` 的新 record。

---

## 4. Dev / CI 標準

### Tier A — Deterministic Fixture Replay（必須）

用途：CI、E2E、contract test。

使用 `AgoraMarketReplayPack.v1`：

```text
OHLCV
corporate actions
public event timestamps
branch-flow fixture
related-party holding snapshot fixture
liquidity/cost model
PIT availability timestamps
```

條件：

- 固定 checksum。
- 固定 start/end。
- 相同 decision lock 產生相同 fills／outcome。
- 嚴格依 `available_at` 防止未來資料。
- Human arm 使用 `pantheon_paper_proxy`。

### Tier B — Dev Historical Replay（整合驗證）

使用 Data Source Registry 中已治理歷史資料與 LEAN paper replay。需要：

- 來源授權／lineage。
- 至少 252 交易日，贏家分點策略建議 2 年以上。
- branch data 與 event data 有 PIT timestamp。
- fill model、fee、tax、slippage 明確。

### Tier C — Sandbox / Real-time Paper（可選）

有行情與 sandbox feed 時可跑，但不作 CI 唯一依賴。

---

## 5. Human Paper Proxy 映射

```text
HumanDecisionLock
→ HumanDecisionArtifact
→ target / AllocationPolicy projection
→ existing DeploymentPlan (paper)
→ RuntimeBinding
→ LEAN paper runtime
→ fills / positions / telemetry
→ HumanOutcomeRecord
```

不得新建 Shadow execution kernel。

### Mapping rules

- `enter` / `add`：依 requestedWeight/quantity 建 target。
- `reduce`：目標權重下降。
- `exit`：目標 0。
- `hold`：維持既有 target。
- `reject` / `no_trade`：不建 order，仍記錄 counterfactual observation window。
- `defer`：建立新的 review time，不形成 execution arm。

---

## 6. Outcome Record

```ts
type HumanOutcomeRecord = {
  outcomeId: string;
  humanDecisionId: string;
  executionObservationKind: HumanExecutionObservationKind;
  sourceRefs: string[];
  fillRefs: string[];
  positionRefs: string[];
  observationWindow: "1d" | "3d" | "5d" | "10d" | "20d" | "custom";
  metrics: {
    returnPct?: number;
    maxDrawdownPct?: number;
    realizedPnl?: number;
    unrealizedPnl?: number;
    slippageBps?: number;
    turnover?: number;
    exposure?: number;
  };
  confidence: number;
  verified: boolean;
  supersedesOutcomeId?: string;
  createdAt: string;
};
```

Confidence 預設：

| kind | confidence |
|---|---:|
| verified_broker_fill | 1.00 |
| imported_broker_fill | 0.80（驗證後可升 0.95） |
| pantheon_paper_proxy | 0.75 |
| manual_declared_outcome | 0.40 |
| declared_no_trade | 1.00（決策本身），counterfactual outcome 依模型來源 |

---

## 7. Shadow Comparison

比較 arms：

```text
base_strategy
private_servant
human_actual
committee_variant
optional_alt_versions
```

所有 arms 必須共享：

```text
decision_time
available_data_cutoff
market_replay_id / feed_id
cost model
observation window
```

比較結果需分開：

- Decision quality。
- Execution quality。
- Cost/slippage。
- Risk-adjusted outcome。
- Confidence／calibration。

不得把 paper proxy 與 verified actual 混在同一 aggregate 而不標示。

---

## 8. Dev Market Data 前置

`AG-E2E-SH-001` 不等待 live market data。Phase 5 E2E 使用 Tier A deterministic fixture replay。

另由 C4 文件定義 Tier B／Tier C 接線。E2E acceptance：

1. HumanDecisionLock immutable。
2. 三個 arms 在同 cutoff 下生成。
3. LEAN paper fills/positions 可查。
4. outcome lineage 可回到 decision lock。
5. UI 正確標示 paper proxy。
6. 無 broker/live/capital side effect。

---

## 9. Manual / Imported Data Guardrails

- 匯入資料需 malware scan、schema validation、checksum、account ownership confirmation。
- 手動回報不能自動產生 approved Alpha 或 persona policy。
- 私人 fill 不進 institutional corpus，除非取得明確授權且完成去識別。
- 所有 import/export 有 audit trail。

---

## 10. Definition of Done

- human arm outcome source 語義不再模糊。
- dev/CI 有 deterministic market replay。
- `pantheon_paper_proxy` 能走既有 LEAN paper path。
- UI 不把 proxy 標成實際成交。
- verified/imported/manual outcome 有明確 confidence 與 lineage。
- Shadow comparison 同 cutoff、同成本模型、不可事後重寫。
