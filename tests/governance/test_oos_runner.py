from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from services.governance.research_activation.oos_runner import (
    SCHEMA_VERSION,
    OOSRunnerError,
    assert_oos_no_order_route,
)


def test_oos_runner_emits_generic_no_order_route_proof(tmp_path: Path) -> None:
    adapter = tmp_path / "safe_adapter.py"
    adapter.write_text(
        "def run_oos(payload):\n"
        "    return {'sharpe': 1.3, 'artifact_state': 'candidate'}\n",
        encoding="utf-8",
    )

    proof = assert_oos_no_order_route(
        lambda: {"oos_metrics": {"sharpe": 1.3}, "artifact_state": "candidate"},
        adapter_id="qlib-prod-oos",
        adapter_kind="Qlib",
        produced_artifact_types=("model_artifact", "evaluation_result", "candidate_packet"),
        adapter_paths=(adapter,),
        repo_root=tmp_path,
        evidence_refs=("support/evidence/qlib/oos.json",),
        label="qlib_oos_step",
        metadata={"activation_tier": "R3"},
    )

    payload = proof.to_dict()

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["adapter_id"] == "qlib-prod-oos"
    assert payload["adapter_kind"] == "qlib"
    assert payload["no_order_route"] is True
    assert payload["static_scan"]["passed"] is True
    assert payload["dynamic_probe"]["passed"] is True
    assert payload["dynamic_probe"]["broker_outbox_count"] == 0
    assert payload["produced_artifact_types"] == [
        "model_artifact",
        "evaluation_result",
        "candidate_packet",
    ]
    assert payload["execution_targets"] == ["research", "registry_review"]


def test_oos_runner_fails_closed_on_static_broker_order_route(tmp_path: Path) -> None:
    adapter = tmp_path / "unsafe_adapter.py"
    adapter.write_text(
        "from services.broker.main import submit_order\n"
        "def run_oos(payload):\n"
        "    return submit_order(payload)\n",
        encoding="utf-8",
    )

    with pytest.raises(OOSRunnerError, match="static_forbidden_import"):
        assert_oos_no_order_route(
            lambda: {"oos_metrics": {"sharpe": 1.3}},
            adapter_id="unsafe-oos",
            adapter_kind="finrl",
            produced_artifact_types=("model_artifact", "evaluation_result"),
            adapter_paths=(adapter,),
            repo_root=tmp_path,
        )


def test_oos_runner_fails_closed_on_dynamic_broker_import(tmp_path: Path) -> None:
    adapter = tmp_path / "safe_adapter.py"
    adapter.write_text("def run_oos(payload):\n    return {'ok': True}\n", encoding="utf-8")

    def unsafe_oos_step() -> object:
        return importlib.import_module("blocked_broker_route_fixture.orders")

    with pytest.raises(OOSRunnerError, match="dynamic_no_order_route_violation"):
        assert_oos_no_order_route(
            unsafe_oos_step,
            adapter_id="runtime-touching-oos",
            adapter_kind="rllib",
            produced_artifact_types=("model_artifact", "evaluation_result"),
            adapter_paths=(adapter,),
            repo_root=tmp_path,
            forbidden_import_prefixes=("blocked_broker_route_fixture",),
        )


def test_oos_runner_fails_closed_on_order_capable_output_controls(
    tmp_path: Path,
) -> None:
    adapter = tmp_path / "safe_adapter.py"
    adapter.write_text("def run_oos(payload):\n    return {'ok': True}\n", encoding="utf-8")

    with pytest.raises(OOSRunnerError, match="forbidden_adapter_output"):
        assert_oos_no_order_route(
            lambda: {"oos_metrics": {"sharpe": 1.3}},
            adapter_id="order-output-oos",
            adapter_kind="vectorbt",
            produced_artifact_types=("model_artifact", "broker_order_route"),
            adapter_paths=(adapter,),
            repo_root=tmp_path,
        )

    with pytest.raises(OOSRunnerError, match="order_capable_execution_target"):
        assert_oos_no_order_route(
            lambda: {"oos_metrics": {"sharpe": 1.3}},
            adapter_id="runtime-target-oos",
            adapter_kind="statsmodels",
            produced_artifact_types=("model_artifact", "evaluation_result"),
            execution_targets=("research", "runtime"),
            adapter_paths=(adapter,),
            repo_root=tmp_path,
        )
