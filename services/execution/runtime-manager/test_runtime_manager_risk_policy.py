"""RiskPolicy gate coverage for the execution-plane RuntimeManager."""
from __future__ import annotations

import importlib.util as _ilu
import sys
from pathlib import Path
from typing import Any

import pytest


_HERE = Path(__file__).parent


def _load(name: str) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = _ilu.spec_from_file_location(name, _HERE / f"{name}.py")
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_rm = _load("runtime_manager")
_rb = _load("runtime_binding")

DeployRuntimeRequest = _rm.DeployRuntimeRequest
RuntimeManager = _rm.RuntimeManager
RuntimeManagerError = _rm.RuntimeManagerError
RuntimeBindingStore = _rb.RuntimeBindingStore


def _request(**overrides: Any) -> Any:
    payload = {
        "plan_id": "plan-001",
        "persona_capital_binding_id": "pcb-001",
        "runtime_id": "rt-001",
        "capital_pool_id": "pool-001",
        "artifact_id": "artifact-alpha",
        "artifact_version": "1.0.0",
        "deployment_mode": "paper",
        "plan_status": "approved",
    }
    payload.update(overrides)
    return DeployRuntimeRequest(**payload)


def test_runtime_launch_rejects_risk_policy_identity_mismatch() -> None:
    manager = RuntimeManager(RuntimeBindingStore())

    with pytest.raises(RuntimeManagerError, match="RiskPolicy"):
        manager.deploy(
            _request(
                risk_policy_ref="risk-other",
                risk_policy={"risk_policy_id": "risk-main"},
            )
        )


def test_runtime_launch_records_allowed_risk_policy_evaluation() -> None:
    manager = RuntimeManager(RuntimeBindingStore())

    outcome = manager.deploy(
        _request(
            risk_policy_ref="risk-main",
            risk_policy={"risk_policy_id": "risk-main", "allowed_stages": ["paper"]},
        )
    )

    assert outcome.binding.metadata["risk_policy_evaluation"]["decision"] == "allowed"
