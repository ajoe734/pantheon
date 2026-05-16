"""Tests for Source/Evidence -> StrategySpec conversion."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.knowledge.evidence.models import EvidenceBundle, EvidenceItem
from services.registry.service import app as registry_app
from services.registry.storage import reset_store
from services.research.strategy_spec.conversion import (
    StrategySpecConversionError,
    StrategySpecConversionService,
)
from services.research.strategy_spec.models import validate_strategy_spec_payload
from services.source_ingestion.connectors import SourceRecord


def test_conversion_service_builds_strategy_spec_and_registry_payload_from_source_material() -> None:
    result = StrategySpecConversionService().convert_source_material(
        _bundle(),
        source_records=[_paper_source(), _repo_source()],
        evidence_items=[_paper_item(), _repo_item()],
        created_at="2026-05-16T08:00:00Z",
    )

    spec = result.strategy_spec.to_dict()
    registry_payload = result.registry_payload

    validate_strategy_spec_payload(spec)
    assert spec["strategy_id"].startswith("strat-a-lightgbm-cross-sectional-alpha-paper-")
    assert spec["lifecycle_state"] == "draft"
    assert spec["market_scope"]["symbols"] == ["RESEARCH_UNIVERSE"]
    assert spec["market_scope"]["frequency"] == "1d"
    assert {"ref": "src-paper-alpha-001", "kind": "source_record"} in spec["data_dependencies"]
    assert {"ref": "src-repo-alpha-001", "kind": "source_record"} in spec["data_dependencies"]
    assert spec["evidence_refs"][0] == {
        "ref_type": "evidence_bundle",
        "ref_id": "evbundle-alpha-001",
        "association": "source_evidence",
    }
    assert spec["code_refs"][0]["path"] == "examples/lightgbm_alpha.py"
    assert spec["metadata"]["source_seed_id"] == result.strategy_spec_seed.seed_id
    assert spec["metadata"]["registry_write_performed"] is False
    assert spec["metadata"]["execution_route"] == "none"

    assert registry_payload["strategy_id"] == spec["strategy_id"]
    assert registry_payload["source_seed_id"] == result.strategy_spec_seed.seed_id
    assert registry_payload["lineage"]["source_run_ids"] == [result.strategy_spec_seed.seed_id]
    assert registry_payload["lineage"]["source_dataset_refs"] == [
        "src-paper-alpha-001",
        "src-repo-alpha-001",
    ]
    assert registry_payload["storage_ref"] == {
        "backend": "inline",
        "path": "$.entry.metadata.strategy_spec",
    }
    assert registry_payload["checksum"].startswith("sha256:")
    assert registry_payload["strategy_spec"] == spec
    assert result.candidate_advance_request == {"target_state": "candidate"}
    assert result.seed_promotion_lineage_edge["edge_type"] == "strategy_spec_seed_promoted"
    assert result.evidence_code_lineage_edge["edge_type"] == "strategy_spec_evidence_code_linked"


def test_converted_registry_payload_registers_and_advances_to_candidate() -> None:
    reset_store()
    try:
        result = StrategySpecConversionService().convert_source_material(
            _bundle(),
            source_records=[_paper_source(), _repo_source()],
            evidence_items=[_paper_item(), _repo_item()],
            created_at="2026-05-16T08:00:00Z",
        )
        client = TestClient(registry_app)

        create_resp = client.post("/api/registry/strategy-specs", json=result.registry_payload)

        assert create_resp.status_code == 200, create_resp.text
        created = create_resp.json()
        registry_id = created["entry"]["registry_id"]
        assert created["entry"]["artifact_type"] == "strategy_spec"
        assert created["entry"]["artifact_state"] == "draft"
        assert created["entry"]["storage_ref"] == result.registry_payload["storage_ref"]
        assert created["entry"]["checksum"] == result.registry_payload["checksum"]
        assert created["entry"]["metadata"]["strategy_spec"]["strategy_id"] == result.strategy_spec.strategy_id

        advance_resp = client.post(
            f"/api/registry/strategy-specs/{registry_id}/advance",
            json=result.candidate_advance_request,
        )

        assert advance_resp.status_code == 200, advance_resp.text
        assert advance_resp.json()["entry"]["artifact_state"] == "candidate"
        assert advance_resp.json()["deployment_stage"] == "none"
    finally:
        reset_store()


def test_rejected_source_cannot_convert_to_strategy_spec() -> None:
    with pytest.raises(StrategySpecConversionError, match="Rejected sources"):
        StrategySpecConversionService().convert_source_material(
            _bundle(),
            source_records=[_paper_source(status="rejected"), _repo_source()],
            evidence_items=[_paper_item(), _repo_item()],
        )


def test_conversion_safety_metadata_cannot_be_overridden() -> None:
    result = StrategySpecConversionService().convert_source_material(
        _bundle(),
        source_records=[_paper_source(), _repo_source()],
        evidence_items=[_paper_item(), _repo_item()],
        metadata={
            "research_only": False,
            "registry_write_performed": True,
            "execution_route": "live",
        },
    )

    metadata = result.strategy_spec.to_dict()["metadata"]
    assert metadata["research_only"] is True
    assert metadata["registry_write_performed"] is False
    assert metadata["execution_route"] == "none"


def _bundle() -> EvidenceBundle:
    return EvidenceBundle(
        evidence_bundle_id="evbundle-alpha-001",
        source_ids=["src-paper-alpha-001", "src-repo-alpha-001"],
        evidence_item_ids=["evi-paper-alpha-001", "evi-repo-alpha-001"],
        summary="Evidence for a LightGBM TW equity cross-sectional alpha.",
        citation_refs=["alpha-paper#abstract", "alpha-repo#example"],
        confidence=0.9,
        license_scope="open",
        access_scope=["research"],
        created_by="Codex",
        trace_refs=["trace-alpha-bundle"],
    )


def _paper_source(status: str = "normalized") -> SourceRecord:
    return SourceRecord(
        source_id="src-paper-alpha-001",
        connector_id="conn-openalex-papers",
        source_type="paper",
        title="A LightGBM cross-sectional alpha paper",
        content_ref="https://doi.org/10.1000/alpha",
        status=status,
        trace_id="trace-paper-source",
        metadata={
            "license_scope": "open",
            "access_scope": ["research"],
            "keywords": ["LightGBM", "cross-sectional alpha"],
            "strategy_seed": {
                "hypothesis": "LightGBM TW equity factors can rank 5-day forward returns.",
                "asset_class": ["equity"],
                "market_scope": ["Taiwan", "TWSE", "TPEx"],
                "holding_period": "5 trading days",
                "required_data": [
                    "point-in-time daily OHLCV",
                    "corporate-action adjusted prices",
                ],
                "backend_hint": "qlib",
                "feature_hints": ["momentum", "volatility", "liquidity"],
                "label_hints": ["5_day_forward_return"],
                "risk_notes": ["survivorship bias check"],
            },
            "governance": {"direct_execution_allowed": False},
        },
    )


def _repo_source() -> SourceRecord:
    return SourceRecord(
        source_id="src-repo-alpha-001",
        connector_id="conn-github-allowlist",
        source_type="repo",
        title="LightGBM alpha implementation",
        content_ref="https://github.com/example/alpha",
        trace_id="trace-repo-source",
        metadata={
            "code_refs": [
                {
                    "path": "examples/lightgbm_alpha.py",
                    "commit": "abc123",
                    "symbol": "build_alpha_features",
                    "line_start": 12,
                    "line_end": 48,
                    "url": "https://github.com/example/alpha/blob/abc123/examples/lightgbm_alpha.py#L12-L48",
                    "association": "strategy_code_reference",
                }
            ],
            "governance": {"direct_execution_allowed": False},
        },
    )


def _paper_item() -> EvidenceItem:
    return EvidenceItem(
        evidence_item_id="evi-paper-alpha-001",
        source_id="src-paper-alpha-001",
        item_type="abstract",
        content_ref="https://doi.org/10.1000/alpha#abstract",
        citation_label="alpha-paper#abstract",
        body="LightGBM ranks TWSE equities with momentum and volatility features.",
        confidence=0.82,
        trace_refs=["trace-paper-evidence"],
    )


def _repo_item() -> EvidenceItem:
    return EvidenceItem(
        evidence_item_id="evi-repo-alpha-001",
        source_id="src-repo-alpha-001",
        item_type="code_snippet",
        content_ref="https://github.com/example/alpha/blob/abc123/examples/lightgbm_alpha.py#L12-L48",
        citation_label="alpha-repo#example",
        body="build_alpha_features computes rolling momentum and volatility factors.",
        confidence=0.78,
        trace_refs=["trace-repo-evidence"],
    )
