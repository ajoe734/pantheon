"""Independent verification for PSD-SQLITE-VERIFY-001.

Exercises `DistillationJobQueue`'s corrupt-database-family recovery
(`services/source_ingestion/distillation_worker.py`) directly, as an
independent reviewer script separate from the implementation's own test
suite (`services/source_ingestion/tests/test_distillation_worker.py`).

Does not modify any implementation file. Self-bootstraps the repository root
onto `sys.path`, so it runs with any interpreter that has the repository's
third-party dependencies installed (a bare system `python3` included) --
no provisioning, PYTHONPATH, or repo-root cwd is required:

    python3 docs/04/pantheon_pre_shutdown_gap_sa_sd_2026-09-02/evidence/verify-sqlite/verify_sqlite_family_recovery.py

Exits 0 and prints a JSON report on success; exits 1 on any check failure.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.source_ingestion.distillation_worker import (  # noqa: E402
    DistillationJobQueue,
    DistillationJobStatus,
)

CASES = {
    "db_only": (),
    "wal_only": ("-wal",),
    "shm_only": ("-shm",),
    "full_family": ("-wal", "-shm"),
}


def _write_corrupt_family(
    base: Path, sidecar_suffixes: tuple[str, ...]
) -> dict[str, bytes]:
    main_bytes = b"SQLite format 3\x00" + b"\xff" * 200
    base.write_bytes(main_bytes)
    original_bytes = {"main": main_bytes}
    for suffix in sidecar_suffixes:
        sidecar_bytes = f"stale{suffix}".encode()
        Path(f"{base}{suffix}").write_bytes(sidecar_bytes)
        original_bytes[suffix] = sidecar_bytes
    return original_bytes


def _verify_case(case_name: str, sidecar_suffixes: tuple[str, ...], tmp_root: Path) -> dict:
    case_dir = tmp_root / case_name
    case_dir.mkdir(parents=True, exist_ok=True)
    db_path = case_dir / "family.sqlite3"
    original_bytes = _write_corrupt_family(db_path, sidecar_suffixes)

    fixed_now = lambda: 1_700_000_000.0  # noqa: E731 - fixed clock for deterministic recovery ids
    checks: dict[str, bool] = {}

    queue = DistillationJobQueue(db_path, now=fixed_now)

    receipts = list(case_dir.glob("family.sqlite3.corrupt-*.receipt.json"))
    checks["exactly_one_receipt_written"] = len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8")) if receipts else {}
    checks["probe_passed"] = receipt.get("probe") == "passed"

    recovery_id = receipt.get("recovery_id", "")
    moved_kinds = {member["kind"] for member in receipt.get("members", [])}
    expected_kinds = {"main"} | set(sidecar_suffixes)
    # Every member this test wrote must appear in the receipt. sqlite may
    # also discover a transient -shm mid-open even when this test did not
    # write one; that member is genuinely part of the family at discovery
    # time and is allowed, but nothing outside {main, -wal, -shm} is.
    checks["all_written_members_quarantined"] = expected_kinds.issubset(moved_kinds)
    checks["no_unexpected_member_kind_quarantined"] = moved_kinds <= {"main", "-wal", "-shm"}

    quarantine_targets_exist = all(
        case_dir.joinpath(
            f"family.sqlite3{'' if kind == 'main' else kind}.corrupt-{recovery_id}"
        ).exists()
        for kind in moved_kinds
    )
    checks["quarantine_targets_present_on_disk"] = quarantine_targets_exist

    # The quarantined copy of "main" and "-wal" must be a byte-identical
    # rename of the original corrupt content -- i.e. quarantine is a pure
    # rename, not a destructive rewrite. "-shm" is excluded from this
    # byte-identity check: opening a WAL-mode database rewrites the shared
    # memory index in place before the malformed-header error surfaces, so
    # sqlite itself -- not this recovery path -- mutates that sidecar's
    # bytes pre-quarantine whenever both -wal and -shm already exist.
    checks["quarantined_bytes_match_original_corrupt_bytes"] = all(
        case_dir.joinpath(
            f"family.sqlite3{'' if kind == 'main' else kind}.corrupt-{recovery_id}"
        ).read_bytes()
        == original_bytes[kind]
        for kind in expected_kinds & {"main", "-wal"}
        if case_dir.joinpath(
            f"family.sqlite3{'' if kind == 'main' else kind}.corrupt-{recovery_id}"
        ).exists()
    )

    fresh_queue_writable = False
    fresh_queue_readable = False
    try:
        job = queue.enqueue(f"src-after-{case_name}")
        fresh_queue_writable = job.status == DistillationJobStatus.PENDING
        fresh_queue_readable = queue.get(f"src-after-{case_name}") is not None
    except Exception:
        pass
    checks["fresh_queue_accepts_write_read_probe"] = fresh_queue_writable and fresh_queue_readable

    reopened = DistillationJobQueue(db_path)
    checks["fresh_queue_durable_across_reopen"] = (
        reopened.get(f"src-after-{case_name}") is not None
    )

    checks["no_second_recovery_receipt_on_clean_reopen"] = (
        len(list(case_dir.glob("family.sqlite3.corrupt-*.receipt.json"))) == 1
    )

    return {
        "case": case_name,
        "sidecar_suffixes": list(sidecar_suffixes),
        "checks": checks,
        "passed": all(checks.values()),
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="psd-sqlite-verify-") as tmp:
        tmp_root = Path(tmp)
        results = [
            _verify_case(case_name, suffixes, tmp_root)
            for case_name, suffixes in CASES.items()
        ]
    shutil.rmtree(tmp_root, ignore_errors=True)

    overall_passed = all(result["passed"] for result in results)
    report = {
        "schema_version": "pantheon.independent_verification.v1",
        "task_id": "PSD-SQLITE-VERIFY-001",
        "target_implementation": "services/source_ingestion/distillation_worker.py",
        "cases": results,
        "overall_passed": overall_passed,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if overall_passed else 1


if __name__ == "__main__":
    sys.exit(main())
