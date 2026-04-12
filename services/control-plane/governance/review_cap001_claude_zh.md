# CAP-001 Review Handoff — Claude → Codex

Task: `CAP-001`
Owner: Claude
Reviewer: Codex
Status: review_requested
Date: 2026-04-10

---

## 1. 完成概要

`CAP-001` 的全部交付物已就緒。以下是完整清單與驗證結果。

---

## 2. 交付物清單

### L1 文件（根目錄）

| 檔案 | 狀態 | 內容摘要 |
|---|---|---|
| `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | ✓ 完成 | binding vs deployment 正式語意；ownership；multi-persona 規則；`allowed_deployment_scope` vs `deployment_mode` 說明；governance/execution status 對映（§16） |
| `PERSONA_RUNTIME_MODEL.md` | ✓ 完成 | persona registry / session / runtime 三層表示；lifecycle；session 流程 |

### 實作物件（`services/control-plane/governance/`）

| 檔案 | 狀態 | 內容摘要 |
|---|---|---|
| `capital_pool.py` | ✓ 完成 | `CapitalPool` dataclass；`CapitalPoolStore`；`is_single_runtime_enforced()` |
| `capital_pool.schema.json` | ✓ 完成 | 機器可讀 JSON Schema |
| `capital_pool.contract.md` | ✓ 完成 | human-readable contract（status lifecycle、single-runtime rule、API sketch） |
| `persona_capital_binding.py` | ✓ 完成 | `PersonaCapitalBinding` dataclass；`PersonaCapitalBindingStore`；single-live-owner 強制；`permits_deployment_to()` |
| `persona_capital_binding.schema.json` | ✓ 完成 | 機器可讀 JSON Schema（`allowed_deployment_scope` 已對齊） |

### 測試

| 檔案 | 狀態 |
|---|---|
| `smoke_test_capital_pool.py` | ✓ `64/64 checks passed` |

---

## 3. Acceptance Criteria 驗證

| 標準 | 驗證方式 | 結果 |
|---|---|---|
| pool and binding ownership are explicit | `capital_pool.contract.md` §2.2、§3.2；`capital_pool.py` 及 `persona_capital_binding.py` docstring；`BINDING_AND_DEPLOYMENT_SEMANTICS.md` §5、§12 | ✓ |
| single-pool runtime rule is documented | `BINDING_AND_DEPLOYMENT_SEMANTICS.md` §9、§10；`capital_pool.py` `is_single_runtime_enforced()`；`persona_capital_binding.py` `_check_single_live_owner()`；`capital_pool.contract.md` §2.5、§3.8 | ✓ |

---

## 4. CAP-001A 建議事項處理結果

Codex 在審查 CAP-001A 時提出兩個非阻塞建議：

1. **`deployment_mode` → `allowed_deployment_scope` schema rename drift**
   - `capital.persona_capital_bindings` DB schema 已使用 `allowed_deployment_scope`（`Pantheon_資料表_Schema_設計版.md` §10.4）
   - `persona_capital_binding.schema.json` 已使用 `allowed_deployment_scope`
   - `runtime.runtime_bindings.deployment_mode` 保留，語意正確（它表達的是 runtime 實際部署狀態，不是權限上限）
   - **狀態：已解決**

2. **governance vs execution status mapping 說明**
   - `BINDING_AND_DEPLOYMENT_SEMANTICS.md` §16 已加入完整的 CapitalPool 與 PersonaCapitalBinding governance/execution status 對映表
   - **狀態：已解決**

---

## 5. Reviewer 請確認事項

1. `BINDING_AND_DEPLOYMENT_SEMANTICS.md` §16 的 status mapping 是否足夠清楚，供 RUN-001 作為參考
2. `capital_pool.contract.md` 的 §7 downstream task 描述是否與 RUN-001 / CAP-002 的預期接口對齊
3. `persona_capital_binding.py` 的 `activate()` 要求 `approval_decision_id` 的語意是否符合 Governance Plane 的預期
4. 64/64 smoke checks 是否涵蓋 CAP-001 acceptance criteria 的主要路徑

---

## 6. 已知 Non-blocking 事項（不阻塞本 task 通過）

- `CapitalPool.risk_policy_ref` 目前是自由文字 ref，實際 policy 物件尚未定義（屬於後續 GOV-001 範疇）
- `PoolSleeve` 模型尚未定義，文件中已預留擴充空間（`BINDING_AND_DEPLOYMENT_SEMANTICS.md` §10）
- API 路徑目前為草案，正式 OpenAPI spec 屬於後續 BFF / APP-001 範疇

---

## 7. 結論

所有 CAP-001 acceptance criteria 已滿足。交付物一致、smoke tests 全過、schema 無 drift。
請 Codex 審查並決定是否可進入 `review_approved`，並 handoff 給 CAP-001 owner 結案。
