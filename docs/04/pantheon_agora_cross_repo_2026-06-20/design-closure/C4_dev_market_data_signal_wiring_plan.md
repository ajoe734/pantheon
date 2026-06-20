# C4 — Dev 環境市場資料與 Signal Producer 接線計畫

> 狀態：Design Frozen v1.0  
> 阻擋解除：`AG-E2E-SH-001`、`AG-E2E-TR-001`  
> 原則：CI/E2E 不依賴 live market feed；先以受治理 deterministic replay 證明完整右半圈，再接 dev historical/sandbox feed。沿用 Data Source Registry、Research artifacts、AllocationPolicyArtifact、DeploymentPlan、RuntimeBinding、LEAN paper 與 Telemetry。

---

## 1. 三層資料模式

### Mode A — Deterministic Fixture Replay（CI 必須）

`AgoraMarketReplayPack.v1`：

```text
252 日 OHLCV
公開事件與 available_at
關係人持股 snapshot fixture
分點日買賣 fixture
流動性／費稅／滑價參數
corporate actions
regime labels（只作驗證 fixture）
```

要求：固定 checksum、可重播、PIT-safe、無網路依賴。

### Mode B — Governed Historical Replay（dev integration）

從 Data Source Registry 選已授權資料，至少：

```text
OHLCV 2 年以上
branch flow 1 年以上（若策略需要）
public event timeline
corporate actions
liquidity/cost inputs
```

### Mode C — Sandbox / Delayed Real-time Paper（可選）

有授權 feed 時啟用；不作 CI 唯一 gate，不啟用真實 broker/capital。

---

## 2. Data Contract

每筆 record 必須有：

```text
instrument
market_timestamp
available_at
source_dataset_ref
license_scope
revision_id
```

事件資料另有 `announced_at`、`effective_at`；回測只能在 `available_at <= decision_time` 時使用。

---

## 3. Signal Producer — 不新建策略引擎

使用既有 Research/Artifact 路徑：

```text
Selected StrategyVersion
→ Research/Model/Rule Artifact
→ signal_snapshot 或 AllocationPolicyArtifact
→ governed TradingEvent projection
→ Human/Servant/Base decision locks
→ DeploymentPlan/RuntimeBinding (paper)
→ LEAN fills/positions/telemetry
```

新增的只是 Agora-facing projection／mapping，不是另一個 execution engine。

---

## 4. AgoraSignalProjection

```ts
type AgoraSignalProjection = {
  projectionId: string;
  strategyId: string;
  strategyVersionId: string;
  artifactRef: string;
  dataCutoff: string;
  generatedAt: string;
  candidates: Array<{
    instrument: string;
    signalType: "entry"|"add"|"reduce"|"exit"|"hold";
    score?: number;
    confidence?: number;
    expectedValue?: number;
    targetWeight?: number;
    rationale: string;
    evidenceRefs: string[];
    invalidation: string[];
  }>;
  lineageRefs: string[];
};
```

---

## 5. Winner Branch Fixture

Fixture 必須包含：

- 至少 20 支股票。
- 至少 15 個分點。
- 3 組可辨識分點遷移 pattern。
- 10 個公開事件。
- 正例、負例、false positive、低 coverage case。
- 一個關係人—分點中度概率映射。
- 一個高度表面相關但 placebo 失敗案例。

用來驗證候選 scoring、information lead policy、dashboard widgets、shadow outcome。

---

## 6. LEAN Paper 接線

Mode A/B 都要：

```text
create paper DeploymentPlan
create RuntimeBinding
materialize approved/draft-eligible paper artifact per policy
launch/replay LEAN paper algorithm
emit orders/fills/positions
emit canonical telemetry
query lineage back to StrategyVersion and decision locks
```

若實際 Launcher 在 dev 尚未可用，CI 先使用既有 execution smoke harness，但 acceptance packet 必須清楚標 proof level；不可宣稱 EP4/EP5。

---

## 7. E2E Scenarios

### AG-E2E-TR-001

```text
Winner Branch StrategyVersion
→ candidate pool
→ trading room dashboard
→ entry/exit events
→ user decision
→ paper intent
```

### AG-E2E-SH-001

```text
same cutoff
→ base arm
→ servant arm
→ human paper proxy arm
→ LEAN paper outcomes
→ attribution
```

Acceptance：deterministic、no future data、same costs、lineage complete、no live side effect。

---

## 8. Data Quality Gates

```text
coverage threshold
missing interval threshold
revision awareness
corporate action adjustment
symbol mapping validity
timezone normalization
PIT audit
license check
```

不通過時 strategy/candidate 顯示 `data_blocked`，不可產生假精確分數。

---

## 9. Dev Deployment

建議 profile：

```text
PANTHEON_AGORA_MARKET_MODE=fixture|historical|sandbox
PANTHEON_AGORA_REPLAY_PACK=<ref>
PANTHEON_AGORA_SIGNAL_PROJECTION_ENABLED=1
PANTHEON_AGORA_PAPER_PROXY_ENABLED=1
```

預設 `fixture`；sandbox 需獨立 activation flag。

---

## 10. Definition of Done

- Fixture replay pack 與 checksum landed。
- Data/PIT/license schema landed。
- Signal projection 連到既有 Artifact/Allocation 路徑。
- Trading Room 與 Shadow E2E 在 CI 可重播。
- Dev historical replay 有獨立 acceptance。
- 無 live broker、capital 或 OpenClaw execution-kernel side effect。
