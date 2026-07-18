"""Read-only hosted proof for the canonical paper lifecycle projector.

The probe never writes telemetry.  It waits for a complete, naturally emitted
paper aggregate in committed ``telemetry_events`` rows, then proves that the
currently published projector generation contains the same events and stable
identity while its controller is authoritative live truth.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Awaitable, Callable, Mapping, Sequence
import uuid

from services.trade_journey.lifecycle_projector import (
    JOURNEY_STORE_SCHEMA,
    LOOP_STORE_SCHEMA,
    STABLE_IDENTITY_FIELDS,
    LifecycleProjector,
    _fingerprint,
)


SCHEMA_VERSION = "pantheon.loop-prod-tel-002-hosted-proof.v1"
TASK_ID = "LOOP-PROD-TEL-002"
REQUIRED_EVENT_TYPES = (
    "signal_generation",
    "trade_decision",
    "order_submitted",
    "paper_fill_simulated",
    "position_snapshot",
)
RECONCILIATION_TYPES = ("reconciliation_completed", "reconciliation_failed")
QUERY_TYPES = (*REQUIRED_EVENT_TYPES, *RECONCILIATION_TYPES)
EXPECTED_STAGES = {
    "signal_generation": "signal_generation",
    "trade_decision": "trade_decision",
    "order_submitted": "order_submission",
    "paper_fill_simulated": "fill_management",
    "position_snapshot": "ledger_booking",
    "reconciliation_completed": "reconciliation",
    "reconciliation_failed": "reconciliation",
}
EXPECTED_PRODUCERS = {
    **{event_type: "execution.paper_runtime" for event_type in REQUIRED_EVENT_TYPES},
    **{
        event_type: "reconciliation-drift.scheduled"
        for event_type in RECONCILIATION_TYPES
    },
}
PAPER_LIFECYCLE_UUID_NAMESPACE = uuid.UUID("1760784c-c9e0-47eb-b0aa-d37f58d892df")


class ProbeError(RuntimeError):
    """A redaction-safe, fail-closed probe rejection."""

    def __init__(self, code: str, message: str, *, timed_out: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.timed_out = timed_out


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class AsyncpgTelemetrySource:
    """Take a bounded, read-only snapshot of committed lifecycle rows."""

    def __init__(
        self,
        dsn: str,
        *,
        row_limit: int = 20_000,
        query_types: Sequence[str] = QUERY_TYPES,
    ) -> None:
        self._dsn = dsn
        self._row_limit = row_limit
        self._query_types = tuple(query_types)

    async def high_watermark(self) -> int:
        try:
            import asyncpg  # type: ignore[import]

            conn = await asyncpg.connect(self._dsn)
            try:
                async with conn.transaction(isolation="repeatable_read", readonly=True):
                    return int(
                        await conn.fetchval(
                            "SELECT COALESCE(MAX(ingested_seq), 0) FROM telemetry_events "
                            "WHERE event_type = ANY($1::text[])",
                            list(self._query_types),
                        )
                        or 0
                    )
            finally:
                await conn.close()
        except Exception as exc:  # noqa: BLE001 - never include DSN/error text
            raise ProbeError(
                "source_query_error", "committed telemetry snapshot query failed"
            ) from exc

    async def snapshot_after(self, baseline_high_watermark: int) -> tuple[int, list[dict[str, Any]]]:
        try:
            import asyncpg  # type: ignore[import]

            conn = await asyncpg.connect(self._dsn)
            try:
                async with conn.transaction(isolation="repeatable_read", readonly=True):
                    high = int(
                        await conn.fetchval(
                            "SELECT COALESCE(MAX(ingested_seq), 0) FROM telemetry_events "
                            "WHERE event_type = ANY($1::text[])",
                            list(self._query_types),
                        )
                        or 0
                    )
                    records = await conn.fetch(
                        "SELECT ingested_seq, ingested_at, event_id, event_type, "
                        "created_at, payload FROM telemetry_events "
                        "WHERE ingested_seq > $1 AND event_type = ANY($2::text[]) "
                        "ORDER BY ingested_seq ASC LIMIT $3",
                        int(baseline_high_watermark),
                        list(self._query_types),
                        self._row_limit,
                    )
            finally:
                await conn.close()
        except Exception as exc:  # noqa: BLE001 - never include DSN/error text
            raise ProbeError(
                "source_query_error", "committed telemetry snapshot query failed"
            ) from exc

        rows: list[dict[str, Any]] = []
        try:
            for record in reversed(records):
                payload = record["payload"]
                if isinstance(payload, str):
                    payload = json.loads(payload)
                rows.append(
                    {
                        "ingested_seq": int(record["ingested_seq"]),
                        "ingested_at": record["ingested_at"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "event_id": str(record["event_id"]),
                        "event_type": str(record["event_type"]),
                        "created_at": record["created_at"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "payload": dict(payload),
                    }
                )
        except Exception as exc:  # noqa: BLE001 - source content is never exported
            raise ProbeError(
                "source_decode_error", "committed telemetry snapshot could not be normalized"
            ) from exc
        return high, rows

    async def snapshot(self) -> tuple[int, list[dict[str, Any]]]:
        return await self.snapshot_after(0)


async def _source_high_watermark(source: Any) -> int:
    high_watermark = getattr(source, "high_watermark", None)
    if callable(high_watermark):
        return int(await high_watermark())
    high, _rows = await source.snapshot()
    return int(high)


async def _source_snapshot_after(
    source: Any, baseline_high_watermark: int
) -> tuple[int, list[dict[str, Any]]]:
    snapshot_after = getattr(source, "snapshot_after", None)
    if callable(snapshot_after):
        high, rows = await snapshot_after(baseline_high_watermark)
        return int(high), list(rows)
    high, rows = await source.snapshot()
    return int(high), [
        row
        for row in rows
        if int(row.get("ingested_seq") or 0) > baseline_high_watermark
    ]


def _complete_candidates(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        try:
            event = LifecycleProjector._source_event(row)
            identity = LifecycleProjector._identity(event)
            sequence_no = LifecycleProjector._sequence_no(event)
        except Exception:  # invalid committed rows cannot establish proof
            continue
        if identity["environment"].lower() != "paper" or str(
            event.get("execution_mode") or ""
        ).lower() != "paper":
            continue
        key = tuple(identity[field] for field in STABLE_IDENTITY_FIELDS)
        group = groups.setdefault(key, {"identity": identity, "events": []})
        event_type = str(event["event_type"])
        envelope = event.get("correlation_envelope")
        metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
        metadata_envelope = metadata.get("correlation_envelope")
        if not isinstance(envelope, Mapping) or not isinstance(metadata_envelope, Mapping):
            continue
        causal_parent_id = str(event.get("causal_parent_id") or "")
        metadata_parent_id = str(metadata.get("causal_parent_id") or "")
        metadata_sequence = metadata.get("sequence_no")
        if (
            str(envelope.get("event_id") or "") != str(event["event_id"])
            or str(envelope.get("causation_event_id") or "") != causal_parent_id
            or metadata_parent_id != causal_parent_id
            or str(envelope.get("producer") or "") != EXPECTED_PRODUCERS.get(event_type)
            or envelope.get("producer_revision") != 1
            or str(envelope.get("event_time") or "") != str(event["created_at"])
            or str(envelope.get("received_at") or "") != str(event["created_at"])
            or str(event.get("source_mode") or metadata.get("source_mode") or "")
            != "live"
            or metadata_sequence != sequence_no
            or event.get("aggregate_type") != "trade_journey"
            or str(event.get("aggregate_id") or "") != identity["journey_id"]
        ):
            continue
        expected_metadata_envelope_id = (
            str(event["event_id"])
            if event_type in RECONCILIATION_TYPES
            else causal_parent_id
        )
        if str(metadata_envelope.get("event_id") or "") != expected_metadata_envelope_id:
            continue
        group["events"].append({
            "event_id": str(event["event_id"]),
            "event_type": event_type,
            "ingested_seq": int(row["ingested_seq"]),
            "sequence_no": sequence_no,
            "producer": str(envelope["producer"]),
            "causal_parent_event_id": causal_parent_id,
            "reconciliation_evaluation_id": str(
                metadata.get("reconciliation_evaluation_id") or ""
            ),
        })
    complete: list[dict[str, Any]] = []
    for group in groups.values():
        selected = sorted(group["events"], key=lambda item: item["sequence_no"])
        event_types = [item["event_type"] for item in selected]
        middle = event_types[2:-1]
        if (
            len(selected) < 6
            or event_types[:2] != ["signal_generation", "trade_decision"]
            or event_types[-1] not in RECONCILIATION_TYPES
            or len(middle) % 3 != 0
            or middle
            != ["order_submitted", "paper_fill_simulated", "position_snapshot"]
            * (len(middle) // 3)
        ):
            continue
        if [item["sequence_no"] for item in selected] != list(
            range(1, len(selected) + 1)
        ):
            continue
        if any(
            current["ingested_seq"] >= following["ingested_seq"]
            for current, following in zip(selected, selected[1:])
        ):
            continue
        if selected[0]["causal_parent_event_id"] != f"signal:{group['identity']['signal_id']}":
            continue
        if any(
            following["causal_parent_event_id"] != current["event_id"]
            for current, following in zip(selected, selected[1:])
        ):
            continue
        expected_decision_id = str(
            uuid.uuid5(
                PAPER_LIFECYCLE_UUID_NAMESPACE,
                f"{selected[0]['event_id']}:trade_decision",
            )
        )
        if selected[1]["event_id"] != expected_decision_id:
            continue
        valid_derived_ids = True
        for index in range(2, len(selected) - 1, 3):
            order, fill, position = selected[index : index + 3]
            if order["event_id"] != str(
                uuid.uuid5(
                    PAPER_LIFECYCLE_UUID_NAMESPACE,
                    f"{fill['event_id']}:order_submitted",
                )
            ) or position["event_id"] != str(
                uuid.uuid5(
                    PAPER_LIFECYCLE_UUID_NAMESPACE,
                    f"{fill['event_id']}:position_snapshot",
                )
            ):
                valid_derived_ids = False
                break
        if not valid_derived_ids:
            continue
        reconciliation = selected[-1]
        evaluation_id = reconciliation["reconciliation_evaluation_id"]
        if not evaluation_id or reconciliation["event_id"] != str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"pantheon:scheduled-reconciliation:{evaluation_id}",
            )
        ):
            continue
        group["selected_events"] = selected
        group["max_ingested_seq"] = max(item["ingested_seq"] for item in selected)
        complete.append(group)
    return sorted(complete, key=lambda item: item["max_ingested_seq"], reverse=True)


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeError("projection_read_error", "current projector generation is unreadable") from exc
    if not isinstance(value, dict):
        raise ProbeError("projection_read_error", "current projector generation is malformed")
    return value


def _current_projection(root: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    try:
        generation = (root / "current").resolve(strict=True)
        generation.relative_to((root / "generations").resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ProbeError("projection_read_error", "current projector generation is unavailable") from exc
    manifest = _read_object(generation / "manifest.json")
    journeys = _read_object(generation / "trade_journey_events.json")
    loops = _read_object(generation / "loop_runs.json")
    if manifest.get("schema_version") != "pantheon.lifecycle-projection-bundle.v1":
        raise ProbeError("projection_manifest_mismatch", "projector bundle manifest is not canonical")
    if manifest.get("journey_sha256") != _fingerprint(journeys) or manifest.get(
        "loop_runs_sha256"
    ) != _fingerprint(loops):
        raise ProbeError("projection_integrity_mismatch", "projector bundle fingerprints do not match")
    generation_number = manifest.get("generation")
    if journeys.get("generation") != generation_number or loops.get("generation") != generation_number:
        raise ProbeError("projection_generation_mismatch", "projector bundle generation is inconsistent")
    if journeys.get("schema_version") != JOURNEY_STORE_SCHEMA or loops.get("schema_version") != LOOP_STORE_SCHEMA:
        raise ProbeError("projection_schema_mismatch", "projector read model schema is not canonical")
    if journeys.get("controller") != loops.get("controller"):
        raise ProbeError("projection_controller_mismatch", "projector bundle controllers disagree")
    return journeys, loops, generation.name


def _correlate(
    *,
    candidate: Mapping[str, Any],
    baseline_high_watermark: int,
    high_watermark: int,
    journeys: Mapping[str, Any],
    loops: Mapping[str, Any],
    generation_name: str,
    expected_sha: str,
) -> dict[str, Any]:
    controller = loops.get("controller")
    if not isinstance(controller, Mapping):
        raise ProbeError("projection_controller_missing", "projector controller is missing")
    if str(controller.get("deployment_sha") or "") != expected_sha:
        raise ProbeError("deployment_sha_mismatch", "projector deployment SHA does not match expected SHA")
    expected_controller = {
        "mode": "live",
        "accepted_live": True,
        "truth_level": "canonical_live",
        "status": "ready",
    }
    if any(controller.get(key) != value for key, value in expected_controller.items()):
        raise ProbeError("controller_not_canonical_live", "projector controller is not accepted canonical live truth")
    checkpoint = int(controller.get("checkpoint") or 0)
    if checkpoint < high_watermark or int(controller.get("backlog") or 0) != 0:
        raise ProbeError("checkpoint_not_caught_up", "projector checkpoint does not cover the committed snapshot")

    identity = candidate["identity"]
    projected = journeys.get("events")
    if not isinstance(projected, list):
        raise ProbeError("journey_projection_malformed", "Trade Journey events are malformed")
    projected_by_id = {
        str(event.get("canonical_event_id")): event
        for event in projected
        if isinstance(event, Mapping) and event.get("canonical_event_id")
    }
    for source_event in candidate["selected_events"]:
        event = projected_by_id.get(source_event["event_id"])
        if not event:
            raise ProbeError("journey_event_missing", "committed lifecycle event is absent from Trade Journey")
        if event.get("stage") != EXPECTED_STAGES[source_event["event_type"]]:
            raise ProbeError("journey_stage_mismatch", "Trade Journey stage does not match committed lifecycle event")
        if event.get("source_mode") != "live" or event.get("accepted_live") is not True:
            raise ProbeError("journey_event_not_live", "Trade Journey event is not accepted live truth")
        if int(event.get("source_offset") or 0) != source_event["ingested_seq"]:
            raise ProbeError("journey_offset_mismatch", "Trade Journey source offset does not match committed telemetry")
        if any(str(event.get(field) or "") != identity[field] for field in STABLE_IDENTITY_FIELDS):
            raise ProbeError("journey_identity_mismatch", "Trade Journey stable identity does not match committed telemetry")

    records = loops.get("records")
    loop = records.get(identity["loop_run_id"]) if isinstance(records, Mapping) else None
    if not isinstance(loop, Mapping):
        raise ProbeError("loop_run_missing", "canonical loop-run is missing")
    if any(str(loop.get(field) or "") != identity[field] for field in STABLE_IDENTITY_FIELDS):
        raise ProbeError("loop_identity_mismatch", "loop-run stable identity does not match committed telemetry")
    if loop.get("accepted_live") is not True or loop.get("projection_mode") != "live":
        raise ProbeError("loop_run_not_live", "loop-run is not accepted live truth")
    if loop.get("status") not in {"completed", "completed_with_variance"}:
        raise ProbeError("loop_run_not_terminal", "loop-run is not reconciled terminal truth")

    return {
        "source": {
            "store": "telemetry_events",
            "read_only": True,
            "baseline_high_watermark": baseline_high_watermark,
            "source_high_watermark": high_watermark,
            "candidate_max_ingested_seq": candidate["max_ingested_seq"],
        },
        "identity": {field: identity[field] for field in STABLE_IDENTITY_FIELDS},
        "events": list(candidate["selected_events"]),
        "projection": {
            "generation": loops.get("generation"),
            "generation_name": generation_name,
            "checkpoint": checkpoint,
            "deployment_sha": controller.get("deployment_sha"),
            "mode": controller.get("mode"),
            "accepted_live": controller.get("accepted_live"),
            "truth_level": controller.get("truth_level"),
            "status": controller.get("status"),
            "loop_status": loop.get("status"),
            "loop_run_id": identity["loop_run_id"],
            "journey_id": identity["journey_id"],
        },
    }


async def run_probe(
    *,
    source: Any,
    projection_root: Path,
    expected_sha: str,
    timeout_seconds: float,
    poll_seconds: float,
    baseline_high_watermark: int | None = None,
    sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    if not expected_sha or expected_sha == "unknown":
        raise ProbeError("invalid_expected_sha", "a concrete expected deployment SHA is required")
    if baseline_high_watermark is not None and baseline_high_watermark < 0:
        raise ProbeError("invalid_baseline_high_watermark", "baseline high watermark must be non-negative")
    deadline = monotonic() + max(0.0, timeout_seconds)
    last_error = ProbeError("no_complete_paper_aggregate", "no complete committed paper lifecycle aggregate matched")
    observed_baseline_high_watermark = baseline_high_watermark
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise ProbeError(
                last_error.code, last_error.safe_message, timed_out=True
            ) from last_error
        try:
            if observed_baseline_high_watermark is None:
                high = await asyncio.wait_for(
                    _source_high_watermark(source), timeout=remaining
                )
                rows: list[dict[str, Any]] = []
            else:
                high, rows = await asyncio.wait_for(
                    _source_snapshot_after(source, observed_baseline_high_watermark),
                    timeout=remaining,
                )
        except asyncio.TimeoutError as exc:
            raise ProbeError(
                "source_query_timeout",
                "committed telemetry snapshot exceeded the probe deadline",
                timed_out=True,
            ) from exc
        if observed_baseline_high_watermark is None:
            observed_baseline_high_watermark = high
        candidates = _complete_candidates(rows)
        if candidates:
            try:
                journeys, loops, generation_name = _current_projection(projection_root)
            except ProbeError as exc:
                last_error = exc
            else:
                for candidate in candidates:
                    try:
                        proof = _correlate(
                            candidate=candidate,
                            baseline_high_watermark=observed_baseline_high_watermark,
                            high_watermark=high,
                            journeys=journeys,
                            loops=loops,
                            generation_name=generation_name,
                            expected_sha=expected_sha,
                        )
                        return {
                            "schema_version": SCHEMA_VERSION,
                            "task_id": TASK_ID,
                            "outcome": "passed",
                            "observed_at": _utc_now(),
                            "expected_deployment_sha": expected_sha,
                            "proof": proof,
                            "redaction": {"dsn_included": False, "payloads_included": False},
                        }
                    except ProbeError as exc:
                        last_error = exc
        if monotonic() >= deadline:
            raise ProbeError(
                last_error.code, last_error.safe_message, timed_out=True
            ) from last_error
        await sleeper(min(max(0.05, poll_seconds), max(0.0, deadline - monotonic())))


def _failure_artifact(
    *,
    expected_sha: str,
    code: str,
    message: str,
    timed_out: bool | None = None,
) -> dict[str, Any]:
    failure: dict[str, Any] = {"code": code, "message": message}
    if timed_out is not None:
        failure["timed_out"] = timed_out
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": TASK_ID,
        "outcome": "failed",
        "observed_at": _utc_now(),
        "expected_deployment_sha": expected_sha,
        "failure": failure,
        "redaction": {"dsn_included": False, "payloads_included": False},
    }


def write_failure_artifact(
    output: Path,
    *,
    expected_sha: str,
    code: str,
    message: str,
) -> dict[str, Any]:
    """Write a redacted failure artifact when hosted transport fails."""
    artifact = _failure_artifact(
        expected_sha=expected_sha,
        code=code,
        message=message,
    )
    _atomic_write_json(output, artifact)
    return artifact


async def execute(
    *,
    source: Any,
    projection_root: Path,
    expected_sha: str,
    output: Path,
    timeout_seconds: float,
    poll_seconds: float,
    baseline_high_watermark: int | None = None,
    sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[int, dict[str, Any]]:
    try:
        artifact = await run_probe(
            source=source,
            projection_root=projection_root,
            expected_sha=expected_sha,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            baseline_high_watermark=baseline_high_watermark,
            sleeper=sleeper,
            monotonic=monotonic,
        )
        code = 0
    except ProbeError as exc:
        artifact = _failure_artifact(
            expected_sha=expected_sha,
            code=exc.code,
            message=exc.safe_message,
            timed_out=exc.timed_out,
        )
        code = 1
    except Exception:  # noqa: BLE001 - keep unexpected failures redacted and durable
        artifact = _failure_artifact(
            expected_sha=expected_sha,
            code="unexpected_probe_error",
            message="hosted lifecycle probe failed unexpectedly",
        )
        code = 1
    _atomic_write_json(output, artifact)
    return code, artifact


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline-high-watermark", type=int)
    parser.add_argument("--print-high-watermark", action="store_true")
    parser.add_argument(
        "--timeout-seconds", "--timeout", dest="timeout", type=float, default=300.0
    )
    parser.add_argument(
        "--poll-seconds", "--poll", dest="poll", type=float, default=2.0
    )
    args = parser.parse_args(argv)
    dsn = os.getenv("TELEMETRY_DB_DSN", "").strip()
    root = os.getenv("LIFECYCLE_PROJECTION_ROOT", "").strip()
    if args.print_high_watermark:
        if not dsn:
            print(
                json.dumps(
                    {
                        "outcome": "failed",
                        "failure": {
                            "code": "configuration_missing",
                            "message": "telemetry DSN is required",
                        },
                        "redaction": {"dsn_included": False},
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            return 1
        try:
            high_watermark = asyncio.run(
                _source_high_watermark(AsyncpgTelemetrySource(dsn))
            )
        except ProbeError as exc:
            print(
                json.dumps(
                    {
                        "outcome": "failed",
                        "failure": {
                            "code": exc.code,
                            "message": exc.safe_message,
                            "timed_out": exc.timed_out,
                        },
                        "redaction": {"dsn_included": False},
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            return 1
        print(high_watermark)
        return 0
    if args.output is None:
        parser.error("--output is required unless --print-high-watermark is used")
    if not dsn or not root:
        write_failure_artifact(
            args.output,
            expected_sha=args.expected_sha,
            code="configuration_missing",
            message="telemetry DSN and projection root are required",
        )
        return 1
    code, artifact = asyncio.run(
        execute(
            source=AsyncpgTelemetrySource(dsn),
            projection_root=Path(root),
            expected_sha=args.expected_sha,
            output=args.output,
            timeout_seconds=args.timeout,
            poll_seconds=args.poll,
            baseline_high_watermark=args.baseline_high_watermark,
        )
    )
    print(json.dumps({"outcome": artifact["outcome"], "output": str(args.output)}, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
