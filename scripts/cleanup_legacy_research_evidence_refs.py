#!/usr/bin/env python3
"""Remove legacy artifact-derived evidence refs from BFF projection stores.

The old research projector promoted every research artifact into an evidence ref
(`evref-rart-*`) and marked it verified with `producer_record` credibility. Those
rows are artifact provenance, not canonical evidence. This cleanup is idempotent
and also removes stale insight-card references to the deleted refs.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def _backup_file(path: Path, timestamp: str) -> str | None:
    if not path.exists():
        return None
    candidate = path.with_name(f"{path.name}.bak-legacy-research-evidence-{timestamp}")
    suffix = 1
    while candidate.exists():
        suffix += 1
        candidate = path.with_name(f"{path.name}.bak-legacy-research-evidence-{timestamp}-{suffix}")
    shutil.copy2(path, candidate)
    return str(candidate)


def _is_legacy_research_ref_id(ref_id: str) -> bool:
    return ref_id.startswith("evref-rart-")


def _is_legacy_research_evidence(row: dict[str, Any]) -> bool:
    ref_id = str(row.get("ref_id") or row.get("id") or "")
    credibility = row.get("credibility") if isinstance(row.get("credibility"), dict) else {}
    return (
        _is_legacy_research_ref_id(ref_id)
        or credibility.get("tier") == "producer_record"
        or credibility.get("verification_method") == "research_orchestrator_projection"
    )


def cleanup(out_dir: str | os.PathLike[str], *, backup: bool = True) -> dict[str, Any]:
    root = Path(out_dir)
    evidence_path = root / "evidence_refs.json"
    insight_path = root / "insight_cards.json"
    evidence_refs = _read_json(evidence_path)
    insight_cards = _read_json(insight_path)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_paths: list[str] = []

    removed_ref_ids = {
        str(row.get("ref_id") or row.get("id") or ref_id)
        for ref_id, row in evidence_refs.items()
        if isinstance(row, dict) and _is_legacy_research_evidence(row)
    }
    if removed_ref_ids:
        evidence_refs = {
            ref_id: row
            for ref_id, row in evidence_refs.items()
            if str(row.get("ref_id") or row.get("id") or ref_id) not in removed_ref_ids
        }
        if backup:
            backup_path = _backup_file(evidence_path, timestamp)
            if backup_path:
                backup_paths.append(backup_path)
        _write_json(evidence_path, evidence_refs)

    legacy_ref_ids_in_insights = {
        str(ref)
        for insight in insight_cards.values()
        if isinstance(insight, dict)
        if isinstance(insight.get("supporting_evidence_refs"), list)
        for ref in insight.get("supporting_evidence_refs", [])
        if _is_legacy_research_ref_id(str(ref))
    }
    insight_ref_ids_to_remove = removed_ref_ids | legacy_ref_ids_in_insights
    insights_touched = 0
    if insight_ref_ids_to_remove and insight_cards:
        for insight in insight_cards.values():
            if not isinstance(insight, dict):
                continue
            refs = insight.get("supporting_evidence_refs")
            if not isinstance(refs, list):
                continue
            cleaned = [ref for ref in refs if str(ref) not in insight_ref_ids_to_remove]
            if cleaned != refs:
                insight["supporting_evidence_refs"] = cleaned
                insights_touched += 1
        if insights_touched:
            if backup:
                backup_path = _backup_file(insight_path, timestamp)
                if backup_path:
                    backup_paths.append(backup_path)
            _write_json(insight_path, insight_cards)

    return {
        "backup_paths": backup_paths,
        "removed_evidence_ref_count": len(removed_ref_ids),
        "removed_evidence_ref_ids": sorted(removed_ref_ids),
        "removed_insight_ref_ids": sorted(insight_ref_ids_to_remove),
        "insight_cards_touched": insights_touched,
        "out_dir": str(root),
    }


def main() -> int:
    out_dir = os.environ.get("OUT_DIR") or os.environ.get("BFF_DATA_DIR") or "/data/bff"
    print(json.dumps(cleanup(out_dir), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
