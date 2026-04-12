# LIN-002 Review — Codex

日期：2026-04-10
結論：`request changes`

## Re-review Addendum

Qwen 這一輪已修掉前一版 review 的三個 blocker：

- 四個 query family 現在都有明確輸出 `derived_only = true`
- `refs` envelope 的 8 個 key 現在都存在
- alias drift conflict markers 與 `feedback_adapter` 的 marker shape 已對齊
- 新增 3 個 regression tests，25/25 tests PASS，benchmark 仍在 SLA 內

但 re-review 後仍有一個 contract-level 漏洞未收斂，因此本 task 仍不能 approve。

### 4. `telemetry_event_trace` 的 `refs` 仍會漏掉 target telemetry event 自身的 semantic refs / `trace_id`

- 檔案：
  - `services/telemetry/lineage_read/service.py:684`
  - `services/telemetry/lineage_read/service.py:707`
  - `services/telemetry/lineage_read/service.py:748`
  - `services/telemetry/lineage_read/service.py:907`
  - `services/telemetry/lineage_read/service.py:968`
  - `services/registry/lineage/read_model_contract.md:166`
  - `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md:183`
- 問題：
  - `_build_refs_from_chains()` 只掃 `upstream_chain + downstream_chain`。
  - `telemetry_event_trace` enrich path 會把 target event 的 `artifact_ref` / `runtime_ref` 加進 chain，但**不會把 target telemetry event 自己放進 chain**。
  - 結果是 target event 自己攜帶的 semantic refs 不會被納入 `refs`：
    - `trace_id`
    - `strategy_id`
    - `registry_id`
    - 以及任何只存在於 target event、但沒有從其他 chain item 回推到的 telemetry semantic refs
- 直接驗證：
```bash
python3 - <<'PY'
from services.telemetry.lineage_read.service import LineageReadService

corpus = {
  "node_sets": {
    "capital_pools": [{"pool_id": "pool-1", "single_runtime_enforced": True, "created_at": "2026-04-10T00:00:00Z"}],
    "persona_capital_bindings": [{"binding_id": "pb-1", "capital_pool_id": "pool-1", "created_at": "2026-04-10T00:00:00Z"}],
    "deployment_plans": [{"plan_id": "plan-1", "capital_pool_id": "pool-1", "binding_id": "pb-1", "artifact_id": "art-1", "artifact_version": "1.0.0", "created_at": "2026-04-10T00:00:00Z"}],
    "runtime_bindings": [{"binding_id": "rb-1", "capital_pool_id": "pool-1", "plan_id": "plan-1", "persona_capital_binding_id": "pb-1", "artifact_id": "art-1", "artifact_version": "1.0.0", "runtime_id": "rt-1", "status": "active", "effective_at": "2026-04-10T00:00:00Z"}],
    "telemetry_events": [{
      "event_id": "evt-1",
      "event_type": "deploy_completed",
      "binding_id": "rb-1",
      "plan_id": "plan-1",
      "capital_pool_id": "pool-1",
      "persona_capital_binding_id": "pb-1",
      "artifact_id": "art-1",
      "artifact_version": "1.0.0",
      "runtime_id": "rt-1",
      "trace_id": "trace-123",
      "strategy_id": "strat-1",
      "registry_id": "reg-1",
      "event_produced_at": "2026-04-10T00:00:01Z"
    }]
  }
}

svc = LineageReadService()
svc.load_corpus(corpus)
print(svc.query("telemetry_event_trace", event_id="evt-1")["refs"])
PY
```
- 實際結果：
  - `trace_ids = []`
  - `strategy_ids = []`
  - `registry_ids = []`
  - 這些值明明存在於 target telemetry event，但因為 target node 沒被納入 `refs` 聚合而直接消失
- 影響：
  - `telemetry_event_trace` 目前仍不符合 LIN-001 / L1 summary contract 對 `refs` 的要求
  - 下游 incident / evolution / BFF consumer 會收到「key 存在但 target evidence 漏值」的 projection，這比完全缺 key 更危險，因為測試與 caller 都容易誤判為 payload 完整

## Required Fix Direction (Re-review)

1. `_build_refs_from_chains()` 需要納入 target node 本身，或讓各 query family 在 build refs 前把 target aggregate 的 canonical semantic refs 明確餵進聚合器。
2. 至少補一個 regression test，直接守 `telemetry_event_trace` 會把 target event 的 `trace_id` / `strategy_id` / `registry_id` 放進 `refs`。
3. 最好把現有「只驗 key set」的 envelope test 擴成「target-carried refs 也會出現在 value set」。

## Findings

### 1. 所有 query family 都沒有輸出 LIN-001 / L1 明定的 summary envelope，會直接破壞下游 consumer contract

- 檔案：
  - `services/telemetry/lineage_read/service.py:635`
  - `services/telemetry/lineage_read/service.py:699`
  - `services/telemetry/lineage_read/service.py:740`
  - `services/telemetry/lineage_read/service.py:833`
  - `services/registry/lineage/read_model_contract.md:166`
  - `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md:183`
- 問題：
  - LIN-001 / L1 已經把 summary projection contract 鎖定成固定 envelope，至少要明確帶：
    - `derived_only = true`
    - `projection_updated_at`
    - `upstream_chain[]`
    - `downstream_chain[]`
    - `conflict_markers[]`
    - `refs.{strategy_ids, registry_ids, runtime_binding_ids, deployment_plan_ids, capital_pool_ids, persona_capital_binding_ids, artifact_refs, trace_ids}`
  - 但 LIN-002 四個 enrich path 回傳的 payload 都只剩：
    - `target_type`
    - `target_id`
    - `projection_updated_at`
    - chain arrays
    - `conflict_markers`
    - 少量 query-family-specific counter
  - `derived_only` 與整個 `refs` envelope 完全沒有出現在 LIN-002 輸出裡。
- 直接驗證：
```bash
python3 - <<'PY'
import json
from pathlib import Path
from services.telemetry.lineage_read.service import LineageReadService
corpus = json.loads(Path("services/registry/lineage/lin001a_benchmark_corpus.json").read_text())
svc = LineageReadService()
svc.load_corpus(corpus)
for family, params in [
    ("runtime_binding_projection", {"binding_id": "rb-alpha-live-001"}),
    ("capital_pool_projection", {"pool_id": "pool-alpha"}),
    ("telemetry_event_trace", {"event_id": "evt-beta-rollback-pnl-001"}),
    ("forensic_plan_trace", {"plan_id": "plan-beta-rollback"}),
]:
    result = svc.query(family, **params)
    print(family, sorted(result.keys()))
PY
```
- 實際結果：
  - 四個 query family 都只輸出 `_meta` 與各自 counter，沒有 `derived_only`
  - 也沒有 `refs`
- 影響：
  - `INC-001`、`EVO-003`、`APP-001/APP-002` consume 的是 LIN-001 鎖定的 read-model contract，不是 LIN-002 自行縮水後的 payload
  - 目前 benchmark / unit tests 只驗 ID 與 budget，沒有驗 envelope，所以 regression 被假綠燈蓋掉

### 2. telemetry read path 沒有承接 LIN-001 的 alias normalization / drift conflict 規則，會靜默吃掉 canonical mismatch

- 檔案：
  - `services/telemetry/lineage_read/service.py:723`
  - `services/telemetry/lineage_read/service.py:740`
  - `services/telemetry/feedback_adapter.py:244`
  - `services/telemetry/feedback_adapter.py:295`
  - `services/telemetry/feedback_adapter.py:359`
  - `services/registry/lineage/read_model_contract.md:193`
- 問題：
  - LIN-001 已在 telemetry slice 鎖定 alias normalization 與 mismatch surfacing：
    - `binding_id` vs `runtime_binding_id`
    - `plan_id` vs `deployment_plan_id`
    - `environment` vs `deployment_stage`
    - `artifact_version` vs `target.artifact_version`
  - `FeedbackStoreAdapter.build_lineage_record()` / `build_lineage_summary()` 會把這些 drift 轉成 `conflict_markers[]`。
  - 但 LIN-002 service 直接吃 raw corpus event，完全沒有重用這套 normalization / conflict logic；目前只有 rollback 與 single-runtime 兩類 markers。
- 直接重現：
```bash
python3 - <<'PY'
from services.telemetry.lineage_read.service import LineageReadService
corpus = {
  "node_sets": {
    "capital_pools": [{"pool_id": "pool-1", "single_runtime_enforced": True, "created_at": "2026-04-10T00:00:00Z"}],
    "persona_capital_bindings": [{"binding_id": "pb-1", "capital_pool_id": "pool-1", "created_at": "2026-04-10T00:00:00Z"}],
    "deployment_plans": [{"plan_id": "plan-1", "capital_pool_id": "pool-1", "binding_id": "pb-1", "artifact_id": "art-1", "artifact_version": "1.0.0", "created_at": "2026-04-10T00:00:00Z"}],
    "runtime_bindings": [{"binding_id": "rb-1", "capital_pool_id": "pool-1", "plan_id": "plan-1", "persona_capital_binding_id": "pb-1", "artifact_id": "art-1", "artifact_version": "1.0.0", "runtime_id": "rt-1", "status": "active", "effective_at": "2026-04-10T00:00:00Z"}],
    "telemetry_events": [{
      "event_id": "evt-1",
      "event_type": "deploy_completed",
      "binding_id": "rb-legacy",
      "runtime_binding_id": "rb-1",
      "plan_id": "plan-legacy",
      "deployment_plan_id": "plan-1",
      "capital_pool_id": "pool-1",
      "persona_capital_binding_id": "pb-1",
      "artifact_id": "art-1",
      "artifact_version": "1.0.1",
      "target": {"artifact_version": "1.0.0"},
      "runtime_id": "rt-1",
      "deployment_stage": "live",
      "environment": "canary",
      "event_produced_at": "2026-04-10T00:00:01Z"
    }]
  }
}
svc = LineageReadService()
svc.load_corpus(corpus)
print(svc.query("telemetry_event_trace", event_id="evt-1")["conflict_markers"])
PY
```
- 實際結果：
  - `telemetry_event_trace` 回傳 `[]`
  - 同一筆 event 如果走 `FeedbackStoreAdapter.build_lineage_record()`，會得到 4 個 canonical mismatch markers：
    - `runtime_binding_alias_mismatch`
    - `deployment_plan_alias_mismatch`
    - `deployment_stage_alias_mismatch`
    - `artifact_version_target_mismatch`
- 影響：
  - LIN-002 目前不是在「優化 LIN-001 contract」，而是在繞過 LIN-001 已鎖定的 canonical mismatch surfacing
  - 下游 consumer 會收到看似乾淨、其實已靜默吞掉 drift 的結果

### 3. 測試宣稱覆蓋 alias drift，但實際 suite 沒有任何一個測試守這兩個 contract regression

- 檔案：
  - `services/telemetry/lineage_read/test_service.py:9`
  - `services/telemetry/lineage_read/test_service.py:172`
  - `services/telemetry/lineage_read/test_service.py:271`
- 問題：
  - test module header 寫了「Conflict marker detection (rollback, alias drift)」。
  - 但整個 suite 只有：
    - missing node
    - rollback markers
    - benchmark case 的 expected IDs / marker IDs
  - 沒有任何 case 驗：
    - `derived_only`
    - `refs`
    - alias mismatch marker propagation
    - semantic alias fields / normalized envelope
- 結果：
  - 目前兩個 blocker 都能在 `22 tests OK`、benchmark 全綠的情況下直接漏掉

## Verification I Ran

```bash
python3 -m unittest services/telemetry/lineage_read/test_service.py
python3 services/telemetry/lineage_read/benchmark.py --enforce-budgets
python3 - <<'PY'
import json
from pathlib import Path
from services.telemetry.lineage_read.service import LineageReadService
corpus = json.loads(Path("services/registry/lineage/lin001a_benchmark_corpus.json").read_text())
svc = LineageReadService()
svc.load_corpus(corpus)
for family, params in [
    ("runtime_binding_projection", {"binding_id": "rb-alpha-live-001"}),
    ("capital_pool_projection", {"pool_id": "pool-alpha"}),
    ("telemetry_event_trace", {"event_id": "evt-beta-rollback-pnl-001"}),
    ("forensic_plan_trace", {"plan_id": "plan-beta-rollback"}),
]:
    print(family, sorted(svc.query(family, **params).keys()))
PY
python3 - <<'PY'
from services.telemetry.lineage_read.service import LineageReadService
corpus = {
  "node_sets": {
    "capital_pools": [{"pool_id": "pool-1", "single_runtime_enforced": True, "created_at": "2026-04-10T00:00:00Z"}],
    "persona_capital_bindings": [{"binding_id": "pb-1", "capital_pool_id": "pool-1", "created_at": "2026-04-10T00:00:00Z"}],
    "deployment_plans": [{"plan_id": "plan-1", "capital_pool_id": "pool-1", "binding_id": "pb-1", "artifact_id": "art-1", "artifact_version": "1.0.0", "created_at": "2026-04-10T00:00:00Z"}],
    "runtime_bindings": [{"binding_id": "rb-1", "capital_pool_id": "pool-1", "plan_id": "plan-1", "persona_capital_binding_id": "pb-1", "artifact_id": "art-1", "artifact_version": "1.0.0", "runtime_id": "rt-1", "status": "active", "effective_at": "2026-04-10T00:00:00Z"}],
    "telemetry_events": [{
      "event_id": "evt-1",
      "event_type": "deploy_completed",
      "binding_id": "rb-legacy",
      "runtime_binding_id": "rb-1",
      "plan_id": "plan-legacy",
      "deployment_plan_id": "plan-1",
      "capital_pool_id": "pool-1",
      "persona_capital_binding_id": "pb-1",
      "artifact_id": "art-1",
      "artifact_version": "1.0.1",
      "target": {"artifact_version": "1.0.0"},
      "runtime_id": "rt-1",
      "deployment_stage": "live",
      "environment": "canary",
      "event_produced_at": "2026-04-10T00:00:01Z"
    }]
  }
}
svc = LineageReadService()
svc.load_corpus(corpus)
print(svc.query("telemetry_event_trace", event_id="evt-1"))
PY
python3 - <<'PY'
from services.telemetry.feedback_adapter import FeedbackStoreAdapter
adapter = FeedbackStoreAdapter()
event = {
  "event_id": "evt-1",
  "event_type": "deploy_completed",
  "binding_id": "rb-legacy",
  "runtime_binding_id": "rb-1",
  "plan_id": "plan-legacy",
  "deployment_plan_id": "plan-1",
  "capital_pool_id": "pool-1",
  "persona_capital_binding_id": "pb-1",
  "artifact_id": "art-1",
  "artifact_version": "1.0.1",
  "target": {"artifact_version": "1.0.0"},
  "runtime_id": "rt-1",
  "deployment_stage": "live",
  "environment": "canary",
  "created_at": "2026-04-10T00:00:01Z"
}
print(adapter.build_lineage_record(event)["conflict_markers"])
PY
```

## Required Fix Direction

1. 回到 LIN-001 / L1 envelope，讓四個 query family 都明確輸出 `derived_only` 與完整 `refs`.
2. 不要另起一套 drift semantics；直接重用或等價實作 `feedback_adapter` 的 alias normalization / conflict marker 規則。
3. 補最少一組 regression tests，直接守：
   - `derived_only` 存在
   - `refs` key set 完整
   - alias mismatch marker 會從 telemetry event trace surfacing 出來
