"""Integration proof that research training paths do not route broker orders."""
from __future__ import annotations

from pathlib import Path

from services.governance.research_activation.no_order_route_scanner import (
    assert_no_order_route_after_training,
    scan_default_research_adapters,
    scan_paths,
)


def _ohlcv_records(instruments: tuple[str, ...] = ("AAA", "BBB", "CCC")) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    closes = {
        "AAA": [100.0, 101.5, 102.1, 101.8, 103.4, 104.1, 103.6, 104.8, 105.2],
        "BBB": [50.0, 50.4, 50.9, 50.6, 51.3, 51.7, 51.1, 51.9, 52.2],
        "CCC": [75.0, 75.6, 76.1, 75.8, 76.9, 77.5, 77.2, 78.1, 78.7],
    }
    for instrument in instruments:
        for idx, close in enumerate(closes[instrument], start=1):
            records.append(
                {
                    "instrument": instrument,
                    "date": f"2024-01-{idx:02d}",
                    "open": round(close * 0.995, 4),
                    "high": round(close * 1.01, 4),
                    "low": round(close * 0.99, 4),
                    "close": close,
                    "volume": 500000 + idx * 10000,
                }
            )
    return records


def _qlib_dataset() -> dict[str, object]:
    return {
        "dataset_id": "dataset:no-order-qlib",
        "strategy_id": "no-order-qlib-strategy",
        "source_dataset_refs": ["dataset:no-order-source"],
        "source_strategy_spec_id": "strategy-spec:no-order-qlib-v1",
        "data_frequency": "daily",
        "records": _ohlcv_records(("AAA", "BBB")),
    }


def _finrl_dataset() -> dict[str, object]:
    return {
        "dataset_id": "dataset:no-order-finrl",
        "strategy_id": "no-order-finrl-strategy",
        "source_dataset_refs": ["dataset:no-order-source"],
        "source_strategy_spec_id": "strategy-spec:no-order-finrl-v1",
        "decision_focus": "exit_timing",
        "data_frequency": "daily",
        "action_labels": ["hold", "buy_small", "sell_small", "buy_large", "sell_large"],
        "position_ratio": 0.2,
        "cash_ratio": 0.8,
        "records": _ohlcv_records(("AAA", "BBB")),
    }


def _rllib_dataset() -> dict[str, object]:
    return {
        "dataset_id": "dataset:no-order-rllib",
        "strategy_id": "no-order-rllib-strategy",
        "source_dataset_refs": ["dataset:no-order-source"],
        "source_strategy_spec_id": "strategy-spec:no-order-rllib-v1",
        "decision_focus": "rebalance",
        "data_frequency": "daily",
        "transaction_cost_pct": 0.001,
        "slippage_bps": 5,
        "max_position_size": 0.3,
        "records": _ohlcv_records(("AAA", "BBB", "CCC")),
    }


def _trl_events() -> list[dict[str, object]]:
    return [
        {
            "feedback_event_id": "fb-no-order-001",
            "actor_role": "operator",
            "promotion_state": "candidate",
            "action": "approve",
            "strategy_family": "equity_cross_sectional",
            "operator_id": "op-001",
            "artifact": {"artifact_id": "artifact-001", "sharpe": 1.2},
        },
        {
            "feedback_event_id": "fb-no-order-002",
            "actor_role": "approver",
            "promotion_state": "paper",
            "action": "reject",
            "strategy_family": "stat_arb",
            "operator_id": "op-002",
            "artifact": {"artifact_id": "artifact-002", "sharpe": 0.2},
        },
        {
            "feedback_event_id": "fb-no-order-003",
            "actor_role": "operator",
            "promotion_state": "candidate",
            "action": "edit",
            "strategy_family": "equity_cross_sectional",
            "operator_id": "op-003",
            "artifact": {"artifact_id": "artifact-003-original", "sharpe": 0.7},
            "artifact_edited": {"artifact_id": "artifact-003-edited", "sharpe": 1.0},
        },
    ]


def test_static_scanner_passes_default_research_adapter_roots() -> None:
    result = scan_default_research_adapters(Path.cwd())

    assert result.passed, result.to_dict()
    assert "services/research/finrl/adapter.py" in result.checked_files
    assert "services/research/rllib/adapter/rllib_adapter.py" in result.checked_files
    assert "services/research/qlib/adapter/qlib_adapter.py" in result.checked_files
    assert "services/learning/trl/adapter/trl_adapter.py" in result.checked_files


def test_static_scanner_detects_forbidden_broker_order_route(tmp_path: Path) -> None:
    bad_adapter = tmp_path / "bad_adapter.py"
    bad_adapter.write_text(
        "from services.broker.main import submit_order\n"
        "def train_step(payload):\n"
        "    return submit_order(payload)\n",
        encoding="utf-8",
    )

    result = scan_paths([bad_adapter], repo_root=tmp_path)

    assert result.passed is False
    assert {violation.kind for violation in result.violations} == {
        "forbidden_import",
        "forbidden_call",
    }


def test_training_steps_leave_broker_outbox_empty() -> None:
    from services.learning.trl.adapter.trl_adapter import run_trl_dpo_workflow
    from services.research.finrl.engine.finrl_adapter import run_finrl_workflow
    from services.research.qlib.adapter.qlib_adapter import run_qlib_workflow
    from services.research.rllib.adapter.rllib_adapter import run_rllib_workflow

    training_steps = {
        "finrl_stub_training_step": lambda: run_finrl_workflow(_finrl_dataset()),
        "rllib_stub_training_step": lambda: run_rllib_workflow(_rllib_dataset()),
        "qlib_stub_training_step": lambda: run_qlib_workflow(_qlib_dataset()),
        "trl_stub_training_step": lambda: run_trl_dpo_workflow(
            _trl_events(),
            dataset_id="dataset:no-order-trl",
            strategy_id="no-order-trl-strategy",
            source_dataset_refs=["dataset:no-order-feedback"],
        ),
    }

    proofs = [
        assert_no_order_route_after_training(training_step, label=label)
        for label, training_step in training_steps.items()
    ]

    assert {proof.label for proof in proofs} == set(training_steps)
    assert all(proof.passed for proof in proofs)
    assert all(proof.broker_outbox_count == 0 for proof in proofs)
    assert all(proof.broker_outbox == tuple() for proof in proofs)
