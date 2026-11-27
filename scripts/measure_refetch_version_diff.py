#!/usr/bin/env python3
"""Read-only: which fields differ between consecutive versions of the same news source.

DISTILLATION-SAME-ARTICLE-REPAID-PER-REFETCH-20261127 acceptance 1.

Opens the distillation outbox SQLite (``/data/source-ingest/distill_job_queue.jsonl`` on hosted)
with ``mode=ro`` and reads ``distillation_source_versions``. For every source with at least
``--min-versions`` versions it compares each version with the previous one and reports

* ``changed_fields``: which top-level fields and which metadata keys differ,
* ``extraction_text_changed``: whether the text one HYPOTHESIS_CARD call sends differs,
* ``input_digest_changed``: whether the full extraction input digest differs.

Output is counts and key names only: no article text, titles or URLs. It writes nothing.

    python3 scripts/measure_refetch_version_diff.py /data/source-ingest/distill_job_queue.jsonl \
        --prefix collector:collector-news-rss: --min-versions 3 --min-sources 20
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.source_ingestion.hypothesis_card import (  # noqa: E402
    MAX_PROMPT_TEXT_CHARS,
    extraction_input_digest,
    extraction_origin,
    stored_text,
)

TOP_FIELDS = ("title", "content_ref", "connector_id", "source_type")


def _open_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _digest(payload: dict[str, Any]) -> tuple[str, str] | None:
    metadata = payload.get("metadata") or {}
    origin = extraction_origin(str(payload.get("source_type") or ""), metadata)
    text = stored_text(metadata)
    if origin is None or not text:
        return None
    prompt_text = text[:MAX_PROMPT_TEXT_CHARS]
    return prompt_text, extraction_input_digest(
        prompt_text, source_record_id=str(payload.get("source_id") or ""), origin=origin
    )


def compare(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    changed = [f"top:{k}" for k in TOP_FIELDS if previous.get(k) != current.get(k)]
    pm, cm = previous.get("metadata") or {}, current.get("metadata") or {}
    changed += [f"metadata:{k}" for k in sorted(set(pm) | set(cm)) if pm.get(k) != cm.get(k)]
    before, after = _digest(previous), _digest(current)
    return {
        "changed_fields": changed,
        "extraction_text_changed": None if before is None or after is None else before[0] != after[0],
        "input_digest_changed": None if before is None or after is None else before[1] != after[1],
    }


def measure(path: Path, *, prefix: str, min_versions: int, min_sources: int, limit_sources: int) -> dict[str, Any]:
    with _open_read_only(path) as connection:
        sources = [
            str(r[0]) for r in connection.execute(
                "SELECT source_id FROM distillation_source_versions WHERE source_id LIKE ? AND source_digest <> ''"
                " GROUP BY source_id HAVING COUNT(*) >= ? ORDER BY COUNT(*) DESC LIMIT ?",
                (prefix + "%", min_versions, limit_sources),
            )
        ]
        field_counts: Counter[str] = Counter()
        text_changed = digest_changed = unmeasurable = pairs = 0
        per_source: list[dict[str, Any]] = []
        for source_id in sources:
            payloads = [
                json.loads(str(r[0])) for r in connection.execute(
                    "SELECT payload_json FROM distillation_source_versions WHERE source_id = ?"
                    " AND source_digest <> '' ORDER BY committed_at, rowid", (source_id,),
                )
            ]
            src_text = src_digest = 0
            for previous, current in zip(payloads, payloads[1:]):
                result = compare(previous, current)
                pairs += 1
                field_counts.update(result["changed_fields"])
                if result["input_digest_changed"] is None:
                    unmeasurable += 1
                    continue
                text_changed += bool(result["extraction_text_changed"])
                digest_changed += bool(result["input_digest_changed"])
                src_text += bool(result["extraction_text_changed"])
                src_digest += bool(result["input_digest_changed"])
            per_source.append({"versions": len(payloads), "pairs": len(payloads) - 1,
                               "text_changed_pairs": src_text, "input_digest_changed_pairs": src_digest})
    return {
        "sources_measured": len(sources),
        "meets_min_sources": len(sources) >= min_sources,
        "consecutive_pairs": pairs,
        "pairs_extraction_text_changed": text_changed,
        "pairs_input_digest_changed": digest_changed,
        "pairs_unmeasurable": unmeasurable,
        "differing_field_counts": dict(field_counts.most_common()),
        "sources_with_any_input_change": sum(1 for s in per_source if s["input_digest_changed_pairs"]),
        "per_source": per_source,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("queue_path", type=Path)
    parser.add_argument("--prefix", default="collector:collector-news-rss:")
    parser.add_argument("--min-versions", type=int, default=3)
    parser.add_argument("--min-sources", type=int, default=20)
    parser.add_argument("--limit-sources", type=int, default=200)
    args = parser.parse_args(argv)
    report = measure(args.queue_path, prefix=args.prefix, min_versions=args.min_versions,
                     min_sources=args.min_sources, limit_sources=args.limit_sources)
    print(json.dumps(report, sort_keys=True, ensure_ascii=False, indent=1))
    return 0 if report["meets_min_sources"] else 1


if __name__ == "__main__":
    sys.exit(main())
