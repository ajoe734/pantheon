"""Cheap builder of a hosted-shaped daily bars tree for tests (not production code).

``DailyBarsStore.write_day`` pushes every row through Python (validation, merge by key, digest, row to
arrow conversion): about 0.4 s of ``make_rows`` plus 0.7 s of ``write_day`` per 22,500 row day, so 500 days
took about 215 s on the dev host. The date partitions of a test tree differ only in the ``trade_date`` value.
So one day is written through the real ``write_day`` (the template), and every other day is produced from the
template's own parquet parts in one pass: the ``trade_date`` column is replaced as an arrow column and each part
is written with the same options ``write_day`` uses (zstd, ``ROW_GROUP_ROWS``, same part names and split).
The manifest is the template's with the date, byte sizes and digest swapped. The digest is the template's
canonical JSON with the date text replaced, which is the same bytes ``day_digest`` would hash.
``test_history_availability.py`` compares one day built both ways (files, manifest, lineage).
"""
from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Sequence

from services.source_ingestion import daily_bars_store as dbs
from services.source_ingestion.daily_bars_store import DailyBarsStore
from services.source_ingestion.tests.test_daily_bars_store import make_rows

# Design of record: SD_MARKET_DATA_ONE_WRITE_PER_DAY_2026-11-16.md as amended 2026-11-17 (decision F, PR 7523):
# the official listings of 2026-11-17 hold 52,958 symbols, 50,883 of them warrants (2,075 shares and ETFs).
# The earlier 22,500 / 20,200 figures are marked CORRECTED there (about 2.4 times too low).
HOSTED_SYMBOLS = 52_958
HOSTED_WARRANTS = 50_883


def build_hosted_shaped(root: Path, dates: Sequence[str], symbols: int = HOSTED_SYMBOLS,
                        warrants: int = HOSTED_WARRANTS) -> DailyBarsStore:
    """Write ``dates`` (sorted ISO dates) under ``root``; the first date goes through ``write_day``."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    store = DailyBarsStore(root)
    d0 = dates[0]
    rows = make_rows(symbols, trade_date=d0, warrants=warrants)
    assert store.write_day(d0, rows, ingest_run_id=f"r-{d0}")
    template = store.read_manifest(d0)
    parts = [pq.read_table(store.bars_dir / e["path"]) for e in template["files"]]
    date_index = parts[0].schema.get_field_index("trade_date")
    keyed = sorted((r["market"], r["symbol"], r["trade_date"], r["content_hash"]) for r in rows)
    digest_text = dbs._canonical(keyed)
    assert digest_text.count(d0) == len(rows) and dbs._sha(keyed) == template["content_digest"]
    def write_one(d: str) -> None:
        files = []
        for entry, part in zip(template["files"], parts):
            path = store.bars_dir / entry["path"].replace(d0, d)
            path.parent.mkdir(parents=True, exist_ok=True)
            column = pa.repeat(pa.scalar(d, type=pa.string()), part.num_rows)
            pq.write_table(part.set_column(date_index, "trade_date", column), path, compression="zstd",
                           row_group_size=dbs.ROW_GROUP_ROWS)
            files.append({**entry, "path": entry["path"].replace(d0, d), "bytes": path.stat().st_size})
        manifest = {**template, "trade_date": d, "files": files, "bytes": sum(f["bytes"] for f in files),
                    "content_digest": hashlib.sha256(digest_text.replace(d0, d).encode("utf-8")).hexdigest()}
        store._manifest_path(d).write_bytes(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        store._write_lineage(d, f"r-{d}", {}, None, None, [{"lineage_ref": "run-1"}])
    # arrow releases the GIL while encoding and compressing, so a few threads cut the wall time
    with ThreadPoolExecutor(max_workers=min(4, os.cpu_count() or 1)) as pool:
        list(pool.map(write_one, dates[1:]))
    return store
