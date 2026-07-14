from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from services.source_ingestion import controller_worker
from services.source_ingestion.controller_state import (
    ControllerState,
    ControllerStateError,
    ControllerStateStore,
)
from services.source_ingestion.controller_worker import (
    ControllerConfig,
    ControllerTickError,
    _personas_from_payload,
    _validate_terminal_readback as _validate_terminal_readback_impl,
    run_controller_tick,
)


CONNECTOR_ID = "tw-official-market-datasets"
DATASET = "tw_price_daily"
FINAL_TRUTH_LEVEL = "reconciled_live_proof"


def _deployment() -> dict[str, Any]:
    return {
        "git_sha": "sha-test",
        "image_digest": "image-test",
        "build_time": "2026-07-14T08:00:00Z",
        "deployment_id": "deployment-test",
        "runtime_instance_id": "runtime-test",
        "identity_observed_at": "2026-07-14T08:00:00Z",
        "identity_complete": True,
    }


def _state(**overrides: Any) -> ControllerState:
    values: dict[str, Any] = {
        "controller_id": "source-ingestion-test:generation-test",
        "controller_name": "source-ingestion-controller",
        "environment": "test",
        "tenant_id": "tenant-test",
        "deployment": _deployment(),
        "started_at": "2026-07-13T00:00:00Z",
    }
    values.update(overrides)
    return ControllerState(**values)


def _config(tmp_path: Path, *, truth_level: str = FINAL_TRUTH_LEVEL) -> ControllerConfig:
    return ControllerConfig(
        api_url="http://source-ingest.test:8097",
        database_url="postgresql://unused",
        interval_seconds=60,
        max_concurrency=2,
        max_ticks=1,
        state_path=tmp_path / "controller-state.json",
        alive_path=None,
        timeout_seconds=5.0,
        lease_seconds=120,
        truth_level=truth_level,
        controller_token="controller-test-token-that-is-at-least-32-characters",
    )


def _requirement() -> dict[str, Any]:
    return {
        "dataset": DATASET,
        "market": "TW",
        "cadence": "daily",
        "source_class": "live_pull",
        "connector_candidates": [CONNECTOR_ID],
        "policy_gates": ["public-source-only"],
    }


def _personas() -> tuple[dict[str, Any], ...]:
    return (
        {
            "persona_id": "persona-source-test",
            "required_data_sources": [_requirement()],
        },
    )


def _desired_meta() -> dict[str, Any]:
    return {
        "authority": "file:///desired-state.json",
        "transport": "deployment_file",
        "sha256": "desired-state-sha",
        "persona_count": 1,
        "requirement_count": 1,
        "read_at": "2026-07-14T08:00:00Z",
    }


def _reconcile() -> dict[str, Any]:
    return {
        "desired_state_sha256": "desired-state-sha",
        "summary": {
            "persona_count": 1,
            "total": 1,
            "satisfied": 1,
            "mutated": 0,
            "skipped": 0,
            "conflicts": 0,
            "unsupported": 0,
        },
        "results": [
            {
                "persona_id": "persona-source-test",
                "actions": [
                    {
                        "dataset": DATASET,
                        "connector_id": CONNECTOR_ID,
                        "status": "satisfied",
                    }
                ],
            }
        ],
    }


def _schedule() -> dict[str, Any]:
    return {
        "summary": {
            "total_due": 1,
            "total_run": 1,
            "total_succeeded": 1,
            "total_failed": 0,
        }
    }


def _actual_readback() -> dict[str, Any]:
    captured_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": "source_ingest_controller_readback.v1",
        "captured_at": captured_at,
        "controller_state": {
            "controller_id": "source-ingestion-test:generation-test",
            "sequence_no": 1,
            "deployment": _deployment(),
        },
        "requirement_snapshot": {
            "desired_state_sha256": "desired-state-sha",
            "authoritative": True,
            "sequence": 1,
        },
        "connector_count": 1,
        "source_record_count": 1,
        "dlq_count": 0,
        "frontier_backlog": 0,
        "max_lag_seconds": 5,
        "connectors": [
            {
                "connector_id": CONNECTOR_ID,
                "configured": True,
                "desired_state": {
                    "dataset": DATASET,
                    "market": "TW",
                    "cadence": "daily",
                    "source_class": "live_pull",
                    "policy_gates": ["public-source-only"],
                    "policy_gate_results": {
                        "public-source-only": {
                            "passed": True,
                            "authority": "connector_auth_policy",
                        }
                    },
                },
                "connector": {
                    "connector_id": CONNECTOR_ID,
                    "auth_type": "none",
                    "secret_ref_id": None,
                },
                "schedule": {
                    "connector_id": CONNECTOR_ID,
                    "enabled": True,
                    "interval_seconds": 86400,
                },
                "freshness": {
                    "status": "fresh",
                    "is_due": False,
                    "schedule_enabled": True,
                    "staleness_seconds": 5,
                    "last_ingest_run_id": "run-source-test",
                    "latest_run": {
                        "ingest_run_id": "run-source-test",
                        "status": "completed",
                    },
                },
                "latest_source_record": {
                    "source_id": "tw-official:tw_price_daily:test",
                    "connector_id": CONNECTOR_ID,
                    "status": "normalized",
                    "content_ref": "tw-official://tw_price_daily/TWSE/2330/2026-07-14/test",
                    "trace_id": "trace-source-test",
                    "created_at": "2026-07-14T08:00:04Z",
                    "provenance": {
                        "provider": "TWSE OpenAPI",
                        "dataset": DATASET,
                        "available_time": captured_at,
                        "api_endpoint": "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
                        "access_scope": "public",
                        "license_scope": "public_open_data",
                        "schema_hash": "tw-official-market-record.v1",
                        "source_ingest_run_id": "run-source-test",
                    },
                },
                "source_health": {
                    "source_id": CONNECTOR_ID,
                    "status": "ok",
                    "checked_at": "2026-07-14T08:00:05Z",
                    "last_success_at": "2026-07-14T08:00:04Z",
                    "metadata": {"last_ingest_run_id": "run-source-test"},
                },
            }
        ],
    }


def _validate_terminal_readback(
    *,
    reconcile: dict[str, Any],
    schedule: dict[str, Any],
    actual: dict[str, Any],
) -> None:
    controller_state = actual["controller_state"]
    _validate_terminal_readback_impl(
        reconcile=reconcile,
        schedule=schedule,
        actual=actual,
        expected_controller_id=controller_state["controller_id"],
        expected_sequence_no=controller_state["sequence_no"],
        expected_deployment=controller_state["deployment"],
    )


class RecordingStateStore(ControllerStateStore):
    def __init__(self, path: Path, events: list[str]) -> None:
        super().__init__(path)
        self.events = events

    def save(self, state: ControllerState) -> None:
        if state.total_successes:
            label = "store.success"
        elif state.total_failures:
            label = "store.failure"
        else:
            label = "store.tick_started"
        self.events.append(label)
        super().save(state)


class RecordingWriter:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[dict[str, Any]] = []

    def _record(
        self,
        name: str,
        loop_id: str,
        truth_level: str,
        *,
        reason: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.events.append(f"writer.{name}")
        self.calls.append(
            {
                "name": name,
                "loop_id": loop_id,
                "truth_level": truth_level,
                "reason": reason,
                "kwargs": kwargs,
            }
        )

    async def record_heartbeat(
        self,
        loop_id: str,
        truth_level: str = "scheduled_tick",
        **kwargs: Any,
    ) -> None:
        self._record("heartbeat", loop_id, truth_level, **kwargs)

    async def record_tick(
        self,
        loop_id: str,
        truth_level: str = "scheduled_tick",
        **kwargs: Any,
    ) -> None:
        self._record("tick", loop_id, truth_level, **kwargs)

    async def record_success(
        self,
        loop_id: str,
        truth_level: str = "scheduled_tick",
        **kwargs: Any,
    ) -> None:
        self._record("success", loop_id, truth_level, **kwargs)

    async def record_failure(
        self,
        loop_id: str,
        reason: str,
        truth_level: str = "scheduled_tick",
        **kwargs: Any,
    ) -> None:
        self._record("failure", loop_id, truth_level, reason=reason, **kwargs)

    async def record_repair(
        self,
        loop_id: str,
        reason: str,
        truth_level: str = "scheduled_tick",
        **kwargs: Any,
    ) -> None:
        self._record("repair", loop_id, truth_level, reason=reason, **kwargs)


def _call(writer: RecordingWriter, name: str) -> dict[str, Any]:
    return next(call for call in writer.calls if call["name"] == name)


def _patch_successful_tick(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> None:
    def load_desired_state(*, timeout_seconds: float) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
        assert timeout_seconds == 5.0
        events.append("load_desired_state")
        return _personas(), _desired_meta()

    def reconcile_desired_state(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["personas"] == _personas()
        events.append("reconcile_desired_state")
        return _reconcile()

    def run_schedule_tick(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["max_concurrency"] == 2
        assert kwargs["force_connector_ids"] == []
        events.append("run_schedule_tick")
        return _schedule()

    def read_actual_state(**kwargs: Any) -> dict[str, Any]:
        events.append("read_actual_state")
        return _actual_readback()

    original_validate = controller_worker._validate_terminal_readback

    def validate_terminal_readback(**kwargs: Any) -> None:
        events.append("validate_terminal_readback")
        original_validate(**kwargs)

    monkeypatch.setattr(controller_worker, "load_desired_state", load_desired_state)
    monkeypatch.setattr(controller_worker, "reconcile_desired_state", reconcile_desired_state)
    monkeypatch.setattr(controller_worker, "run_schedule_tick", run_schedule_tick)
    monkeypatch.setattr(controller_worker, "read_actual_state", read_actual_state)
    monkeypatch.setattr(controller_worker, "_validate_terminal_readback", validate_terminal_readback)


def test_controller_state_store_round_trips_all_restart_truth(tmp_path: Path) -> None:
    state = _state()
    state.record_tick_started()
    state.record_success(
        desired_state=_desired_meta(),
        reconcile={"connector_ids": [CONNECTOR_ID]},
        schedule={"summary": _schedule()["summary"]},
        actual_readback={"connector_count": 1, "source_record_count": 1},
    )
    store = ControllerStateStore(tmp_path / "controller-state.json")

    store.save(state)

    loaded = store.load()
    assert loaded == state
    assert loaded is not None
    assert loaded.to_dict() == state.to_dict()


def test_controller_state_store_rejects_checksum_corruption(tmp_path: Path) -> None:
    path = tmp_path / "controller-state.json"
    store = ControllerStateStore(path)
    store.save(_state())
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["state"]["deployment"]["git_sha"] = "tampered-without-checksum-update"
    path.write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(ControllerStateError, match="checksum mismatch"):
        store.load()


def test_controller_state_counts_ticks_missed_while_worker_was_down() -> None:
    now = datetime(2026, 7, 14, 8, 5, tzinfo=timezone.utc)
    state = _state(last_tick_at=(now - timedelta(seconds=190)).isoformat())

    missed = state.record_startup_missed(interval_seconds=60, now=now)

    assert missed == 2
    assert state.startup_missed_ticks == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"personas": ["not-a-persona-object"]},
        {
            "personas": [
                {
                    "persona_id": "persona-source-test",
                    "required_data_sources": ["not-a-requirement-object"],
                }
            ]
        },
    ],
)
def test_personas_from_payload_rejects_malformed_items(payload: dict[str, Any]) -> None:
    with pytest.raises(ControllerTickError) as raised:
        _personas_from_payload(payload)

    assert raised.value.stage == "desired_state_validate"


def test_personas_from_payload_rejects_duplicate_requirement_within_persona() -> None:
    requirement = _requirement()
    payload = {
        "personas": [
            {
                "persona_id": "persona-source-test",
                "required_data_sources": [requirement, deepcopy(requirement)],
            }
        ]
    }

    with pytest.raises(ControllerTickError) as raised:
        _personas_from_payload(payload)

    assert raised.value.stage == "desired_state_validate"
    assert "duplicate" in str(raised.value).lower()


def test_personas_from_payload_accepts_authoritative_empty_snapshot() -> None:
    assert _personas_from_payload({"personas": [], "authoritative_snapshot": True}) == ()


def test_terminal_readback_accepts_fresh_matching_provenance_rich_connector() -> None:
    _validate_terminal_readback(
        reconcile=_reconcile(),
        schedule=_schedule(),
        actual=_actual_readback(),
    )


def test_terminal_readback_rejects_missing_authoritative_connector() -> None:
    actual = _actual_readback()
    actual["connectors"] = []

    with pytest.raises(ControllerTickError) as raised:
        _validate_terminal_readback(reconcile=_reconcile(), schedule=_schedule(), actual=actual)

    assert raised.value.stage == "actual_readback"


def test_terminal_readback_rejects_record_from_wrong_connector() -> None:
    actual = _actual_readback()
    actual["connectors"][0]["latest_source_record"]["connector_id"] = "wrong-connector"

    with pytest.raises(ControllerTickError) as raised:
        _validate_terminal_readback(reconcile=_reconcile(), schedule=_schedule(), actual=actual)

    assert raised.value.stage == "actual_readback"


def test_terminal_readback_rejects_stale_connector() -> None:
    actual = _actual_readback()
    actual["connectors"][0]["freshness"].update(
        {"status": "due", "is_due": True, "staleness_seconds": 172800}
    )

    with pytest.raises(ControllerTickError) as raised:
        _validate_terminal_readback(reconcile=_reconcile(), schedule=_schedule(), actual=actual)

    assert raised.value.stage == "actual_readback"


@pytest.mark.parametrize("wrong_field", ["desired_state", "provenance"])
def test_terminal_readback_rejects_wrong_dataset(wrong_field: str) -> None:
    actual = _actual_readback()
    if wrong_field == "desired_state":
        actual["connectors"][0]["desired_state"]["dataset"] = "tw_institutional_flow"
    else:
        actual["connectors"][0]["latest_source_record"]["provenance"]["dataset"] = "tw_institutional_flow"

    with pytest.raises(ControllerTickError) as raised:
        _validate_terminal_readback(reconcile=_reconcile(), schedule=_schedule(), actual=actual)

    assert raised.value.stage == "actual_readback"


@pytest.mark.parametrize(
    "missing_key",
    ["provider", "dataset", "available_time", "api_endpoint", "license_scope", "schema_hash"],
)
def test_terminal_readback_rejects_incomplete_provenance(missing_key: str) -> None:
    actual = _actual_readback()
    actual["connectors"][0]["latest_source_record"]["provenance"].pop(missing_key)

    with pytest.raises(ControllerTickError) as raised:
        _validate_terminal_readback(reconcile=_reconcile(), schedule=_schedule(), actual=actual)

    assert raised.value.stage == "actual_readback"


def test_terminal_readback_rejects_failed_source_health() -> None:
    actual = _actual_readback()
    actual["connectors"][0]["source_health"]["status"] = "failed"

    with pytest.raises(ControllerTickError) as raised:
        _validate_terminal_readback(reconcile=_reconcile(), schedule=_schedule(), actual=actual)

    assert raised.value.stage == "actual_readback"


def test_terminal_readback_rejects_stale_provider_data_even_after_fresh_ingest() -> None:
    actual = _actual_readback()
    actual["connectors"][0]["latest_source_record"]["provenance"]["available_time"] = "2020-01-01T00:00:00Z"

    with pytest.raises(ControllerTickError) as raised:
        _validate_terminal_readback(reconcile=_reconcile(), schedule=_schedule(), actual=actual)

    assert "source_data_stale" in str(raised.value)


def test_terminal_readback_rejects_deployment_identity_contradiction() -> None:
    actual = _actual_readback()
    actual["controller_state"]["deployment"]["git_sha"] = "sha-contradicted"

    with pytest.raises(ControllerTickError) as raised:
        _validate_terminal_readback_impl(
            reconcile=_reconcile(),
            schedule=_schedule(),
            actual=actual,
            expected_controller_id="source-ingestion-test:generation-test",
            expected_sequence_no=1,
            expected_deployment=_deployment(),
        )

    assert raised.value.stage == "actual_readback"


def test_run_controller_tick_orders_terminal_success_after_readback_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    config = _config(tmp_path)
    state = _state()
    store = RecordingStateStore(config.state_path, events)
    writer = RecordingWriter(events)
    _patch_successful_tick(monkeypatch, events)

    result = run_controller_tick(config=config, state=state, store=store, writer=writer)

    assert result["status"] == "ok"
    assert state.total_ticks == 1
    assert state.total_successes == 1
    assert ControllerStateStore(config.state_path).load() == state
    assert events == [
        "store.tick_started",
        "writer.heartbeat",
        "writer.tick",
        "read_actual_state",
        "load_desired_state",
        "reconcile_desired_state",
        "run_schedule_tick",
        "read_actual_state",
        "validate_terminal_readback",
        "writer.success",
        "store.success",
    ]
    assert _call(writer, "heartbeat")["truth_level"] == "scheduled_tick"
    assert _call(writer, "tick")["truth_level"] == "scheduled_tick"
    assert _call(writer, "success")["truth_level"] == FINAL_TRUTH_LEVEL


def test_run_controller_tick_persists_explicit_failure_with_nonterminal_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    config = _config(tmp_path, truth_level="proven_live_evidence")
    state = _state()
    store = RecordingStateStore(config.state_path, events)
    writer = RecordingWriter(events)

    def read_pre_actual_state(**kwargs: Any) -> dict[str, Any]:
        events.append("read_actual_state")
        return _actual_readback()

    def fail_desired_state(*, timeout_seconds: float) -> Any:
        events.append("load_desired_state")
        raise ControllerTickError("desired_state_read", "authoritative snapshot unavailable")

    monkeypatch.setattr(controller_worker, "read_actual_state", read_pre_actual_state)
    monkeypatch.setattr(controller_worker, "load_desired_state", fail_desired_state)

    with pytest.raises(ControllerTickError) as raised:
        run_controller_tick(config=config, state=state, store=store, writer=writer)

    persisted = ControllerStateStore(config.state_path).load()
    assert raised.value.stage == "desired_state_read"
    assert persisted is not None
    assert persisted.last_failure_stage == "desired_state_read"
    assert persisted.last_failure_reason == "authoritative snapshot unavailable"
    assert persisted.consecutive_failures == 1
    assert persisted.total_failures == 1
    assert persisted.total_successes == 0
    assert events == [
        "store.tick_started",
        "writer.heartbeat",
        "writer.tick",
        "read_actual_state",
        "load_desired_state",
        "store.failure",
        "writer.failure",
    ]
    assert _call(writer, "heartbeat")["truth_level"] == "scheduled_tick"
    assert _call(writer, "tick")["truth_level"] == "scheduled_tick"
    assert _call(writer, "failure")["truth_level"] == "scheduled_tick"


def test_run_controller_tick_records_repair_after_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    config = _config(tmp_path)
    state = _state(consecutive_failures=2, total_failures=2)
    store = RecordingStateStore(config.state_path, events)
    writer = RecordingWriter(events)
    _patch_successful_tick(monkeypatch, events)

    run_controller_tick(config=config, state=state, store=store, writer=writer)

    assert state.consecutive_failures == 0
    assert state.last_repair_at is not None
    assert [call["name"] for call in writer.calls][-2:] == ["success", "repair"]
    assert events.index("writer.success") < events.index("writer.repair")
    persisted = ControllerStateStore(config.state_path).load()
    assert persisted is not None
    assert persisted.last_repair_at == state.last_repair_at


def test_refresh_runtime_identity_replaces_stale_persisted_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state(sequence_no=7, total_successes=3)
    monkeypatch.setenv("GIT_SHA", "sha-current")
    monkeypatch.setenv("IMAGE_DIGEST", "image-current")
    monkeypatch.setenv("BUILD_TIME", "2026-07-14T08:00:00Z")
    monkeypatch.setenv("SOURCE_INGEST_DEPLOYMENT_ID", "deployment-current")
    monkeypatch.setenv("PANTHEON_ENV", "test")
    monkeypatch.setenv("PANTHEON_TENANT_ID", "tenant-test")
    monkeypatch.setenv("PANTHEON_CONTROLLER_ID", "source-ingestion-current")
    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_GENERATION_ID", "generation-current")

    refreshed = controller_worker.refresh_runtime_identity(state)

    assert refreshed.deployment["git_sha"] == "sha-current"
    assert refreshed.deployment["image_digest"] == "image-current"
    assert refreshed.deployment["build_time"] == "2026-07-14T08:00:00Z"
    assert refreshed.deployment["deployment_id"] == "deployment-current"
    assert refreshed.deployment["identity_complete"] is True
    assert refreshed.deployment["runtime_instance_id"]
    assert refreshed.controller_id == "source-ingestion-current:generation-current"
    assert refreshed.sequence_no == 7
    assert refreshed.total_successes == 3
