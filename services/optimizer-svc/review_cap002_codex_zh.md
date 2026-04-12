# CAP-002 Review Packet

Reviewer: Copilot  
Owner: Codex  
Date: 2026-04-10

## 範圍

本輪為 `CAP-002` 在 `optimizer-svc` 內新增 v1 multi-persona synthesis module，對齊
`MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md` 的以下要求：

- weighted fusion 必須在 `optimizer-svc` 內部實作
- 同一 pool / scope 只產出一個 canonical synthesis artifact
- 每次聚合都必須留下 `conflict_resolution_log`
- long/short 高 conviction、risk posture、高重要性 pool、高風險首次 canary/live 等情況需能送 committee

## 主要產物

- `services/optimizer-svc/portfolio_synthesis/models.py`
- `services/optimizer-svc/portfolio_synthesis/synthesizer.py`
- `services/optimizer-svc/portfolio_synthesis/__init__.py`
- `services/optimizer-svc/test_portfolio_synthesis.py`
- `services/optimizer-svc/smoke_test_portfolio_synthesis.py`

## 我確認過的行為

1. `PortfolioSynthesizer.synthesize()` 仍維持直接回傳 outcome（artifact 或 committee referral）。
2. 新增 `synthesize_with_log()`，可同時取回 outcome 與 `ConflictResolutionLog`。
3. 新增 `last_conflict_resolution_log`，讓下列三種路徑都能讀到 log：
   - weighted fusion 成功
   - committee escalation
   - all proposals hard-vetoed 後拋出 `SynthesisError`
4. `PoolRiskPolicy` 現在以 `metadata.asset_classes` 判斷 forbidden asset class，比拿 `directions` 當 asset class 更符合語義。
5. committee escalation 新增高風險策略族首次進 `canary/live` 的判斷，對齊 L1 §6.3。

## 驗證

已執行：

```bash
python3 -m py_compile \
  services/optimizer-svc/portfolio_synthesis/__init__.py \
  services/optimizer-svc/portfolio_synthesis/models.py \
  services/optimizer-svc/portfolio_synthesis/synthesizer.py \
  services/optimizer-svc/test_portfolio_synthesis.py \
  services/optimizer-svc/smoke_test_portfolio_synthesis.py

python3 -m unittest discover -s services/optimizer-svc -p 'test_*.py'

python3 services/optimizer-svc/smoke_test_portfolio_synthesis.py
```

結果：

- `py_compile` pass
- `unittest`: 7 tests pass
- smoke test: 3/3 groups pass

## reviewer 建議檢查點

1. `committee_override` 是否要僅作為 log path 標記，還是未來要有單獨 artifact method 的更細分語意。
2. `metadata` 欄位目前承載 `asset_classes`、`strategy_family_risk`、`first_deployment_in_scope` 等 normalization 輸入；若後續要固定欄位，可在 follow-on 補 schema。

## 結論

此 packet 對應 `CAP-002` owner handoff。就目前 acceptance 而言，weighted fusion、單一 artifact、以及 conflict log 三項已可驗證。
