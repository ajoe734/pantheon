"""DISTILLATION-SAME-ARTICLE-REPAID-PER-REFETCH-20261127: read-only version diff measurement."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts import measure_refetch_version_diff as m
from services.source_ingestion.connectors.base import SourceRecord
from services.source_ingestion.distillation_worker import DistillationJobQueue

BODY = "Stocks with rising monthly revenue tend to outperform over the next month."


def _record(sid: str, n: int, body: str = BODY) -> SourceRecord:
    return SourceRecord(
        source_id=sid, connector_id="c", source_type="news", title="SECRET TITLE", content_ref="https://x/secret",
        status="normalized", metadata={"body": body, "content_sha256": f"sha256:{n:064x}", "fetched_at": f"t{n}"},
    )


def test_measure_separates_item_xml_noise_from_changed_extraction_text(tmp_path: Path, capsys) -> None:
    queue = DistillationJobQueue(tmp_path / "q.sqlite")
    for i in range(20):  # three refetches, only collector fields differ
        for n in (1, 2, 3):
            queue.enqueue_source_record(_record(f"collector:collector-news-rss:n{i}", n), enqueued_at=100.0 * n + i)
    for n, body in ((1, BODY), (2, BODY), (3, BODY + " Revised.")):  # one real revision
        queue.enqueue_source_record(_record("collector:collector-news-rss:revised", n, body), enqueued_at=100.0 * n)
    queue.enqueue_source_record(_record("collector:collector-news-rss:short", 1), enqueued_at=1.0)
    queue.enqueue_source_record(_record("other:src", 1), enqueued_at=1.0)

    report = m.measure(queue.path, prefix="collector:collector-news-rss:", min_versions=3, min_sources=20,
                       limit_sources=100)
    assert report["sources_measured"] == 21 and report["meets_min_sources"]
    assert report["consecutive_pairs"] == 42
    assert report["pairs_extraction_text_changed"] == 1 and report["pairs_input_digest_changed"] == 1
    assert report["sources_with_any_input_change"] == 1
    assert report["differing_field_counts"]["metadata:content_sha256"] == 42
    assert report["differing_field_counts"]["metadata:fetched_at"] == 42
    assert "metadata:body" in report["differing_field_counts"]
    assert "SECRET" not in json.dumps(report)  # counts and key names only
    assert m.main([str(queue.path), "--min-sources", "20"]) == 0
    assert "SECRET" not in capsys.readouterr().out


def test_measure_never_writes(tmp_path: Path) -> None:
    queue = DistillationJobQueue(tmp_path / "q.sqlite")
    queue.enqueue_source_record(_record("collector:collector-news-rss:a", 1), enqueued_at=1.0)
    before = Path(queue.path).read_bytes()
    assert m.main([str(queue.path), "--min-sources", "5"]) == 1  # too few sources: fails, reads only
    assert Path(queue.path).read_bytes() == before
    with sqlite3.connect(f"file:{queue.path}?mode=ro", uri=True) as c:
        assert c.execute("SELECT COUNT(*) FROM distillation_source_versions").fetchone()[0] == 1
