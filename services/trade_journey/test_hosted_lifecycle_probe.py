from __future__ import annotations

import asyncio
import json
from pathlib import Path
import uuid

import pytest

from services.trade_journey import hosted_lifecycle_probe as probe
from services.trade_journey.lifecycle_projector import LifecycleProjector
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


def _natural_lifecycle_rows() -> list[dict]:
    by_type = {row["event_type"]: row for row in lifecycle_rows()}
    event_types = [*probe.REQUIRED_EVENT_TYPES, "reconciliation_completed"]
    selected = [json.loads(json.dumps(by_type[event_type])) for event_type in event_types]
    signal_event_id = selected[0]["event_id"]
    fill_event_id = selected[3]["event_id"]
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
                f"{fill_event_id}:order_submitted",
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


def _publish(tmp_path: Path, *, sha: str = "deployed-sha", mode: str = "live") -> tuple[Path, list[dict]]:
    root = tmp_path / "projection"
    rows = _natural_lifecycle_rows()
    projector = LifecycleProjector(
        state_path=root / "controller_state.json",
        bundle_root=root,
        deployment_sha=sha,
    )
    projector.project_records(rows, mode=mode, source_high_watermark=len(rows))
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
    assert artifact["proof"]["source"]["source_high_watermark"] == 6
    assert [event["event_type"] for event in artifact["proof"]["events"]] == [
        "signal_generation",
        "trade_decision",
        "order_submitted",
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


def test_probe_reads_only_rows_after_baseline_for_incremental_source(tmp_path):
    root = tmp_path / "projection"
    shifted_rows = _natural_lifecycle_rows()
    for row in shifted_rows:
        row["ingested_seq"] += 100
    LifecycleProjector(
        state_path=root / "controller_state.json",
        bundle_root=root,
        deployment_sha="deployed-sha",
    ).project_records(shifted_rows, mode="live", source_high_watermark=106)
    source = IncrementalSource(baseline=100, high=106, rows=shifted_rows)

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
    assert artifact["proof"]["source"]["source_high_watermark"] == 106


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
    projector = LifecycleProjector(
        state_path=root / "controller_state.json",
        bundle_root=root,
        deployment_sha="deployed-sha",
    )
    projector.record_source_failure("secret-postgres-dsn-would-not-be-exported")

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
    unrelated["ingested_seq"] = 7
    LifecycleProjector(
        state_path=root / "controller_state.json",
        bundle_root=root,
        deployment_sha="new-sha",
    ).project_records([unrelated], mode="live", source_high_watermark=7)

    output = tmp_path / "old-lifecycle.json"
    code, artifact = asyncio.run(
        probe.execute(
            source=BaselineSource(7, rows),
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
