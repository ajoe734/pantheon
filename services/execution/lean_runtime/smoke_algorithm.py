from __future__ import annotations

import hashlib
import importlib
import json
import os
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, Mapping

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.execution.artifact_loader import (
    ArtifactLoader,
    materialize_execution_projection,
)
from services.execution.lean_runtime.bootstrap_contract import (
    PANTHEON_LEAN_REMOTE,
    PANTHEON_LEAN_SOURCE_PATH,
    materialize_runtime_bootstrap_request,
)


LEAN_ALGORITHM_PATH = REPO_ROOT / "lean" / "Algorithm.Python"

SMOKE_STRATEGY_ID = "lean-smoke-alpha"
SMOKE_VERSION = "1.0.0"
SMOKE_SYMBOL = "AAPL.US"
SMOKE_TICKER = "AAPL"
SMOKE_SIGNAL_ID = "sig-lean-smoke-001"
SMOKE_BINDING_ID = "rtb-lean-smoke-001"
SMOKE_RUNTIME_ID = "rt-lean-smoke-001"
SMOKE_PLAN_ID = "dp-lean-smoke-001"
SMOKE_CAPITAL_POOL_ID = "pool-lean-smoke-001"
SMOKE_PERSONA_CAPITAL_BINDING_ID = "pcb-lean-smoke-001"
SMOKE_BRIDGE_COMMIT = "lean-algo-smoke-bridge"


@dataclass(frozen=True)
class SyntheticOhlcvBar:
    trading_date: date
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["trading_date"] = self.trading_date.isoformat()
        return payload


@dataclass(frozen=True)
class AlgorithmSmokeResult:
    synthetic_bar_count: int
    raw_on_data_callbacks: int
    executed_on_data_callbacks: int
    fill_count: int
    fill_events: list[dict[str, Any]]
    loaded_metadata: dict[str, Any]
    loaded_signal: dict[str, Any]
    runtime_context: dict[str, Any]
    bootstrap_env: dict[str, str]
    broker_production_live_enabled: str | None
    object_store_keys: list[str]
    synthetic_ohlcv: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class InMemoryLeanObjectStore:
    """Small LEAN Object Store double supporting .NET and Python method names."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    def ContainsKey(self, key: str) -> bool:  # noqa: N802
        return key in self._objects

    def contains_key(self, key: str) -> bool:
        return self.ContainsKey(key)

    def Read(self, key: str) -> str:  # noqa: N802
        return self._objects[key].decode("utf-8")

    def read(self, key: str) -> str:
        return self.Read(key)

    def ReadBytes(self, key: str) -> bytes:  # noqa: N802
        return self._objects[key]

    def read_bytes(self, key: str) -> bytes:
        return self.ReadBytes(key)

    def Save(self, key: str, value: str | bytes) -> None:  # noqa: N802
        self._objects[key] = value if isinstance(value, bytes) else value.encode("utf-8")

    def save(self, key: str, value: str | bytes) -> None:
        self.Save(key, value)

    def SaveBytes(self, key: str, value: bytes) -> None:  # noqa: N802
        self._objects[key] = value

    def save_bytes(self, key: str, value: bytes) -> None:
        self.SaveBytes(key, value)

    def keys(self) -> list[str]:
        return sorted(self._objects)


class SyntheticLeanBar:
    def __init__(self, bar: SyntheticOhlcvBar) -> None:
        self.Symbol = bar.symbol
        self.Time = datetime.combine(bar.trading_date, datetime.min.time())
        self.Open = bar.open
        self.High = bar.high
        self.Low = bar.low
        self.Close = bar.close
        self.Value = bar.close
        self.Volume = bar.volume


class SyntheticSlice(dict[str, SyntheticLeanBar]):
    def ContainsKey(self, key: Any) -> bool:  # noqa: N802
        return str(key) in self


def run_algorithm_smoke() -> AlgorithmSmokeResult:
    """Run the CPU-only LEAN Python algorithm smoke against synthetic OHLCV."""

    artifact_payload = _artifact_payload()
    artifact_bytes = json.dumps(artifact_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    checksum = _checksum(artifact_bytes)
    metadata = _artifact_metadata(checksum)

    store = InMemoryLeanObjectStore()
    projection = ArtifactLoader.build_projection(SMOKE_STRATEGY_ID, SMOKE_VERSION)
    materialize_execution_projection(
        store,
        SimpleNamespace(
            metadata_key=projection.metadata_key,
            artifact_key=projection.artifact_key,
            metadata=metadata,
        ),
        artifact_bytes,
    )

    bootstrap_env = _bootstrap_env(checksum)
    algorithm_class = _load_algorithm_class()
    synthetic_bars = build_synthetic_ohlcv()

    with _patched_env(bootstrap_env, {"BROKER_PRODUCTION_LIVE_ENABLED": "false"}):
        algorithm = algorithm_class()
        algorithm.object_store = store
        algorithm.ObjectStore = store
        algorithm.Initialize()
        for bar in synthetic_bars:
            algorithm.OnData(SyntheticSlice({SMOKE_TICKER: SyntheticLeanBar(bar)}))
        observations = algorithm.get_smoke_observations()
        broker_flag = os.environ.get("BROKER_PRODUCTION_LIVE_ENABLED")

    fill_events = list(observations["fill_events"])
    return AlgorithmSmokeResult(
        synthetic_bar_count=len(synthetic_bars),
        raw_on_data_callbacks=int(observations["raw_on_data_callbacks"]),
        executed_on_data_callbacks=int(observations["executed_on_data_callbacks"]),
        fill_count=len(fill_events),
        fill_events=fill_events,
        loaded_metadata=dict(observations["loaded_metadata"]),
        loaded_signal=dict(observations["loaded_signal"]),
        runtime_context=dict(observations["runtime_context"]),
        bootstrap_env=dict(bootstrap_env),
        broker_production_live_enabled=broker_flag,
        object_store_keys=store.keys(),
        synthetic_ohlcv=[bar.to_dict() for bar in synthetic_bars],
    )


def run_algorithm_smoke_from_binding(
    plan_dict: dict,
    binding: Any,
) -> AlgorithmSmokeResult:
    """Run algorithm smoke using fixture-derived plan and RuntimeManager-created binding.

    This is the end-to-end variant: runtime_context returned by the algorithm
    will carry plan_dict['plan_id'] and binding.binding_id, proving the paper
    run is composed from the fixture identities rather than SMOKE_* constants.
    """
    artifact_payload = _artifact_payload()
    artifact_bytes = json.dumps(artifact_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    checksum = _checksum(artifact_bytes)
    metadata = _artifact_metadata_from_binding(checksum, plan_dict, binding)

    store = InMemoryLeanObjectStore()
    projection = ArtifactLoader.build_projection(SMOKE_STRATEGY_ID, SMOKE_VERSION)
    materialize_execution_projection(
        store,
        SimpleNamespace(
            metadata_key=projection.metadata_key,
            artifact_key=projection.artifact_key,
            metadata=metadata,
        ),
        artifact_bytes,
    )

    bootstrap_env = _bootstrap_env_from_binding(checksum, plan_dict, binding)
    algorithm_class = _load_algorithm_class()
    synthetic_bars = build_synthetic_ohlcv()

    with _patched_env(bootstrap_env, {"BROKER_PRODUCTION_LIVE_ENABLED": "false"}):
        algorithm = algorithm_class()
        algorithm.object_store = store
        algorithm.ObjectStore = store
        algorithm.Initialize()
        for bar in synthetic_bars:
            algorithm.OnData(SyntheticSlice({SMOKE_TICKER: SyntheticLeanBar(bar)}))
        observations = algorithm.get_smoke_observations()
        broker_flag = os.environ.get("BROKER_PRODUCTION_LIVE_ENABLED")

    fill_events = list(observations["fill_events"])
    return AlgorithmSmokeResult(
        synthetic_bar_count=len(synthetic_bars),
        raw_on_data_callbacks=int(observations["raw_on_data_callbacks"]),
        executed_on_data_callbacks=int(observations["executed_on_data_callbacks"]),
        fill_count=len(fill_events),
        fill_events=fill_events,
        loaded_metadata=dict(observations["loaded_metadata"]),
        loaded_signal=dict(observations["loaded_signal"]),
        runtime_context=dict(observations["runtime_context"]),
        bootstrap_env=dict(bootstrap_env),
        broker_production_live_enabled=broker_flag,
        object_store_keys=store.keys(),
        synthetic_ohlcv=[bar.to_dict() for bar in synthetic_bars],
    )


def build_synthetic_ohlcv() -> list[SyntheticOhlcvBar]:
    return [
        SyntheticOhlcvBar(date(2026, 1, 5), SMOKE_TICKER, 100.0, 101.5, 99.5, 101.0, 1000),
        SyntheticOhlcvBar(date(2026, 1, 6), SMOKE_TICKER, 101.0, 102.0, 100.5, 101.5, 1100),
        SyntheticOhlcvBar(date(2026, 1, 7), SMOKE_TICKER, 101.5, 103.0, 101.0, 102.5, 1200),
        SyntheticOhlcvBar(date(2026, 1, 8), SMOKE_TICKER, 102.5, 103.5, 102.0, 103.0, 1300),
        SyntheticOhlcvBar(date(2026, 1, 9), SMOKE_TICKER, 103.0, 104.0, 102.5, 103.5, 1400),
    ]


def _artifact_payload() -> dict[str, Any]:
    return {
        "artifact_kind": "pantheon_lean_algorithm_smoke",
        "signal": {
            "signal_id": SMOKE_SIGNAL_ID,
            "version": "1.0",
            "strategy_id": SMOKE_STRATEGY_ID,
            "timestamp": "2026-01-05T14:30:00Z",
            "symbol": SMOKE_SYMBOL,
            "action": "BUY",
            "direction": "LONG",
            "quantity": 7,
            "quantity_type": "SHARES",
            "order_type": "MARKET",
            "metadata": {
                "confidence_score": 1.0,
                "source_task": "LEAN-ALGO-001",
            },
        },
    }


def _artifact_metadata_from_binding(checksum: str, plan: dict, binding: Any) -> dict[str, Any]:
    return {
        "registry_id": plan["artifact_id"],
        "strategy_id": plan.get("strategy_id", SMOKE_STRATEGY_ID),
        "version": plan["artifact_version"],
        "artifact_type": plan.get("artifact_type", "execution_bundle"),
        "artifact_state": "approved",
        "deployment_stage": plan["target_stage"],
        "promotion_state": plan["target_stage"],
        "checksum": checksum,
        "lineage": {
            "source_run_ids": ["train-lean-smoke-001"],
            "source_dataset_refs": ["synthetic-ohlcv-five-days"],
        },
        "created_at": "2026-01-05T00:00:00Z",
        "runtime_binding_id": binding.binding_id,
        "deployment_plan_id": plan["plan_id"],
    }


def _bootstrap_env_from_binding(checksum: str, plan: dict, binding: Any) -> dict[str, str]:
    deployment_plan = {
        "plan_id": plan["plan_id"],
        "approval_decision_id": plan.get("approval_decision_id", "appr-ooda-e2e-005-001"),
        "artifact_id": plan["artifact_id"],
        "artifact_version": plan["artifact_version"],
        "artifact_state": "approved",
        "artifact_checksum": checksum,
        "strategy_id": plan.get("strategy_id", SMOKE_STRATEGY_ID),
        "capital_pool_id": plan["capital_pool_id"],
        "target_stage": plan["target_stage"],
        "runtime_role": plan["target_stage"],
        "persona_capital_binding_id": binding.persona_capital_binding_id,
        "runtime_config_status": "approved",
        "risk_policy_ref": "risk-policy-lean-smoke",
        "risk_policy_evaluation": {
            "risk_policy_id": "risk-policy-lean-smoke",
            "risk_policy_version": "v1",
            "capital_pool_id": plan["capital_pool_id"],
            "target_type": "runtime_launch",
            "target_id": plan["plan_id"],
            "decision": "allowed",
            "checks": [],
            "blocking_reasons": [],
            "warnings": [],
            "evaluated_at": "2026-06-09T00:00:00Z",
            "trace_id": "trace-risk-policy-lean-smoke",
        },
    }
    runtime_binding = {
        "binding_id": binding.binding_id,
        "runtime_id": binding.runtime_id,
        "plan_id": binding.plan_id,
        "artifact_id": binding.artifact_id,
        "artifact_version": binding.artifact_version,
        "capital_pool_id": binding.capital_pool_id,
        "deployment_mode": binding.deployment_mode,
        "persona_capital_binding_id": binding.persona_capital_binding_id,
        "metadata": {
            "engine_bridge_repo": PANTHEON_LEAN_REMOTE,
            "engine_bridge_path": PANTHEON_LEAN_SOURCE_PATH,
            "engine_bridge_commit": SMOKE_BRIDGE_COMMIT,
        },
    }
    request = materialize_runtime_bootstrap_request(
        deployment_plan=deployment_plan,
        runtime_binding=runtime_binding,
        request_id=f"rbr-e2e-005-{binding.binding_id}",
        trace_id=f"trace-e2e-005-{binding.binding_id}",
    )
    env = request.to_runtime_env()
    env["PANTHEON_ARTIFACT_TYPE"] = "execution_bundle"
    return env


def _artifact_metadata(checksum: str) -> dict[str, Any]:
    return {
        "registry_id": f"reg-{SMOKE_STRATEGY_ID}-{SMOKE_VERSION}",
        "strategy_id": SMOKE_STRATEGY_ID,
        "version": SMOKE_VERSION,
        "artifact_type": "execution_bundle",
        "artifact_state": "approved",
        "deployment_stage": "paper",
        "promotion_state": "paper",
        "checksum": checksum,
        "lineage": {
            "source_run_ids": ["train-lean-smoke-001"],
            "source_dataset_refs": ["synthetic-ohlcv-five-days"],
        },
        "created_at": "2026-01-05T00:00:00Z",
        "runtime_binding_id": SMOKE_BINDING_ID,
        "deployment_plan_id": SMOKE_PLAN_ID,
    }


def _bootstrap_env(checksum: str) -> dict[str, str]:
    deployment_plan = {
        "plan_id": SMOKE_PLAN_ID,
        "approval_decision_id": "appr-lean-smoke-001",
        "artifact_id": f"reg-{SMOKE_STRATEGY_ID}-{SMOKE_VERSION}",
        "artifact_version": SMOKE_VERSION,
        "artifact_state": "approved",
        "artifact_checksum": checksum,
        "strategy_id": SMOKE_STRATEGY_ID,
        "capital_pool_id": SMOKE_CAPITAL_POOL_ID,
        "target_stage": "paper",
        "runtime_role": "paper",
        "persona_capital_binding_id": SMOKE_PERSONA_CAPITAL_BINDING_ID,
        "runtime_config_status": "approved",
        "risk_policy_ref": "risk-policy-lean-smoke",
        "risk_policy_evaluation": {
            "risk_policy_id": "risk-policy-lean-smoke",
            "risk_policy_version": "v1",
            "capital_pool_id": SMOKE_CAPITAL_POOL_ID,
            "target_type": "runtime_launch",
            "target_id": SMOKE_PLAN_ID,
            "decision": "allowed",
            "checks": [],
            "blocking_reasons": [],
            "warnings": [],
            "evaluated_at": "2026-06-09T00:00:00Z",
            "trace_id": "trace-risk-policy-lean-smoke",
        },
    }
    runtime_binding = {
        "binding_id": SMOKE_BINDING_ID,
        "runtime_id": SMOKE_RUNTIME_ID,
        "plan_id": SMOKE_PLAN_ID,
        "artifact_id": f"reg-{SMOKE_STRATEGY_ID}-{SMOKE_VERSION}",
        "artifact_version": SMOKE_VERSION,
        "capital_pool_id": SMOKE_CAPITAL_POOL_ID,
        "deployment_mode": "paper",
        "persona_capital_binding_id": SMOKE_PERSONA_CAPITAL_BINDING_ID,
        "metadata": {
            "engine_bridge_repo": PANTHEON_LEAN_REMOTE,
            "engine_bridge_path": PANTHEON_LEAN_SOURCE_PATH,
            "engine_bridge_commit": SMOKE_BRIDGE_COMMIT,
        },
    }
    request = materialize_runtime_bootstrap_request(
        deployment_plan=deployment_plan,
        runtime_binding=runtime_binding,
        request_id="rbr-lean-smoke-001",
        trace_id="trace-lean-smoke-001",
    )
    env = request.to_runtime_env()
    env["PANTHEON_ARTIFACT_TYPE"] = "execution_bundle"
    return env


def _load_algorithm_class() -> type:
    if str(LEAN_ALGORITHM_PATH) not in sys.path:
        sys.path.insert(0, str(LEAN_ALGORITHM_PATH))
    module = importlib.import_module("pantheon_algo.smoke_loader_test")
    return module.PantheonSmokeLoaderAlgorithm


def _checksum(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


@contextmanager
def _patched_env(updates: Mapping[str, str], extra: Mapping[str, str]) -> Iterator[None]:
    keys = set(updates) | set(extra)
    previous = {key: os.environ.get(key) for key in keys}
    try:
        os.environ.update({key: str(value) for key, value in updates.items()})
        os.environ.update({key: str(value) for key, value in extra.items()})
        yield
    finally:
        for key in keys:
            os.environ.pop(key, None)
        for key, value in previous.items():
            if value is not None:
                os.environ[key] = value


if __name__ == "__main__":
    print(json.dumps(run_algorithm_smoke().to_dict(), indent=2, sort_keys=True))
