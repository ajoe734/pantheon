# Gemini OSS Alignment Audit

**Audit ID:** AUD-GEMINI-001  
**Auditor:** Gemini  
**Date:** 2026-04-02  

## Summary
本次審計確認 Gemini 負責的任務中，本地基礎建設（Foundation）與合約工作（Contract）路徑清晰，但涉及外部 OSS 整合（OpenClaw, MLflow, TRL, Ray）的部分普遍存在「只有規格、沒有實作整合」的問題。所有 Epic B/E/F 的任務都必須補齊版本釘選與適配器邊界。

## Findings

### Category A: Local Foundation (Valid as-is)
- **`REG-002` (Promotion Gate)**: 這是本地 Python 實作的治理邏輯，不依賴外部框架，符合預期。已實作 `gate.py` 與 `cli.py`。

### Category B: Local Contract Only (Valid but needs follow-up)
- **`EX-001` (Artifact Loader Contract)**: 已完成，但僅為 Markdown 合約。雖然已通過 Claude 審查，但實作部分（LEAN C# 端的 `IObjectStore` 整合）尚未開始，目前屬於「Contract Only」。

### Category C: Upstream OSS Integration (Needs correction)
- **`OC-002` (OpenClaw Cron)**: 
    - **問題**: 目前僅有「定義」任務，未指定如何接合上游 `OpenClaw` 的 Workflow Entrypoints。
    - **缺口**: 需要釘選上游 Repo 版本，定義如何將 `StrategySpec` 轉換為上游 Workflow 參數。
- **`RS-001` (Research Ingestion)**: 
    - **問題**: 尚未鎖定 `OpenAlex API` 或 `GitHub REST API` 的具體封裝方式。
    - **缺口**: 缺乏針對結構化來源的 Ingestion Adapter 邊界定義。
- **`LP-003` (MLflow/W&B Integration)**: 
    - **問題**: 尚未決定使用 `MLflow` 還是 `W&B` 作為 Experiment Registry。
    - **缺口**: 需要選擇工具、釘選版本，並定義從 `REG-001` 到實驗後端的 Metadata 映射。
- **`LP-004` & `LP-005` (TRL/FinRL/RLlib)**: 
    - **問題**: 雖在 Dockerfile 中有部分套件占位，但缺乏受治理的 I/O 邊界。
    - **缺口**: 需要釘選版本並定義適配器，確保 RL 訓練輸出的 Policy 必須通過 `REG-001` 註冊。

## Missing Upstream Integrations
| 任務 | 目標 OSS | 缺失項 |
|---|---|---|
| `OC-002` | OpenClaw | Upstream repo ref, version pin, workflow entrypoint mapping |
| `RS-001` | OpenAlex/GitHub | API client selection, source normalization adapter |
| `LP-003` | MLflow/W&B | Tool selection (MLflow preferred for GCP), version pin |
| `LP-004` | TRL | Package dependency, preference-loop adapter |
| `LP-005` | FinRL/Ray | Specific version pins for Ray[Tune/RLlib], training output adapter |

## Missing Smoke Tests
- **`EX-001`**: 缺乏在 LEAN Runtime 實際呼叫 `ObjectStore` 讀取 `metadata.json` 的冒煙測試。
- **`REG-002`**: 雖然有 CLI，但缺乏與 `SignalStore` 整合的端到端狀態變更測試。

## Recommended Task Corrections
1. **`EX-001`**: 標記為 `done (contract-only)`，建立 `EX-002` 處理 LEAN-native C# 實作。
2. **`OC-002`**: 更新驗收準則，必須包含對上游 OpenClaw 的配置實作。
3. **`LP-003`**: 優先決定使用 MLflow，並在 `OSS_INTEGRATION_CHECKLIST.md` 中更新狀態。
4. **建立新 Spike**: 針對 `OpenClaw` 上游 Repo 的整合方式進行評估。
