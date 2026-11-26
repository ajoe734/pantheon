"""TW-MARGIN-DLQ-BACKLOG-BLOCKS-BOUNDED-REFRESH-20261126.

A bounded refresh fails only on dead letters it added in this run (the steady-pull pre/post
rule); the older unresolved backlog of the selected connectors is reported with counts and
oldest age. Operators resolve entries whose cause is fixed through the existing
``/api/source-ingest/dlq/replay`` path, which records an audit action and never deletes.

Everything below runs the real controller tick against the real source-ingest runtime store
(dead-letter journal, controller state, requirement snapshot, readback projection, replay
router). Only the provider schedule RPC and the desired-state load are stand-ins: the schedule
stand-in either does nothing (a clean run) or rejects one entry through the real journal.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.foundation import DeadLetterStatus, EnvironmentScope, EventEnvelope, TraceContext
from services.source_ingestion import chip_publication_schedule, controller_worker
from services.source_ingestion.controller_state import ControllerStateStore
from services.source_ingestion.controller_worker import ControllerTickError, run_controller_tick
from services.source_ingestion.routers.ingest_operations import create_ingest_operations_router
from services.source_ingestion.runtime import create_runtime
from services.source_ingestion.tests.test_controller_worker import (
    CONNECTOR_ID as TEMPLATE_CONNECTOR_ID,
    RecordingStateStore,
    RecordingWriter,
    _actual_readback,
    _call,
    _config,
    _desired_meta,
    _personas,
    _reconcile,
    _schedule,
    _state,
)

MARGIN_ID = "tw-official-twse-margin-balances"
OTHER_ID = "tw-official-tpex-margin-balances"
OLD_SESSION_REJECTED_AT = "2026-11-24T22:30:00Z"
OLD_RULE_REASON = (
    "tw-official-twse-margin-balances tw_margin_short_balance: tw-official-twse-margin-balances: inferred "
    "session 2026-11-24 previous-balance chain matched 1272, mismatched 39; refusing to store"
)


def _connector_readback(connector_id: str) -> dict[str, Any]:
    item = deepcopy(_actual_readback()["connectors"][0])
    item["connector_id"] = connector_id
    item["connector"]["connector_id"] = connector_id
    item["schedule"]["connector_id"] = connector_id
    item["latest_source_record"]["connector_id"] = connector_id
    item["latest_source_record"]["source_id"] = f"{connector_id}:latest"
    item["source_health"]["source_id"] = connector_id
    return item


def _margin_reconcile() -> dict[str, Any]:
    reconcile = _reconcile()
    reconcile["desired_state_sha256"] = "b" * 64
    reconcile["results"][0]["actions"][0]["connector_id"] = MARGIN_ID
    return reconcile


@pytest.fixture
def margin_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Real runtime store with the four 2026-11-24 dead letters of the old 99% rule."""
    runtime = create_runtime(data_dir=tmp_path / "owner")
    connectors = [_connector_readback(MARGIN_ID), _connector_readback(OTHER_ID)]
    monkeypatch.setattr(runtime, "_controller_connector_readbacks", lambda: deepcopy(connectors))
    ControllerStateStore(runtime.CONTROLLER_STATE_PATH).save(_state(sequence_no=1))
    runtime.requirement_snapshot_store.append(
        desired_state_sha256="b" * 64, bindings={}, persona_count=1, authority="test://margin-dlq-backlog"
    )

    def reject(connector_id: str, *, rejected_at: str | None = None, reason: str = OLD_RULE_REASON) -> str:
        event = EventEnvelope.new(
            event_type="source_ingestion.scheduled_run_failed",
            aggregate_type="source_ingest_run",
            aggregate_id=f"run-{connector_id}-{len(runtime.dead_letter_queue.entries())}",
            sequence_no=1,
            trace=TraceContext.new(environment=EnvironmentScope(name="dev")),
            producer_service="source-ingestion",
            payload={"connector_id": connector_id, "error": reason, "frontier_id": None},
        )
        entry = runtime.dead_letter_queue.reject(
            event, reason=reason, tags=("source_ingestion", "scheduled_ingest", "retry_exhausted")
        )
        if rejected_at is not None:
            runtime.dead_letter_queue.replace_entry(replace(entry, rejected_at=rejected_at))
        return entry.entry_id

    old_ids = [reject(MARGIN_ID, rejected_at=OLD_SESSION_REJECTED_AT) for _ in range(4)]
    return runtime, reject, old_ids


def _run_bounded_tick(runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, on_schedule=None):
    events: list[str] = []
    reads: list[dict[str, Any]] = []

    def read_actual_state(**_: Any) -> dict[str, Any]:
        runtime.dead_letter_queue.load_from_spill()
        actual = runtime._controller_readback_payload()
        reads.append(actual)
        return deepcopy(actual)

    def run_schedule_tick(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["exclusive_connector_ids"] == [MARGIN_ID]
        if on_schedule is not None:
            on_schedule()
        schedule = _schedule()
        schedule["summary"]["exclusive_connector_count"] = 1
        return schedule

    monkeypatch.setattr(controller_worker, "load_desired_state", lambda **_: (_personas(), _desired_meta()))
    monkeypatch.setattr(controller_worker, "reconcile_desired_state", lambda **_: _margin_reconcile())
    monkeypatch.setattr(controller_worker, "read_actual_state", read_actual_state)
    monkeypatch.setattr(controller_worker, "run_schedule_tick", run_schedule_tick)
    monkeypatch.setattr(controller_worker, "run_retention_sweep", lambda **_: {"status": "skipped"})
    config = _config(tmp_path, exclusive_connector_ids=(MARGIN_ID,))
    state = _state()
    writer = RecordingWriter(events)
    store = RecordingStateStore(config.state_path, events)
    return config, state, writer, store, reads


def test_template_connector_is_not_the_margin_connector() -> None:
    # The template readback is reused only for its shape; the scope under test is the margin connector.
    assert TEMPLATE_CONNECTOR_ID != MARGIN_ID
    assert MARGIN_ID in chip_publication_schedule.margin_chain_connector_ids()


def test_bounded_refresh_passes_over_old_backlog_and_reports_it(
    margin_owner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, reject, old_ids = margin_owner
    reject(OTHER_ID, rejected_at="2026-11-20T00:00:00Z")  # out of scope: reported globally, not here
    before = runtime.DLQ_STORE_PATH.read_bytes()
    config, state, writer, store, reads = _run_bounded_tick(runtime, tmp_path, monkeypatch)

    result = run_controller_tick(config=config, state=state, store=store, writer=writer)

    assert result["status"] == "ok"
    readback = _call(writer, "success")["kwargs"]["payload"]["actual_readback"]
    assert readback["pre_existing_unresolved_dlq_by_connector"] == {MARGIN_ID: 4}
    assert readback["pre_existing_unresolved_dlq_count"] == 4
    backlog = readback["pre_existing_unresolved_dlq_backlog"]
    assert backlog["count"] == 4
    assert set(backlog["by_connector"]) == {MARGIN_ID}
    margin = backlog["by_connector"][MARGIN_ID]
    assert margin["unresolved_count"] == 4
    assert margin["oldest_rejected_at"] == OLD_SESSION_REJECTED_AT
    captured = datetime.fromisoformat(reads[0]["captured_at"].replace("Z", "+00:00"))
    oldest = datetime(2026, 11, 24, 22, 30, tzinfo=timezone.utc)
    assert margin["oldest_age_seconds"] == int((captured - oldest).total_seconds())
    # The global truth still carries every unresolved entry; nothing was resolved or deleted.
    assert readback["unresolved_dlq_count"] == 5
    assert _call(writer, "success")["kwargs"]["dlq_count"] == 5
    assert [e.entry_id for e in runtime.dead_letter_queue.entries()][:4] == old_ids
    assert runtime.DLQ_STORE_PATH.read_bytes() == before
    # Readback added per run: one small object per selected connector with a backlog, kept in the
    # bounded controller-state summary that the refresh result returns.
    assert len(json.dumps(backlog, sort_keys=True)) < 256
    assert result["actual_readback"]["pre_existing_unresolved_dlq_backlog"] == backlog
    assert state.actual_readback["pre_existing_unresolved_dlq_backlog"] == backlog


def test_bounded_refresh_fails_on_one_entry_added_in_this_run(
    margin_owner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, reject, _ = margin_owner
    config, state, writer, store, _ = _run_bounded_tick(
        runtime, tmp_path, monkeypatch, on_schedule=lambda: reject(MARGIN_ID, reason="new failure")
    )

    with pytest.raises(ControllerTickError, match=r"1 new unresolved dead-letter entrie\(s\) in selected connector scope"):
        run_controller_tick(config=config, state=state, store=store, writer=writer)
    failure = _call(writer, "failure")
    assert failure["kwargs"]["dlq_count"] == 5
    assert state.last_failure_stage == "actual_readback"


def test_bounded_refresh_without_a_trusted_pre_map_keeps_the_strict_rule(
    margin_owner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _, _ = margin_owner
    config, state, writer, store, _ = _run_bounded_tick(runtime, tmp_path, monkeypatch)
    real_read = controller_worker.read_actual_state
    calls = {"n": 0}

    def contradictory_pre(**kwargs: Any) -> dict[str, Any]:
        actual = real_read(**kwargs)
        calls["n"] += 1
        if calls["n"] == 1:  # pre-tick read: totals contradict the per-connector map
            actual["unresolved_dlq_count"] = 0
        return actual

    monkeypatch.setattr(controller_worker, "read_actual_state", contradictory_pre)
    with pytest.raises(ControllerTickError, match=r"has 4 unresolved dead-letter"):
        run_controller_tick(config=config, state=state, store=store, writer=writer)


def test_oldest_age_is_none_when_source_readback_lacks_the_timestamp(margin_owner) -> None:
    runtime, _, _ = margin_owner
    actual = runtime._controller_readback_payload()
    assert actual["dlq_oldest_unresolved_at_by_connector"] == {MARGIN_ID: OLD_SESSION_REJECTED_AT}
    actual.pop("dlq_oldest_unresolved_at_by_connector")
    backlog = controller_worker.preexisting_dlq_backlog(actual, [MARGIN_ID, OTHER_ID])
    assert backlog == {
        "count": 4,
        "by_connector": {
            MARGIN_ID: {"unresolved_count": 4, "oldest_rejected_at": None, "oldest_age_seconds": None}
        },
    }


def test_resolved_entries_leave_the_oldest_unresolved_timestamp(margin_owner) -> None:
    runtime, reject, old_ids = margin_owner
    reject(MARGIN_ID, rejected_at="2026-11-25T22:30:00Z")
    for entry in runtime.dead_letter_queue.entries():
        if entry.entry_id in old_ids:
            runtime.dead_letter_queue.replace_entry(replace(entry, status=DeadLetterStatus.REPLAYED))
    actual = runtime._controller_readback_payload()
    assert actual["dlq_oldest_unresolved_at_by_connector"] == {MARGIN_ID: "2026-11-25T22:30:00Z"}


# --- Operator resolution: replay through the existing route, audited, nothing deleted -------------


def _chain_rows(total: int, revised: int) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """1311 symbols as on 2026-11-24; ``revised`` previous balances were revised by the exchange."""
    rows, stored = [], {}
    for index in range(total):
        symbol = f"{1000 + index}"
        stored[symbol] = {"margin_balance": 100 + index, "short_balance": index}
        delta = 1 if index < revised else 0
        rows.append(
            {"symbol": symbol, "margin_previous_balance": 100 + index + delta, "short_previous_balance": index}
        )
    return rows, stored


def _replay_margin_session(event: EventEnvelope) -> str:
    """Stand-in for the connector re-run: the 2026-11-24 payload through the live chain rule."""
    assert event.payload["connector_id"] == MARGIN_ID
    rows, stored = _chain_rows(1311, 39)
    evidence = chip_publication_schedule.verify_margin_session_chain(
        MARGIN_ID, date(2026, 11, 24), rows, stored
    )
    assert evidence["status"] == "chain_verified"
    return f"source_ingest_run:replay-{event.aggregate_id}"


def _replay_client(runtime) -> TestClient:
    app = FastAPI()
    app.include_router(create_ingest_operations_router(runtime))
    return TestClient(app)


def test_old_rule_refuses_and_new_rule_accepts_the_2026_11_24_session(monkeypatch: pytest.MonkeyPatch) -> None:
    rows, stored = _chain_rows(1311, 39)
    monkeypatch.setattr(chip_publication_schedule, "MARGIN_CHAIN_MIN_MATCH_RATIO", 0.99)
    with pytest.raises(chip_publication_schedule.ChipSessionUndecidable, match="matched 1272, mismatched 39"):
        chip_publication_schedule.verify_margin_session_chain(MARGIN_ID, date(2026, 11, 24), rows, stored)
    monkeypatch.setattr(chip_publication_schedule, "MARGIN_CHAIN_MIN_MATCH_RATIO", 0.90)
    assert chip_publication_schedule.verify_margin_session_chain(
        MARGIN_ID, date(2026, 11, 24), rows, stored
    )["matched_count"] == 1272


def test_operator_replay_resolves_fixed_entries_with_audit_and_no_deletion(
    margin_owner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, reject, old_ids = margin_owner
    unrelated = reject(OTHER_ID, rejected_at="2026-11-20T00:00:00Z")
    monkeypatch.setattr(runtime.pipeline, "replay_source_event", _replay_margin_session)
    journal_before = runtime.DLQ_STORE_PATH.read_bytes()

    response = _replay_client(runtime).post(
        "/api/source-ingest/dlq/replay",
        json={
            "entry_ids": old_ids,
            "reason": "TW-MARGIN-DLQ-BACKLOG-BLOCKS-BOUNDED-REFRESH-20261126: 2026-11-24 refused by the "
            "retired 99% chain rule; cause fixed by #7839 (0.90)",
            "actor_id": "source-ingest-operator",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["summary"]["applied"] == 4
    assert payload["selected_entry_ids"] == sorted(old_ids)
    assert {item["entry_id"] for item in payload["correlated_resolutions"]} == set(old_ids)
    assert all(item["status"] == "replayed" for item in payload["correlated_resolutions"])
    # Audit: one replay action per entry, with the operator's reason.
    audit = [json.loads(line) for line in runtime.AUDIT_STORE_PATH.read_text().splitlines() if line.strip()]
    applied = [a for a in audit if a["action_type"] == "foundation.dlq.replay.applied"]
    assert len(applied) == 4
    assert all("cause fixed by #7839" in a["reason"] for a in applied)
    # Nothing deleted: the journal only grew, every entry is still present, the unrelated one untouched.
    journal_after = runtime.DLQ_STORE_PATH.read_bytes()
    assert journal_after.startswith(journal_before)
    reloaded = {e.entry_id: e for e in runtime.dead_letter_queue.entries()}
    assert set(old_ids) | {unrelated} <= set(reloaded)
    assert all(reloaded[i].status == DeadLetterStatus.REPLAYED for i in old_ids)
    assert all(reloaded[i].audit_action_ref for i in old_ids)
    assert reloaded[unrelated].status == DeadLetterStatus.PENDING
    # The next bounded refresh sees no margin backlog.
    actual = runtime._controller_readback_payload()
    assert controller_worker.scoped_unresolved_dlq_by_connector(actual, [MARGIN_ID]) == {MARGIN_ID: 0}
    assert MARGIN_ID not in actual["dlq_oldest_unresolved_at_by_connector"]


def test_operator_replay_that_still_fails_keeps_entries_unresolved(
    margin_owner, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _, old_ids = margin_owner

    def still_refused(event: EventEnvelope) -> str:
        raise RuntimeError("chain still refused")

    monkeypatch.setattr(runtime.pipeline, "replay_source_event", still_refused)
    response = _replay_client(runtime).post(
        "/api/source-ingest/dlq/replay", json={"entry_ids": old_ids[:1], "reason": "operator retry"}
    )
    assert response.status_code == 200, response.text
    entries = {e.entry_id: e for e in runtime.dead_letter_queue.entries()}
    assert entries[old_ids[0]].status == DeadLetterStatus.REPLAY_FAILED
    assert all(entries[i].status == DeadLetterStatus.PENDING for i in old_ids[1:])
    actual = runtime._controller_readback_payload()
    assert controller_worker.scoped_unresolved_dlq_by_connector(actual, [MARGIN_ID]) == {MARGIN_ID: 4}
