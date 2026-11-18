"""One switch for the wall-clock bounds of the source ingestion tests (SOURCE-INGEST-TEST-WALL-CLOCK-BOUNDS-DETERMINISTIC-20261118).

A required check runs on shared runners: one slow ``fsync`` or a stolen CPU slice turns a bound such as
"a 500-key write transaction takes at most 250 ms" into a failure of an unrelated pull request.  Same pattern as
``services/telemetry/test_runtime_summary_persist_cost.py`` (``PANTHEON_RUN_PERSIST_COST_BENCHMARK``):

* by default every wall-clock bound is **measured and reported but not judged**; the tests still run every
  other assertion (row counts, bytes, counters such as "one transaction per 500 keys") that does not depend
  on the speed of the host;
* with ``PANTHEON_SOURCE_INGEST_WALL_CLOCK_BOUNDS=1`` the same bounds, with the same numbers, are judged.

The bound values live in the tests, next to what they protect.  This module only decides whether a
measurement is judged and where it is reported.

Measure the bounds on a host (for example a hosted-shaped copy) and print the measured values as JSON::

    PANTHEON_SOURCE_INGEST_WALL_CLOCK_BOUNDS=1 python -m services.source_ingestion.tests.wall_clock_bounds

That runs ``test_snapshot_current_record_store.py`` (pass other pytest arguments after ``--``), judges the
bounds, and prints one JSON object ``{"judged": ..., "exit_code": ..., "measurements": [...]}`` where every
measurement has ``name``, ``measured``, ``bound``, ``unit`` and ``within_bound``.  Without the variable the
same command only reports the numbers (``judged`` is false).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ENV_SWITCH = "PANTHEON_SOURCE_INGEST_WALL_CLOCK_BOUNDS"
ENV_REPORT = "PANTHEON_SOURCE_INGEST_WALL_CLOCK_REPORT"  # JSON-lines file every measurement is appended to

RECORDED: list[dict[str, Any]] = []


def judged() -> bool:
    """True only when the explicit opt-in is set (``1``/``true``/``yes``/``on``)."""
    return os.environ.get(ENV_SWITCH, "").strip().lower() in {"1", "true", "yes", "on"}


def check_many(*checks: tuple) -> None:
    """Record every ``(name, measured, bound[, kwargs])`` first, then fail once listing each bound that was exceeded.

    Judged mode therefore reports all measurements of a test, not just the first bound that failed.
    """
    failures: list[str] = []
    for name, measured, bound, *rest in checks:
        try:
            check(name, measured, bound, **(rest[0] if rest else {}))
        except AssertionError as exc:
            failures.append(str(exc))
    if failures:
        raise AssertionError("; ".join(failures))


def check(name: str, measured: float, bound: float, *, unit: str = "ms", strict: bool = False) -> bool:
    """Record one measurement against its bound; fail the test only when the opt-in switch is set.

    ``strict`` selects ``measured < bound`` (the test's own operator) instead of ``measured <= bound``.
    Returns whether the measurement is within the bound, so a caller can report it either way.
    """
    within = measured < bound if strict else measured <= bound
    row = {
        "name": name,
        "test": os.environ.get("PYTEST_CURRENT_TEST", "").split(" ")[0],
        "measured": round(float(measured), 3),
        "bound": bound,
        "unit": unit,
        "operator": "<" if strict else "<=",
        "within_bound": within,
        "judged": judged(),
    }
    RECORDED.append(row)
    report = os.environ.get(ENV_REPORT)
    if report:
        with open(report, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    if judged() and not within:
        raise AssertionError(
            f"{name}: {measured:.1f} {unit} is not {row['operator']} {bound} {unit} ({ENV_SWITCH} is set)"
        )
    return within


def count_parquet_reads(monkeypatch) -> dict[str, int]:
    """Count what a parquet read really does, in files and row groups, not seconds (the counter behind a latency bound).

    ``opens`` is the number of ``ParquetFile`` objects created and ``row_groups`` the number of row groups decoded.
    """
    import pyarrow.parquet as pq

    counts = {"opens": 0, "row_groups": 0}
    original_open, original_read = pq.ParquetFile, pq.ParquetFile.read_row_groups

    class CountingParquetFile(original_open):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            counts["opens"] += 1
            super().__init__(*args, **kwargs)

        def read_row_groups(self, row_groups, *args, **kwargs):
            counts["row_groups"] += len(row_groups)
            return original_read(self, row_groups, *args, **kwargs)

    monkeypatch.setattr(pq, "ParquetFile", CountingParquetFile)
    return counts


def main(argv: list[str]) -> int:
    pytest_args = argv[argv.index("--") + 1 :] if "--" in argv else []
    if not pytest_args:
        pytest_args = [str(Path(__file__).with_name("test_snapshot_current_record_store.py")), "-q", "-p", "no:cacheprovider"]
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.jsonl"
        env = {**os.environ, ENV_REPORT: str(report)}
        code = subprocess.run([sys.executable, "-m", "pytest", *pytest_args], env=env, stdout=sys.stderr).returncode
        rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()] if report.exists() else []
    print(json.dumps({"judged": judged(), "exit_code": code, "measurements": rows}, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
