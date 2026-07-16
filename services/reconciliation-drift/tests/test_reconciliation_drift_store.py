from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SERVICE_DIR = Path(__file__).resolve().parents[1]


def _load_store_module():
    spec = importlib.util.spec_from_file_location(
        "reconciliation_drift_store_test",
        SERVICE_DIR / "store.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_json_store_recovers_concatenated_maps_and_rewrites_valid_json(tmp_path: Path) -> None:
    module = _load_store_module()
    store = module.ReconciliationDriftStore(tmp_path)
    store.evaluations_path.write_text(
        json.dumps({"eval-a": {"evaluation_id": "eval-a", "status": "ok"}})
        + json.dumps({"eval-b": {"evaluation_id": "eval-b", "status": "warning"}}),
        encoding="utf-8",
    )

    evaluations = store.list_evaluations()

    assert [item["evaluation_id"] for item in evaluations] == ["eval-a", "eval-b"]

    store.put_evaluation({"evaluation_id": "eval-c", "status": "critical"})

    payload = json.loads(store.evaluations_path.read_text(encoding="utf-8"))
    assert set(payload) == {"eval-a", "eval-b", "eval-c"}


def test_json_store_treats_unrecoverable_map_as_empty(tmp_path: Path) -> None:
    module = _load_store_module()
    store = module.ReconciliationDriftStore(tmp_path)
    store.alerts_path.write_text("{not-json", encoding="utf-8")

    assert store.list_alert_handoffs() == []

    store.put_alert_handoff({"alert_id": "alert-a", "status": "sent"})

    payload = json.loads(store.alerts_path.read_text(encoding="utf-8"))
    assert set(payload) == {"alert-a"}
