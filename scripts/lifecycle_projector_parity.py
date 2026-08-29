#!/usr/bin/env python3
"""LIFECYCLE-PROJ-MIGRATE-001 CLI: deterministic old/new parity report.

Compares the legacy JSON Trade Journey / loop-run read-model bundle against
the relational rows the backfill/shadow worker wrote, using stable scoped
hashes per category (stage, journey, loop, identity, quarantine). Read-only
against both sides. Every mismatch must be explicitly classified via
``--classifications``; an unclassified mismatch is a blocking defect and the
command exits non-zero.

Usage::

    python3 scripts/lifecycle_projector_parity.py \\
      --dsn "$LIFECYCLE_PROJECTION_DSN" \\
      --legacy-journey-events /data/bff/lifecycle-projection/current/trade_journey_events.json \\
      --legacy-loop-runs /data/bff/lifecycle-projection/current/loop_runs.json \\
      --legacy-controller-state /data/bff/lifecycle-projection/controller_state.json \\
      --out docs/deployment/evidence/lifecycle-projector/LIFECYCLE-PROJ-MIGRATE-001/parity-report.json
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from services.trade_journey.projection_migration import (
    StreamingMultisetDigest,
    compare_category,
    iter_legacy_aggregates,
    legacy_identity_rows,
    legacy_journey_rows,
    legacy_loop_rows,
    legacy_quarantine_rows,
    legacy_stage_rows,
    projection_identity_rows,
    projection_journey_rows,
    projection_loop_rows,
    projection_quarantine_rows,
    projection_stage_rows,
    sha256_file,
    summarize_parity,
    validate_legacy_controller_binding,
)


def _fetch_rows(dsn: str, schema: str, table: str, columns: list[str]) -> list[dict[str, Any]]:
    import psycopg  # type: ignore[import]

    column_list = ", ".join(columns)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {column_list} FROM {schema}.{table}")
        rows = cur.fetchall()
    result = []
    for row in rows:
        record = dict(zip(columns, row))
        for key, value in list(record.items()):
            if isinstance(value, str) and key.endswith("_summary"):
                try:
                    record[key] = json.loads(value)
                except ValueError:
                    pass
        result.append(record)
    return result


def _iter_rows(
    dsn: str, schema: str, table: str, columns: list[str]
) -> Iterable[dict[str, Any]]:
    """Stream one relational category through a server-side cursor."""

    import psycopg  # type: ignore[import]

    if not schema.replace("_", "").isalnum():
        raise ValueError("invalid projection schema")
    column_list = ", ".join(columns)
    with psycopg.connect(dsn) as conn:
        with conn.cursor(name=f"parity_{table}") as cur:
            cur.execute(f"SELECT {column_list} FROM {schema}.{table}")
            while True:
                rows = cur.fetchmany(1000)
                if not rows:
                    return
                for row in rows:
                    record = dict(zip(columns, row))
                    for key, value in list(record.items()):
                        if isinstance(value, str) and key.endswith("_summary"):
                            try:
                                record[key] = json.loads(value)
                            except ValueError:
                                pass
                    yield record


def _update_digest(
    digest: StreamingMultisetDigest, rows: Iterable[Mapping[str, Any]]
) -> None:
    for row in rows:
        digest.update(row)


def _stream_legacy_baseline_parity(
    *,
    dsn: str,
    schema: str,
    controller_state: Path,
    expected_sha256: str,
    controller_id: str,
    legacy_checkpoint: int,
    classifications: Mapping[str, str],
) -> dict[str, Any]:
    actual_sha256 = sha256_file(controller_state)
    if actual_sha256 != expected_sha256.lower():
        raise ValueError("legacy controller-state checksum does not match accepted evidence")
    controller = validate_legacy_controller_binding(
        controller_state,
        expected_controller_id=controller_id,
        expected_checkpoint=legacy_checkpoint,
    )

    legacy = {name: StreamingMultisetDigest() for name in ("stage", "journey", "loop", "identity", "quarantine")}
    for _journey_id, aggregate in iter_legacy_aggregates(controller_state):
        _update_digest(legacy["stage"], legacy_stage_rows(aggregate.journey_events))
        _update_digest(legacy["journey"], legacy_journey_rows(aggregate.journey_events))
        if aggregate.loop_record:
            _update_digest(
                legacy["loop"],
                legacy_loop_rows(
                    {str(aggregate.loop_record["loop_run_id"]): aggregate.loop_record}
                ),
            )
        _update_digest(legacy["identity"], legacy_identity_rows(aggregate.journey_events))

    projection = {name: StreamingMultisetDigest() for name in legacy}
    adapters: dict[str, tuple[str, list[str], Callable[[Iterable[Any]], list[dict[str, Any]]]]] = {
        "stage": (
            "journey_stages",
            ["tenant_id", "environment", "journey_id", "source_event_id", "stage_name", "stage_status"],
            projection_stage_rows,
        ),
        "journey": (
            "journeys",
            ["tenant_id", "environment", "journey_id", "status", "is_terminal"],
            projection_journey_rows,
        ),
        "loop": (
            "loop_runs",
            ["tenant_id", "environment", "loop_run_id", "status", "lifecycle_summary"],
            projection_loop_rows,
        ),
        "identity": (
            "identity_links",
            ["tenant_id", "environment", "identifier_type", "identifier_value", "journey_id"],
            projection_identity_rows,
        ),
        "quarantine": (
            "quarantine",
            ["event_id", "ingested_seq"],
            projection_quarantine_rows,
        ),
    }
    for category, (table, columns, adapter) in adapters.items():
        for row in _iter_rows(dsn, schema, table, columns):
            _update_digest(projection[category], adapter([row]))

    categories: dict[str, Any] = {}
    mismatch_count = 0
    unexplained_mismatch_count = 0
    for category in legacy:
        legacy_hash = legacy[category].hexdigest()
        projection_hash = projection[category].hexdigest()
        match = (
            legacy[category].count == projection[category].count
            and legacy_hash == projection_hash
        )
        classification = None if match else classifications.get(category)
        mismatch_count += int(not match)
        unexplained_mismatch_count += int(not match and not classification)
        categories[category] = {
            "match": match,
            "legacy_count": legacy[category].count,
            "new_count": projection[category].count,
            "legacy_hash": legacy_hash,
            "new_hash": projection_hash,
            "classification": classification,
        }
    return {
        "source_sha256": actual_sha256,
        "controller_id": controller["controller_id"],
        "controller_checkpoint": controller["checkpoint"],
        "controller_deployment_sha": controller["deployment_sha"],
        "categories": categories,
        "mismatch_count": mismatch_count,
        "unexplained_mismatch_count": unexplained_mismatch_count,
    }


def _load_classifications(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dsn", default=os.getenv("LIFECYCLE_PROJECTION_DSN", ""))
    parser.add_argument(
        "--schema", default=os.getenv("LIFECYCLE_PROJECTION_SCHEMA", "trade_journey_projection")
    )
    parser.add_argument("--legacy-journey-events", type=Path, default=None)
    parser.add_argument("--legacy-loop-runs", type=Path, default=None)
    parser.add_argument("--legacy-controller-state", type=Path, required=True)
    parser.add_argument("--expected-legacy-sha256", default="")
    parser.add_argument("--controller-id", default="canonical-lifecycle-projector")
    parser.add_argument("--legacy-checkpoint", type=int, default=None)
    parser.add_argument(
        "--classifications", type=Path, default=None,
        help="JSON object mapping category name to a documented reason an intended difference is not a defect",
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    if not args.dsn:
        parser.error("--dsn or LIFECYCLE_PROJECTION_DSN is required")

    classifications = _load_classifications(args.classifications)
    if args.expected_legacy_sha256:
        if args.legacy_checkpoint is None:
            parser.error("--legacy-checkpoint is required with --expected-legacy-sha256")
        summary = _stream_legacy_baseline_parity(
            dsn=args.dsn,
            schema=args.schema,
            controller_state=args.legacy_controller_state,
            expected_sha256=args.expected_legacy_sha256,
            controller_id=args.controller_id,
            legacy_checkpoint=args.legacy_checkpoint,
            classifications=classifications,
        )
        rendered = json.dumps(summary, sort_keys=True, indent=2)
        print(rendered)
        if args.out is not None:
            args.out.write_text(rendered, encoding="utf-8")
        return 0 if summary["unexplained_mismatch_count"] == 0 else 1

    if args.legacy_journey_events is None or args.legacy_loop_runs is None:
        parser.error(
            "legacy JSON parity requires --legacy-journey-events and --legacy-loop-runs, "
            "or exact-checksum streaming mode with --expected-legacy-sha256"
        )

    legacy_events = json.loads(args.legacy_journey_events.read_text(encoding="utf-8")).get("events", [])
    legacy_records = json.loads(args.legacy_loop_runs.read_text(encoding="utf-8")).get("records", {})
    legacy_quarantine = json.loads(args.legacy_controller_state.read_text(encoding="utf-8")).get(
        "quarantine", []
    )

    stage_rows = _fetch_rows(
        args.dsn, args.schema, "journey_stages",
        ["tenant_id", "environment", "journey_id", "source_event_id", "stage_name", "stage_status"],
    )
    journey_rows = _fetch_rows(
        args.dsn, args.schema, "journeys",
        ["tenant_id", "environment", "journey_id", "status", "is_terminal"],
    )
    loop_rows = _fetch_rows(
        args.dsn, args.schema, "loop_runs",
        ["tenant_id", "environment", "loop_run_id", "status", "lifecycle_summary"],
    )
    identity_rows = _fetch_rows(
        args.dsn, args.schema, "identity_links",
        ["tenant_id", "environment", "identifier_type", "identifier_value", "journey_id"],
    )
    quarantine_rows = _fetch_rows(args.dsn, args.schema, "quarantine", ["event_id", "ingested_seq"])

    results = [
        compare_category(
            "stage", legacy_stage_rows(legacy_events), projection_stage_rows(stage_rows),
            key_fields=["journey_id", "source_event_id", "stage_name"],
            classification=classifications.get("stage"),
        ),
        compare_category(
            "journey", legacy_journey_rows(legacy_events), projection_journey_rows(journey_rows),
            key_fields=["journey_id"], classification=classifications.get("journey"),
        ),
        compare_category(
            "loop", legacy_loop_rows(legacy_records), projection_loop_rows(loop_rows),
            key_fields=["loop_run_id"], classification=classifications.get("loop"),
        ),
        compare_category(
            "identity", legacy_identity_rows(legacy_events), projection_identity_rows(identity_rows),
            key_fields=["identifier_type", "identifier_value"],
            classification=classifications.get("identity"),
        ),
        compare_category(
            "quarantine", legacy_quarantine_rows(legacy_quarantine), projection_quarantine_rows(quarantine_rows),
            key_fields=["event_id"], classification=classifications.get("quarantine"),
        ),
    ]
    summary = summarize_parity(results)
    rendered = json.dumps(summary, sort_keys=True, indent=2)
    print(rendered)
    if args.out is not None:
        args.out.write_text(rendered, encoding="utf-8")
    return 0 if summary["unexplained_mismatch_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
