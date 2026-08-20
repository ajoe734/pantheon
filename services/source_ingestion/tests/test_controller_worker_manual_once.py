"""Tests for bounded manual one-tick Source controller execution and reconcile-only default."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services.source_ingestion import controller_worker
from services.source_ingestion.controller_state import ControllerState, ControllerStateStore
from services.source_ingestion.controller_worker import (
    ControllerConfig,
    ControllerTickError,
    RECONCILE_AND_PULL_MODE,
    RECONCILE_ONLY_MODE,
    run_controller_once,
    run_controller_tick,
)


CONNECTOR_ID = "tw-official-market-datasets"
DATASET = "tw_price_daily"


def _deployment() -> dict[str, Any]:
    return {
        "git_sha": "test-sha-manual-once",
        "image_digest": "test-image-manual-once",
        "build_time": "2026-08-20T00:00:00Z",
        "deployment_id": "test-deployment-manual-once",
        "runtime_instance_id": "test-runtime-manual-once",
        "identity_observed_at": "2026-08-20T00:00:00Z",
        "identity_complete": True,
    }


def _state(**overrides: Any) -> ControllerState:
    values: dict[str, Any] = {
        "controller_id": "source-ingestion-test:manual-once",
        "controller_name": "source-ingestion-controller",
        "environment": "test",
        "tenant_id": "tenant-test",
        "deployment": _deployment(),
        "started_at": "2026-08-20T00:00:00Z",
    }
    values.update(overrides)
    return ControllerState(**values)


def _desired_meta() -> dict[str, Any]:
    return {
        "authority": "file:test_desired_state.json",
        "persona_count": 1,
        "requirement_count": 1,
        "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    }


def _personas() -> tuple[dict[str, Any], ...]:
    return (
        {
            "persona_id": "macro-analyst",
            "required_data_sources": [
                {
                    "dataset": DATASET,
                    "market": "TW",
                    "cadence": "daily",
                    "source_class": "live_pull",
                    "connector_candidates": [CONNECTOR_ID],
                    "policy_gates": ["public-source-only"],
                }
            ],
        },
    )


def _reconcile() -> dict[str, Any]:
    return {
        "desired_state_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "reconciled_at": "2026-08-20T00:00:00Z",
        "results": [
            {
                "action": "active_unchanged",
                "connector_id": CONNECTOR_ID,
                "mutated": False,
                "reason": "already matches desired state",
            }
        ],
        "summary": {
            "active_unchanged": 1,
            "created": 0,
            "retired": 0,
            "total_desired": 1,
            "updated": 0,
        },
    }


def _schedule() -> dict[str, Any]:
    return {
        "results": [
            {
                "connector_id": CONNECTOR_ID,
                "records_distilled": 10,
                "records_ingested": 10,
                "records_pulled": 10,
                "status": "success",
            }
        ],
        "summary": {
            "failed_pulls": 0,
            "total_connectors": 1,
            "total_records_ingested": 10,
            "total_records_pulled": 10,
            "total_successes": 1,
        },
    }


def _actual(state_seq: int = 1) -> dict[str, Any]:
    return {
        "captured_at": "2026-08-20T00:00:05Z",
        "connectors": [
            {
                "connector_id": CONNECTOR_ID,
                "status": "active",
                "latest_source_record": {
                    "source_id": "src-twse-001",
                    "collected_at": "2026-08-20T00:00:05Z",
                    "status": "valid",
                },
                "latest_schedule_event": {
                    "event_type": "scheduled_tick",
                    "timestamp": "2026-08-20T00:00:05Z",
                    "status": "success",
                },
            }
        ],
        "active_connector_count": 1,
        "total_source_records": 10,
        "frontier_backlog": 0,
        "max_lag_seconds": 0,
        "unresolved_dlq_count": 0,
        "controller_state": {
            "controller_id": "source-ingestion-test:manual-once",
            "sequence_no": state_seq,
            "deployment": _deployment(),
        },
    }


class RecordingWriter:
    def __init__(self) -> None:
        self.heartbeats: list[dict[str, Any]] = []
        self.ticks: list[dict[str, Any]] = []
        self.successes: list[dict[str, Any]] = []
        self.failures: list[dict[str, Any]] = []
        self.repairs: list[dict[str, Any]] = []

    async def record_heartbeat(self, loop_id: str, truth_level: str = "scheduled_tick", **kwargs: Any) -> None:
        self.heartbeats.append({"loop_id": loop_id, "truth_level": truth_level, **kwargs})

    async def record_tick(self, loop_id: str, truth_level: str = "scheduled_tick", **kwargs: Any) -> None:
        self.ticks.append({"loop_id": loop_id, "truth_level": truth_level, **kwargs})

    async def record_success(self, loop_id: str, truth_level: str = "scheduled_tick", **kwargs: Any) -> None:
        self.successes.append({"loop_id": loop_id, "truth_level": truth_level, **kwargs})

    async def record_failure(self, loop_id: str, reason: str, truth_level: str = "scheduled_tick", **kwargs: Any) -> None:
        self.failures.append({"loop_id": loop_id, "reason": reason, "truth_level": truth_level, **kwargs})

    async def record_repair(self, loop_id: str, reason: str, truth_level: str = "scheduled_tick", **kwargs: Any) -> None:
        self.repairs.append({"loop_id": loop_id, "reason": reason, "truth_level": truth_level, **kwargs})


def test_default_config_is_reconcile_only_with_zero_provider_egress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SOURCE_INGEST_CONTROLLER_MODE", raising=False)
    monkeypatch.delenv("SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL", raising=False)
    monkeypatch.delenv("SOURCE_INGEST_CONTROLLER_MAX_TICKS", raising=False)
    monkeypatch.delenv("SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS", raising=False)
    monkeypatch.delenv("SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS", raising=False)
    monkeypatch.setattr(
        controller_worker,
        "load_controller_token",
        lambda **kwargs: "token-32-chars-minimum-test-mock",
    )

    config = controller_worker.config_from_env()

    assert config.mode == RECONCILE_ONLY_MODE
    assert config.truth_level == "scheduled_tick"
    assert config.max_ticks == 0
    assert config.force_connector_ids == ()
    assert config.exclusive_connector_ids == ()


def test_reconcile_only_tick_executes_zero_provider_egress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called_schedule = False

    def mock_schedule_tick(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal called_schedule
        called_schedule = True
        return _schedule()

    monkeypatch.setattr(controller_worker, "load_desired_state", lambda **kwargs: (_personas(), _desired_meta()))
    monkeypatch.setattr(controller_worker, "reconcile_desired_state", lambda **kwargs: _reconcile())
    monkeypatch.setattr(controller_worker, "read_actual_state", lambda **kwargs: _actual(1))
    monkeypatch.setattr(controller_worker, "_validate_due_state_readback", lambda **kwargs: None)
    monkeypatch.setattr(controller_worker, "run_schedule_tick", mock_schedule_tick)

    config = ControllerConfig(
        api_url="http://source-ingest.test:8097",
        database_url="",
        interval_seconds=60,
        max_concurrency=2,
        max_ticks=0,
        state_path=tmp_path / "controller-state.json",
        alive_path=None,
        timeout_seconds=5.0,
        lease_seconds=120,
        truth_level="scheduled_tick",
        controller_token="token-32-chars-minimum-test-mock",
        mode=RECONCILE_ONLY_MODE,
        force_connector_ids=(),
        exclusive_connector_ids=(),
    )
    state = _state()
    store = ControllerStateStore(config.state_path)
    writer = RecordingWriter()

    result = run_controller_tick(config=config, state=state, store=store, writer=writer)

    assert result["status"] == "ok"
    assert result["controller_mode"] == RECONCILE_ONLY_MODE
    assert result["provider_egress_attempted"] is False
    assert called_schedule is False
    assert len(writer.successes) == 1
    assert writer.successes[0]["truth_level"] == "scheduled_tick"


def test_reconcile_only_rejects_connector_selection_and_invalid_truth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_MODE", RECONCILE_ONLY_MODE)
    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS", CONNECTOR_ID)
    with pytest.raises(ValueError, match="must not select provider connector execution"):
        controller_worker.config_from_env()

    monkeypatch.delenv("SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS", raising=False)
    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL", "reconciled_live_proof")
    with pytest.raises(ValueError, match="reconcile_only mode must use scheduled_tick truth"):
        controller_worker.config_from_env()


def test_reconcile_and_pull_requires_bounded_max_ticks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_MODE", RECONCILE_AND_PULL_MODE)
    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS", CONNECTOR_ID)
    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_MAX_TICKS", "0")
    with pytest.raises(ValueError, match="SOURCE_INGEST_CONTROLLER_MAX_TICKS between 1 and 24"):
        controller_worker.config_from_env()

    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_MAX_TICKS", "25")
    with pytest.raises(ValueError, match="SOURCE_INGEST_CONTROLLER_MAX_TICKS between 1 and 24"):
        controller_worker.config_from_env()


def test_reconcile_and_pull_requires_explicit_connector_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_MODE", RECONCILE_AND_PULL_MODE)
    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_MAX_TICKS", "1")
    monkeypatch.delenv("SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS", raising=False)
    with pytest.raises(ValueError, match="reconcile_and_pull mode requires explicitly selected connector IDs"):
        controller_worker.config_from_env()


def test_run_controller_once_rejects_empty_exclusive_connectors(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "controller-state.json"
    with pytest.raises(ValueError, match="reconcile_and_pull mode requires explicitly selected connector IDs"):
        run_controller_once(
            mode=RECONCILE_AND_PULL_MODE,
            exclusive_connector_ids=[],
            state_path=state_path,
            controller_token="test-token-32-chars-minimum-mock",
        )

    with pytest.raises(ValueError, match="reconcile_and_pull mode requires explicitly selected connector IDs"):
        run_controller_once(
            mode=RECONCILE_AND_PULL_MODE,
            exclusive_connector_ids=None,
            state_path=state_path,
            controller_token="test-token-32-chars-minimum-mock",
        )


def test_run_controller_once_pulls_selected_allowlisted_connectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    passed_forced: list[str] = []
    passed_exclusive: list[str] = []

    def mock_schedule_tick(*args: Any, **kwargs: Any) -> dict[str, Any]:
        passed_forced.extend(kwargs.get("force_connector_ids") or [])
        passed_exclusive.extend(kwargs.get("exclusive_connector_ids") or [])
        return _schedule()

    monkeypatch.setattr(controller_worker, "load_desired_state", lambda **kwargs: (_personas(), _desired_meta()))
    monkeypatch.setattr(controller_worker, "reconcile_desired_state", lambda **kwargs: _reconcile())
    monkeypatch.setattr(controller_worker, "read_actual_state", lambda **kwargs: _actual(1))
    monkeypatch.setattr(controller_worker, "_validate_terminal_readback", lambda **kwargs: None)
    monkeypatch.setattr(controller_worker, "run_schedule_tick", mock_schedule_tick)

    state_path = tmp_path / "controller-state.json"
    writer = RecordingWriter()
    result = run_controller_once(
        mode=RECONCILE_AND_PULL_MODE,
        exclusive_connector_ids=[CONNECTOR_ID],
        api_url="http://source-ingest.test:8097",
        state_path=state_path,
        controller_token="test-token-32-chars-minimum-mock",
        timeout_seconds=5.0,
        max_concurrency=2,
        writer=writer,
    )

    assert result["status"] == "ok"
    assert result["controller_mode"] == RECONCILE_AND_PULL_MODE
    assert result["provider_egress_attempted"] is True
    assert passed_exclusive == [CONNECTOR_ID]
    assert passed_forced == [CONNECTOR_ID]
    assert len(writer.successes) == 1
    assert writer.successes[0]["truth_level"] == "reconciled_live_proof"

    # Verify state store was persisted with sequence increment
    store = ControllerStateStore(state_path)
    loaded = store.load()
    assert loaded is not None
    assert loaded.sequence_no >= 1
    assert loaded.total_successes == 1
    assert loaded.consecutive_failures == 0


def _mp_worker_run_controller_once(
    result_queue: Any,
    shared_tick_counter: Any,
    shared_barrier: Any,
    state_path_str: str,
    op_key: str,
    connector_id: str,
    token: str,
    delay_in_tick: float = 0.05,
) -> None:
    import time
    from pathlib import Path
    from services.source_ingestion import controller_worker

    def mock_load_desired_state(**kwargs: Any) -> Any:
        return (_personas(), _desired_meta())

    def mock_reconcile(**kwargs: Any) -> Any:
        return _reconcile()

    def mock_read_actual(**kwargs: Any) -> Any:
        return _actual(1)

    def mock_validate_terminal(**kwargs: Any) -> Any:
        return None

    def mock_schedule_tick(*args: Any, **kwargs: Any) -> dict[str, Any]:
        with shared_tick_counter.get_lock():
            shared_tick_counter.value += 1
        if delay_in_tick > 0:
            time.sleep(delay_in_tick)
        return _schedule()

    controller_worker.load_desired_state = mock_load_desired_state
    controller_worker.reconcile_desired_state = mock_reconcile
    controller_worker.read_actual_state = mock_read_actual
    controller_worker._validate_terminal_readback = mock_validate_terminal
    controller_worker.run_schedule_tick = mock_schedule_tick

    writer = RecordingWriter()

    try:
        if shared_barrier is not None:
            shared_barrier.wait(timeout=5.0)
        res = controller_worker.run_controller_once(
            operation_key=op_key,
            mode=controller_worker.RECONCILE_AND_PULL_MODE,
            exclusive_connector_ids=[connector_id],
            state_path=Path(state_path_str),
            controller_token=token,
            writer=writer,
        )
        result_queue.put({"success": True, "result": res})
    except Exception as exc:
        result_queue.put({
            "success": False,
            "error_type": type(exc).__name__,
            "error_msg": str(exc),
            "stage": getattr(exc, "stage", None),
        })


def test_run_controller_once_idempotent_replay_and_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seq_counter = 0
    schedule_call_count = 0

    def mock_actual(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal seq_counter
        return _actual(seq_counter)

    def mock_schedule(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal schedule_call_count
        schedule_call_count += 1
        return _schedule()

    monkeypatch.setattr(controller_worker, "load_desired_state", lambda **kwargs: (_personas(), _desired_meta()))
    monkeypatch.setattr(controller_worker, "reconcile_desired_state", lambda **kwargs: _reconcile())
    monkeypatch.setattr(controller_worker, "read_actual_state", mock_actual)
    monkeypatch.setattr(controller_worker, "_validate_terminal_readback", lambda **kwargs: None)
    monkeypatch.setattr(controller_worker, "run_schedule_tick", mock_schedule)

    state_path = tmp_path / "controller-state.json"

    # First run with explicit operation_key
    seq_counter = 1
    writer1 = RecordingWriter()
    res1 = run_controller_once(
        operation_key="op-acceptance-001",
        mode=RECONCILE_AND_PULL_MODE,
        exclusive_connector_ids=[CONNECTOR_ID],
        state_path=state_path,
        controller_token="test-token-32-chars-minimum-mock",
        writer=writer1,
    )
    assert res1["status"] == "ok"
    assert res1["state_sequence_no"] == 1
    assert res1["provider_egress_attempted"] is True
    assert res1["replayed"] is False
    assert res1["deduplicated"] is False
    assert res1["operation_key"] == "op-acceptance-001"
    assert "request_fingerprint" in res1
    assert schedule_call_count == 1
    assert len(writer1.successes) == 1

    # Second run (replayed with same operation_key and matching fingerprint)
    seq_counter = 2
    writer2 = RecordingWriter()
    res2 = run_controller_once(
        operation_key="op-acceptance-001",
        mode=RECONCILE_AND_PULL_MODE,
        exclusive_connector_ids=[CONNECTOR_ID],
        state_path=state_path,
        controller_token="test-token-32-chars-minimum-mock",
        writer=writer2,
    )
    assert res2["status"] == "ok"
    assert res2["state_sequence_no"] == 1  # Sequence unchanged
    assert res2["provider_egress_attempted"] is False  # Replay must NOT invoke forced provider tick
    assert res2["replayed"] is True
    assert res2["deduplicated"] is True
    assert res2["operation_key"] == "op-acceptance-001"
    assert res2["request_fingerprint"] == res1["request_fingerprint"]
    assert schedule_call_count == 1  # Schedule tick was NOT invoked a second time!
    assert len(writer2.successes) == 0  # No redundant telemetry tick recorded

    # Check store state: total_successes must remain 1
    store = ControllerStateStore(state_path)
    state = store.load()
    assert state is not None
    assert state.sequence_no == 1
    assert state.total_successes == 1
    assert "op-acceptance-001" in state.recent_operations
    assert state.recent_operations["op-acceptance-001"]["request_fingerprint"] == res1["request_fingerprint"]


def test_run_controller_once_rejects_operation_key_conflict_on_mismatched_request_parameters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    schedule_call_count = 0

    def mock_schedule(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal schedule_call_count
        schedule_call_count += 1
        return _schedule()

    monkeypatch.setattr(controller_worker, "load_desired_state", lambda **kwargs: (_personas(), _desired_meta()))
    monkeypatch.setattr(controller_worker, "reconcile_desired_state", lambda **kwargs: _reconcile())
    monkeypatch.setattr(controller_worker, "read_actual_state", lambda **kwargs: _actual(1))
    monkeypatch.setattr(controller_worker, "_validate_terminal_readback", lambda **kwargs: None)
    monkeypatch.setattr(controller_worker, "_validate_due_state_readback", lambda **kwargs: None)
    monkeypatch.setattr(controller_worker, "run_schedule_tick", mock_schedule)

    state_path = tmp_path / "controller-state.json"
    token = "test-token-32-chars-minimum-mock"
    op_key = "op-fixed-key-001"

    # Run 1: initial execution with CONNECTOR_ID
    res1 = run_controller_once(
        operation_key=op_key,
        mode=RECONCILE_AND_PULL_MODE,
        exclusive_connector_ids=[CONNECTOR_ID],
        state_path=state_path,
        controller_token=token,
        writer=RecordingWriter(),
    )
    assert res1["status"] == "ok"
    assert res1["replayed"] is False
    assert schedule_call_count == 1
    fp1 = res1["request_fingerprint"]

    # Replay with IDENTICAL parameters: must succeed with replay
    res_replay = run_controller_once(
        operation_key=op_key,
        mode=RECONCILE_AND_PULL_MODE,
        exclusive_connector_ids=[CONNECTOR_ID],
        state_path=state_path,
        controller_token=token,
        writer=RecordingWriter(),
    )
    assert res_replay["status"] == "ok"
    assert res_replay["replayed"] is True
    assert res_replay["deduplicated"] is True
    assert res_replay["request_fingerprint"] == fp1
    assert schedule_call_count == 1

    # Conflict 1: Reusing op_key with DIFFERENT connector IDs
    with pytest.raises(ControllerTickError) as exc_info1:
        run_controller_once(
            operation_key=op_key,
            mode=RECONCILE_AND_PULL_MODE,
            exclusive_connector_ids=["different-connector-id"],
            state_path=state_path,
            controller_token=token,
            writer=RecordingWriter(),
        )
    assert exc_info1.value.stage == "operation_key_conflict"
    assert "different request parameters" in str(exc_info1.value)
    assert schedule_call_count == 1

    # Conflict 2: Reusing op_key with DIFFERENT mode (reconcile_only)
    with pytest.raises(ControllerTickError) as exc_info2:
        run_controller_once(
            operation_key=op_key,
            mode=RECONCILE_ONLY_MODE,
            exclusive_connector_ids=[],
            state_path=state_path,
            controller_token=token,
            writer=RecordingWriter(),
        )
    assert exc_info2.value.stage == "operation_key_conflict"
    assert "different request parameters" in str(exc_info2.value)
    assert schedule_call_count == 1

    # Conflict 3: Reusing op_key with DIFFERENT api_url
    with pytest.raises(ControllerTickError) as exc_info3:
        run_controller_once(
            operation_key=op_key,
            mode=RECONCILE_AND_PULL_MODE,
            exclusive_connector_ids=[CONNECTOR_ID],
            api_url="http://different-api-host:9999",
            state_path=state_path,
            controller_token=token,
            writer=RecordingWriter(),
        )
    assert exc_info3.value.stage == "operation_key_conflict"
    assert "different request parameters" in str(exc_info3.value)
    assert schedule_call_count == 1


def test_run_controller_once_records_failure_and_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(controller_worker, "load_desired_state", lambda **kwargs: (_personas(), _desired_meta()))
    monkeypatch.setattr(controller_worker, "reconcile_desired_state", lambda **kwargs: _reconcile())
    monkeypatch.setattr(controller_worker, "read_actual_state", lambda **kwargs: _actual(1))

    def failing_schedule(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("upstream provider network error")

    monkeypatch.setattr(controller_worker, "run_schedule_tick", failing_schedule)

    state_path = tmp_path / "controller-state.json"
    writer = RecordingWriter()

    with pytest.raises(ControllerTickError, match="upstream provider network error"):
        run_controller_once(
            mode=RECONCILE_AND_PULL_MODE,
            exclusive_connector_ids=[CONNECTOR_ID],
            state_path=state_path,
            controller_token="test-token-32-chars-minimum-mock",
            writer=writer,
        )

    store = ControllerStateStore(state_path)
    state = store.load()
    assert state is not None
    assert state.consecutive_failures == 1
    assert state.total_failures == 1
    assert "upstream provider network error" in (state.last_failure_reason or "")


def test_run_controller_once_concurrent_same_key_multiprocess(
    tmp_path: Path,
) -> None:
    import multiprocessing as mp

    ctx = mp.get_context("spawn")
    state_path = tmp_path / "controller-state.json"
    result_queue = ctx.Queue()
    shared_tick_counter = ctx.Value("i", 0)
    shared_barrier = ctx.Barrier(2)
    op_key = "op-concurrent-same-key-999"
    token = "test-token-32-chars-minimum-mock"

    p1 = ctx.Process(
        target=_mp_worker_run_controller_once,
        args=(result_queue, shared_tick_counter, shared_barrier, str(state_path), op_key, CONNECTOR_ID, token, 0.05),
    )
    p2 = ctx.Process(
        target=_mp_worker_run_controller_once,
        args=(result_queue, shared_tick_counter, shared_barrier, str(state_path), op_key, CONNECTOR_ID, token, 0.05),
    )

    p1.start()
    p2.start()

    p1.join(timeout=10.0)
    p2.join(timeout=10.0)

    assert p1.exitcode == 0
    assert p2.exitcode == 0

    results: list[dict[str, Any]] = []
    while not result_queue.empty():
        results.append(result_queue.get_nowait())

    assert len(results) == 2
    for r in results:
        assert r["success"] is True, f"subprocess failed: {r}"
        res = r["result"]
        assert res["status"] == "ok"
        assert res["operation_key"] == op_key
        assert "request_fingerprint" in res

    # Exactly ONE provider tick was executed across both processes!
    assert shared_tick_counter.value == 1

    # Exactly one executed the tick, exactly one received deduplicated replay
    replayed_flags = [r["result"]["replayed"] for r in results]
    assert sorted(replayed_flags) == [False, True]

    deduplicated_flags = [r["result"]["deduplicated"] for r in results]
    assert sorted(deduplicated_flags) == [False, True]

    egress_flags = [r["result"]["provider_egress_attempted"] for r in results]
    assert sorted(egress_flags) == [False, True]

    # Verify lock file exists
    lock_path = state_path.with_name(f"{state_path.name}.lock")
    assert lock_path.exists()
