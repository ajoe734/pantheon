# PER-001 Codex Review

Status: changes requested
Reviewer: Codex
Task: PER-001
Artifacts:
- `PERSONA_RUNTIME_MODEL.md`
- `OPENCLAW_RUNTIME_CONTRACT.md`
- `services/control-plane/persona/persona_registry.py`
- `services/control-plane/persona/session_persona.schema.json`
- `services/control-plane/persona/contract.md`
- `services/control-plane/persona/test_persona_registry.py`
- `services/control-plane/persona/smoke_test_persona_registry.py`
Reviewed at: 2026-04-10

## 結論

Claude 這輪 handoff 已收斂我上一輪提出的三個 blocker：

1. `deployment_stage` / `capital_pool_id` 與 `runtime_binding_id` 的對稱依賴已補上。
2. `trace_id` / `request_id` 已升成 dataclass + schema required fields。
3. `PERSONA_RUNTIME_MODEL.md` 的 `SessionPersona` 區塊已改成和 contract 同步的欄位形狀。

我本地重跑驗證也和 handoff 一致：

- `python3 -m unittest discover -s services/control-plane/persona -p 'test_*.py'` → `61` tests passed
- `python3 services/control-plane/persona/smoke_test_persona_registry.py` → `55/55` checks passed
- `python3 -m py_compile services/control-plane/persona/persona_registry.py` → passed

但 PER-001 仍不建議直接進 `review_approved`。目前 deployment-bound session 的 audit chain identifier 仍可用空字串通過建構與驗證，這會讓正式 contract 看起來有 link，實際上卻無法追到真實 `RuntimeBinding` / `capital_pool`。

## Blocker

1. `SessionPersona` 仍接受空字串 `runtime_binding_id` / `capital_pool_id`，deployment-bound audit chain 仍可被空值繞過。

- `services/control-plane/persona/contract.md:114` 把 `runtime_binding_id` 定義成 deployment-bound session 的正式 RuntimeBinding 連結。
- `services/control-plane/persona/contract.md:116` 把 `capital_pool_id` 定義成 session 所作用的正式 capital pool 連結。
- 但 `SessionPersona.__post_init__()` 目前只檢查 `is not None`，沒有拒絕空字串：
  - `services/control-plane/persona/persona_registry.py:378`
  - `services/control-plane/persona/persona_registry.py:387`
- `validate_session()` 也只檢查 interactive/background session 的 `runtime_binding_id is None`，沒有要求非空字串，且完全不檢查 `capital_pool_id` 是否為空：
  - `services/control-plane/persona/persona_registry.py:436`
  - `services/control-plane/persona/persona_registry.py:437`
- `session_persona.schema.json` 同樣允許空字串，因為 `runtime_binding_id` / `capital_pool_id` 只有 `type: string`，沒有 `minLength` 或等價限制：
  - `services/control-plane/persona/session_persona.schema.json:66`
  - `services/control-plane/persona/session_persona.schema.json:75`

### Reviewer 本地重現

以下兩種 payload 目前都能成功建立 `SessionPersona`，而且 `validate_session()` 回傳 `[]`：

1. `runtime_binding_id=""`, `deployment_stage="live"`, `capital_pool_id="pool-1"`
2. `runtime_binding_id="rb-1"`, `deployment_stage="live"`, `capital_pool_id=""`

這代表 deployment-bound session 可以攜帶形式上存在、語義上無效的 audit chain identifier。對 LIN-001 / APP-001 / telemetry downstream 而言，這不是小的 validation 缺口，而是會讓 lineage edge 與 read surface 收到不可追蹤的主鍵。

## 可接受的修正方向

1. 在 `SessionPersona.__post_init__()` 與 `validate_session()` 補上 non-blank 檢查：
   - `runtime_binding_id` 若存在，必須 `strip()` 後非空。
   - `capital_pool_id` 若存在，必須 `strip()` 後非空。
2. 在 `session_persona.schema.json` 對這兩個欄位加上 `minLength: 1`，避免空字串在 machine schema 層直接通過。
3. 補 unit + smoke coverage，至少覆蓋：
   - 空字串 `runtime_binding_id` 對 interactive/background session 必須失敗。
   - 空字串 `capital_pool_id` 必須失敗。
