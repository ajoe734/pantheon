# APP-001 審查核准（Codex）

**任務**: `APP-001`  
**作者**: Qwen  
**審查者**: Codex  
**狀態**: APPROVED  
**日期**: 2026-04-10

## 結論

這版 `APP-001` 可以核准。Qwen 交付的三份正式文件已把 `APP-001A/B/C` 的前置成果收斂成可落地的 v1 合約，涵蓋 33 個 canonical BFF surfaces、6 個 consultation surfaces、degraded operator path、RBAC、composed views 與 read-only 邊界。  

本輪 reviewer 做了三個必要 cleanup，避免 formal contract 再次長出 pseudo-canonical object 或 write-path 歧義：

1. `BFF_API_CONTRACT.md` 的 `IN-05` 已從未定義的 `KillSwitchState` 收斂回 `FreezeOrder` + `RuntimeBinding` 的 composed read shape，和 `APP-001A` inventory、`BFF_HA`、`KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY` 對齊。
2. `CONSULTATION_SURFACE_CONTRACT.md` 已把 consultation surfaces 明確收斂為 `SessionPersona` / `SessionPersona.metadata.consultation.*` 的 read projection，不再把 `ConsultationOutcome` / `ConsultSession` 當成新的 pseudo-canonical object。
3. `BFF_API_CONTRACT.md` 的 read-only guarantee 已移除含糊的 command-facade 說法，明確限定 APP-001 只覆蓋 GET surfaces；任何 admin / write path 仍屬 separate path，不得借殼成 BFF 偽寫入面。

## 核准依據

1. `services/control-plane/bff/BFF_API_CONTRACT.md` 已正式鎖定：
   - versioned query/response envelope
   - per-surface RBAC 與 degraded behavior
   - 4 條 composed views
   - SSE realtime feed v1 決策
   - read-only guarantee 與 secondary control path boundary
2. `services/control-plane/bff/CONSULTATION_SURFACE_CONTRACT.md` 已守住 consultation task 邊界：
   - consultation 來源仍是 `SessionPersona` / `ConsultPolicy`
   - BFF 只讀 Persona Plane 已持久化的 session metadata
   - 不自行綜合 responder vote / rationale / evidence 成新的 owner truth
3. `services/control-plane/bff/DEGRADED_OPERATOR_PATH.md` 已把 partial degradation、total BFF outage、secondary control path、admin CLI requirement 與「never show none」規則寫清楚，足夠供 APP-002 接續 operator path 細化。

## 驗證

- 人工逐份核對：
  - `services/control-plane/bff/BFF_API_CONTRACT.md`
  - `services/control-plane/bff/CONSULTATION_SURFACE_CONTRACT.md`
  - `services/control-plane/bff/DEGRADED_OPERATOR_PATH.md`
- 交叉比對來源：
  - `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`
  - `PERSONA_RUNTIME_MODEL.md`
  - `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md`
  - `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`
  - `BINDING_AND_DEPLOYMENT_SEMANTICS.md`
- reviewer cleanup 後再檢查：
  - 不再出現 `KillSwitchState`
  - 不再把 `ConsultationOutcome` / `ConsultSession` 當成 canonical object
  - 不再殘留 `PERSONA_RUNTIME_MODEL_MODEL.md` typo 或 `command facade` 歧義
- 本輪未執行自動化測試；此 task 屬 design contract / review artifact，無對應 runtime code path 需要 smoke。

## 結果

`APP-001` 已完成本輪目標：governed BFF 與 consultation surface 的 formal contract 已成形，read-only 與 no-parallel-truth 邊界清楚，degraded operator path 也已文件化。後續可由 owner 依 canonical lifecycle 正式收尾為 `done`，並讓 `APP-002` 以這組 contracts 繼續定義 operator-facing deployment / incident / evolution surfaces。
