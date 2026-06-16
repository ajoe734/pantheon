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
from typing import Any, Iterator, Mapping, Sequence

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
    loaded_signals: list[dict[str, Any]]
    packet_target_executions: list[dict[str, Any]]
    loaded_strategy_packet: dict[str, Any]
    loaded_packet_targets: list[dict[str, Any]]
    runtime_context: dict[str, Any]
    bootstrap_env: dict[str, str]
    broker_production_live_enabled: str | None
    object_store_keys: list[str]
    synthetic_ohlcv: list[dict[str, Any]]
    artifact_payload_checksum: str

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
            algorithm.OnData(SyntheticSlice({bar.symbol: SyntheticLeanBar(bar)}))
        observations = algorithm.get_smoke_observations()
        broker_flag = os.environ.get("BROKER_PRODUCTION_LIVE_ENABLED")

    fill_events = list(observations["fill_events"])
    readback_payload = _read_artifact_payload_from_store(store, projection.artifact_key)
    return AlgorithmSmokeResult(
        synthetic_bar_count=len(synthetic_bars),
        raw_on_data_callbacks=int(observations["raw_on_data_callbacks"]),
        executed_on_data_callbacks=int(observations["executed_on_data_callbacks"]),
        fill_count=len(fill_events),
        fill_events=fill_events,
        loaded_metadata=dict(observations["loaded_metadata"]),
        loaded_signal=dict(observations["loaded_signal"]),
        loaded_signals=_loaded_signals_from_readback(readback_payload),
        packet_target_executions=[],
        loaded_strategy_packet=dict(readback_payload.get("strategy_packet") or {}),
        loaded_packet_targets=[dict(target) for target in readback_payload.get("packet_targets") or []],
        runtime_context=dict(observations["runtime_context"]),
        bootstrap_env=dict(bootstrap_env),
        broker_production_live_enabled=broker_flag,
        object_store_keys=store.keys(),
        synthetic_ohlcv=[bar.to_dict() for bar in synthetic_bars],
        artifact_payload_checksum=checksum,
    )


def run_algorithm_smoke_from_binding(
    plan_dict: dict,
    binding: Any,
    *,
    strategy_packet: Mapping[str, Any] | None = None,
    packet_targets: Sequence[Mapping[str, Any]] | None = None,
    signal: Mapping[str, Any] | None = None,
) -> AlgorithmSmokeResult:
    """Run algorithm smoke using fixture-derived plan and RuntimeManager-created binding.

    This is the end-to-end variant: runtime_context returned by the algorithm
    will carry plan_dict['plan_id'] and binding.binding_id, proving the paper
    run is composed from the fixture identities rather than SMOKE_* constants.
    """
    artifact_payload = _artifact_payload(
        strategy_packet=strategy_packet,
        packet_targets=packet_targets,
        signal=signal,
    )
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
    synthetic_bars = build_synthetic_ohlcv(
        ticker=str(artifact_payload["signal"]["symbol"]).split(".", 1)[0],
        start_close=_signal_start_close(artifact_payload["signal"]),
    )

    with _patched_env(bootstrap_env, {"BROKER_PRODUCTION_LIVE_ENABLED": "false"}):
        algorithm = algorithm_class()
        algorithm.object_store = store
        algorithm.ObjectStore = store
        algorithm.Initialize()
        for bar in synthetic_bars:
            algorithm.OnData(SyntheticSlice({bar.symbol: SyntheticLeanBar(bar)}))
        observations = algorithm.get_smoke_observations()
        broker_flag = os.environ.get("BROKER_PRODUCTION_LIVE_ENABLED")

    fill_events = list(observations["fill_events"])
    readback_payload = _read_artifact_payload_from_store(store, projection.artifact_key)
    packet_target_executions = _execute_packet_target_smokes(
        plan_dict=plan_dict,
        binding=binding,
        strategy_packet=strategy_packet,
        packet_targets=packet_targets or [],
    )
    return AlgorithmSmokeResult(
        synthetic_bar_count=len(synthetic_bars),
        raw_on_data_callbacks=int(observations["raw_on_data_callbacks"]),
        executed_on_data_callbacks=int(observations["executed_on_data_callbacks"]),
        fill_count=len(fill_events),
        fill_events=fill_events,
        loaded_metadata=dict(observations["loaded_metadata"]),
        loaded_signal=dict(observations["loaded_signal"]),
        loaded_signals=_loaded_signals_from_readback(readback_payload),
        packet_target_executions=packet_target_executions,
        loaded_strategy_packet=dict(readback_payload.get("strategy_packet") or {}),
        loaded_packet_targets=[dict(target) for target in readback_payload.get("packet_targets") or []],
        runtime_context=dict(observations["runtime_context"]),
        bootstrap_env=dict(bootstrap_env),
        broker_production_live_enabled=broker_flag,
        object_store_keys=store.keys(),
        synthetic_ohlcv=[bar.to_dict() for bar in synthetic_bars],
        artifact_payload_checksum=checksum,
    )


def build_synthetic_ohlcv(*, ticker: str = SMOKE_TICKER, start_close: float = 101.0) -> list[SyntheticOhlcvBar]:
    close = max(float(start_close), 1.0)
    return [
        SyntheticOhlcvBar(date(2026, 1, 5), ticker, close - 1.0, close + 0.5, close - 1.5, close, 1000),
        SyntheticOhlcvBar(date(2026, 1, 6), ticker, close, close + 1.0, close - 0.5, close + 0.5, 1100),
        SyntheticOhlcvBar(date(2026, 1, 7), ticker, close + 0.5, close + 2.0, close, close + 1.5, 1200),
        SyntheticOhlcvBar(date(2026, 1, 8), ticker, close + 1.5, close + 2.5, close + 1.0, close + 2.0, 1300),
        SyntheticOhlcvBar(date(2026, 1, 9), ticker, close + 2.0, close + 3.0, close + 1.5, close + 2.5, 1400),
    ]


def _artifact_payload(
    *,
    strategy_packet: Mapping[str, Any] | None = None,
    packet_targets: Sequence[Mapping[str, Any]] | None = None,
    signal: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    targets = [dict(target) for target in packet_targets or []]
    if signal is None and targets:
        target_signal = targets[0].get("signal")
        if isinstance(target_signal, Mapping):
            signal = target_signal
    payload = {
        "artifact_kind": "pantheon_lean_algorithm_smoke",
        "signal": dict(signal or _default_smoke_signal()),
    }
    if strategy_packet is not None:
        payload["packet_schema"] = "pantheon_lean_evolved_strategy_packet_v1"
        payload["strategy_packet"] = dict(strategy_packet)
        payload["packet_targets"] = targets
    return payload


def _default_smoke_signal() -> dict[str, Any]:
    return {
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
    }


def _signal_start_close(signal: Mapping[str, Any]) -> float:
    metadata = signal.get("metadata") if isinstance(signal.get("metadata"), Mapping) else {}
    market_data = metadata.get("market_data") if isinstance(metadata.get("market_data"), Mapping) else {}
    for source in (market_data, metadata, signal):
        if not isinstance(source, Mapping):
            continue
        for key in ("close", "market_price", "last_price", "price", "limit_price"):
            value = source.get(key)
            if value in (None, ""):
                continue
            try:
                price = float(value)
            except (TypeError, ValueError):
                continue
            if price > 0:
                return price
    return 101.0


def _read_artifact_payload_from_store(store: InMemoryLeanObjectStore, artifact_key: str) -> dict[str, Any]:
    raw = store.read_bytes(artifact_key).decode("utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RuntimeError("LEAN smoke artifact readback must be a JSON object")
    return payload


def _loaded_signals_from_readback(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    loaded_signals: list[dict[str, Any]] = []
    for target in payload.get("packet_targets") or []:
        if not isinstance(target, Mapping):
            continue
        signal = target.get("signal")
        if isinstance(signal, Mapping):
            loaded_signals.append(dict(signal))
    if loaded_signals:
        return loaded_signals
    signal = payload.get("signal")
    return [dict(signal)] if isinstance(signal, Mapping) else []


def _execute_packet_target_smokes(
    *,
    plan_dict: Mapping[str, Any],
    binding: Any,
    strategy_packet: Mapping[str, Any] | None,
    packet_targets: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if not strategy_packet or not packet_targets:
        return []
    executions: list[dict[str, Any]] = []
    for target in packet_targets:
        if not isinstance(target, Mapping):
            continue
        signal = target.get("signal")
        if not isinstance(signal, Mapping):
            continue
        artifact_payload = _artifact_payload(
            strategy_packet=strategy_packet,
            packet_targets=packet_targets,
            signal=signal,
        )
        artifact_bytes = json.dumps(
            artifact_payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        checksum = _checksum(artifact_bytes)
        metadata = _artifact_metadata_from_binding(checksum, dict(plan_dict), binding)
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
        bootstrap_env = _bootstrap_env_from_binding(checksum, dict(plan_dict), binding)
        algorithm_class = _load_algorithm_class()
        synthetic_bars = build_synthetic_ohlcv(
            ticker=str(signal["symbol"]).split(".", 1)[0],
            start_close=_signal_start_close(signal),
        )
        with _patched_env(bootstrap_env, {"BROKER_PRODUCTION_LIVE_ENABLED": "false"}):
            algorithm = algorithm_class()
            algorithm.object_store = store
            algorithm.ObjectStore = store
            algorithm.Initialize()
            for bar in synthetic_bars:
                algorithm.OnData(SyntheticSlice({bar.symbol: SyntheticLeanBar(bar)}))
            observations = algorithm.get_smoke_observations()
            broker_flag = os.environ.get("BROKER_PRODUCTION_LIVE_ENABLED")
        fill_events = [dict(event) for event in observations["fill_events"]]
        loaded_signal = dict(observations["loaded_signal"])
        executions.append(
            {
                "target_ref": target.get("target_ref"),
                "target_signal_id": target.get("signal_id"),
                "target_symbol": target.get("execution_symbol"),
                "loaded_signal_id": loaded_signal.get("signal_id"),
                "loaded_signal_symbol": loaded_signal.get("symbol"),
                "loaded_signal_source_target_ref": (
                    loaded_signal.get("metadata", {}).get("packet_target_ref")
                    if isinstance(loaded_signal.get("metadata"), Mapping)
                    else None
                ),
                "fill_count": len(fill_events),
                "fill_signal_ids": [event.get("signal_id") for event in fill_events],
                "fill_symbols": [event.get("symbol") for event in fill_events],
                "runtime_binding_id": observations["runtime_context"].get(
                    "runtime_binding_id"
                ),
                "deployment_plan_id": observations["runtime_context"].get(
                    "deployment_plan_id"
                ),
                "artifact_payload_checksum": checksum,
                "broker_production_live_enabled": broker_flag,
                "replay": {
                    "target_signal_loaded": loaded_signal.get("signal_id")
                    == target.get("signal_id"),
                    "target_symbol_loaded": loaded_signal.get("symbol")
                    == target.get("execution_symbol"),
                    "target_ref_bound": (
                        loaded_signal.get("metadata", {}).get("packet_target_ref")
                        if isinstance(loaded_signal.get("metadata"), Mapping)
                        else None
                    )
                    == target.get("target_ref"),
                    "fill_event_emitted": len(fill_events) >= 1,
                    "fill_event_matches_loaded_signal": any(
                        event.get("signal_id") == loaded_signal.get("signal_id")
                        for event in fill_events
                    ),
                    "runtime_context_bound": observations["runtime_context"].get(
                        "runtime_binding_id"
                    )
                    == binding.binding_id
                    and observations["runtime_context"].get("deployment_plan_id")
                    == plan_dict["plan_id"],
                    "paper_only_guard_retained": broker_flag == "false",
                },
            }
        )
    return executions


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
