"""OODA source -> StrategySpec transition e2e test."""

from __future__ import annotations

import hashlib
import importlib
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from services.registry.service import app as registry_app
from services.registry.storage import reset_store
from services.research.strategy_spec.conversion import StrategySpecConversionService
from services.research.strategy_spec.models import validate_strategy_spec_payload


FIXTURE_PATH = Path(__file__).with_name("fixtures") / "sample_internal_research_note.md"


def test_internal_research_note_ingests_converts_and_registers_strategy_spec(tmp_path, monkeypatch) -> None:
    source_client = _source_ingest_client(tmp_path, monkeypatch)
    registry_client = TestClient(registry_app)
    reset_store()

    try:
        note_text = FIXTURE_PATH.read_text(encoding="utf-8")
        expected_content_hash = f"sha256:{hashlib.sha256(note_text.encode('utf-8')).hexdigest()}"
        source_record_id = "src-ooda-e2e-001-internal-note"

        ingest_response = source_client.post(
            "/api/source-ingest/source-records",
            json={
                "connector": {
                    "connector_id": "conn-ooda-e2e-internal-notes",
                    "source_type": "internal_note",
                    "provider": "Pantheon Research Ops",
                    "license_scope": "internal",
                },
                "trace_id": "trace-ooda-e2e-001-source-ingest",
                "trigger_type": "manual",
                "records": [
                    {
                        "source_id": source_record_id,
                        "connector_id": "conn-ooda-e2e-internal-notes",
                        "source_type": "internal_note",
                        "title": "TW Equity Momentum Quality Seed",
                        "content_ref": f"file://{FIXTURE_PATH}",
                        "status": "normalized",
                        "trace_id": "trace-ooda-e2e-001-source-record",
                        "metadata": {
                            "body": note_text,
                            "citation_label": "sample_internal_research_note.md#strategy-seed",
                            "access_scope": ["research"],
                            "keywords": ["Taiwan", "TWSE", "TPEx", "momentum", "quality"],
                            "strategy_seed": {
                                "hypothesis": (
                                    "TWSE and TPEx equities with positive 60-day momentum, stable profitability, "
                                    "and moderate volume expansion can rank five-day forward returns."
                                ),
                                "asset_class": ["equity"],
                                "market_scope": ["Taiwan", "TWSE", "TPEx"],
                                "holding_period": "5 trading days",
                                "required_data": [
                                    "point-in-time daily OHLCV",
                                    "adjusted close",
                                    "fundamentals",
                                    "sector classifications",
                                    "five-day forward return labels",
                                ],
                                "backend_hint": "qlib",
                                "feature_hints": [
                                    "60-day momentum",
                                    "return volatility",
                                    "turnover expansion",
                                    "gross margin stability",
                                ],
                                "label_hints": ["five_day_forward_return"],
                                "risk_notes": [
                                    "survivorship bias check",
                                    "corporate-action adjustment check",
                                    "sector neutralization review",
                                ],
                            },
                            "governance": {
                                "direct_execution_allowed": False,
                                "broker_consumption": "not_direct_action",
                            },
                        },
                    }
                ],
            },
        )

        assert ingest_response.status_code == 201, ingest_response.text
        ingest_payload = ingest_response.json()
        assert ingest_payload["run"]["status"] == "completed"
        assert ingest_payload["evidence_refs"]["source_ids"] == [source_record_id]

        readback_response = source_client.get(f"/api/source-ingest/source-records/{source_record_id}")
        assert readback_response.status_code == 200, readback_response.text
        source_record = readback_response.json()["source_record"]
        assert source_record["status"] == "normalized"
        assert source_record["metadata"]["content_hash"] == expected_content_hash

        evidence_bundle_id = ingest_payload["evidence_refs"]["evidence_bundle_id"]
        bundle_response = source_client.get(f"/api/source-ingest/evidence/bundles/{evidence_bundle_id}")
        assert bundle_response.status_code == 200, bundle_response.text
        evidence_bundle = bundle_response.json()["bundle"]

        item_ids = ingest_payload["evidence_refs"]["evidence_item_ids"]
        evidence_items = []
        for evidence_item_id in item_ids:
            item_response = source_client.get(f"/api/source-ingest/evidence/items/{evidence_item_id}")
            assert item_response.status_code == 200, item_response.text
            evidence_items.append(item_response.json()["item"])

        conversion = StrategySpecConversionService(created_by="Codex").convert_source_material(
            evidence_bundle,
            source_records=[source_record],
            evidence_items=evidence_items,
            strategy_id="strat-ooda-e2e-001-tw-momentum-quality",
            title="TW Equity Momentum Quality",
            version="1.0.0",
            created_at="2026-05-16T09:00:00Z",
            metadata={
                "task_id": "OODA-E2E-001",
                "source_record_id": source_record_id,
                "source_content_hash": source_record["metadata"]["content_hash"],
            },
        )

        strategy_spec = conversion.strategy_spec.to_dict()
        validate_strategy_spec_payload(strategy_spec)
        assert strategy_spec["lifecycle_state"] == "draft"
        assert strategy_spec["provenance"]["source_refs"] == [source_record_id]
        assert {"ref": source_record_id, "kind": "source_record"} in strategy_spec["data_dependencies"]
        assert strategy_spec["metadata"]["source_record_id"] == source_record_id
        assert strategy_spec["metadata"]["source_content_hash"] == expected_content_hash
        assert any(ref.get("source_id") == source_record_id for ref in strategy_spec["evidence_refs"])

        lineage_edge = conversion.evidence_code_lineage_edge
        assert lineage_edge["source_ids"] == [source_record_id]
        assert any(ref.get("source_id") == source_record_id for ref in lineage_edge["evidence_refs"])

        registry_response = registry_client.post(
            "/api/registry/strategy-specs",
            json=conversion.registry_payload,
        )

        assert registry_response.status_code == 200, registry_response.text
        registry_entry = registry_response.json()["entry"]
        assert registry_entry["artifact_type"] == "strategy_spec"
        assert registry_entry["artifact_state"] == "draft"
        assert registry_entry["strategy_id"] == strategy_spec["strategy_id"]
        assert registry_entry["lineage"]["source_dataset_refs"] == [source_record_id]
        assert registry_entry["checksum"] == conversion.registry_payload["checksum"]
        assert registry_entry["metadata"]["strategy_spec"]["metadata"]["source_content_hash"] == expected_content_hash
        assert registry_response.json()["deployment_stage"] == "none"
    finally:
        reset_store()


def _source_ingest_client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("SOURCE_INGEST_DATA_DIR", str(tmp_path / "source-ingest"))
    monkeypatch.setenv("SOURCE_INGEST_MAX_RECORDS", "3")
    for key in (
        "SOURCE_INGEST_STORE_PATH",
        "SOURCE_INGEST_CONNECTOR_STORE_PATH",
        "SOURCE_INGEST_EVIDENCE_STORE_PATH",
        "SOURCE_INGEST_DLQ_PATH",
        "SOURCE_INGEST_AUDIT_PATH",
        "SOURCE_INGEST_SCHEDULE_CONFIG_PATH",
    ):
        monkeypatch.delenv(key, raising=False)

    sys.modules.pop("services.source_ingestion.main", None)
    module = importlib.import_module("services.source_ingestion.main")
    module = importlib.reload(module)
    return TestClient(module.app)
