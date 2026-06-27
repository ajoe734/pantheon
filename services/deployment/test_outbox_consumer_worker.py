"""
Unit tests for the deployment saga outbox consumer worker.

Acceptance (LOOP-AUTO-DEP-001):
- Deployment outbox events are consumed durably.
- Duplicate outbox events are idempotent (status=duplicate is not an error).
- Consumer exposes health: last success, last failure.
"""
from __future__ import annotations

import importlib
import json
import sys
import urllib.error
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Isolate the worker module from any live service
# ---------------------------------------------------------------------------

_WORKER_MODULE = "services.deployment.outbox_consumer_worker"


def _load_worker():
    if _WORKER_MODULE in sys.modules:
        return importlib.import_module(_WORKER_MODULE)
    return importlib.import_module(_WORKER_MODULE)


@pytest.fixture()
def worker():
    return _load_worker()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _outbox_record(event_id: str, sequence_no: int = 1) -> dict[str, Any]:
    return {
        "owner_service": "deployment",
        "event": {
            "event_id": event_id,
            "event_type": "deployment_plan_approved",
            "aggregate_type": "deployment_plan",
            "aggregate_id": "plan-001",
            "sequence_no": sequence_no,
            "causal_parent_id": None,
            "event_time": "2026-06-27T10:00:00Z",
            "emitted_at": "2026-06-27T10:00:00Z",
            "trace_id": f"trace-{event_id}",
            "idempotency_key": f"idem-{event_id}",
            "payload": {},
        },
        "status": "pending",
        "delivery_attempts": 0,
        "published_at": None,
        "last_error": None,
    }


def _inbox_receipt(event_id: str, *, status: str = "applied") -> dict[str, Any]:
    return {
        "consumer_name": "deployment-outbox-consumer",
        "event_id": event_id,
        "idempotency_key": f"idem-{event_id}",
        "aggregate_type": "deployment_plan",
        "aggregate_id": "plan-001",
        "sequence_no": 1,
        "trace_id": f"trace-{event_id}",
        "status": status,
        "processed_at": "2026-06-27T10:00:01Z",
        "notes": None,
    }


# ---------------------------------------------------------------------------
# fetch_pending_outbox
# ---------------------------------------------------------------------------


class TestFetchPendingOutbox:
    def test_returns_empty_when_no_pending(self, worker):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.read.return_value = b"[]"
            mock_urlopen.return_value = mock_cm

            result = worker.fetch_pending_outbox(api_url="http://localhost:8095")

        assert result == []

    def test_returns_pending_records(self, worker):
        records = [_outbox_record("evt-001"), _outbox_record("evt-002")]
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.read.return_value = json.dumps(records).encode("utf-8")
            mock_urlopen.return_value = mock_cm

            result = worker.fetch_pending_outbox(api_url="http://localhost:8095")

        assert len(result) == 2
        assert result[0]["event"]["event_id"] == "evt-001"

    def test_url_includes_pending_filter(self, worker):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.read.return_value = b"[]"
            mock_urlopen.return_value = mock_cm

            worker.fetch_pending_outbox(api_url="http://localhost:8095")

        called_url = mock_urlopen.call_args[0][0].full_url
        assert "status=pending" in called_url


# ---------------------------------------------------------------------------
# consume_event
# ---------------------------------------------------------------------------


class TestConsumeEvent:
    def test_returns_receipt_on_applied(self, worker):
        receipt = _inbox_receipt("evt-001", status="applied")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.read.return_value = json.dumps(receipt).encode("utf-8")
            mock_urlopen.return_value = mock_cm

            result = worker.consume_event(
                api_url="http://localhost:8095",
                event_id="evt-001",
                consumer_name="deployment-outbox-consumer",
            )

        assert result["status"] == "applied"
        assert result["event_id"] == "evt-001"

    def test_duplicate_receipt_returned_without_error(self, worker):
        receipt = _inbox_receipt("evt-001", status="duplicate")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.read.return_value = json.dumps(receipt).encode("utf-8")
            mock_urlopen.return_value = mock_cm

            result = worker.consume_event(
                api_url="http://localhost:8095",
                event_id="evt-001",
                consumer_name="deployment-outbox-consumer",
            )

        assert result["status"] == "duplicate"

    def test_posts_to_correct_url(self, worker):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.read.return_value = json.dumps(_inbox_receipt("evt-42")).encode("utf-8")
            mock_urlopen.return_value = mock_cm

            worker.consume_event(
                api_url="http://localhost:8095",
                event_id="evt-42",
                consumer_name="test-consumer",
            )

        called_url = mock_urlopen.call_args[0][0].full_url
        assert "/api/deployment/outbox/evt-42/consume" in called_url


# ---------------------------------------------------------------------------
# run_poll — idempotency and error handling
# ---------------------------------------------------------------------------


class TestRunPoll:
    def test_no_events_returns_zero_consumed(self, worker):
        with patch.object(worker, "fetch_pending_outbox", return_value=[]):
            result = worker.run_poll(
                api_url="http://localhost:8095",
                consumer_name="test-consumer",
            )

        assert result == {
            "events_found": 0,
            "consumed": 0,
            "duplicates": 0,
            "errors": [],
        }

    def test_new_event_counts_as_consumed(self, worker):
        records = [_outbox_record("evt-001")]
        receipt = _inbox_receipt("evt-001", status="applied")
        with (
            patch.object(worker, "fetch_pending_outbox", return_value=records),
            patch.object(worker, "consume_event", return_value=receipt),
        ):
            result = worker.run_poll(
                api_url="http://localhost:8095",
                consumer_name="test-consumer",
            )

        assert result["events_found"] == 1
        assert result["consumed"] == 1
        assert result["duplicates"] == 0
        assert result["errors"] == []

    def test_duplicate_event_counts_as_duplicate_not_error(self, worker):
        records = [_outbox_record("evt-001")]
        receipt = _inbox_receipt("evt-001", status="duplicate")
        with (
            patch.object(worker, "fetch_pending_outbox", return_value=records),
            patch.object(worker, "consume_event", return_value=receipt),
        ):
            result = worker.run_poll(
                api_url="http://localhost:8095",
                consumer_name="test-consumer",
            )

        assert result["events_found"] == 1
        assert result["consumed"] == 0
        assert result["duplicates"] == 1
        assert result["errors"] == []

    def test_out_of_order_event_counts_as_error_not_consumed(self, worker):
        records = [_outbox_record("evt-002", sequence_no=2)]
        receipt = _inbox_receipt("evt-002", status="out_of_order")
        with (
            patch.object(worker, "fetch_pending_outbox", return_value=records),
            patch.object(worker, "consume_event", return_value=receipt),
        ):
            result = worker.run_poll(
                api_url="http://localhost:8095",
                consumer_name="test-consumer",
            )

        assert result["events_found"] == 1
        assert result["consumed"] == 0
        assert result["duplicates"] == 0
        assert "out_of_order" in result["errors"][0]

    def test_multiple_events_consumed_independently(self, worker):
        records = [_outbox_record("evt-001"), _outbox_record("evt-002"), _outbox_record("evt-003")]
        receipts = {
            "evt-001": _inbox_receipt("evt-001", status="applied"),
            "evt-002": _inbox_receipt("evt-002", status="duplicate"),
            "evt-003": _inbox_receipt("evt-003", status="applied"),
        }

        def _consume(*, api_url, event_id, consumer_name, timeout_seconds=10.0):
            return receipts[event_id]

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=records),
            patch.object(worker, "consume_event", side_effect=_consume),
        ):
            result = worker.run_poll(
                api_url="http://localhost:8095",
                consumer_name="test-consumer",
            )

        assert result["events_found"] == 3
        assert result["consumed"] == 2
        assert result["duplicates"] == 1
        assert result["errors"] == []

    def test_http_error_is_recorded_not_raised(self, worker):
        records = [_outbox_record("evt-err")]

        def _consume(**_kwargs):
            raise urllib.error.HTTPError(
                url="http://x", code=500, msg="Internal Server Error", hdrs=None, fp=None
            )

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=records),
            patch.object(worker, "consume_event", side_effect=_consume),
        ):
            result = worker.run_poll(
                api_url="http://localhost:8095",
                consumer_name="test-consumer",
            )

        assert result["errors"] != []
        assert "evt-err" in result["errors"][0]

    def test_missing_event_id_recorded_as_error(self, worker):
        bad_record = {"event": {}, "status": "pending"}
        with patch.object(worker, "fetch_pending_outbox", return_value=[bad_record]):
            result = worker.run_poll(
                api_url="http://localhost:8095",
                consumer_name="test-consumer",
            )

        assert result["errors"] != []
        assert result["consumed"] == 0


# ---------------------------------------------------------------------------
# health file
# ---------------------------------------------------------------------------


class TestWriteHealth:
    def test_writes_json_to_path(self, worker, tmp_path):
        health_path = str(tmp_path / "consumer_health.json")
        state = {"status": "ok", "last_success": "2026-06-27T10:00:00Z", "ticks": 1}
        worker._write_health(health_path, state)
        content = json.loads(Path(health_path).read_text("utf-8"))
        assert content["status"] == "ok"
        assert content["ticks"] == 1

    def test_silently_ignores_write_failure(self, worker):
        worker._write_health("/nonexistent/path/health.json", {"status": "ok"})


# ---------------------------------------------------------------------------
# main loop — max_ticks integration
# ---------------------------------------------------------------------------


class TestMain:
    def test_max_ticks_terminates_cleanly(self, worker, monkeypatch, capsys):
        monkeypatch.setenv("DEPLOYMENT_API_URL", "http://localhost:8095")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_INTERVAL_SECONDS", "1")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_MAX_TICKS", "2")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_HEALTH_FILE", "")

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[]),
            patch("time.sleep"),
        ):
            exit_code = worker.main()

        assert exit_code == 0
        captured = capsys.readouterr().out
        lines = [line for line in captured.strip().splitlines() if line]
        assert len(lines) == 2
        last = json.loads(lines[-1])
        assert last["tick"] == 2

    def test_health_reflects_last_success(self, worker, monkeypatch, capsys):
        monkeypatch.setenv("DEPLOYMENT_API_URL", "http://localhost:8095")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_INTERVAL_SECONDS", "1")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_MAX_TICKS", "1")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_HEALTH_FILE", "")

        records = [_outbox_record("evt-main-001")]
        receipt = _inbox_receipt("evt-main-001", status="applied")
        with (
            patch.object(worker, "fetch_pending_outbox", return_value=records),
            patch.object(worker, "consume_event", return_value=receipt),
            patch("time.sleep"),
        ):
            worker.main()

        captured = capsys.readouterr().out
        line = json.loads(captured.strip())
        assert line["health"]["total_consumed"] == 1
        assert line["health"]["last_success"] is not None
        assert line["health"]["last_failure"] is None

    def test_health_reflects_last_failure_on_error(self, worker, monkeypatch, capsys):
        monkeypatch.setenv("DEPLOYMENT_API_URL", "http://localhost:8095")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_INTERVAL_SECONDS", "1")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_MAX_TICKS", "1")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_HEALTH_FILE", "")

        records = [_outbox_record("evt-fail-001")]

        def _failing_consume(**_kwargs):
            raise RuntimeError("network timeout")

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=records),
            patch.object(worker, "consume_event", side_effect=_failing_consume),
            patch("time.sleep"),
        ):
            worker.main()

        captured = capsys.readouterr().out
        line = json.loads(captured.strip())
        assert line["health"]["status"] == "degraded"
        assert line["health"]["last_failure"] is not None
        assert line["health"]["last_failure_reason"] is not None

    def test_health_file_written_each_tick(self, worker, monkeypatch, tmp_path, capsys):
        health_file = str(tmp_path / "health.json")
        monkeypatch.setenv("DEPLOYMENT_API_URL", "http://localhost:8095")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_INTERVAL_SECONDS", "1")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_MAX_TICKS", "2")
        monkeypatch.setenv("DEPLOYMENT_OUTBOX_CONSUMER_HEALTH_FILE", health_file)

        with (
            patch.object(worker, "fetch_pending_outbox", return_value=[]),
            patch("time.sleep"),
        ):
            worker.main()

        content = json.loads(Path(health_file).read_text("utf-8"))
        assert content["ticks"] == 2
        assert content["status"] == "ok"
