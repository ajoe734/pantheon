"""Read only pin source: which ingest runs and trade dates do gate proofs and paper evidence still reference?

SOURCE-HOSTED-LISTING-STATE-AND-PIN-SOURCE-20261118. Design of record: SD_MARKET_DATA_ONE_WRITE_PER_DAY_2026-11-16
section 4.3 and 7. It adds no store: it reads the evidence documents that already exist (JSON or JSONL files under
the configured evidence paths) and returns

* the pinned ingest run ids  -> ``JsonlIngestScheduleStore(pinned_run_ids=PinSource(...).run_ids)`` keeps those
  receipts past retention, and the retention sweeper takes the same set;
* the pinned trade dates     -> ``scripts/cleanup_market_run_files.py --pinned-dates`` (see ``main``).

What the evidence actually references (reproduced on recorded data, see the task evidence):

=====================================  ==========================================================  ==========
field                                  written by                                                  gives
=====================================  ==========================================================  ==========
``audit.ingest_run_id``                gate proof, services/governance/research_activation/         run id
                                       production_data_proof.py:268 (AuditProof), activation
                                       packet services/research/qlib/adapter/production_activation.py:171
``ingest_run_id`` (dataset manifest)   services/learning/qlib/activation/dataset_manifest.py:246   run id
``lineage.ingest_run_ids``             market snapshot / paper session lineage,                     run ids
                                       services/source_ingestion/requirement_state.py:732,
                                       checked in services/trade_journey/hosted_lifecycle_probe.py:997
``source_ingest_run_id``               source record provenance,                                    run id
                                       services/training-session/source_dataset_authority.py:359
``ingest_run_id`` (signal metadata)    paper signal context, services/execution/lean_runtime/        run id
                                       executor.py:390
``points[].event_time``                snapshot points (requirement_state.py:562, the trade day)    date
``normalized_row.date``                the normalized row a source record carries (recorded paper    date
                                       job evidence: S5-LOOPS-001 source readbacks)
``trade_date`` / ``trade_dates``       source records and proofs that name a session               date(s)
=====================================  ==========================================================  ==========

Recorded paper job evidence (S5-LOOPS-001) names run ids and storage refs, and no trade date field; so the date
partitions that hold a pinned run's run files (``--run-files-root``) are pinned dates as well.

Dataset digests (``storage.checksum``) are not read: no file under the run tree is named by a digest alone.

Fail closed: when the evidence cannot be read (path missing, file unreadable or not JSON, over the bounds) or
nothing is configured, ``read`` raises ``PinSourceUnavailable`` with a readable reason. A caller that cannot
resolve the pins must drop nothing; ``JsonlIngestScheduleStore`` does exactly that and reports the reason.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

PIN_PATHS_ENV = "SOURCE_INGEST_PIN_EVIDENCE_PATHS"  # os.pathsep separated files or directories
MAX_FILES = 20000
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_DEPTH = 40

RUN_ID_KEYS = frozenset({"ingest_run_id", "source_ingest_run_id", "last_ingest_run_id"})
RUN_ID_LIST_KEYS = frozenset({"ingest_run_ids"})
DATE_KEYS = frozenset({"trade_date"})
DATE_LIST_KEYS = frozenset({"trade_dates"})
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


class PinSourceUnavailable(RuntimeError):
    """The pin references cannot be resolved; the message says why."""


@dataclass(frozen=True)
class PinSet:
    run_ids: frozenset[str]
    trade_dates: frozenset[str]
    files_read: int
    paths: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"pinned_run_ids": sorted(self.run_ids), "pinned_trade_dates": sorted(self.trade_dates),
                "files_read": self.files_read, "paths": list(self.paths)}


def _walk(node: Any, runs: set[str], dates: set[str], depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            if key in RUN_ID_KEYS and isinstance(value, str) and value.strip():
                runs.add(value.strip())
            elif key in RUN_ID_LIST_KEYS and isinstance(value, list):
                runs.update(v.strip() for v in value if isinstance(v, str) and v.strip())
            elif key in DATE_KEYS and isinstance(value, str) and _DATE_RE.match(value.strip()):
                dates.add(value.strip()[:10])
            elif key in DATE_LIST_KEYS and isinstance(value, list):
                dates.update(v.strip()[:10] for v in value if isinstance(v, str) and _DATE_RE.match(v.strip()))
            elif key == "normalized_row" and isinstance(value, dict):
                d = value.get("trade_date") or value.get("date")
                if isinstance(d, str) and _DATE_RE.match(d.strip()):
                    dates.add(d.strip()[:10])
            elif key == "points" and isinstance(value, list):
                for point in value:
                    ev = point.get("event_time") if isinstance(point, dict) else None
                    if isinstance(ev, str) and _DATE_RE.match(ev.strip()):
                        dates.add(ev.strip()[:10])
            if isinstance(value, (dict, list)):
                _walk(value, runs, dates, depth + 1)
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, (dict, list)):
                _walk(item, runs, dates, depth + 1)


def _documents(path: Path) -> Iterator[Any]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise PinSourceUnavailable(f"pin evidence file unreadable: {path}: {exc}") from exc
    if size > MAX_FILE_BYTES:
        raise PinSourceUnavailable(f"pin evidence file over {MAX_FILE_BYTES} bytes: {path}")
    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".jsonl":
            for line in text.splitlines():
                if line.strip():
                    yield json.loads(line)
        else:
            yield json.loads(text)
    except (OSError, ValueError) as exc:
        raise PinSourceUnavailable(f"pin evidence file is not readable JSON: {path}: {exc}") from exc


class PinSource:
    """Read only. One call = one scan; nothing is cached or written."""

    def __init__(self, paths: Iterable[str | Path], *, run_files_root: str | Path | None = None) -> None:
        self.paths = tuple(Path(p) for p in paths if str(p).strip())
        # Market data store root: the date partitions that hold a pinned run's ``tw_price_daily`` run files are
        # pinned dates too (the recorded evidence names runs, rarely dates).
        self.run_files_root = Path(run_files_root) if run_files_root else None

    @classmethod
    def from_env(cls, environ: Any = None, *, run_files_root: str | Path | None = None) -> "PinSource":
        raw = str((environ if environ is not None else os.environ).get(PIN_PATHS_ENV, "") or "")
        return cls((p for p in raw.split(os.pathsep) if p.strip()), run_files_root=run_files_root)

    def _run_file_dates(self, run_ids: frozenset[str]) -> set[str]:
        root = self.run_files_root
        if root is None or not run_ids:
            return set()
        dirs: list[Path] = []
        norm = root / "normalized" / "tw_price_daily"
        if norm.is_dir():
            dirs += [d for d in norm.glob("date=*") if d.is_dir()]
        raw = root / "raw"
        if raw.is_dir():
            dirs += [d for d in raw.glob("*/tw_price_daily/date=*") if d.is_dir()]
        out: set[str] = set()
        for d in dirs:
            day = d.name[len("date="):]
            if not _DATE_RE.match(day):
                continue
            try:
                names = {n.split(".", 1)[0] for n in os.listdir(d)}
            except OSError as exc:
                raise PinSourceUnavailable(f"run file partition unreadable: {d}: {exc}") from exc
            if names & run_ids:
                out.add(day[:10])
        return out

    def _files(self) -> list[Path]:
        files: list[Path] = []
        for root in self.paths:
            if root.is_file():
                files.append(root)
            elif root.is_dir():
                files.extend(sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in (".json", ".jsonl")))
            else:
                raise PinSourceUnavailable(f"pin evidence path not found: {root}")
            if len(files) > MAX_FILES:
                raise PinSourceUnavailable(f"pin evidence has more than {MAX_FILES} files")
        return files

    def read(self, *, with_dates: bool = True) -> PinSet:
        if not self.paths:
            raise PinSourceUnavailable(f"pin_source_not_configured: set {PIN_PATHS_ENV} to the gate proof and paper evidence paths")
        files = self._files()
        runs: set[str] = set()
        dates: set[str] = set()
        for f in files:
            for doc in _documents(f):
                _walk(doc, runs, dates)
        if with_dates:
            dates |= self._run_file_dates(frozenset(runs))
        return PinSet(frozenset(runs), frozenset(dates), len(files), tuple(str(p) for p in self.paths))

    # The callable ``JsonlIngestScheduleStore(pinned_run_ids=...)`` takes; it raises when unresolved.
    def run_ids(self) -> frozenset[str]:
        return self.read(with_dates=False).run_ids

    def trade_dates(self) -> frozenset[str]:
        return self.read().trade_dates

    def health(self) -> dict[str, Any]:
        try:
            pins = self.read()
        except PinSourceUnavailable as exc:
            return {"resolved": False, "reason": str(exc)}
        return {"resolved": True, "pinned_run_id_count": len(pins.run_ids),
                "pinned_trade_date_count": len(pins.trade_dates), "files_read": pins.files_read}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Print the pinned ingest run ids and trade dates (read only).")
    ap.add_argument("--path", action="append", default=[], help="evidence file or directory (default: $%s)" % PIN_PATHS_ENV)
    ap.add_argument("--run-files-root", help="market data store root: date partitions of pinned runs' run files are pinned dates")
    ap.add_argument("--dates-out", help="write the JSON list of pinned trade dates (input of cleanup_market_run_files --pinned-dates)")
    ap.add_argument("--run-ids-out", help="write the JSON list of pinned ingest run ids")
    args = ap.parse_args(argv)
    source = (PinSource(args.path, run_files_root=args.run_files_root) if args.path
              else PinSource.from_env(run_files_root=args.run_files_root))
    try:
        pins = source.read()
    except PinSourceUnavailable as exc:
        print(json.dumps({"resolved": False, "reason": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    if args.dates_out:
        Path(args.dates_out).write_text(json.dumps(sorted(pins.trade_dates)), encoding="utf-8")
    if args.run_ids_out:
        Path(args.run_ids_out).write_text(json.dumps(sorted(pins.run_ids)), encoding="utf-8")
    print(json.dumps(pins.to_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
