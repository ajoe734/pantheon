"""Steady-tick overhead: JsonlConfiguredConnectorStore incremental journal reads.

Every result must equal what a full replay (a fresh store on the same journal) returns.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from services.source_ingestion import configured
from services.source_ingestion.configured import JsonlConfiguredConnectorStore
from services.source_ingestion.connectors import SourceConnector, SourceEvidenceError


def _connector(connector_id: str) -> SourceConnector:
    return SourceConnector(
        connector_id=connector_id, source_type="internal_note",
        provider="Pantheon steady tick test", license_scope="internal",
    )


def _fetch(tag: str) -> dict:
    return {
        "mode": "static_records", "next_watermark": tag,
        "records": [{"source_id": f"src-{tag}", "title": tag, "content_ref": f"memory://steady/{tag}"}],
    }


def _view(store: JsonlConfiguredConnectorStore):
    configs, states = store.read_snapshot()
    return (
        [c.to_dict() for c in store.list_configs()],
        [c.to_dict() for c in configs],
        states,
        {c.connector.connector_id: store.get_config(c.connector.connector_id).to_dict() for c in configs},
        {c.connector.connector_id: store.get_fetch_state(c.connector.connector_id) for c in configs},
    )


def _assert_equals_full_replay(store: JsonlConfiguredConnectorStore, path: Path) -> None:
    assert _view(store) == _view(JsonlConfiguredConnectorStore(path))


def test_incremental_reads_equal_full_replay_across_own_and_foreign_appends(tmp_path: Path) -> None:
    path = tmp_path / "connector_config.jsonl"
    store = JsonlConfiguredConnectorStore(path)
    other = JsonlConfiguredConnectorStore(path)  # stands for another process
    for i in range(5):
        store.upsert_config(_connector(f"c{i}"), _fetch(f"t{i}"))
    _assert_equals_full_replay(store, path)
    for n in range(4):
        store.record_fetch_attempt("c1", success=n % 2 == 0, error="boom", collector_outcome={"n": n})
    _assert_equals_full_replay(store, path)
    other.upsert_config(_connector("c9"), _fetch("t9"))
    other.record_fetch_attempt("c2", success=True)
    assert store.get_config("c9") is not None
    assert store.get_fetch_state("c2")["attempts"] == 1
    _assert_equals_full_replay(store, path)
    _assert_equals_full_replay(other, path)


def test_unchanged_journal_is_not_read_again_and_appends_read_only_the_tail(tmp_path: Path) -> None:
    path = tmp_path / "connector_config.jsonl"
    store = JsonlConfiguredConnectorStore(path)
    for i in range(20):
        store.upsert_config(_connector(f"c{i}"), _fetch(f"t{i}"))
    real_loads = json.loads
    with mock.patch.object(configured.json, "loads", side_effect=real_loads) as loads:
        store.list_configs()  # may re-read this process's own last append once
        loads.reset_mock()
        store.list_configs()
        store.read_snapshot()
        store.get_fetch_state("c1")
        assert loads.call_count == 0
        JsonlConfiguredConnectorStore(path).record_fetch_attempt("c3", success=True)
        loads.reset_mock()
        store.read_snapshot()
        assert loads.call_count == 1  # only the appended line


def test_replaced_truncated_and_rewritten_journals_fall_back_to_full_replay(tmp_path: Path) -> None:
    path = tmp_path / "connector_config.jsonl"
    store = JsonlConfiguredConnectorStore(path)
    for i in range(4):
        store.upsert_config(_connector(f"c{i}"), _fetch(f"t{i}"))
    store.record_fetch_attempt("c0", success=True)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    # truncated (same inode, shorter)
    path.write_text("".join(lines[:2]), encoding="utf-8")
    _assert_equals_full_replay(store, path)
    assert len(store.list_configs()) == 2
    # replaced by a different file with more content
    replacement = tmp_path / "new.jsonl"
    replacement.write_text("".join(lines), encoding="utf-8")
    os.replace(replacement, path)
    _assert_equals_full_replay(store, path)
    assert len(store.list_configs()) == 4
    # same size rewritten in place with different content
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("t1", "x1"), encoding="utf-8")
    _assert_equals_full_replay(store, path)
    assert store.get_config("c1").fetch["next_watermark"] == "x1"
    # removed
    path.unlink()
    assert store.list_configs() == []
    _assert_equals_full_replay(store, path)


def test_unterminated_tail_matches_full_replay_and_invalid_rows_still_fail(tmp_path: Path) -> None:
    path = tmp_path / "connector_config.jsonl"
    store = JsonlConfiguredConnectorStore(path)
    store.upsert_config(_connector("c0"), _fetch("t0"))
    JsonlConfiguredConnectorStore(path).upsert_config(_connector("c1"), _fetch("t1"))
    path.write_bytes(path.read_bytes().rstrip(b"\n"))  # last row without newline
    _assert_equals_full_replay(store, path)
    with path.open("ab") as handle:
        handle.write(b"\n")
    _assert_equals_full_replay(store, path)
    with path.open("ab") as handle:
        handle.write(b"{not json}\n")
    with pytest.raises(SourceEvidenceError):
        store.list_configs()
    with pytest.raises(SourceEvidenceError):
        JsonlConfiguredConnectorStore(path)


def test_returned_values_do_not_alias_the_cache(tmp_path: Path) -> None:
    path = tmp_path / "connector_config.jsonl"
    store = JsonlConfiguredConnectorStore(path)
    store.upsert_config(_connector("c0"), _fetch("t0"))
    store.record_fetch_attempt("c0", success=True, collector_outcome={"k": [1]})
    store.get_config("c0").fetch["records"].append({"mutated": True})
    store.get_fetch_state("c0")["collector_outcome"]["k"].append(2)
    store.list_configs()[0].fetch["records"].clear()
    _assert_equals_full_replay(store, path)


# --------------------------------------------------------------------------------------------------
# Hosted-shape steady tick: the reconcile that changes nothing and the readback must not replay the
# connector journal per call, and their payloads must equal what a full replay per call returns.
# --------------------------------------------------------------------------------------------------
import copy  # noqa: E402
import json as _json  # noqa: E402
from dataclasses import replace  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

from services.source_ingestion import controller_worker as cw  # noqa: E402
from services.source_ingestion import runtime as runtime_module  # noqa: E402
from services.source_ingestion.api_models import PersonaSourceProvisioningRequest  # noqa: E402
from services.source_ingestion.tests import steady_tick_hosted_shape as shape  # noqa: E402
from services.source_ingestion.tests import test_controller_worker as tcw  # noqa: E402
from services.source_ingestion.tests.test_collector_newest_session_hosted_throughput import (  # noqa: E402,F401
    TPEX, TWSE, hosted_index, hosted_run, isolated_cache,
)
from services.source_ingestion.tests.test_scheduled_connector import admission_stack, client  # noqa: E402,F401

FIXED_NOW = datetime(2026, 11, 18, 8, 0, 0, tzinfo=timezone.utc)


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: D401
        return FIXED_NOW if tz is not None else FIXED_NOW.replace(tzinfo=None)


@pytest.fixture()
def hosted(admission_stack, monkeypatch):
    http, module, _reads, wanted, files, transport, _tick = admission_stack
    entries = [e for e in _json.loads(files["bundles/index.json"])["bundles"] if e["source_id"] not in {TWSE, TPEX}]
    for source, count in ((TWSE, 1377), (TPEX, 6090)):
        for fetched in ("2026-11-13T12:18:42Z", "2026-11-13T18:18:44Z"):
            more, entry = hosted_run(source, count, fetched=fetched)
            files.update(more)
            entries.append(entry)
    files["bundles/index.json"] = hosted_index(entries)
    files["status.json"] = _json.dumps({"sources": {s: {
        "status": "ok", "offered": True, "last_run_at": "2026-11-14T06:19:33Z", "cadence_seconds": 21600,
    } for s in wanted}}).encode()
    monkeypatch.setenv("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS", "11376.TWSE,16089.TPEX")
    runtime = module.runtime
    config = replace(
        tcw._config(runtime.CONTROLLER_STATE_PATH.parent, truth_level="scheduled_tick",
                    mode=cw.COLLECTOR_STEADY_PULL_MODE),
        state_path=runtime.CONTROLLER_STATE_PATH, max_ticks=0, max_concurrency=1, interval_seconds=60,
        timeout_seconds=30,
    )
    state, store, writer = tcw._state(), cw.ControllerStateStore(runtime.CONTROLLER_STATE_PATH), tcw.RecordingWriter([])

    def tick():
        return cw.run_controller_tick(config=config, state=state, store=store, writer=writer)

    for _ in range(3):
        assert tick()["status"] == "ok"
    shape.grow_receipt_journal(runtime.SCHEDULE_STORE_PATH)
    shape.grow_connector_journal(runtime.CONNECTOR_STORE_PATH)
    shape.add_pending_dlq(runtime, [c.connector.connector_id for c in runtime.connector_store.list_configs()])
    personas, _meta = cw.load_desired_state()
    return types_namespace(runtime=runtime, tick=tick, personas=list(personas))


def types_namespace(**kwargs):
    import types

    return types.SimpleNamespace(**kwargs)


def _reconcile(hosted, *, dry_run: bool, personas=None):
    personas = hosted.personas if personas is None else personas
    runtime = hosted.runtime
    request = PersonaSourceProvisioningRequest(
        personas=list(personas), authoritative_snapshot=not dry_run, dry_run=dry_run,
        desired_state_sha256=runtime._desired_state_digest(list(personas)), source_authority="steady-tick-test",
    )
    return runtime.reconcile_persona_source_provisioning(request, authorization="Bearer " + runtime.controller_token)


class _ReplayCounter:
    """Counts journal lines parsed by JsonlConfiguredConnectorStore; ``full=True`` forces the old behaviour."""

    def __init__(self, monkeypatch, *, full: bool) -> None:
        self.lines = 0
        original_apply = JsonlConfiguredConnectorStore._apply_line_unlocked
        original_reload = JsonlConfiguredConnectorStore._reload_unlocked
        counter = self

        def apply(store, line, line_no):
            counter.lines += 1
            return original_apply(store, line, line_no)

        def reload(store):
            if full:
                store._read_key = None
            return original_reload(store)

        monkeypatch.setattr(JsonlConfiguredConnectorStore, "_apply_line_unlocked", apply)
        monkeypatch.setattr(JsonlConfiguredConnectorStore, "_reload_unlocked", reload)


_VOLATILE = {"captured_at", "age_seconds", "staleness_seconds", "seconds_until_due", "max_lag_seconds"}


def _strip_time(value):
    if isinstance(value, dict):
        return {k: _strip_time(v) for k, v in value.items() if k not in _VOLATILE}
    if isinstance(value, list):
        return [_strip_time(v) for v in value]
    return value


def test_unchanged_reconcile_parses_no_journal_and_returns_the_same_payload(hosted, monkeypatch) -> None:
    monkeypatch.setattr(runtime_module, "datetime", _FixedDatetime)
    _reconcile(hosted, dry_run=False)  # catches up with the large journals once
    journal_lines = len(hosted.runtime.CONNECTOR_STORE_PATH.read_text(encoding="utf-8").splitlines())
    assert journal_lines >= shape.HOSTED_CONNECTOR_LINES
    size_before = hosted.runtime.CONNECTOR_STORE_PATH.stat().st_size

    with monkeypatch.context() as scoped:
        incremental = _ReplayCounter(scoped, full=False)
        fast = _reconcile(hosted, dry_run=False)
    assert fast["summary"]["mutated"] == 0 and fast["summary"]["conflicts"] == 0
    assert incremental.lines <= 5, incremental.lines  # at most this process's own last appends

    with monkeypatch.context() as scoped:
        full = _ReplayCounter(scoped, full=True)
        slow = _reconcile(hosted, dry_run=False)
    assert full.lines >= 100 * journal_lines  # the replay-per-call cost this task removes (>= 100 replays)
    assert _strip_time(fast) == _strip_time(slow)
    assert hosted.runtime.CONNECTOR_STORE_PATH.stat().st_size == size_before  # unchanged reconcile writes nothing there


def test_changed_reconcile_still_repairs_and_payloads_match_a_full_replay(hosted, monkeypatch) -> None:
    runtime = hosted.runtime
    monkeypatch.setattr(runtime_module, "datetime", _FixedDatetime)
    _reconcile(hosted, dry_run=False)
    drifted = next(s for s in runtime.schedule_config_store.list_schedules() if s.connector_id.startswith("collector-"))
    runtime.schedule_config_store.upsert_schedule(
        drifted.connector_id, interval_seconds=drifted.interval_seconds + 7, enabled=True
    )
    planned_fast = _reconcile(hosted, dry_run=True)
    with monkeypatch.context() as scoped:
        _ReplayCounter(scoped, full=True)
        planned_slow = _reconcile(hosted, dry_run=True)
    assert _strip_time(planned_fast) == _strip_time(planned_slow)
    assert any(a["schedule_action"] == "would_repair" for r in planned_fast["results"] for a in r["actions"])

    applied = _reconcile(hosted, dry_run=False)
    assert applied["summary"]["mutated"] >= 1
    assert any(a["schedule_action"] == "repaired" for r in applied["results"] for a in r["actions"])
    settled = _reconcile(hosted, dry_run=False)
    assert settled["summary"]["mutated"] == 0


def test_controller_readback_payload_equals_full_replay_payload(hosted, monkeypatch) -> None:
    runtime = hosted.runtime
    monkeypatch.setattr(runtime_module, "datetime", _FixedDatetime)
    fast = copy.deepcopy(runtime._controller_readback_payload())
    with monkeypatch.context() as scoped:
        _ReplayCounter(scoped, full=True)
        slow = runtime._controller_readback_payload()
    assert _strip_time(fast) == _strip_time(slow)
    assert fast["connector_count"] >= 20 and fast["pending_dlq_count"] >= shape.HOSTED_PENDING_DLQ
