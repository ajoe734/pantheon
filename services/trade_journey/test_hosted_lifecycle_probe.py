from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import types
import uuid

import pytest

from services.trade_journey import hosted_lifecycle_probe as probe
from services.trade_journey.lifecycle_projector import LifecycleProjector, _fingerprint
from services.trade_journey.materializer import JourneyMaterializer
from services.trade_journey.test_lifecycle_projector import lifecycle_rows


class FakeSource:
    def __init__(self, high: int, rows: list[dict]) -> None:
        self.high = high
        self.rows = rows
        self.calls = 0

    async def snapshot(self):
        self.calls += 1
        if self.calls == 1:
            return 0, []
        return self.high, self.rows


class BaselineSource:
    def __init__(self, high: int, rows: list[dict]) -> None:
        self.high = high
        self.rows = rows

    async def snapshot(self):
        return self.high, self.rows


class BrokenSource:
    async def snapshot(self):
        raise RuntimeError("postgresql://secret-host/secret-database")


class IncrementalSource:
    def __init__(self, baseline: int, high: int, rows: list[dict]) -> None:
        self.baseline = baseline
        self.high = high
        self.rows = rows
        self.high_watermark_calls = 0
        self.snapshot_after_baselines: list[int] = []
        self.snapshot_calls = 0

    async def high_watermark(self) -> int:
        self.high_watermark_calls += 1
        return self.baseline

    async def snapshot_after(self, baseline: int):
        self.snapshot_after_baselines.append(baseline)
        return self.high, self.rows

    async def snapshot(self):
        self.snapshot_calls += 1
        raise AssertionError("incremental source should not use full snapshot")


class ProjectionSnapshot:
    backend = "postgres"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.candidates: list[dict] = []

    async def current_projection(self, candidate):
        self.candidates.append(dict(candidate))
        journeys, loops, _generation = probe._current_projection(self.root)
        return journeys, loops, f"postgres-revision-{loops['generation']}"


def test_asyncpg_telemetry_source_filters_watermark_and_snapshot(monkeypatch):
    calls: list[tuple] = []

    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class Connection:
        def transaction(self, **kwargs):
            calls.append(("transaction", kwargs))
            return Transaction()

        async def fetchval(self, query: str, event_types: list[str]) -> int:
            calls.append(("fetchval", query, tuple(event_types)))
            return 106

        async def fetch(
            self,
            query: str,
            baseline: int,
            event_types: list[str],
            row_limit: int,
        ) -> list[dict]:
            calls.append(("fetch", query, baseline, tuple(event_types), row_limit))
            return []

        async def close(self) -> None:
            calls.append(("close",))

    async def connect(dsn: str) -> Connection:
        calls.append(("connect", dsn))
        return Connection()

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=connect))
    source = probe.AsyncpgTelemetrySource("postgresql://unit", row_limit=17)

    assert asyncio.run(source.high_watermark()) == 106
    assert asyncio.run(source.snapshot_after(100)) == (106, [])

    fetchval = next(call for call in calls if call[0] == "fetchval")
    fetch = next(call for call in calls if call[0] == "fetch")
    assert "event_type = ANY" in fetchval[1]
    assert "event_type = ANY" in fetch[1]
    assert fetchval[2] == probe.QUERY_TYPES
    assert fetch[2] == 100
    assert fetch[3] == probe.QUERY_TYPES
    assert fetch[4] == 17


def _natural_lifecycle_rows() -> list[dict]:
    by_type = {row["event_type"]: row for row in lifecycle_rows()}
    event_types = [*probe.REQUIRED_EVENT_TYPES, "reconciliation_completed"]
    selected = [json.loads(json.dumps(by_type[event_type])) for event_type in event_types]
    signal_event_id = selected[0]["event_id"]
    fill_event_id = selected[5]["event_id"]
    evaluation_id = "evaluation-paper-001"
    event_ids = [
        signal_event_id,
        str(
            uuid.uuid5(
                probe.PAPER_LIFECYCLE_UUID_NAMESPACE,
                f"{signal_event_id}:trade_decision",
            )
        ),
        str(
            uuid.uuid5(
                probe.PAPER_LIFECYCLE_UUID_NAMESPACE,
                f"{fill_event_id}:risk_evaluation",
            )
        ),
        str(
            uuid.uuid5(
                probe.PAPER_LIFECYCLE_UUID_NAMESPACE,
                f"{fill_event_id}:order_submitted",
            )
        ),
        str(
            uuid.uuid5(
                probe.PAPER_LIFECYCLE_UUID_NAMESPACE,
                f"{fill_event_id}:order_accepted",
            )
        ),
        fill_event_id,
        str(
            uuid.uuid5(
                probe.PAPER_LIFECYCLE_UUID_NAMESPACE,
                f"{fill_event_id}:position_snapshot",
            )
        ),
        str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"pantheon:scheduled-reconciliation:{evaluation_id}",
            )
        ),
    ]
    causal_parent = "signal:signal-paper-001"
    for ingested_seq, (row, event_id) in enumerate(zip(selected, event_ids), start=1):
        event = row["payload"]
        event_type = event["event_type"]
        producer = (
            "reconciliation-drift.scheduled"
            if event_type == "reconciliation_completed"
            else "execution.paper_runtime"
        )
        row["event_id"] = event_id
        event["event_id"] = event_id
        event["sequence_no"] = ingested_seq
        event["causal_parent_id"] = causal_parent
        event["source_mode"] = "live"
        event["aggregate_type"] = "trade_journey"
        event["aggregate_id"] = "tj-paper-001"
        event["metadata"]["sequence_no"] = ingested_seq
        event["metadata"]["causal_parent_id"] = causal_parent
        event["metadata"]["source_mode"] = "live"
        if event_type == "reconciliation_completed":
            event["metadata"]["reconciliation_evaluation_id"] = evaluation_id
        event["correlation_envelope"].update(
            {
                "event_id": event_id,
                "causation_event_id": causal_parent,
                "producer": producer,
                "producer_revision": 1,
                "event_time": event["created_at"],
                "received_at": event["created_at"],
            }
        )
        row["ingested_seq"] = ingested_seq
        causal_parent = event_id
    for index, row in enumerate(selected):
        event = row["payload"]
        if event["event_type"] == "reconciliation_completed":
            metadata_envelope = event["correlation_envelope"]
        elif index == 0:
            metadata_envelope = {"event_id": "signal:signal-paper-001"}
        else:
            metadata_envelope = selected[index - 1]["payload"]["correlation_envelope"]
        event["metadata"]["correlation_envelope"] = json.loads(
            json.dumps(metadata_envelope)
        )
    return selected


def _publish(
    tmp_path: Path,
    *,
    sha: str = "deployed-sha",
    mode: str = "live",
    rows: list[dict] | None = None,
    source_high_watermark: int | None = None,
    generation: int = 1,
) -> tuple[Path, list[dict]]:
    root = tmp_path / "projection"
    generations_dir = root / "generations"
    gen_dir = generations_dir / f"gen-{generation:06d}"
    gen_dir.mkdir(parents=True, exist_ok=True)
    if rows is None:
        rows = _natural_lifecycle_rows()

    hw = source_high_watermark if source_high_watermark is not None else (len(rows) if rows else 0)
    last_seq = rows[-1].get("ingested_seq", hw) if rows else hw
    last_event_id = rows[-1].get("event_id", "event-001") if rows else "event-001"
    last_ts = rows[-1].get("ingested_at") or (rows[-1].get("payload", {}).get("created_at")) or "2026-08-22T00:00:00Z"

    ctrl_dict = {
        "controller_id": "canonical-lifecycle-projector",
        "status": "ready",
        "deployment_sha": sha,
        "mode": mode,
        "accepted_live": mode == "live",
        "truth_level": "canonical_live" if mode == "live" else "not_accepted_live",
        "checkpoint": hw,
        "source_high_watermark": hw,
        "backlog": 0,
        "last_processed_ingested_seq": last_seq,
        "last_processed_event_id": last_event_id,
        "last_processed_timestamp": last_ts,
        "quarantine_count": 0,
        "generation": generation,
    }

    events_list = []
    for row in rows:
        evt = LifecycleProjector._source_event(row)
        identity = LifecycleProjector._identity(evt)
        seq = int(row.get("ingested_seq") or 0)
        event_type = str(evt.get("event_type") or "")
        stage_name = probe.EXPECTED_STAGES.get(event_type, "research_rationale")
        events_list.append({
            **identity,
            "canonical_event_id": str(evt.get("event_id") or ""),
            "stage": stage_name,
            "stage_status": "completed",
            "source_mode": mode,
            "accepted_live": mode == "live",
            "source_offset": seq,
        })

    candidate_identity = (
        LifecycleProjector._identity(LifecycleProjector._source_event(rows[0]))
        if rows
        else {}
    )
    loop_run_id = candidate_identity.get("loop_run_id") or "loop-paper-001"

    journeys = {
        "schema_version": probe.JOURNEY_STORE_SCHEMA,
        "generation": generation,
        "controller": ctrl_dict,
        "events": events_list,
    }
    loops = {
        "schema_version": probe.LOOP_STORE_SCHEMA,
        "generation": generation,
        "controller": ctrl_dict,
        "records": {
            loop_run_id: {
                **candidate_identity,
                "status": "completed",
                "accepted_live": mode == "live",
                "projection_mode": mode,
                "projection_revision": generation,
            }
        },
    }
    manifest = {
        "schema_version": "pantheon.lifecycle-projection-bundle.v1",
        "generation": generation,
        "journey_sha256": _fingerprint(journeys),
        "loop_runs_sha256": _fingerprint(loops),
    }

    (gen_dir / "trade_journey_events.json").write_text(json.dumps(journeys), encoding="utf-8")
    (gen_dir / "loop_runs.json").write_text(json.dumps(loops), encoding="utf-8")
    (gen_dir / "controller_state.json").write_text(json.dumps(ctrl_dict), encoding="utf-8")
    (gen_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    current_symlink = root / "current"
    if current_symlink.exists() or current_symlink.is_symlink():
        current_symlink.unlink()
    os.symlink(gen_dir, current_symlink)
    return root, rows


def _execute(
    tmp_path: Path,
    *,
    root: Path,
    rows: list[dict],
    expected_sha: str = "deployed-sha",
) -> tuple[int, dict]:
    return asyncio.run(
        probe.execute(
            source=FakeSource(len(rows), rows),
            projection_root=root,
            expected_sha=expected_sha,
            output=tmp_path / "evidence.json",
            timeout_seconds=0.1,
            poll_seconds=0.001,
        )
    )


def test_probe_correlates_committed_events_to_live_journey_and_loop(tmp_path):
    root, rows = _publish(tmp_path)

    code, artifact = _execute(tmp_path, root=root, rows=rows)

    assert code == 0
    assert artifact["outcome"] == "passed"
    assert artifact["proof"]["source"]["baseline_high_watermark"] == 0
    assert artifact["proof"]["source"]["source_high_watermark"] == 8
    assert [event["event_type"] for event in artifact["proof"]["events"]] == [
        "signal_generation",
        "trade_decision",
        "risk_evaluation",
        "order_submitted",
        "order_accepted",
        "paper_fill_simulated",
        "position_snapshot",
        "reconciliation_completed",
    ]
    assert artifact["proof"]["projection"] == {
        **artifact["proof"]["projection"],
        "accepted_live": True,
        "truth_level": "canonical_live",
        "status": "ready",
        "deployment_sha": "deployed-sha",
        "loop_status": "completed",
    }
    raw = (tmp_path / "evidence.json").read_text(encoding="utf-8")
    assert json.loads(raw) == artifact
    assert "postgresql://" not in raw
    assert artifact["redaction"] == {"dsn_included": False, "payloads_included": False}


def test_probe_correlates_from_relational_projection_snapshot(tmp_path):
    root, rows = _publish(tmp_path)
    projection = ProjectionSnapshot(root)

    code, artifact = asyncio.run(
        probe.execute(
            source=BaselineSource(len(rows), rows),
            projection_root=None,
            projection_source=projection,
            expected_sha="deployed-sha",
            output=tmp_path / "relational-evidence.json",
            timeout_seconds=0.1,
            poll_seconds=0.001,
            baseline_high_watermark=0,
        )
    )

    assert code == 0
    assert artifact["proof"]["projection"]["backend"] == "postgres"
    assert artifact["proof"]["projection"]["generation_name"] == (
        "postgres-revision-1"
    )
    assert len(projection.candidates) == 1


def test_relational_projection_source_reads_one_repeatable_snapshot(
    tmp_path,
    monkeypatch,
):
    root, rows = _publish(tmp_path)
    candidate = probe._complete_candidates(rows)[0]
    journeys, loops, _generation = probe._current_projection(root)
    loop = loops["records"][candidate["identity"]["loop_run_id"]]
    transactions: list[dict] = []
    queries: list[str] = []

    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class Connection:
        def transaction(self, **kwargs):
            transactions.append(kwargs)
            return Transaction()

        async def fetchrow(self, query, *params):
            queries.append(query)
            if ".controller " in query:
                return {
                    "controller_id": "canonical-lifecycle-projector",
                    "checkpoint_seq": 8,
                    "source_high_watermark": 8,
                    "backlog_count": 0,
                    "projection_revision": 1,
                    "deployment_sha": "deployed-sha",
                    "mode": "live",
                    "status": "ready",
                    "accepted_live": True,
                    "last_poll_at": "2026-08-21T00:00:00Z",
                    "last_error_message": "",
                    "unresolved_quarantine_count": 0,
                }
            if ".journeys " in query:
                return {
                    "current_identity_summary": {},
                    "projection_revision": 1,
                }
            if ".loop_runs " in query:
                return {
                    "tenant_id": candidate["identity"]["tenant_id"],
                    "environment": candidate["identity"]["environment"],
                    "loop_run_id": candidate["identity"]["loop_run_id"],
                    "journey_id": candidate["identity"]["journey_id"],
                    "status": loop["status"],
                    "lifecycle_summary": loop,
                    "freshness_lineage": {
                        "mode": "live",
                        "accepted_live": True,
                    },
                    "contract_payload": loop,
                    "projection_revision": 1,
                }
            raise AssertionError(query)

        async def fetch(self, query, *params):
            queries.append(query)
            return [
                {
                    "source_event_id": event["canonical_event_id"],
                    "stage_name": event["stage"],
                    "stage_status": event["stage_status"],
                    "source_ingested_seq": event["source_offset"],
                    "contract_fields": event,
                }
                for event in journeys["events"]
            ]

        async def close(self):
            return None

    async def connect(dsn):
        assert dsn == "postgresql://redacted"
        return Connection()

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=connect))
    source = probe.AsyncpgRelationalProjectionSource("postgresql://redacted")

    relational_journeys, relational_loops, generation = asyncio.run(
        source.current_projection(candidate)
    )

    assert transactions == [{"isolation": "repeatable_read", "readonly": True}]
    assert len(queries) == 4
    assert relational_journeys["journey_present"] is True
    assert relational_loops["controller"]["accepted_live"] is True
    assert generation == "postgres-revision-1"


def test_probe_retries_projection_integrity_until_bundle_is_valid(tmp_path):
    root, rows = _publish(tmp_path)
    generation = (root / "current").resolve(strict=True)
    manifest_path = generation / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["journey_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    repairs = 0

    async def repair_manifest(_delay: float) -> None:
        nonlocal repairs
        if repairs == 0:
            journeys = json.loads(
                (generation / "trade_journey_events.json").read_text(encoding="utf-8")
            )
            loops = json.loads((generation / "loop_runs.json").read_text(encoding="utf-8"))
            manifest["journey_sha256"] = _fingerprint(journeys)
            manifest["loop_runs_sha256"] = _fingerprint(loops)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        repairs += 1

    code, artifact = asyncio.run(
        probe.execute(
            source=BaselineSource(len(rows), rows),
            projection_root=root,
            expected_sha="deployed-sha",
            output=tmp_path / "retry-integrity.json",
            timeout_seconds=1,
            poll_seconds=0.001,
            baseline_high_watermark=0,
            sleeper=repair_manifest,
        )
    )

    assert code == 0
    assert artifact["outcome"] == "passed"
    assert repairs == 1


def test_probe_reads_only_rows_after_baseline_for_incremental_source(tmp_path):
    shifted_rows = _natural_lifecycle_rows()
    for row in shifted_rows:
        row["ingested_seq"] += 100
    root, _ = _publish(tmp_path, rows=shifted_rows, source_high_watermark=108)
    source = IncrementalSource(baseline=100, high=108, rows=shifted_rows)

    code, artifact = asyncio.run(
        probe.execute(
            source=source,
            projection_root=root,
            expected_sha="deployed-sha",
            output=tmp_path / "incremental.json",
            timeout_seconds=0.1,
            poll_seconds=0.001,
        )
    )

    assert code == 0
    assert artifact["outcome"] == "passed"
    assert source.high_watermark_calls == 1
    assert source.snapshot_after_baselines == [100]
    assert source.snapshot_calls == 0
    assert artifact["proof"]["source"]["baseline_high_watermark"] == 100
    assert artifact["proof"]["source"]["source_high_watermark"] == 108


def test_probe_uses_explicit_baseline_without_initial_high_watermark(tmp_path):
    shifted_rows = _natural_lifecycle_rows()
    for row in shifted_rows:
        row["ingested_seq"] += 200
    root, _ = _publish(tmp_path, rows=shifted_rows, source_high_watermark=208)
    source = IncrementalSource(baseline=999, high=208, rows=shifted_rows)

    code, artifact = asyncio.run(
        probe.execute(
            source=source,
            projection_root=root,
            expected_sha="deployed-sha",
            output=tmp_path / "explicit-baseline.json",
            timeout_seconds=0.1,
            poll_seconds=0.001,
            baseline_high_watermark=200,
        )
    )

    assert code == 0
    assert artifact["outcome"] == "passed"
    assert source.high_watermark_calls == 0
    assert source.snapshot_after_baselines == [200]
    assert source.snapshot_calls == 0
    assert artifact["proof"]["source"]["baseline_high_watermark"] == 200
    assert artifact["proof"]["source"]["source_high_watermark"] == 208


def test_main_prints_high_watermark_without_projection_root_or_output(monkeypatch, capsys):
    class Source:
        async def high_watermark(self):
            return 42

    monkeypatch.setenv("TELEMETRY_DB_DSN", "postgresql://secret-host/secret-database")
    monkeypatch.delenv("LIFECYCLE_PROJECTION_ROOT", raising=False)
    monkeypatch.setattr(probe, "AsyncpgTelemetrySource", lambda _dsn: Source())

    code = probe.main(["--expected-sha", "deployed-sha", "--print-high-watermark"])

    assert code == 0
    captured = capsys.readouterr()
    assert captured.out == "42\n"
    assert captured.err == ""


def test_probe_times_out_without_a_complete_natural_aggregate(tmp_path):
    output = tmp_path / "timeout.json"

    code, artifact = asyncio.run(
        probe.execute(
            source=FakeSource(12, []),
            projection_root=tmp_path / "missing",
            expected_sha="deployed-sha",
            output=output,
            timeout_seconds=0,
            poll_seconds=0.01,
        )
    )

    assert code == 1
    assert artifact["failure"]["code"] == "no_complete_paper_aggregate"
    assert json.loads(output.read_text(encoding="utf-8")) == artifact


def test_probe_rejects_deployment_sha_mismatch(tmp_path):
    root, rows = _publish(tmp_path, sha="other-sha")

    code, artifact = _execute(tmp_path, root=root, rows=rows)

    assert code == 1
    assert artifact["failure"] == {
        "code": "deployment_sha_mismatch",
        "message": "projector deployment SHA does not match expected SHA",
        "timed_out": True,
    }


@pytest.mark.parametrize("mode", ["backfill", "replay"])
def test_probe_rejects_manual_projection_truth(tmp_path, mode):
    root, rows = _publish(tmp_path, mode=mode)

    code, artifact = _execute(tmp_path, root=root, rows=rows)

    assert code == 1
    assert artifact["failure"]["code"] == "controller_not_canonical_live"


def test_probe_rejects_degraded_controller(tmp_path):
    root, rows = _publish(tmp_path)
    ctrl_path = (root / "current" / "controller_state.json").resolve()
    ctrl = json.loads(ctrl_path.read_text(encoding="utf-8"))
    ctrl["status"] = "degraded"
    ctrl["error"] = "secret-postgres-dsn-would-not-be-exported"
    ctrl_path.write_text(json.dumps(ctrl), encoding="utf-8")

    journeys_path = (root / "current" / "trade_journey_events.json").resolve()
    journeys = json.loads(journeys_path.read_text(encoding="utf-8"))
    journeys["controller"] = ctrl
    journeys_path.write_text(json.dumps(journeys), encoding="utf-8")

    loops_path = (root / "current" / "loop_runs.json").resolve()
    loops = json.loads(loops_path.read_text(encoding="utf-8"))
    loops["controller"] = ctrl
    loops_path.write_text(json.dumps(loops), encoding="utf-8")

    manifest_path = (root / "current" / "manifest.json").resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["journey_sha256"] = _fingerprint(journeys)
    manifest["loop_runs_sha256"] = _fingerprint(loops)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    code, artifact = _execute(tmp_path, root=root, rows=rows)

    assert code == 1
    assert artifact["failure"]["code"] == "controller_not_canonical_live"
    assert "secret" not in json.dumps(artifact)


def test_probe_rejects_tampered_projection_manifest(tmp_path):
    root, rows = _publish(tmp_path)
    manifest_path = (root / "current" / "manifest.json").resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["journey_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    code, artifact = _execute(tmp_path, root=root, rows=rows)

    assert code == 1
    assert artifact["failure"]["code"] == "projection_integrity_mismatch"


def test_probe_rejects_old_lifecycle_inherited_by_expected_deployment(tmp_path):
    root, rows = _publish(tmp_path, sha="old-sha")
    unrelated = json.loads(json.dumps(lifecycle_rows()[2]))
    unrelated["ingested_seq"] = 9
    _publish(tmp_path, sha="new-sha", rows=[unrelated], source_high_watermark=9, generation=2)

    output = tmp_path / "old-lifecycle.json"
    code, artifact = asyncio.run(
        probe.execute(
            source=BaselineSource(9, rows),
            projection_root=root,
            expected_sha="new-sha",
            output=output,
            timeout_seconds=0,
            poll_seconds=0.001,
        )
    )

    assert code == 1
    assert artifact["failure"]["code"] == "no_complete_paper_aggregate"


def test_probe_rejects_out_of_sequence_lifecycle(tmp_path):
    root, rows = _publish(tmp_path)
    rows[1]["payload"]["metadata"]["sequence_no"] = 9

    code, artifact = _execute(tmp_path, root=root, rows=rows)

    assert code == 1
    assert artifact["failure"]["code"] == "no_complete_paper_aggregate"


@pytest.mark.parametrize("mutation", ["producer", "sequence", "causal_parent"])
def test_probe_rejects_noncanonical_lifecycle_provenance(tmp_path, mutation):
    root, rows = _publish(tmp_path)
    target = rows[2]["payload"]
    if mutation == "producer":
        target["correlation_envelope"]["producer"] = "manufactured.fixture"
    elif mutation == "sequence":
        target["sequence_no"] = 30
        target["metadata"]["sequence_no"] = 30
    else:
        target["causal_parent_id"] = "not-the-previous-event"
        target["metadata"]["causal_parent_id"] = "not-the-previous-event"
        target["correlation_envelope"]["causation_event_id"] = "not-the-previous-event"

    code, artifact = _execute(tmp_path, root=root, rows=rows)

    assert code == 1
    assert artifact["failure"]["code"] == "no_complete_paper_aggregate"


def test_source_failure_writes_only_redacted_evidence(tmp_path):
    output = tmp_path / "failure.json"

    code, artifact = asyncio.run(
        probe.execute(
            source=BrokenSource(),
            projection_root=tmp_path / "missing",
            expected_sha="deployed-sha",
            output=output,
            timeout_seconds=1,
            poll_seconds=0.001,
        )
    )

    raw = output.read_text(encoding="utf-8")
    assert code == 1
    assert artifact["failure"]["code"] == "unexpected_probe_error"
    assert "secret" not in raw
