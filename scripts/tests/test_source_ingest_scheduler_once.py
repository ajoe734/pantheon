"""Tests for the retained scripts/source_ingest_scheduler_once.py CLI entrypoint."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import scripts.source_ingest_scheduler_once as scheduler_once
from services.source_ingestion import controller_worker


CONNECTOR_ID = "tw-official-market-datasets"


def test_main_runs_bounded_one_shot_pull_with_connectors(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_kwargs: dict[str, Any] = {}

    def mock_run_controller_once(**kwargs: Any) -> dict[str, Any]:
        captured_kwargs.update(kwargs)
        return {
            "status": "ok",
            "controller_mode": kwargs.get("mode", "reconcile_and_pull"),
            "provider_egress_attempted": True,
            "state_sequence_no": 1,
            "desired_state": {"authority": "test"},
            "reconcile_summary": {"total_desired": 1},
            "schedule_summary": {"total_successes": 1},
            "actual_readback": {"connector_count": 1},
        }

    monkeypatch.setattr(scheduler_once, "run_controller_once", mock_run_controller_once)

    rc = scheduler_once.main(["tw-official-market-datasets", "--max-concurrency", "3"])
    captured = capsys.readouterr()

    assert rc == 0
    assert captured_kwargs["mode"] == "reconcile_and_pull"
    assert captured_kwargs["exclusive_connector_ids"] == ["tw-official-market-datasets"]
    assert captured_kwargs["max_concurrency"] == 3

    payload = json.loads(captured.out)
    assert payload["status"] == "ok"
    assert payload["provider_egress_attempted"] is True


def test_main_supports_flag_connectors_and_force_connectors(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_kwargs: dict[str, Any] = {}

    def mock_run_controller_once(**kwargs: Any) -> dict[str, Any]:
        captured_kwargs.update(kwargs)
        return {"status": "ok", "controller_mode": "reconcile_and_pull"}

    monkeypatch.setattr(scheduler_once, "run_controller_once", mock_run_controller_once)

    rc = scheduler_once.main([
        "--connector", "c1,c2",
        "-c", "c3",
        "--force-connector", "f1",
    ])
    assert rc == 0
    assert captured_kwargs["exclusive_connector_ids"] == ["c1", "c2", "c3"]
    assert captured_kwargs["force_connector_ids"] == ["f1"]


def test_main_supports_reconcile_only_mode(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_kwargs: dict[str, Any] = {}

    def mock_run_controller_once(**kwargs: Any) -> dict[str, Any]:
        captured_kwargs.update(kwargs)
        return {
            "status": "ok",
            "controller_mode": "reconcile_only",
            "provider_egress_attempted": False,
        }

    monkeypatch.setattr(scheduler_once, "run_controller_once", mock_run_controller_once)

    rc = scheduler_once.main(["--mode", "reconcile_only"])
    captured = capsys.readouterr()

    assert rc == 0
    assert captured_kwargs["mode"] == "reconcile_only"
    assert captured_kwargs["exclusive_connector_ids"] == []
    payload = json.loads(captured.out)
    assert payload["controller_mode"] == "reconcile_only"
    assert payload["provider_egress_attempted"] is False


def test_main_reads_environment_variable_defaults(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_kwargs: dict[str, Any] = {}

    def mock_run_controller_once(**kwargs: Any) -> dict[str, Any]:
        captured_kwargs.update(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr(scheduler_once, "run_controller_once", mock_run_controller_once)
    monkeypatch.setenv("SOURCE_INGEST_CONNECTORS", "env-c1,env-c2")
    monkeypatch.setenv("SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY", "4")
    monkeypatch.setenv("SOURCE_INGEST_API_URL", "http://custom-api:9999")

    rc = scheduler_once.main([])
    assert rc == 0
    assert captured_kwargs["exclusive_connector_ids"] == ["env-c1", "env-c2"]
    assert captured_kwargs["max_concurrency"] == 4
    assert captured_kwargs["api_url"] == "http://custom-api:9999"


def test_main_rejects_reconcile_and_pull_without_connectors(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS", raising=False)
    monkeypatch.delenv("SOURCE_INGEST_CONNECTORS", raising=False)

    rc = scheduler_once.main([])
    captured = capsys.readouterr()

    assert rc == 1
    err_payload = json.loads(captured.err)
    assert err_payload["status"] == "failed"
    assert err_payload["stage"] == "validation"
    assert "reconcile_and_pull mode requires at least one explicitly selected connector ID" in err_payload["error"]

    rc2 = scheduler_once.main(["--mode", "reconcile_and_pull"])
    captured2 = capsys.readouterr()
    assert rc2 == 1
    err_payload2 = json.loads(captured2.err)
    assert err_payload2["status"] == "failed"
    assert err_payload2["stage"] == "validation"


def test_main_supports_operation_key_flag_and_env(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_kwargs: dict[str, Any] = {}

    def mock_run_controller_once(**kwargs: Any) -> dict[str, Any]:
        captured_kwargs.update(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr(scheduler_once, "run_controller_once", mock_run_controller_once)

    rc = scheduler_once.main(["tw-official-market-datasets", "--operation-key", "my-op-key-123"])
    assert rc == 0
    assert captured_kwargs["operation_key"] == "my-op-key-123"

    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_OPERATION_KEY", "env-op-key-456")
    rc2 = scheduler_once.main(["tw-official-market-datasets"])
    assert rc2 == 0
    assert captured_kwargs["operation_key"] == "env-op-key-456"


def test_main_handles_controller_error_and_returns_exit_code_1(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_run(**kwargs: Any) -> dict[str, Any]:
        raise controller_worker.ControllerTickError("mock_stage", "readback validation failed")

    monkeypatch.setattr(scheduler_once, "run_controller_once", failing_run)

    rc = scheduler_once.main(["tw-official-market-datasets"])
    captured = capsys.readouterr()

    assert rc == 1
    err_payload = json.loads(captured.err)
    assert err_payload["status"] == "failed"
    assert err_payload["stage"] == "mock_stage"
    assert "readback validation failed" in err_payload["error"]


def test_main_handles_operation_key_conflict(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_run(**kwargs: Any) -> dict[str, Any]:
        raise controller_worker.ControllerTickError(
            "operation_key_conflict",
            "operation key 'op-123' already executed with different request parameters",
        )

    monkeypatch.setattr(scheduler_once, "run_controller_once", failing_run)

    rc = scheduler_once.main(["tw-official-market-datasets", "--operation-key", "op-123"])
    captured = capsys.readouterr()

    assert rc == 1
    err_payload = json.loads(captured.err)
    assert err_payload["status"] == "failed"
    assert err_payload["stage"] == "operation_key_conflict"
    assert "operation key 'op-123' already executed with different request parameters" in err_payload["error"]
