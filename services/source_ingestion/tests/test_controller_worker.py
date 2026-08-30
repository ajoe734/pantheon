from __future__ import annotations

import json
import stat
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from services.source_ingestion import controller_worker
from services.source_ingestion import controller_state
from services.source_ingestion.controller_state import (
    ControllerState,
    ControllerStateError,
    ControllerStateStore,
    LEGACY_SCHEMA_VERSION,
    SCHEMA_VERSION,
)
from services.source_ingestion.controller_worker import (
    ControllerConfig,
    ControllerTickError,
    RECONCILE_ONLY_MODE,
    _personas_from_payload,
    _validate_due_state_readback,
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


def _config(
    tmp_path: Path,
    *,
    truth_level: str = FINAL_TRUTH_LEVEL,
    mode: str = controller_worker.RECONCILE_AND_PULL_MODE,
    force_connector_ids: tuple[str, ...] = (),
    exclusive_connector_ids: tuple[str, ...] = (),
    frontier_recovery_connector_ids: tuple[str, ...] = (),
) -> ControllerConfig:
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
        mode=mode,
        force_connector_ids=force_connector_ids,
        exclusive_connector_ids=exclusive_connector_ids,
        frontier_recovery_connector_ids=frontier_recovery_connector_ids,
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
        "pending_dlq_count": 0,
        "unresolved_dlq_count": 0,
        "dlq_status_counts": {
            "pending": 0,
            "replayed": 0,
            "duplicate_skipped": 0,
            "replay_failed": 0,
            "schema_rejected": 0,
        },
        "frontier_backlog": 0,
        "frontier_backlog_by_connector": {},
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


def _large_actual_readback(connector_count: int = 260) -> dict[str, Any]:
    actual = _actual_readback()
    connector_template = actual["connectors"][0]
    connectors = []
    for index in range(connector_count):
        connector_id = f"connector-{index:03d}"
        connector = deepcopy(connector_template)
        connector["connector_id"] = connector_id
        connector["connector"]["connector_id"] = connector_id
        connector["schedule"]["connector_id"] = connector_id
        connector["latest_source_record"]["connector_id"] = connector_id
        connector["latest_source_record"]["source_id"] = f"source-{index:03d}"
        connectors.append(connector)
    actual["connector_count"] = connector_count
    actual["source_record_count"] = connector_count
    actual["connectors"] = connectors
    return actual


def _validate_terminal_readback(
    *,
    reconcile: dict[str, Any],
    schedule: dict[str, Any],
    actual: dict[str, Any],
    expected_exclusive_connector_ids: tuple[str, ...] = (),
) -> None:
    controller_state = actual["controller_state"]
    _validate_terminal_readback_impl(
        reconcile=reconcile,
        schedule=schedule,
        actual=actual,
        expected_controller_id=controller_state["controller_id"],
        expected_sequence_no=controller_state["sequence_no"],
        expected_deployment=controller_state["deployment"],
        expected_exclusive_connector_ids=expected_exclusive_connector_ids,
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


def _patch_successful_tick(
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
    *,
    expected_force_connector_ids: list[str] | None = None,
    expected_exclusive_connector_ids: list[str] | None = None,
) -> None:
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
        assert kwargs["force_connector_ids"] == (expected_force_connector_ids or [])
        assert kwargs["exclusive_connector_ids"] == (expected_exclusive_connector_ids or [])
        assert kwargs["controller_token"] == "controller-test-token-that-is-at-least-32-characters"
        events.append("run_schedule_tick")
        schedule = _schedule()
        schedule["summary"]["exclusive_connector_count"] = len(expected_exclusive_connector_ids or [])
        return schedule

    def read_actual_state(**kwargs: Any) -> dict[str, Any]:
        events.append("read_actual_state")
        return _actual_readback()

    original_validate = controller_worker._validate_terminal_readback

    def validate_terminal_readback(**kwargs: Any) -> int:
        events.append("validate_terminal_readback")
        return original_validate(**kwargs)

    monkeypatch.setattr(controller_worker, "load_desired_state", load_desired_state)
    monkeypatch.setattr(controller_worker, "reconcile_desired_state", reconcile_desired_state)
    monkeypatch.setattr(controller_worker, "run_schedule_tick", run_schedule_tick)
    monkeypatch.setattr(controller_worker, "read_actual_state", read_actual_state)
    monkeypatch.setattr(controller_worker, "_validate_terminal_readback", validate_terminal_readback)


def _patch_successful_reconcile_only_tick(
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
) -> None:
    def load_desired_state(*, timeout_seconds: float) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
        assert timeout_seconds == 5.0
        events.append("load_desired_state")
        return _personas(), _desired_meta()

    def reconcile_desired_state(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["personas"] == _personas()
        events.append("reconcile_desired_state")
        return _reconcile()

    def read_actual_state(**kwargs: Any) -> dict[str, Any]:
        events.append("read_actual_state")
        return _actual_readback()

    original_validate = controller_worker._validate_due_state_readback

    def validate_due_state_readback(**kwargs: Any) -> None:
        events.append("validate_due_state_readback")
        original_validate(**kwargs)

    def forbidden_provider_tick(**kwargs: Any) -> dict[str, Any]:
        raise AssertionError("reconcile-only controller must not execute provider schedules")

    monkeypatch.setattr(controller_worker, "load_desired_state", load_desired_state)
    monkeypatch.setattr(controller_worker, "reconcile_desired_state", reconcile_desired_state)
    monkeypatch.setattr(controller_worker, "read_actual_state", read_actual_state)
    monkeypatch.setattr(controller_worker, "_validate_due_state_readback", validate_due_state_readback)
    monkeypatch.setattr(controller_worker, "run_schedule_tick", forbidden_provider_tick)


def test_config_allows_unbounded_reconcile_only_without_provider_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_MODE", "reconcile_only")
    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL", "scheduled_tick")
    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_MAX_TICKS", "0")
    monkeypatch.delenv("SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS", raising=False)
    monkeypatch.delenv("SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS", raising=False)
    monkeypatch.delenv("SOURCE_INGEST_CONTROLLER_FRONTIER_RECOVERY_CONNECTOR_IDS", raising=False)
    monkeypatch.setattr(
        controller_worker,
        "load_controller_token",
        lambda **kwargs: "controller-test-token-that-is-at-least-32-characters",
    )

    config = controller_worker.config_from_env()

    assert config.mode == "reconcile_only"
    assert config.max_ticks == 0
    assert config.force_connector_ids == ()
    assert config.exclusive_connector_ids == ()
    assert config.frontier_recovery_connector_ids == ()


def test_config_rejects_unbounded_provider_pull_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_MODE", "reconcile_and_pull")
    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL", "reconciled_live_proof")
    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_MAX_TICKS", "0")

    with pytest.raises(ValueError, match="MAX_TICKS between 1 and 24"):
        controller_worker.config_from_env()


def test_config_rejects_provider_selection_in_reconcile_only_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_MODE", "reconcile_only")
    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL", "scheduled_tick")
    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_MAX_TICKS", "0")
    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS", CONNECTOR_ID)

    with pytest.raises(ValueError, match="must not select provider"):
        controller_worker.config_from_env()


def test_config_rejects_frontier_recovery_in_reconcile_only_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_MODE", "reconcile_only")
    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_TRUTH_LEVEL", "scheduled_tick")
    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_MAX_TICKS", "0")
    monkeypatch.delenv("SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS", raising=False)
    monkeypatch.delenv("SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS", raising=False)
    monkeypatch.setenv(
        "SOURCE_INGEST_CONTROLLER_FRONTIER_RECOVERY_CONNECTOR_IDS",
        "historical-static-connector",
    )

    with pytest.raises(ValueError, match="must not select provider"):
        controller_worker.config_from_env()


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


def test_controller_state_failure_projection_stays_bounded_across_100_ticks() -> None:
    state = _state()
    serialized_sizes: list[int] = []

    for tick in range(100):
        actual = _large_actual_readback()
        actual["controller_state"] = {
            "controller_id": state.controller_id,
            "previous_snapshot": state.to_dict(),
        }
        actual["captured_at"] = f"2026-07-14T08:{tick:02d}:00Z"
        state.record_failure(
            stage="actual_readback",
            reason="bounded test failure",
            reconcile={"results": [{"actions": [{"connector_id": "connector-000"}]}]},
            schedule={"summary": {"total_due": 260, "total_failed": 1}},
            actual_readback=actual,
        )
        serialized = state.to_dict()
        serialized_sizes.append(len(json.dumps(serialized, sort_keys=True)))
        assert "controller_state" not in serialized["actual_readback"]
        assert serialized["actual_readback"]["terminal_connectors"]["count"] == 260
        assert len(serialized["actual_readback"]["terminal_connectors"]["items"]) == 260

    assert max(serialized_sizes) < 200_000
    assert max(serialized_sizes) - min(serialized_sizes) < 1_024


def test_controller_state_store_migrates_legacy_state_with_read_only_backup(tmp_path: Path) -> None:
    path = tmp_path / "controller-state.json"
    legacy_payload = _state().to_dict()
    legacy_payload["schema_version"] = LEGACY_SCHEMA_VERSION
    legacy_payload["reconcile"] = {
        "results": [
            {"actions": [{"connector_id": "connector-000"}, {"connector_id": "connector-259"}]}
        ]
    }
    legacy_payload["schedule"] = {"summary": {"total_due": 260, "total_succeeded": 260}}
    legacy_payload["actual_readback"] = _large_actual_readback()
    prior_snapshot = deepcopy(legacy_payload)
    legacy_payload["actual_readback"]["controller_state"] = {"prior_state": prior_snapshot}
    path.write_text(
        json.dumps(
            {
                "state": legacy_payload,
                "checksum_algorithm": "sha256",
                "checksum": controller_state._checksum(legacy_payload),
            }
        ),
        encoding="utf-8",
    )

    store = ControllerStateStore(path)
    migrated = store.load()

    assert migrated is not None
    assert migrated.reconcile["connector_inventory"]["connector_ids"] == ["connector-000", "connector-259"]
    assert migrated.schedule["summary"]["total_due"] == 260
    assert migrated.actual_readback["terminal_connectors"]["items"][259]["terminal"]["source_id"] == "source-259"
    assert "controller_state" not in migrated.actual_readback

    store.save(migrated)

    rewritten = json.loads(path.read_text(encoding="utf-8"))
    backups = list(tmp_path.glob("controller-state.json.legacy-v1-*.json"))
    assert rewritten["state"]["schema_version"] == SCHEMA_VERSION
    assert len(backups) == 1
    assert (backups[0].stat().st_mode & stat.S_IWUSR) == 0
    assert json.loads(backups[0].read_text(encoding="utf-8"))["state"]["schema_version"] == LEGACY_SCHEMA_VERSION


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


@pytest.mark.parametrize(
    "persona",
    [
        {"persona_id": "persona-source-test"},
        {"persona_id": "persona-source-test", "required_data_sources": None},
        {"persona_id": "persona-source-test", "requiredDataSources": []},
    ],
)
def test_personas_from_payload_rejects_missing_or_null_required_data_sources(
    persona: dict[str, Any],
) -> None:
    with pytest.raises(ControllerTickError) as raised:
        _personas_from_payload({"personas": [persona]})

    assert raised.value.stage == "desired_state_validate"
    assert "required_data_sources" in str(raised.value)


def test_personas_from_payload_accepts_explicit_empty_required_data_sources() -> None:
    assert _personas_from_payload(
        {
            "personas": [
                {
                    "persona_id": "persona-source-test",
                    "required_data_sources": [],
                }
            ]
        }
    ) == (
        {
            "persona_id": "persona-source-test",
            "required_data_sources": [],
        },
    )


def test_personas_from_payload_accepts_authoritative_empty_snapshot() -> None:
    assert _personas_from_payload({"personas": [], "authoritative_snapshot": True}) == ()


def test_terminal_readback_accepts_fresh_matching_provenance_rich_connector() -> None:
    _validate_terminal_readback(
        reconcile=_reconcile(),
        schedule=_schedule(),
        actual=_actual_readback(),
    )


def test_due_state_readback_accepts_connector_schedule_without_provider_proof() -> None:
    actual = _actual_readback()
    actual["connectors"][0]["latest_source_record"] = None
    actual["connectors"][0]["source_health"] = None
    actual["source_record_count"] = 0
    pre_actual = deepcopy(actual)

    _validate_due_state_readback(
        reconcile=_reconcile(),
        pre_actual=pre_actual,
        actual=actual,
        expected_controller_id="source-ingestion-test:generation-test",
        expected_sequence_no=1,
        expected_deployment=_deployment(),
    )


@pytest.mark.parametrize("field", ["source_record_count", "dlq_count", "frontier_backlog"])
def test_due_state_readback_rejects_provider_side_effects(field: str) -> None:
    pre_actual = _actual_readback()
    actual = deepcopy(pre_actual)
    actual[field] += 1

    with pytest.raises(ControllerTickError, match="reconcile-only tick changed") as raised:
        _validate_due_state_readback(
            reconcile=_reconcile(),
            pre_actual=pre_actual,
            actual=actual,
            expected_controller_id="source-ingestion-test:generation-test",
            expected_sequence_no=1,
            expected_deployment=_deployment(),
        )

    assert raised.value.stage == "provider_boundary"


def test_terminal_readback_accepts_resolved_historical_dead_letters() -> None:
    actual = _actual_readback()
    actual["dlq_count"] = 1
    actual["dlq_status_counts"]["replayed"] = 1

    _validate_terminal_readback(
        reconcile=_reconcile(),
        schedule=_schedule(),
        actual=actual,
    )


@pytest.mark.parametrize("value", [None, True, -1, "0"])
def test_terminal_readback_rejects_invalid_pending_dead_letter_count(value: object) -> None:
    actual = _actual_readback()
    actual["pending_dlq_count"] = value

    with pytest.raises(ControllerTickError) as raised:
        _validate_terminal_readback(reconcile=_reconcile(), schedule=_schedule(), actual=actual)

    assert raised.value.stage == "actual_readback"


def test_terminal_readback_rejects_missing_pending_dead_letter_count() -> None:
    actual = _actual_readback()
    actual.pop("pending_dlq_count")

    with pytest.raises(ControllerTickError) as raised:
        _validate_terminal_readback(reconcile=_reconcile(), schedule=_schedule(), actual=actual)

    assert raised.value.stage == "actual_readback"


def test_terminal_readback_rejects_unresolved_dead_letter() -> None:
    actual = _actual_readback()
    actual["dlq_count"] = 1
    actual["pending_dlq_count"] = 1
    actual["unresolved_dlq_count"] = 1
    actual["dlq_status_counts"]["pending"] = 1

    with pytest.raises(ControllerTickError, match="unresolved dead-letter") as raised:
        _validate_terminal_readback(reconcile=_reconcile(), schedule=_schedule(), actual=actual)

    assert raised.value.stage == "actual_readback"


@pytest.mark.parametrize("status", ["replay_failed", "schema_rejected"])
def test_terminal_readback_rejects_nonpending_unresolved_dead_letter(status: str) -> None:
    actual = _actual_readback()
    actual["dlq_count"] = 1
    actual["unresolved_dlq_count"] = 1
    actual["dlq_status_counts"][status] = 1

    with pytest.raises(ControllerTickError, match="unresolved dead-letter") as raised:
        _validate_terminal_readback(reconcile=_reconcile(), schedule=_schedule(), actual=actual)

    assert raised.value.stage == "actual_readback"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda actual: actual.update({"dlq_count": 1}),
        lambda actual: actual["dlq_status_counts"].update({"pending": 1}),
        lambda actual: actual["dlq_status_counts"].pop("replayed"),
        lambda actual: actual["dlq_status_counts"].update({"unexpected": 0}),
    ],
)
def test_terminal_readback_rejects_contradictory_dead_letter_counts(mutate: Any) -> None:
    actual = _actual_readback()
    mutate(actual)

    with pytest.raises(ControllerTickError, match="contradictory dead-letter counts") as raised:
        _validate_terminal_readback(reconcile=_reconcile(), schedule=_schedule(), actual=actual)

    assert raised.value.stage == "actual_readback"


@pytest.mark.parametrize("value", [None, True, -1, "0"])
def test_terminal_readback_rejects_invalid_frontier_backlog(value: object) -> None:
    actual = _actual_readback()
    actual["frontier_backlog"] = value

    with pytest.raises(ControllerTickError) as raised:
        _validate_terminal_readback(reconcile=_reconcile(), schedule=_schedule(), actual=actual)

    assert raised.value.stage == "actual_readback"


def test_terminal_readback_rejects_unresolved_frontier_backlog() -> None:
    actual = _actual_readback()
    actual["frontier_backlog"] = 1
    actual["frontier_backlog_by_connector"] = {CONNECTOR_ID: 1}

    with pytest.raises(ControllerTickError, match="unresolved frontier") as raised:
        _validate_terminal_readback(reconcile=_reconcile(), schedule=_schedule(), actual=actual)

    assert raised.value.stage == "actual_readback"


def _frontier_item(
    frontier_id: str,
    connector_id: str,
    *,
    status: str,
    attempts: int,
) -> dict[str, Any]:
    return {
        "frontier_id": frontier_id,
        "connector_id": connector_id,
        "status": status,
        "attempts": attempts,
        "max_attempts": 2,
        "available_at": "2026-08-21T04:44:11Z",
        "updated_at": "2026-08-21T04:44:11Z",
        "last_error": (
            "stale running frontier recovered after worker restart"
            if status == "retry"
            else None
        ),
    }


def test_explicit_frontier_recovery_runs_one_exact_connector_at_a_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frontier = [
        _frontier_item("frontier-a", "historical-a", status="retry", attempts=1),
        _frontier_item("frontier-b", "historical-b", status="queued", attempts=0),
        _frontier_item("frontier-primary", CONNECTOR_ID, status="queued", attempts=0),
    ]
    calls: list[dict[str, Any]] = []

    def read_frontier_state(**kwargs: Any) -> tuple[dict[str, Any], ...]:
        return tuple(deepcopy(frontier))

    def run_schedule_tick(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        connector_id = kwargs["exclusive_connector_ids"][0]
        item = next(
            row
            for row in frontier
            if row["connector_id"] == connector_id and row["status"] in {"queued", "retry"}
        )
        item["status"] = "done"
        ingest_run_id = f"ingest-{item['frontier_id']}"
        item["ingest_run_id"] = ingest_run_id
        return {
            "summary": {"total_ran": 1, "total_failed": 0},
            "failed": [],
            "ran": [
                {
                    "connector_id": connector_id,
                    "frontier": deepcopy(item),
                    "run": {"ingest_run_id": ingest_run_id, "status": "completed"},
                }
            ],
        }

    monkeypatch.setattr(controller_worker, "read_frontier_state", read_frontier_state)
    monkeypatch.setattr(controller_worker, "run_schedule_tick", run_schedule_tick)

    result = controller_worker.recover_explicit_frontier(
        api_url="http://source-ingest.test:8097",
        recovery_connector_ids=("historical-a", "historical-b"),
        allowed_pending_connector_ids=(CONNECTOR_ID,),
        controller_token="controller-test-token-that-is-at-least-32-characters",
        timeout_seconds=5.0,
    )

    assert result["status"] == "converged"
    assert result["requested_connector_count"] == 2
    assert result["recovered_item_count"] == 2
    assert [call["max_concurrency"] for call in calls] == [1, 1]
    assert [call["force_connector_ids"] for call in calls] == [
        ["historical-a"],
        ["historical-b"],
    ]
    assert [call["exclusive_connector_ids"] for call in calls] == [
        ["historical-a"],
        ["historical-b"],
    ]
    assert next(row for row in frontier if row["connector_id"] == CONNECTOR_ID)["status"] == "queued"


def test_explicit_frontier_recovery_rejects_unclassified_connector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        controller_worker,
        "read_frontier_state",
        lambda **kwargs: (
            _frontier_item("frontier-approved", "historical-a", status="retry", attempts=1),
            _frontier_item("frontier-unexpected", "unexpected", status="queued", attempts=0),
        ),
    )

    with pytest.raises(ControllerTickError, match="outside the explicit recovery boundary") as raised:
        controller_worker.recover_explicit_frontier(
            api_url="http://source-ingest.test:8097",
            recovery_connector_ids=("historical-a",),
            controller_token="controller-test-token-that-is-at-least-32-characters",
            timeout_seconds=5.0,
        )

    assert raised.value.stage == "frontier_recovery"


def test_explicit_frontier_recovery_rejects_nonterminal_scheduler_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _frontier_item("frontier-approved", "historical-a", status="retry", attempts=1)
    monkeypatch.setattr(
        controller_worker,
        "read_frontier_state",
        lambda **kwargs: (deepcopy(item),),
    )
    monkeypatch.setattr(
        controller_worker,
        "run_schedule_tick",
        lambda **kwargs: {
            "summary": {"total_ran": 0, "total_failed": 1},
            "failed": [{"connector_id": "historical-a", "error": "provider failed"}],
            "ran": [],
        },
    )

    with pytest.raises(ControllerTickError, match="did not terminalize") as raised:
        controller_worker.recover_explicit_frontier(
            api_url="http://source-ingest.test:8097",
            recovery_connector_ids=("historical-a",),
            controller_token="controller-test-token-that-is-at-least-32-characters",
            timeout_seconds=5.0,
        )

    assert raised.value.stage == "frontier_recovery"
def test_terminal_readback_accepts_unrelated_backlog_for_explicit_exclusive_scope() -> None:
    actual = _actual_readback()
    actual["frontier_backlog"] = 2
    actual["frontier_backlog_by_connector"] = {"unrelated-connector": 2}
    schedule = _schedule()
    schedule["summary"]["exclusive_connector_count"] = 1

    validated_backlog = _validate_terminal_readback_impl(
        reconcile=_reconcile(),
        schedule=schedule,
        actual=actual,
        expected_controller_id=actual["controller_state"]["controller_id"],
        expected_sequence_no=actual["controller_state"]["sequence_no"],
        expected_deployment=actual["controller_state"]["deployment"],
        expected_exclusive_connector_ids=(CONNECTOR_ID,),
    )

    assert validated_backlog == 0


def test_terminal_readback_rejects_target_backlog_for_explicit_exclusive_scope() -> None:
    actual = _actual_readback()
    actual["frontier_backlog"] = 3
    actual["frontier_backlog_by_connector"] = {CONNECTOR_ID: 1, "unrelated-connector": 2}
    schedule = _schedule()
    schedule["summary"]["exclusive_connector_count"] = 1

    with pytest.raises(ControllerTickError, match="selected connector scope"):
        _validate_terminal_readback(
            reconcile=_reconcile(),
            schedule=schedule,
            actual=actual,
            expected_exclusive_connector_ids=(CONNECTOR_ID,),
        )


@pytest.mark.parametrize(
    "backlog_by_connector",
    [None, {CONNECTOR_ID: 0}, {CONNECTOR_ID: True}, {CONNECTOR_ID: 2}],
)
def test_terminal_readback_rejects_contradictory_frontier_counts(
    backlog_by_connector: object,
) -> None:
    actual = _actual_readback()
    actual["frontier_backlog"] = 1
    actual["frontier_backlog_by_connector"] = backlog_by_connector

    with pytest.raises(ControllerTickError, match="contradictory frontier counts"):
        _validate_terminal_readback(reconcile=_reconcile(), schedule=_schedule(), actual=actual)


def test_failure_truth_uses_only_internally_consistent_unresolved_dlq_count() -> None:
    actual = _actual_readback()
    assert controller_worker._trusted_unresolved_dlq_count(actual) == 0

    actual["dlq_count"] = 1
    actual["pending_dlq_count"] = 1
    actual["unresolved_dlq_count"] = 1
    actual["dlq_status_counts"]["pending"] = 1
    assert controller_worker._trusted_unresolved_dlq_count(actual) == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda actual: actual.update({"unresolved_dlq_count": -1}),
        lambda actual: actual.update({"pending_dlq_count": 1}),
        lambda actual: actual.update({"dlq_count": 1}),
        lambda actual: actual["dlq_status_counts"].pop("schema_rejected"),
    ],
)
def test_failure_truth_preserves_prior_dlq_count_on_contradictory_readback(mutate: Any) -> None:
    actual = _actual_readback()
    mutate(actual)

    assert controller_worker._trusted_unresolved_dlq_count(actual) is None


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
    assert _call(writer, "success")["kwargs"]["dlq_count"] == 0


def test_run_controller_tick_reconcile_only_never_executes_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    config = _config(
        tmp_path,
        truth_level="scheduled_tick",
        mode=RECONCILE_ONLY_MODE,
    )
    state = _state()
    store = RecordingStateStore(config.state_path, events)
    writer = RecordingWriter(events)
    _patch_successful_reconcile_only_tick(monkeypatch, events)

    result = run_controller_tick(config=config, state=state, store=store, writer=writer)

    assert result["status"] == "ok"
    assert result["controller_mode"] == "reconcile_only"
    assert result["provider_egress_attempted"] is False
    assert result["schedule_summary"] == {
        "total_reconciled_connectors": 1,
        "total_provider_pulls": 0,
    }
    assert events == [
        "store.tick_started",
        "writer.heartbeat",
        "writer.tick",
        "read_actual_state",
        "load_desired_state",
        "reconcile_desired_state",
        "read_actual_state",
        "validate_due_state_readback",
        "writer.success",
        "store.success",
    ]
    success = _call(writer, "success")
    assert success["truth_level"] == "scheduled_tick"
    assert success["kwargs"]["payload"]["provider_egress_attempted"] is False


def test_run_controller_tick_exclusively_selects_governed_bounded_connector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    config = _config(
        tmp_path,
        force_connector_ids=("unrelated-force",),
        exclusive_connector_ids=(CONNECTOR_ID,),
    )
    state = _state()
    store = RecordingStateStore(config.state_path, events)
    writer = RecordingWriter(events)
    _patch_successful_tick(
        monkeypatch,
        events,
        expected_force_connector_ids=[CONNECTOR_ID],
        expected_exclusive_connector_ids=[CONNECTOR_ID],
    )

    result = run_controller_tick(config=config, state=state, store=store, writer=writer)

    assert result["status"] == "ok"
    assert events.count("run_schedule_tick") == 1
    success = _call(writer, "success")
    assert success["kwargs"]["backlog"] == 0
    assert success["kwargs"]["payload"]["actual_readback"]["validated_frontier_backlog"] == 0
    assert success["kwargs"]["payload"]["actual_readback"]["validated_frontier_connector_ids"] == [CONNECTOR_ID]


def test_run_controller_tick_recovers_explicit_frontier_before_primary_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    config = _config(
        tmp_path,
        exclusive_connector_ids=(CONNECTOR_ID,),
        frontier_recovery_connector_ids=("historical-static",),
    )
    state = _state()
    store = RecordingStateStore(config.state_path, events)
    writer = RecordingWriter(events)
    _patch_successful_tick(
        monkeypatch,
        events,
        expected_force_connector_ids=[CONNECTOR_ID],
        expected_exclusive_connector_ids=[CONNECTOR_ID],
    )

    def recover_explicit_frontier(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["recovery_connector_ids"] == ("historical-static",)
        assert kwargs["allowed_pending_connector_ids"] == [CONNECTOR_ID]
        assert kwargs["controller_token"] == "controller-test-token-that-is-at-least-32-characters"
        events.append("recover_explicit_frontier")
        return {
            "status": "converged",
            "requested_connector_count": 1,
            "recovered_item_count": 1,
        }

    monkeypatch.setattr(
        controller_worker,
        "recover_explicit_frontier",
        recover_explicit_frontier,
    )

    result = run_controller_tick(config=config, state=state, store=store, writer=writer)

    assert events.index("recover_explicit_frontier") < events.index("run_schedule_tick")
    assert result["frontier_recovery"] == {
        "status": "converged",
        "requested_connector_count": 1,
        "recovered_item_count": 1,
    }
    success = _call(writer, "success")
    assert success["kwargs"]["payload"]["frontier_recovery"] == result["frontier_recovery"]


def test_operation_fingerprint_binds_frontier_recovery_allowlist() -> None:
    without_recovery = controller_worker.compute_request_fingerprint(
        mode=controller_worker.RECONCILE_AND_PULL_MODE,
        exclusive_connector_ids=(CONNECTOR_ID,),
    )
    with_recovery = controller_worker.compute_request_fingerprint(
        mode=controller_worker.RECONCILE_AND_PULL_MODE,
        exclusive_connector_ids=(CONNECTOR_ID,),
        frontier_recovery_connector_ids=("historical-static",),
    )

    assert without_recovery != with_recovery


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
    assert _call(writer, "failure")["kwargs"]["dlq_count"] is None


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


def test_terminal_readback_accepts_weekend_taiwan_official_market_bounded_refresh() -> None:
    # Reproducer for run 33283640789:
    # Friday 2026-08-28 official session close evaluated on Sunday 2026-08-30 with fresh receipt.
    actual = _actual_readback()
    actual["connectors"][0]["connector_id"] = "tw-twse-tpex-official-market"
    actual["connectors"][0]["connector"]["connector_id"] = "tw-twse-tpex-official-market"
    actual["connectors"][0]["schedule"]["connector_id"] = "tw-twse-tpex-official-market"
    actual["connectors"][0]["freshness"].update(
        {
            "status": "fresh",
            "is_due": False,
            "staleness_seconds": 5,
            "last_success_at": actual["captured_at"],
            "last_ingest_run_id": "run-tw-weekend",
            "latest_run": {"ingest_run_id": "run-tw-weekend", "status": "completed"},
        }
    )
    actual["connectors"][0]["latest_source_record"].update(
        {
            "source_id": "tw-official:tw_price_daily:TWSE:2330:weekend-proof",
            "connector_id": "tw-twse-tpex-official-market",
            "status": "normalized",
            "provenance": {
                "provider": "TWSE OpenAPI",
                "dataset": DATASET,
                "available_time": "2026-08-28T05:30:00Z",
                "api_endpoint": "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
                "access_scope": "public",
                "license_scope": "official_reference",
                "schema_hash": "tw_price_daily.v1",
                "source_ingest_run_id": "run-tw-weekend",
            },
        }
    )
    actual["connectors"][0]["source_health"]["source_id"] = "tw-twse-tpex-official-market"
    actual["connectors"][0]["source_health"]["metadata"]["last_ingest_run_id"] = "run-tw-weekend"

    reconcile = _reconcile()
    reconcile["results"][0]["actions"][0]["connector_id"] = "tw-twse-tpex-official-market"

    _validate_terminal_readback(
        reconcile=reconcile,
        schedule=_schedule(),
        actual=actual,
    )


def test_terminal_readback_rejects_stale_weekday_taiwan_official_data() -> None:
    actual = _actual_readback()
    actual["connectors"][0]["connector_id"] = "tw-twse-tpex-official-market"
    actual["connectors"][0]["connector"]["connector_id"] = "tw-twse-tpex-official-market"
    actual["connectors"][0]["schedule"]["connector_id"] = "tw-twse-tpex-official-market"
    # Tuesday 2026-08-25 data evaluated on Friday 2026-08-28 without holiday proof
    actual["connectors"][0]["latest_source_record"]["provenance"]["available_time"] = "2026-08-25T05:30:00Z"
    actual["connectors"][0]["latest_source_record"]["source_id"] = "tw-official:tw_price_daily:TWSE:2330:tuesday"
    actual["connectors"][0]["latest_source_record"]["connector_id"] = "tw-twse-tpex-official-market"
    actual["connectors"][0]["source_health"]["source_id"] = "tw-twse-tpex-official-market"

    reconcile = _reconcile()
    reconcile["results"][0]["actions"][0]["connector_id"] = "tw-twse-tpex-official-market"

    with pytest.raises(ControllerTickError, match="source_data_stale"):
        _validate_terminal_readback(
            reconcile=reconcile,
            schedule=_schedule(),
            actual=actual,
        )


def test_terminal_readback_rejects_future_timestamp_taiwan_official_data() -> None:
    actual = _actual_readback()
    actual["connectors"][0]["connector_id"] = "tw-twse-tpex-official-market"
    actual["connectors"][0]["connector"]["connector_id"] = "tw-twse-tpex-official-market"
    actual["connectors"][0]["schedule"]["connector_id"] = "tw-twse-tpex-official-market"
    actual["connectors"][0]["latest_source_record"]["provenance"]["available_time"] = "2099-01-01T00:00:00Z"
    actual["connectors"][0]["latest_source_record"]["source_id"] = "tw-official:tw_price_daily:TWSE:2330:future"
    actual["connectors"][0]["latest_source_record"]["connector_id"] = "tw-twse-tpex-official-market"
    actual["connectors"][0]["source_health"]["source_id"] = "tw-twse-tpex-official-market"

    reconcile = _reconcile()
    reconcile["results"][0]["actions"][0]["connector_id"] = "tw-twse-tpex-official-market"

    with pytest.raises(ControllerTickError, match="source_data_stale"):
        _validate_terminal_readback(
            reconcile=reconcile,
            schedule=_schedule(),
            actual=actual,
        )


def test_terminal_readback_rejects_non_official_lineage_taiwan_data() -> None:
    actual = _actual_readback()
    actual["connectors"][0]["connector_id"] = "tw-twse-tpex-official-market"
    actual["connectors"][0]["connector"]["connector_id"] = "tw-twse-tpex-official-market"
    actual["connectors"][0]["schedule"]["connector_id"] = "tw-twse-tpex-official-market"
    actual["connectors"][0]["latest_source_record"]["source_id"] = "mock-vendor:tw_price_daily:TWSE:2330:mock"
    actual["connectors"][0]["latest_source_record"]["connector_id"] = "tw-twse-tpex-official-market"
    actual["connectors"][0]["latest_source_record"]["provenance"]["available_time"] = "2026-08-28T05:30:00Z"
    actual["connectors"][0]["source_health"]["source_id"] = "tw-twse-tpex-official-market"

    reconcile = _reconcile()
    reconcile["results"][0]["actions"][0]["connector_id"] = "tw-twse-tpex-official-market"

    with pytest.raises(ControllerTickError, match="source_data_stale"):
        _validate_terminal_readback(
            reconcile=reconcile,
            schedule=_schedule(),
            actual=actual,
        )


def test_terminal_readback_rejects_stale_refresh_receipt() -> None:
    actual = _actual_readback()
    actual["connectors"][0]["connector_id"] = "tw-twse-tpex-official-market"
    actual["connectors"][0]["connector"]["connector_id"] = "tw-twse-tpex-official-market"
    actual["connectors"][0]["schedule"]["connector_id"] = "tw-twse-tpex-official-market"
    actual["connectors"][0]["freshness"]["last_success_at"] = "2026-08-20T00:00:00Z"  # >24h stale receipt
    actual["connectors"][0]["latest_source_record"]["provenance"]["available_time"] = "2026-08-28T05:30:00Z"
    actual["connectors"][0]["latest_source_record"]["source_id"] = "tw-official:tw_price_daily:TWSE:2330:stale-receipt"
    actual["connectors"][0]["latest_source_record"]["connector_id"] = "tw-twse-tpex-official-market"
    actual["connectors"][0]["source_health"]["source_id"] = "tw-twse-tpex-official-market"

    reconcile = _reconcile()
    reconcile["results"][0]["actions"][0]["connector_id"] = "tw-twse-tpex-official-market"

    with pytest.raises(ControllerTickError, match="source_data_stale"):
        _validate_terminal_readback(
            reconcile=reconcile,
            schedule=_schedule(),
            actual=actual,
        )

