from __future__ import annotations

import asyncio
import io
import ipaddress
import json
import os
from pathlib import Path
import sys
import types
from urllib.parse import urlsplit
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


from services.source_ingestion.requirement_state import (
    LatestMarketSnapshot,
    MarketSnapshotPoint,
    _checksum,
)

DEFAULT_TEST_CASE_KEY = "dev-paper-release-37647100516-1"
_CANONICAL_P1 = MarketSnapshotPoint(
    event_time="2026-07-14T00:00:00Z",
    close=49000.0,
    source_id="src-binance",
    connector_id="conn-binance-spot",
    content_ref="sha256:abcd",
    ingest_run_id="run-001",
)
_CANONICAL_P2 = MarketSnapshotPoint(
    event_time="2026-07-15T00:00:00Z",
    close=50000.0,
    source_id="src-binance",
    connector_id="conn-binance-spot",
    content_ref="sha256:abcd",
    ingest_run_id="run-001",
)
_CANONICAL_SNAPSHOT = LatestMarketSnapshot(
    symbol="BTC-USDT",
    points=(_CANONICAL_P1, _CANONICAL_P2),
    observed_at="2026-07-15T00:00:01Z",
)
DEFAULT_TEST_SNAPSHOT = _CANONICAL_SNAPSHOT.to_dict()
DEFAULT_TEST_SNAPSHOT["source_ref"] = f"source-ingest://snapshots/{_CANONICAL_SNAPSHOT.snapshot_id}"
DEFAULT_TEST_SNAPSHOT["checksum"] = _checksum(_CANONICAL_SNAPSHOT.to_dict())
DEFAULT_TEST_SNAPSHOT["data_checksum"] = DEFAULT_TEST_SNAPSHOT["checksum"]
DEFAULT_TEST_CASE = {
    "tenant_id": "tenant-a",
    "persona_id": "persona-paper-001",
    "idempotency_key": DEFAULT_TEST_CASE_KEY,
    "runtime_binding_id": "10000000-0000-0000-0000-000000000001",
    "runtime_id": "runtime-paper-001",
    "capital_pool_id": "pool-paper-001",
    "deployment_plan_id": "plan-paper-001",
    "persona_capital_binding_id": "pcb-paper-001",
    "artifact_id": "artifact-paper-001",
    "artifact_version": "1.2.3",
    "artifact_checksum": "sha256-approved-test-checksum",
    "artifact_state": "approved",
    "state": "succeeded",
    "source_snapshot": DEFAULT_TEST_SNAPSHOT,
}


class InMemoryCaseSource:
    def __init__(self, cases: Mapping[str, Any], snapshots: Mapping[str, Any] | None = None):
        self._cases = dict(cases)
        self._snapshots = dict(snapshots or {})

    async def get_case(self, case_key: str) -> dict[str, Any] | None:
        return self._cases.get(case_key)

    async def get_source_snapshot(
        self, snapshot_id: str, tenant_id: str, symbol: str | None = None
    ) -> dict[str, Any] | None:
        return self._snapshots.get(snapshot_id)

    async def verify_source_lineage(self, lineage: Mapping[str, Any], tenant_id: str) -> None:
        pass

    def __getitem__(self, key: str) -> Any:
        return self._cases[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._cases.get(key, default)


DEFAULT_CASE_SOURCE = InMemoryCaseSource(
    {DEFAULT_TEST_CASE_KEY: DEFAULT_TEST_CASE},
    {DEFAULT_TEST_SNAPSHOT["snapshot_id"]: DEFAULT_TEST_SNAPSHOT},
)


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
        if event_type == "signal_generation":
            event["source_worker"] = probe.NATURAL_PRODUCER
            event["artifact_interpreter"] = probe.NATURAL_INTERPRETER
            event["artifact_id"] = DEFAULT_TEST_CASE["artifact_id"]
            event["artifact_version"] = DEFAULT_TEST_CASE["artifact_version"]
            event["artifact_checksum"] = DEFAULT_TEST_CASE["artifact_checksum"]
            event["binding_id"] = DEFAULT_TEST_CASE["runtime_binding_id"]
            event["runtime_id"] = DEFAULT_TEST_CASE["runtime_id"]
            event["capital_pool_id"] = DEFAULT_TEST_CASE["capital_pool_id"]
            event["plan_id"] = DEFAULT_TEST_CASE["deployment_plan_id"]
            event["persona_capital_binding_id"] = DEFAULT_TEST_CASE["persona_capital_binding_id"]
            event["raw_symbol"] = "BTC-USDT"
            event["market_input_ref"] = DEFAULT_TEST_SNAPSHOT["source_ref"]
            event["market_input_snapshot_id"] = DEFAULT_TEST_SNAPSHOT["snapshot_id"]
            event["market_input_observed_at"] = DEFAULT_TEST_SNAPSHOT["observed_at"]
            event["market_input_event_time"] = DEFAULT_TEST_SNAPSHOT["event_time"]
            event["market_input_lineage"] = DEFAULT_TEST_SNAPSHOT["lineage"]
            event["market_input_checksum"] = DEFAULT_TEST_SNAPSHOT["checksum"]
            event["is_real_capital"] = False
            event["is_real_order"] = False
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
    mode: str = "natural",
    case_key: str | None = DEFAULT_TEST_CASE_KEY,
    case_source: Any | None = DEFAULT_CASE_SOURCE,
) -> tuple[int, dict]:
    return asyncio.run(
        probe.execute(
            source=FakeSource(len(rows), rows),
            projection_root=root,
            case_source=case_source,
            expected_sha=expected_sha,
            output=tmp_path / "evidence.json",
            timeout_seconds=0.1,
            poll_seconds=0.001,
            mode=mode,
            case_key=case_key,
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
            case_source=DEFAULT_CASE_SOURCE,
            expected_sha="deployed-sha",
            output=tmp_path / "relational-evidence.json",
            timeout_seconds=0.1,
            poll_seconds=0.001,
            baseline_high_watermark=0,
            case_key=DEFAULT_TEST_CASE_KEY,
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
            case_source=DEFAULT_CASE_SOURCE,
            expected_sha="deployed-sha",
            output=tmp_path / "retry-integrity.json",
            timeout_seconds=1,
            poll_seconds=0.001,
            baseline_high_watermark=0,
            sleeper=repair_manifest,
            case_key=DEFAULT_TEST_CASE_KEY,
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
            case_source=DEFAULT_CASE_SOURCE,
            expected_sha="deployed-sha",
            output=tmp_path / "incremental.json",
            timeout_seconds=0.1,
            poll_seconds=0.001,
            case_key=DEFAULT_TEST_CASE_KEY,
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
            case_source=DEFAULT_CASE_SOURCE,
            expected_sha="deployed-sha",
            output=tmp_path / "explicit-baseline.json",
            timeout_seconds=0.1,
            poll_seconds=0.001,
            baseline_high_watermark=200,
            case_key=DEFAULT_TEST_CASE_KEY,
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
            case_source=DEFAULT_CASE_SOURCE,
            expected_sha="deployed-sha",
            output=output,
            timeout_seconds=0,
            poll_seconds=0.01,
            case_key=DEFAULT_TEST_CASE_KEY,
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
            case_source=DEFAULT_CASE_SOURCE,
            expected_sha="new-sha",
            output=output,
            timeout_seconds=0,
            poll_seconds=0.001,
            case_key=DEFAULT_TEST_CASE_KEY,
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
            case_source=DEFAULT_CASE_SOURCE,
            expected_sha="deployed-sha",
            output=output,
            timeout_seconds=1,
            poll_seconds=0.001,
            case_key=DEFAULT_TEST_CASE_KEY,
        )
    )

    raw = output.read_text(encoding="utf-8")
    assert code == 1
    assert artifact["failure"]["code"] == "unexpected_probe_error"
    assert "secret" not in raw


def _counterexample_rows() -> list[dict]:
    rows = _natural_lifecycle_rows()
    sig = rows[0]["payload"]
    sig["source_worker"] = "loop-prod-tel-002-hosted-stimulus"
    sig["artifact_interpreter"] = None
    sig["artifact_checksum"] = None
    sig["market_input_ref"] = None
    sig["raw_symbol"] = None
    sig["metadata"].pop("source_worker", None)
    sig["metadata"].pop("artifact_interpreter", None)
    sig["metadata"].pop("artifact_checksum", None)
    sig["metadata"].pop("market_input_ref", None)
    sig["metadata"].pop("raw_symbol", None)
    return rows


def test_counterexample_rejected_in_natural_mode(tmp_path):
    root, rows = _publish(tmp_path, rows=_counterexample_rows())
    code, artifact = _execute(tmp_path, root=root, rows=rows, mode="natural")
    assert code == 1
    assert artifact["outcome"] == "failed"
    assert artifact["mode"] == "natural"
    assert artifact["governed_case_key"] == DEFAULT_TEST_CASE_KEY
    assert artifact["failure"]["code"] == "no_complete_paper_aggregate"


def test_counterexample_accepted_in_controlled_stimulus_mode(tmp_path):
    root, rows = _publish(tmp_path, rows=_counterexample_rows())
    code, artifact = _execute(tmp_path, root=root, rows=rows, mode="controlled-stimulus", case_key=None, case_source=None)
    assert code == 0
    assert artifact["outcome"] == "passed"
    assert artifact["mode"] == "controlled-stimulus"
    assert "signal_provenance" in artifact["proof"]
    assert artifact["proof"]["signal_provenance"]["source_worker"] == "loop-prod-tel-002-hosted-stimulus"


@pytest.mark.parametrize(
    "mutation,field,value,expected_error",
    [
        ("producer", "source_worker", "unauthorized-worker", "invalid_producer"),
        ("interpreter", "artifact_interpreter", "unauthorized.func", "invalid_interpreter"),
        ("checksum_none", "artifact_checksum", None, "invalid_checksum"),
        ("checksum_empty", "artifact_checksum", "   ", "invalid_checksum"),
        ("lineage_missing", "market_input_ref", None, "invalid_lineage"),
        ("real_capital", "is_real_capital", True, "invalid_capital_mode"),
        ("real_order", "is_real_order", True, "invalid_capital_mode"),
    ],
)
def test_probe_natural_candidate_validation_rejections(mutation, field, value, expected_error):
    rows = _natural_lifecycle_rows()
    sig = rows[0]["payload"]
    if mutation == "lineage_missing":
        sig["market_input_ref"] = None
    else:
        sig[field] = value
    cands = probe._complete_candidates(rows, mode="controlled-stimulus")
    assert len(cands) == 1
    with pytest.raises(probe.ProbeError) as exc_info:
        probe._validate_natural_candidate(cands[0], DEFAULT_TEST_CASE)
    assert exc_info.value.code == expected_error


@pytest.mark.parametrize(
    "case_key,case_value,expected_error",
    [
        ("tenant_id", "wrong-tenant", "case_identity_mismatch"),
        ("tenant_id", None, "case_identity_mismatch"),
        ("runtime_binding_id", "wrong-binding", "case_identity_mismatch"),
        ("runtime_binding_id", None, "case_identity_mismatch"),
        ("runtime_id", "wrong-runtime", "case_identity_mismatch"),
        ("runtime_id", None, "case_identity_mismatch"),
        ("deployment_plan_id", "wrong-plan", "case_plan_mismatch"),
        ("deployment_plan_id", None, "case_plan_mismatch"),
        ("capital_pool_id", "wrong-pool", "case_capital_mismatch"),
        ("capital_pool_id", None, "case_capital_mismatch"),
        ("artifact_id", "wrong-artifact", "case_artifact_mismatch"),
        ("artifact_id", None, "case_artifact_mismatch"),
        ("artifact_version", "9.9.9", "case_version_mismatch"),
        ("artifact_version", None, "case_version_mismatch"),
        ("artifact_checksum", "sha256-wrong-checksum", "case_checksum_mismatch"),
        ("artifact_checksum", None, "case_checksum_mismatch"),
        ("state", "failed", "case_state_mismatch"),
        ("state", None, "case_state_mismatch"),
    ],
)
def test_probe_natural_case_mismatch_rejections(case_key, case_value, expected_error):
    rows = _natural_lifecycle_rows()
    cands = probe._complete_candidates(rows, mode="controlled-stimulus")
    assert len(cands) == 1
    mismatched_case = dict(DEFAULT_TEST_CASE, **{case_key: case_value})
    with pytest.raises(probe.ProbeError) as exc_info:
        probe._validate_natural_candidate(cands[0], mismatched_case)
    assert exc_info.value.code == expected_error


def test_probe_natural_mode_missing_case_key(tmp_path):
    root, rows = _publish(tmp_path)
    code, artifact = _execute(tmp_path, root=root, rows=rows, mode="natural", case_key=None)
    assert code == 1
    assert artifact["outcome"] == "failed"
    assert artifact["failure"]["code"] == "case_key_missing"


def test_probe_natural_mode_missing_case_source(tmp_path):
    root, rows = _publish(tmp_path)
    code, artifact = _execute(tmp_path, root=root, rows=rows, mode="natural", case_source=None)
    assert code == 1
    assert artifact["outcome"] == "failed"
    assert artifact["failure"]["code"] == "case_source_missing"


def test_probe_natural_mode_case_not_found(tmp_path):
    root, rows = _publish(tmp_path)
    code, artifact = _execute(
        tmp_path, root=root, rows=rows, mode="natural", case_key="nonexistent-key", case_source=DEFAULT_CASE_SOURCE
    )
    assert code == 1
    assert artifact["outcome"] == "failed"
    assert artifact["failure"]["code"] == "case_not_found"


def test_normalize_case_record_from_provisioning_ledger():
    raw_row = {
        "tenant_id": "tenant-dev",
        "persona_id": "persona-dev-001",
        "idempotency_key": "dev-paper-release-1-1",
        "references": {
            "runtime_binding_id": "binding-rel-1",
            "runtime_id": "runtime-rel-1",
            "strategy_artifact_approved": {
                "entry": {
                    "registry_id": "art-1",
                    "version": "2.0.0",
                    "checksum": "sha256-approved",
                }
            },
        },
        "result": {
            "capital_pool_id": "pool-dev-1",
            "deployment_plan_id": "plan-dev-1",
            "persona_capital_binding_id": "pcb-dev-1",
        },
    }
    normalized = probe._normalize_case_record(raw_row)
    assert normalized["tenant_id"] == "tenant-dev"
    assert normalized["persona_id"] == "persona-dev-001"
    assert normalized["idempotency_key"] == "dev-paper-release-1-1"
    assert normalized["runtime_binding_id"] == "binding-rel-1"
    assert normalized["runtime_id"] == "runtime-rel-1"
    assert normalized["capital_pool_id"] == "pool-dev-1"
    assert normalized["deployment_plan_id"] == "plan-dev-1"
    assert normalized["persona_capital_binding_id"] == "pcb-dev-1"
    assert normalized["artifact_id"] == "art-1"
    assert normalized["artifact_version"] == "2.0.0"
    assert normalized["artifact_checksum"] == "sha256-approved"


def test_main_cli_mode_and_case_key_validation(tmp_path):
    out_file = tmp_path / "cli_test.json"
    ret = probe.main(["--expected-sha", "test-sha", "--output", str(out_file), "--mode", "natural"])
    assert ret == 1
    art = json.loads(out_file.read_text(encoding="utf-8"))
    assert art["outcome"] == "failed"
    assert art["mode"] == "natural"
    assert art["failure"]["code"] == "case_key_missing"


def test_normalize_case_record_does_not_invent_ids():
    raw_row = {
        "tenant_id": "tenant-dev",
        "persona_id": "persona-dev-001",
        "idempotency_key": "dev-paper-release-1-1",
        "references": {},
        "result": {},
    }
    normalized = probe._normalize_case_record(raw_row)
    assert normalized["capital_pool_id"] == ""
    assert normalized["deployment_plan_id"] == ""
    assert normalized["persona_capital_binding_id"] == ""
    assert normalized["artifact_id"] == ""
    assert normalized["artifact_version"] == ""
    assert normalized["artifact_checksum"] is None


def test_asyncpg_case_source_anchors_checksum_from_registry(monkeypatch):
    class Connection:
        async def fetchrow(self, query: str, *args):
            if "persona_provisioning" in query:
                return {
                    "tenant_id": "tenant-a",
                    "persona_id": "persona-1",
                    "idempotency_key": "key-1",
                    "state": "succeeded",
                    "references": {
                        "runtime_binding_id": "rb-1",
                        "runtime_id": "rt-1",
                        "strategy_artifact_approved": {
                            "entry": {"registry_id": "art-1", "version": "1.2.3"}
                        },
                    },
                    "result": {
                        "capital_pool_id": "pool-1",
                        "deployment_plan_id": "plan-1",
                        "persona_capital_binding_id": "pcb-1",
                    },
                }
            if "entries" in query:
                return {
                    "payload": json.dumps({
                        "checksum": "sha256-from-registry",
                        "version": "1.2.3",
                        "artifact_state": "approved",
                        "owner_tenant": "tenant-a",
                    })
                }
            return None

        async def close(self):
            pass

    class TransactionContext:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    conn = Connection()
    conn.transaction = lambda **kwargs: TransactionContext(conn)

    monkeypatch.setitem(
        sys.modules,
        "asyncpg",
        types.SimpleNamespace(connect=lambda dsn: asyncio.sleep(0, result=conn)),
    )
    src = probe.AsyncpgCaseSource("postgresql://unit")
    case = asyncio.run(src.get_case("key-1"))
    assert case is not None
    assert case["artifact_checksum"] == "sha256-from-registry"
    assert case["artifact_version"] == "1.2.3"


@pytest.mark.parametrize(
    "registry_row,expected_code",
    [
        (None, "registry_artifact_missing"),
        ({"payload": json.dumps({"artifact_state": "draft", "owner_tenant": "tenant-a", "checksum": "cs", "version": "1"})}, "registry_artifact_not_approved"),
        ({"payload": json.dumps({"artifact_state": "approved", "owner_tenant": "other-tenant", "checksum": "cs", "version": "1"})}, "registry_tenant_mismatch"),
        ({"payload": json.dumps({"artifact_state": "approved", "owner_tenant": "", "checksum": "cs", "version": "1"})}, "registry_tenant_mismatch"),
        ({"payload": json.dumps({"artifact_state": "approved", "checksum": "cs", "version": "1"})}, "registry_tenant_mismatch"),
        ({"payload": json.dumps({"artifact_state": "approved", "owner_tenant": "tenant-a", "checksum": "", "version": "1"})}, "registry_checksum_missing"),
        ({"payload": json.dumps({"artifact_state": "approved", "owner_tenant": "tenant-a", "checksum": "different-cs", "version": "1"})}, "case_checksum_mismatch"),
        ({"payload": json.dumps({"artifact_state": "approved", "owner_tenant": "tenant-a", "checksum": "sha256-from-case", "version": ""})}, "registry_version_missing"),
        ({"payload": json.dumps({"artifact_state": "approved", "owner_tenant": "tenant-a", "checksum": "sha256-from-case", "version": "9.9.9"})}, "case_version_mismatch"),
    ],
)
def test_asyncpg_case_source_registry_adversarial_rejections(monkeypatch, registry_row, expected_code):
    class Connection:
        async def fetchrow(self, query: str, *args):
            if "persona_provisioning" in query:
                return {
                    "tenant_id": "tenant-a",
                    "persona_id": "persona-1",
                    "idempotency_key": "key-1",
                    "state": "succeeded",
                    "references": {
                        "runtime_binding_id": "rb-1",
                        "runtime_id": "rt-1",
                        "strategy_artifact_approved": {
                            "entry": {"registry_id": "art-1", "version": "1.2.3", "checksum": "sha256-from-case"}
                        },
                    },
                    "result": {"capital_pool_id": "pool-1", "deployment_plan_id": "plan-1", "persona_capital_binding_id": "pcb-1"},
                }
            if "entries" in query:
                return registry_row
            return None

        async def close(self):
            pass

    class TransactionContext:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    conn = Connection()
    conn.transaction = lambda **kwargs: TransactionContext(conn)
    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=lambda dsn: asyncio.sleep(0, result=conn)))
    src = probe.AsyncpgCaseSource("postgresql://unit")
    with pytest.raises(probe.ProbeError) as exc_info:
        asyncio.run(src.get_case("key-1"))
    assert exc_info.value.code == expected_code


@pytest.mark.parametrize(
    "mutator,expected_code",
    [
        (lambda p: p.pop("source_worker"), "invalid_producer"),
        (lambda p: p.update(source_worker="wrong-worker"), "invalid_producer"),
        (lambda p: p.pop("artifact_interpreter"), "invalid_interpreter"),
        (lambda p: p.update(artifact_interpreter="wrong.interpreter:fn"), "invalid_interpreter"),
        (lambda p: p.pop("artifact_checksum"), "invalid_checksum"),
        (lambda p: p.update(artifact_checksum=""), "invalid_checksum"),
        (lambda p: p.pop("artifact_version"), "invalid_version"),
        (lambda p: p.update(artifact_version=""), "invalid_version"),
        (lambda p: p.update(is_real_capital=True), "invalid_capital_mode"),
        (lambda p: p.update(is_real_order=True), "invalid_capital_mode"),
        (lambda p: p.pop("market_input_ref"), "invalid_lineage"),
        (lambda p: p.update(market_input_ref="invalid-ref-format"), "invalid_lineage"),
        (lambda p: p.pop("market_input_snapshot_id"), "invalid_snapshot_id"),
        (lambda p: p.update(market_input_snapshot_id="mss-different-snapshot"), "invalid_snapshot_id"),
        (lambda p: p.pop("market_input_observed_at"), "invalid_lineage"),
        (lambda p: p.pop("market_input_event_time"), "invalid_lineage"),
        (lambda p: p.update(market_input_observed_at="bad-iso"), "invalid_lineage"),
        (lambda p: p.update(market_input_event_time="2026-07-15T00:00:10Z", market_input_observed_at="2026-07-15T00:00:01Z"), "invalid_freshness"),
        (lambda p: p.update(market_input_observed_at="2026-07-15T00:00:10Z", signal_event_time="2026-07-15T00:00:01Z"), "invalid_freshness"),
        (lambda p: p.update(market_input_observed_at="2000-01-01T00:00:00Z", market_input_event_time="2000-01-01T00:00:00Z", signal_event_time="2000-01-01T00:00:01Z"), "invalid_freshness"),
        (lambda p: p.update(market_input_observed_at="2400-01-01T00:00:00Z", market_input_event_time="2400-01-01T00:00:00Z", signal_event_time="2400-01-01T00:00:01Z"), "invalid_freshness"),
        (lambda p: p.update(market_input_observed_at="2026-07-15T00:00:00Z", market_input_event_time="2026-07-15T00:00:00Z", signal_event_time="2026-07-15T00:10:00Z"), "invalid_freshness"),
        (lambda p: p.update(raw_symbol="2330.TW", market_input_lineage={"source_ids": ["s"], "connector_ids": ["c"], "content_refs": ["r"], "ingest_run_ids": ["i"]}), "invalid_freshness"),
        (lambda p: p.pop("market_input_lineage"), "invalid_lineage"),
        (lambda p: p.update(market_input_lineage={"source_ids": []}), "invalid_lineage"),
        (lambda p: p.update(market_input_lineage={"source_ids": ["s"], "connector_ids": []}), "invalid_lineage"),
    ],
)
def test_validate_natural_candidate_provenance_rejections(mutator, expected_code):
    rows = _natural_lifecycle_rows()
    cands = probe._complete_candidates(rows, mode="controlled-stimulus")
    cand = json.loads(json.dumps(cands[0]))
    mutator(cand["signal_provenance"])
    with pytest.raises(probe.ProbeError) as exc_info:
        probe._validate_natural_candidate(cand, DEFAULT_TEST_CASE)
    assert exc_info.value.code == expected_code


def test_validate_natural_candidate_rejects_non_approved_case():
    rows = _natural_lifecycle_rows()
    cands = probe._complete_candidates(rows, mode="controlled-stimulus")
    case = dict(DEFAULT_TEST_CASE, artifact_state="draft")
    with pytest.raises(probe.ProbeError) as exc_info:
        probe._validate_natural_candidate(cands[0], case)
    assert exc_info.value.code == "artifact_not_approved"


def test_asyncpg_case_source_verify_source_lineage_unit_rejections(monkeypatch):
    class Connection:
        def __init__(self, record_payload=None):
            self.payload = record_payload or {
                "source_id": "src-1",
                "connector_id": "conn-1",
                "content_ref": "ref-1",
                "metadata": {
                    "tenant_id": "tenant-a",
                    "source_ingest_run_id": "run-1",
                    "content_hash": "sha256-test-checksum",
                },
            }

        async def fetch(self, query: str, *args):
            return [{"record_id": "src-1", "payload": json.dumps(self.payload)}]

        async def close(self):
            pass

    class TransactionContext:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def make_src(payload=None):
        conn = Connection(payload)
        conn.transaction = lambda **kwargs: TransactionContext(conn)
        monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=lambda dsn: asyncio.sleep(0, result=conn)))
        return probe.AsyncpgCaseSource("postgresql://unit")

    good_lineage = {
        "source_ids": ["src-1"],
        "connector_ids": ["conn-1"],
        "content_refs": ["ref-1"],
        "ingest_run_ids": ["run-1"],
        "checksums": ["sha256-test-checksum"],
    }
    src = make_src()
    asyncio.run(src.verify_source_lineage(good_lineage, "tenant-a"))

    bad_source = dict(good_lineage, source_ids=["src-unobserved"])
    with pytest.raises(probe.ProbeError) as exc_info:
        asyncio.run(src.verify_source_lineage(bad_source, "tenant-a"))
    assert exc_info.value.code == "unobserved_source_record"

    with pytest.raises(probe.ProbeError) as exc_info:
        asyncio.run(src.verify_source_lineage(good_lineage, "tenant-b"))
    assert exc_info.value.code == "source_tenant_mismatch"

    bad_conn = dict(good_lineage, connector_ids=["conn-other"])
    with pytest.raises(probe.ProbeError) as exc_info:
        asyncio.run(src.verify_source_lineage(bad_conn, "tenant-a"))
    assert exc_info.value.code == "source_connector_mismatch"

    bad_ref = dict(good_lineage, content_refs=["ref-other"])
    with pytest.raises(probe.ProbeError) as exc_info:
        asyncio.run(src.verify_source_lineage(bad_ref, "tenant-a"))
    assert exc_info.value.code == "source_content_ref_mismatch"

    bad_run = dict(good_lineage, ingest_run_ids=["run-other"])
    with pytest.raises(probe.ProbeError) as exc_info:
        asyncio.run(src.verify_source_lineage(bad_run, "tenant-a"))
    assert exc_info.value.code == "source_ingest_run_mismatch"

    bad_chk = dict(good_lineage, checksums=["sha256-other"])
    with pytest.raises(probe.ProbeError) as exc_info:
        asyncio.run(src.verify_source_lineage(bad_chk, "tenant-a"))
    assert exc_info.value.code == "source_checksum_mismatch"

    missing_tenant_src = make_src({"source_id": "src-1", "connector_id": "conn-1", "content_ref": "ref-1", "metadata": {"source_ingest_run_id": "run-1", "content_hash": "chk-1"}})
    with pytest.raises(probe.ProbeError) as exc_info:
        asyncio.run(missing_tenant_src.verify_source_lineage(good_lineage, "tenant-a"))
    assert exc_info.value.code == "source_tenant_missing"

    missing_conn_src = make_src({"source_id": "src-1", "content_ref": "ref-1", "metadata": {"tenant_id": "tenant-a", "source_ingest_run_id": "run-1", "content_hash": "chk-1"}})
    with pytest.raises(probe.ProbeError) as exc_info:
        asyncio.run(missing_conn_src.verify_source_lineage(good_lineage, "tenant-a"))
    assert exc_info.value.code == "source_connector_missing"

    missing_ref_src = make_src({"source_id": "src-1", "connector_id": "conn-1", "metadata": {"tenant_id": "tenant-a", "source_ingest_run_id": "run-1", "content_hash": "chk-1"}})
    with pytest.raises(probe.ProbeError) as exc_info:
        asyncio.run(missing_ref_src.verify_source_lineage(good_lineage, "tenant-a"))
    assert exc_info.value.code == "source_content_ref_missing"

    missing_run_src = make_src({"source_id": "src-1", "connector_id": "conn-1", "content_ref": "ref-1", "metadata": {"tenant_id": "tenant-a", "content_hash": "chk-1"}})
    with pytest.raises(probe.ProbeError) as exc_info:
        asyncio.run(missing_run_src.verify_source_lineage(good_lineage, "tenant-a"))
    assert exc_info.value.code == "source_ingest_run_missing"

    missing_chk_src = make_src({"source_id": "src-1", "connector_id": "conn-1", "content_ref": "ref-1", "metadata": {"tenant_id": "tenant-a", "source_ingest_run_id": "run-1"}})
    with pytest.raises(probe.ProbeError) as exc_info:
        asyncio.run(missing_chk_src.verify_source_lineage(good_lineage, "tenant-a"))
    assert exc_info.value.code == "source_checksum_missing"


LOOPBACK_HOSTS = frozenset({"localhost", "localhost.localdomain", "127.0.0.1", "::1"})


def _is_loopback_host(host: str) -> bool:
    normalized = (host or "").strip().lower()
    if normalized in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _validate_owned_loopback_pg_dsn(dsn: str) -> str:
    """Validate that DSN targets an owned disposable loopback PostgreSQL instance.

    Rejects non-loopback hosts, unknown ownership, query injection options,
    and non-postgresql schemes without leaking credentials or raw DSN in exceptions.
    """
    if not dsn or not dsn.strip():
        raise ValueError("Test database DSN is empty")
    try:
        parsed = urlsplit(dsn.strip())
    except Exception:
        raise ValueError("Invalid test database DSN") from None

    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("Test database must use postgresql scheme")
    if parsed.query or parsed.fragment:
        raise ValueError("Test database DSN must not contain query parameters or fragments")

    host = (parsed.hostname or "").strip().lower()
    if not host or not _is_loopback_host(host):
        raise ValueError("Test database must target an owned loopback address")

    # Guard against accidental use of the shared docker-compose dev container (port 15432)
    # without explicit owned disposable declaration.
    if parsed.port == 15432:
        owned_flag = os.getenv("PROBE_TEST_POSTGRES_OWNED") or os.getenv("PANTHEON_TEST_POSTGRES_OWNED")
        if not (owned_flag and owned_flag.strip().lower() in {"1", "true", "yes", "disposable"}):
            raise ValueError(
                "Port 15432 is reserved for shared development container; explicit owned disposable opt-in required"
            )

    return dsn.strip()


def test_owned_loopback_dsn_guard_rejections(monkeypatch):
    """Fail-closed on non-loopback hosts, non-postgres schemes, query params, and DSN leakage."""
    monkeypatch.delenv("PROBE_TEST_POSTGRES_OWNED", raising=False)
    monkeypatch.delenv("PANTHEON_TEST_POSTGRES_OWNED", raising=False)
    for bad_dsn in (
        "postgresql://user:pass@192.168.1.10:5432/testdb",
        "postgresql://user:pass@example.com:5432/testdb",
        "postgresql://user:pass@10.0.0.1:5432/testdb",
        "postgresql://user:pass@8.8.8.8:5432/testdb",
    ):
        with pytest.raises(ValueError) as exc_info:
            _validate_owned_loopback_pg_dsn(bad_dsn)
        msg = str(exc_info.value)
        assert "owned loopback address" in msg
        assert "user:pass" not in msg
        assert "192.168.1.10" not in msg
        assert "example.com" not in msg

    with pytest.raises(ValueError) as exc_info:
        _validate_owned_loopback_pg_dsn("mysql://root:secret@127.0.0.1:3306/db")
    assert "postgresql scheme" in str(exc_info.value)
    assert "root:secret" not in str(exc_info.value)

    with pytest.raises(ValueError) as exc_info:
        _validate_owned_loopback_pg_dsn("postgresql://postgres:secret@127.0.0.1:5432/db?sslmode=disable")
    assert "query parameters" in str(exc_info.value)
    assert "secret" not in str(exc_info.value)

    with pytest.raises(ValueError) as exc_info:
        _validate_owned_loopback_pg_dsn("postgresql://postgres:secret@127.0.0.1:15432/postgres")
    assert "shared development container" in str(exc_info.value)
    assert "secret" not in str(exc_info.value)


def test_owned_loopback_dsn_guard_accepts_valid_loopback(monkeypatch):
    """Accepts valid loopback endpoints with clean DSN."""
    valid = "postgresql://postgres:pass@127.0.0.1:32845/postgres"
    assert _validate_owned_loopback_pg_dsn(valid) == valid

    valid_v6 = "postgresql://postgres:pass@[::1]:32845/postgres"
    assert _validate_owned_loopback_pg_dsn(valid_v6) == valid_v6

    monkeypatch.setenv("PROBE_TEST_POSTGRES_OWNED", "true")
    p15432 = "postgresql://postgres:pass@localhost:15432/postgres"
    assert _validate_owned_loopback_pg_dsn(p15432) == p15432


def test_asyncpg_case_source_live_postgres_queries(tmp_path):
    import asyncpg

    raw_dsn = (
        os.getenv("PROBE_TEST_POSTGRES_DSN")
        or os.getenv("PANTHEON_TEST_POSTGRES_DSN")
        or ""
    ).strip()
    if not raw_dsn:
        pytest.skip(
            "Explicit owned loopback test PostgreSQL DSN is not configured "
            "(opt-in via PROBE_TEST_POSTGRES_DSN or PANTHEON_TEST_POSTGRES_DSN)"
        )

    dsn = _validate_owned_loopback_pg_dsn(raw_dsn)
    run_id = uuid.uuid4().hex[:12]
    schema = f"probe_bff_scratch_{run_id}"
    reg_schema = f"probe_reg_scratch_{run_id}"
    source_schema = f"probe_src_scratch_{run_id}"

    snap_file = tmp_path / "latest_market_snapshots.jsonl"
    snap_store = probe.LatestMarketSnapshotStore(snap_file)
    rec = types.SimpleNamespace(
        metadata={
            "symbol_canonical": "BTC-USDT",
            "close": 50000.0,
            "event_time": "2026-07-15T00:00:00Z",
        },
        source_id="src-live-01",
        connector_id="conn-live-01",
        content_ref="ref-live-01",
    )
    snap_store.append_normalized_records([rec], ingest_run_id="run-01", observed_at="2026-07-15T00:00:01Z")
    expected_snap = snap_store.get("BTC-USDT")
    assert expected_snap is not None

    async def run_live():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(f'CREATE SCHEMA "{schema}"')
            await conn.execute(f'CREATE SCHEMA "{reg_schema}"')
            await conn.execute(f'CREATE SCHEMA "{source_schema}"')

            await conn.execute(f"""
                CREATE TABLE {schema}.persona_provisioning (
                    idempotency_key TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    persona_id TEXT NOT NULL,
                    "references" JSONB NOT NULL,
                    result JSONB NOT NULL,
                    state TEXT NOT NULL
                )
            """)
            await conn.execute(f"""
                CREATE TABLE {reg_schema}.entries (
                    record_id TEXT PRIMARY KEY,
                    payload JSONB NOT NULL
                )
            """)
            await conn.execute(f"""
                CREATE TABLE {source_schema}.source_evidence (
                    append_id BIGSERIAL PRIMARY KEY,
                    record_id TEXT NOT NULL,
                    record_type TEXT NOT NULL,
                    payload JSONB NOT NULL,
                    UNIQUE (record_type, record_id)
                )
            """)

            await conn.execute(f"""
                INSERT INTO {schema}.persona_provisioning (idempotency_key, tenant_id, persona_id, "references", result, state)
                VALUES ('case-live-01', 'tenant-live', 'persona-01', '{{"runtime_binding_id": "rb-1", "runtime_id": "rt-1", "strategy_artifact_approved": {{"entry": {{"registry_id": "art-live-01", "version": "1.0.0"}}}}}}'::jsonb, '{{"capital_pool_id": "pool-1", "deployment_plan_id": "plan-1", "persona_capital_binding_id": "pcb-1"}}'::jsonb, 'succeeded')
            """)

            await conn.execute(f"""
                INSERT INTO {reg_schema}.entries (record_id, payload)
                VALUES ('art-live-01', '{{"checksum": "sha256-live-checksum", "version": "1.0.0", "artifact_state": "approved", "owner_tenant": "tenant-live"}}'::jsonb)
            """)

            await conn.execute(f"""
                INSERT INTO {source_schema}.source_evidence (record_id, record_type, payload)
                VALUES ('@t11:tenant-live:src-live-01', 'source_record', '{{"source_id": "src-live-01", "connector_id": "conn-live-01", "content_ref": "ref-live-01", "metadata": {{"tenant_id": "tenant-live", "source_ingest_run_id": "run-01", "content_hash": "sha256-live-checksum"}}}}'::jsonb)
            """)

            await conn.execute(f"""
                INSERT INTO {source_schema}.source_evidence (record_id, record_type, payload)
                VALUES ('@t12:tenant-rogue:src-live-01', 'source_record', '{{"source_id": "src-live-01", "connector_id": "conn-live-01", "content_ref": "ref-live-01", "metadata": {{"tenant_id": "tenant-rogue", "source_ingest_run_id": "run-01", "content_hash": "sha256-live-checksum"}}}}'::jsonb)
            """)

            await conn.execute(f"""
                INSERT INTO {source_schema}.source_evidence (record_id, record_type, payload)
                VALUES ('@t13:tenant-live:src-malformed', 'source_record', '{{"source_id": "src-malformed", "connector_id": "conn-live-01", "content_ref": "ref-live-01", "metadata": "corrupted_non_mapping"}}'::jsonb)
            """)

            src = probe.AsyncpgCaseSource(
                dsn,
                schema=schema,
                registry_schema=reg_schema,
                source_evidence_schema=source_schema,
                snapshot_store=snap_store,
            )

            case = await src.get_case("case-live-01")
            assert case is not None
            assert case["tenant_id"] == "tenant-live"
            assert case["artifact_checksum"] == "sha256-live-checksum"
            assert case["artifact_version"] == "1.0.0"
            assert case["artifact_state"] == "approved"

            snap = await src.get_source_snapshot(expected_snap.snapshot_id, "tenant-live", symbol="BTC-USDT")
            assert snap is not None
            assert snap["snapshot_id"] == expected_snap.snapshot_id
            assert snap["checksum"] is not None

            missing_snap = await src.get_source_snapshot("mss-unknown", "tenant-live", symbol="BTC-USDT")
            assert missing_snap is None

            lineage = {
                "source_ids": ["src-live-01"],
                "connector_ids": ["conn-live-01"],
                "content_refs": ["ref-live-01"],
                "ingest_run_ids": ["run-01"],
                "checksums": ["sha256-live-checksum"],
            }
            await src.verify_source_lineage(lineage, "tenant-live")

            bad_lineage = dict(lineage, source_ids=["src-nonexistent"])
            with pytest.raises(probe.ProbeError) as exc_info:
                await src.verify_source_lineage(bad_lineage, "tenant-live")
            assert exc_info.value.code == "unobserved_source_record"

            with pytest.raises(probe.ProbeError) as exc_info:
                await src.verify_source_lineage(lineage, "wrong-tenant")
            assert exc_info.value.code in {"source_tenant_mismatch", "unobserved_source_record"}

            malformed_lineage = dict(lineage, source_ids=["src-malformed"])
            with pytest.raises(probe.ProbeError) as exc_info:
                await src.verify_source_lineage(malformed_lineage, "tenant-live")
            assert exc_info.value.code in {"source_tenant_missing", "unobserved_source_record"}
        finally:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            await conn.execute(f'DROP SCHEMA IF EXISTS "{reg_schema}" CASCADE')
            await conn.execute(f'DROP SCHEMA IF EXISTS "{source_schema}" CASCADE')
            await conn.close()

    asyncio.run(run_live())


def test_adversarial_qa_future_2029_candidate_strictly_rejected():
    candidate = probe._complete_candidates(
        _natural_lifecycle_rows(), mode="natural", case=DEFAULT_TEST_CASE
    )[0]
    future = json.loads(json.dumps(candidate))
    future["signal_provenance"].update(
        market_input_event_time="2029-01-01T00:00:00Z",
        market_input_observed_at="2029-01-01T00:00:01Z",
        signal_event_time="2029-01-01T00:00:02Z",
    )
    with pytest.raises(probe.ProbeError) as exc_info:
        probe._validate_natural_candidate(future, DEFAULT_TEST_CASE)
    assert exc_info.value.code == "invalid_freshness"


def test_adversarial_qa_fake_connection_empty_metadata_strictly_rejected(monkeypatch):
    class FakeConnection:
        async def fetch(self, *args):
            return [{"record_id": "src-binance", "payload": {"source_id": "src-binance", "metadata": {}}}]

        async def close(self):
            pass

    class FakeContext:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, exc_type, exc, tb):
            return False

    fake_conn = FakeConnection()
    fake_conn.transaction = lambda **kwargs: FakeContext(fake_conn)
    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=lambda dsn: asyncio.sleep(0, result=fake_conn)))
    src = probe.AsyncpgCaseSource("unused-unit-test-dsn")
    candidate = probe._complete_candidates(
        _natural_lifecycle_rows(), mode="natural", case=DEFAULT_TEST_CASE
    )[0]
    with pytest.raises(probe.ProbeError) as exc_info:
        asyncio.run(
            src.verify_source_lineage(
                candidate["signal_provenance"]["market_input_lineage"],
                DEFAULT_TEST_CASE["tenant_id"],
            )
        )
    assert exc_info.value.code == "source_tenant_missing"


def test_adversarial_qa_source_snapshot_binding_validation():
    candidate = probe._complete_candidates(
        _natural_lifecycle_rows(), mode="natural", case=DEFAULT_TEST_CASE
    )[0]
    prov = candidate["signal_provenance"]

    # Wrong snapshot ID
    bad_id_snap = dict(DEFAULT_TEST_SNAPSHOT, snapshot_id="mss-wrong")
    with pytest.raises(probe.ProbeError) as exc_info:
        probe._bind_source_snapshot(bad_id_snap, prov)
    assert exc_info.value.code == "invalid_snapshot_id"

    # Wrong ref
    bad_ref_snap = dict(DEFAULT_TEST_SNAPSHOT, source_ref="source-ingest://snapshots/mss-wrong")
    with pytest.raises(probe.ProbeError) as exc_info:
        probe._bind_source_snapshot(bad_ref_snap, prov)
    assert exc_info.value.code == "invalid_lineage"

    # Wrong event time
    bad_evt_snap = dict(DEFAULT_TEST_SNAPSHOT, event_time="2026-07-14T00:00:00Z")
    with pytest.raises(probe.ProbeError) as exc_info:
        probe._bind_source_snapshot(bad_evt_snap, prov)
    assert exc_info.value.code == "invalid_freshness"

    # Wrong observed at
    bad_obs_snap = dict(DEFAULT_TEST_SNAPSHOT, observed_at="2026-07-14T00:00:01Z")
    with pytest.raises(probe.ProbeError) as exc_info:
        probe._bind_source_snapshot(bad_obs_snap, prov)
    assert exc_info.value.code == "invalid_freshness"

    # Wrong lineage
    bad_lin_snap = dict(DEFAULT_TEST_SNAPSHOT, lineage=dict(DEFAULT_TEST_SNAPSHOT["lineage"], source_ids=["src-wrong"]))
    with pytest.raises(probe.ProbeError) as exc_info:
        probe._bind_source_snapshot(bad_lin_snap, prov)
    assert exc_info.value.code == "invalid_lineage"

    # Missing checksum
    no_chk_snap = dict(DEFAULT_TEST_SNAPSHOT, checksum="", data_checksum="")
    with pytest.raises(probe.ProbeError) as exc_info:
        probe._bind_source_snapshot(no_chk_snap, prov)
    assert exc_info.value.code == "invalid_checksum"

    # Missing snapshot on case fails closed
    case_no_snap = {k: v for k, v in DEFAULT_TEST_CASE.items() if k != "source_snapshot"}
    with pytest.raises(probe.ProbeError) as exc_info:
        probe._validate_natural_candidate(candidate, case_no_snap)
    assert exc_info.value.code == "source_snapshot_missing"


def test_case_blob_cannot_bypass_independent_source_snapshot_lookup(tmp_path):
    root, rows = _publish(tmp_path)

    # Case has source_snapshot, but case_source has NO snapshot in store (returns None)
    case_with_snapshot = dict(DEFAULT_TEST_CASE, source_snapshot=DEFAULT_TEST_SNAPSHOT)
    case_source_missing_snapshot = InMemoryCaseSource(
        {DEFAULT_TEST_CASE_KEY: case_with_snapshot},
        snapshots={},
    )

    code, artifact = _execute(
        tmp_path,
        root=root,
        rows=rows,
        mode="natural",
        case_key=DEFAULT_TEST_CASE_KEY,
        case_source=case_source_missing_snapshot,
    )
    assert code == 1
    assert artifact["outcome"] == "failed"
    assert artifact["failure"]["code"] == "source_snapshot_missing"


def test_adversarial_qa_28ab_snapshot_only_id_strictly_rejected(monkeypatch):
    """Preserve 28ab counterexample: patched urlopen returning only snapshot_id must fail closed."""
    candidate = probe._complete_candidates(
        _natural_lifecycle_rows(), mode="natural", case=DEFAULT_TEST_CASE
    )[0]
    prov = candidate["signal_provenance"]
    fake_response = io.BytesIO(
        json.dumps({"snapshot_id": prov["market_input_snapshot_id"]}).encode("utf-8")
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=5: fake_response)
    src = probe.AsyncpgCaseSource("unused-dsn", source_base_url="http://mock-source-ingest:8097")
    with pytest.raises(probe.ProbeError) as exc_info:
        asyncio.run(src.get_source_snapshot(prov["market_input_snapshot_id"], "tenant-a", symbol="BTC-USDT"))
    assert exc_info.value.code == "source_snapshot_api_error"


def test_bind_source_snapshot_rejects_snapshot_only_id():
    candidate = probe._complete_candidates(
        _natural_lifecycle_rows(), mode="natural", case=DEFAULT_TEST_CASE
    )[0]
    prov = candidate["signal_provenance"]
    with pytest.raises(probe.ProbeError) as exc_info:
        probe._bind_source_snapshot({"snapshot_id": prov["market_input_snapshot_id"]}, prov)
    assert exc_info.value.code in {"invalid_snapshot_schema", "invalid_lineage", "invalid_freshness"}


def test_bind_source_snapshot_rejects_id_derived_checksum_placeholder():
    candidate = probe._complete_candidates(
        _natural_lifecycle_rows(), mode="natural", case=DEFAULT_TEST_CASE
    )[0]
    prov = candidate["signal_provenance"]
    placeholder_snap = dict(DEFAULT_TEST_SNAPSHOT, checksum=prov["market_input_snapshot_id"], data_checksum=prov["market_input_snapshot_id"])
    with pytest.raises(probe.ProbeError) as exc_info:
        probe._bind_source_snapshot(placeholder_snap, prov)
    assert exc_info.value.code == "invalid_checksum"


def test_bind_source_snapshot_rejects_unsupported_schema_version():
    candidate = probe._complete_candidates(
        _natural_lifecycle_rows(), mode="natural", case=DEFAULT_TEST_CASE
    )[0]
    prov = candidate["signal_provenance"]
    bad_schema_snap = dict(DEFAULT_TEST_SNAPSHOT, schema_version="source_ingest_snapshot.v999")
    with pytest.raises(probe.ProbeError) as exc_info:
        probe._bind_source_snapshot(bad_schema_snap, prov)
    assert exc_info.value.code == "invalid_snapshot_schema"


def test_bind_source_snapshot_rejects_insufficient_closes():
    candidate = probe._complete_candidates(
        _natural_lifecycle_rows(), mode="natural", case=DEFAULT_TEST_CASE
    )[0]
    prov = candidate["signal_provenance"]
    bad_closes_snap = dict(DEFAULT_TEST_SNAPSHOT, closes=[50000.0])
    with pytest.raises(probe.ProbeError) as exc_info:
        probe._bind_source_snapshot(bad_closes_snap, prov)
    assert exc_info.value.code == "invalid_lineage"


def test_bind_source_snapshot_rejects_corrupted_points_derived_sha():
    candidate = probe._complete_candidates(
        _natural_lifecycle_rows(), mode="natural", case=DEFAULT_TEST_CASE
    )[0]
    prov = candidate["signal_provenance"]
    corrupted_points = list(DEFAULT_TEST_SNAPSHOT["points"])
    corrupted_points[0] = dict(corrupted_points[0], close=99999.0)
    bad_points_snap = dict(DEFAULT_TEST_SNAPSHOT, points=corrupted_points)
    with pytest.raises(probe.ProbeError) as exc_info:
        probe._bind_source_snapshot(bad_points_snap, prov)
    assert exc_info.value.code in {"invalid_snapshot_id", "invalid_lineage"}


def test_source_api_full_dto_producer_to_verifier(monkeypatch):
    candidate = probe._complete_candidates(
        _natural_lifecycle_rows(), mode="natural", case=DEFAULT_TEST_CASE
    )[0]
    prov = candidate["signal_provenance"]
    fake_response = io.BytesIO(json.dumps(DEFAULT_TEST_SNAPSHOT).encode("utf-8"))
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=5: fake_response)
    src = probe.AsyncpgCaseSource("unused-dsn", source_base_url="http://source-ingest:8097")
    snap = asyncio.run(src.get_source_snapshot(prov["market_input_snapshot_id"], "tenant-a", symbol="BTC-USDT"))
    assert snap is not None
    assert snap["snapshot_id"] == prov["market_input_snapshot_id"]
    assert snap["checksum"] == DEFAULT_TEST_SNAPSHOT["checksum"]
    probe._bind_source_snapshot(snap, prov)



