# Promotion Gate (REG-002)

This root-level file is a legacy compatibility note.

Canonical REG-002 files now live under:

- `services/registry/promotion/README.md`
- `services/registry/promotion/gate.py`
- `services/registry/promotion/cli.py`

Legacy root wrappers remain:

- `gate.py`
- `cli.py`

## 職責
實作受治理的策略生命週期切換。確保任何進入 `paper` 或 `live` 狀態的 Artifact 都具備必要的審核元數據。

## 晉升要求 (Promotion Requirements)

| 目標狀態 | 必要條件 |
| :--- | :--- |
| `candidate` | 1. 複現成功 (`replication_success`) <br> 2. 具備血緣資訊 (`lineage.source_run_id`) |
| `paper` | 1. 風險審查通過 (`evaluation_summary.risk_review_passed`) <br> 2. 具備基本回測指標 (Sharpe Ratio) |
| `live` | 1. 明確的核准人 (`approver`) <br> 2. 具備回退目標版本 (`rollback_target`) |

## 使用範例 (Python)

```python
from gate import PromotionGate, PromotionState

gate = PromotionGate()

# 假設從 Registry 讀取的 entry
entry = {
    "strategy_id": "momentum-alpha",
    "version": "1.2.0",
    "lifecycle_state": "paper",
    "evaluation_summary": {"risk_review_passed": True, "sharpe_ratio": 1.5}
}

# 晉升至 LIVE (會因為缺 approver/rollback_target 而被攔截)
updated_entry = gate.promote(entry, PromotionState.LIVE, approver="human-trader-01")
```


## Workspace Cutover

Operate Pantheon from this repo. Use `scripts/launch-docs-site.sh`, `scripts/ai-status.sh`, and `.orchestrator/` here. The sibling `Lean` checkout is execution-side only and should no longer host Pantheon collaboration state or dashboard processes.
