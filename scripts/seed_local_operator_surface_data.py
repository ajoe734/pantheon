#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
BFF_DIR = ROOT / "services" / "control-plane" / "bff"
if str(BFF_DIR) not in sys.path:
    sys.path.insert(0, str(BFF_DIR))

from read_store import _default_read_data  # type: ignore


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return default
    return json.loads(text)


def _save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def _merge_list_records(existing: list[dict[str, Any]], defaults: Dict[str, dict[str, Any]], key: str) -> int:
    existing_by_id = {
        str(item.get(key)): item
        for item in existing
        if isinstance(item, dict) and item.get(key) not in (None, "")
    }
    added = 0
    for record in defaults.values():
        record_id = str(record.get(key) or "")
        if not record_id or record_id in existing_by_id:
            continue
        existing.append(record)
        existing_by_id[record_id] = record
        added += 1
    return added


def seed_incident_store() -> dict[str, int]:
    defaults = _default_read_data()
    incidents_dir = Path(os.getenv("INCIDENTS_DATA_DIR", "/tmp/pantheon/incidents"))
    incidents_path = incidents_dir / "incidents.json"
    payload = _load_json(incidents_path, {"incidents": [], "postmortems": []})
    if not isinstance(payload, dict):
        payload = {"incidents": [], "postmortems": []}

    incidents = payload.get("incidents")
    if not isinstance(incidents, list):
        incidents = []
        payload["incidents"] = incidents

    postmortems = payload.get("postmortems")
    if not isinstance(postmortems, list):
        postmortems = []
        payload["postmortems"] = postmortems

    added_incidents = _merge_list_records(incidents, defaults.get("incidents", {}), "incident_id")
    added_postmortems = _merge_list_records(postmortems, defaults.get("postmortems", {}), "postmortem_id")
    _save_json(incidents_path, payload)
    return {
        "incident_records_added": added_incidents,
        "postmortem_records_added": added_postmortems,
    }


def seed_evolution_store() -> dict[str, int]:
    defaults = _default_read_data()
    evolution_dir = Path(os.getenv("EVOLUTION_DATA_DIR", "/tmp/pantheon/evolution"))
    decisions_path = evolution_dir / "decisions.json"
    payload = _load_json(decisions_path, {})
    if not isinstance(payload, dict):
        payload = {}

    added = 0
    for decision_id, record in defaults.get("evolution_decisions", {}).items():
        if decision_id in payload:
            continue
        payload[decision_id] = record
        added += 1

    _save_json(decisions_path, payload)
    return {"evolution_records_added": added}


def main() -> int:
    incident_result = seed_incident_store()
    evolution_result = seed_evolution_store()
    summary = {**incident_result, **evolution_result}
    print(json.dumps(summary, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
