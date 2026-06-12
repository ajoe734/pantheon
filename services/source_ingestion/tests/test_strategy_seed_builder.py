from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from services.knowledge.evidence.models import EvidenceBundle, EvidenceItem
from services.source_ingestion.connectors import SourceRecord
from services.source_ingestion.strategy_seed_builder import (
    StrategySpecSeed,
    StrategySpecSeedBuilder,
    StrategySpecSeedError,
    build_strategy_spec_seed_lineage_edge,
)


ROOT = Path(__file__).resolve().parents[3]


def test_builds_strategy_spec_seed_from_evidence_bundle_with_lineage() -> None:
    seed = StrategySpecSeedBuilder().build_seed(
        _bundle(),
        source_records=[_source()],
        evidence_items=[_item()],
        created_at="2026-05-16T07:15:00Z",
    )

    assert seed.source_id == "src-paper-alpha-001"
    assert seed.evidence_bundle_id == "evbundle-alpha-001"
    assert seed.status.value == "draft"
    assert seed.hypothesis == "LightGBM TW equity factors can rank 5-day forward returns."
    assert seed.asset_class == ("equity",)
    assert seed.market_scope == ("Taiwan", "TWSE", "TPEx")
    assert seed.holding_period == "5 trading days"
    assert seed.required_data == ("point-in-time daily OHLCV", "corporate-action adjusted prices")
    assert seed.backend_hint == "qlib"
    assert seed.feature_hints == ("momentum", "volatility", "liquidity")
    assert seed.label_hints == ("5_day_forward_return",)
    assert "survivorship bias check" in seed.risk_notes
    assert seed.confidence == 0.82
    assert seed.negative_memory_match["warning_level"] == "info"
    assert seed.negative_memory_match["similarity"] == 0.0
    assert seed.source_ids == ("src-paper-alpha-001",)
    assert seed.evidence_item_ids == ("evi-alpha-001",)
    assert seed.citation_refs == ("alpha-paper#abstract",)
    assert seed.lineage["evidence_bundle_id"] == "evbundle-alpha-001"
    assert seed.lineage["registry_write_performed"] is False
    assert seed.lineage["execution_route"] == "none"
    assert seed.metadata["research_only"] is True

    schema = json.loads((ROOT / "docs/contracts/strategy_spec_seed.schema.json").read_text(encoding="utf-8"))
    Draft7Validator(schema).validate(seed.to_dict())


def test_strategy_seed_requires_evidence_bundle_id() -> None:
    with pytest.raises(StrategySpecSeedError, match="evidence_bundle_id"):
        StrategySpecSeed(
            seed_id="seed-missing-bundle",
            source_id="src-note-001",
            evidence_bundle_id="",
            hypothesis="A governed note may become a strategy seed.",
            asset_class=["equity"],
            market_scope=["Taiwan"],
            holding_period=None,
            required_data=["governed evidence"],
            backend_hint=None,
            feature_hints=[],
            label_hints=[],
            risk_notes=[],
            confidence=0.7,
        )


def test_rejected_source_record_cannot_build_seed() -> None:
    with pytest.raises(StrategySpecSeedError, match="Rejected sources"):
        StrategySpecSeedBuilder().build_seed(
            _bundle(),
            source_records=[_source(status="rejected")],
            evidence_items=[_item()],
        )


def test_sparse_metadata_seed_infers_available_strategy_hints() -> None:
    bundle = EvidenceBundle(
        evidence_bundle_id="evbundle-sparse-001",
        source_ids=["src-note-sparse-001"],
        evidence_item_ids=["evi-sparse-001"],
        summary="Momentum and volatility features rank TWSE equities by forward return.",
        citation_refs=["note-sparse#1"],
        confidence=0.76,
        license_scope="internal",
        access_scope=["research"],
        created_by="Codex",
    )
    source = SourceRecord(
        source_id="src-note-sparse-001",
        connector_id="conn-internal",
        source_type="internal_note",
        title="Sparse factor note",
        content_ref="note://sparse",
        metadata={
            "keywords": ["momentum", "volatility"],
            "governance": {"direct_execution_allowed": False},
            "allowed_use": ["research", "strategy_seed"],
        },
    )
    item = EvidenceItem(
        evidence_item_id="evi-sparse-001",
        source_id="src-note-sparse-001",
        item_type="text_chunk",
        content_ref="note://sparse#1",
        citation_label="note-sparse#1",
        body="Use daily OHLCV to predict 5-day forward return for cross-sectional ranking.",
        confidence=0.73,
    )

    seed = StrategySpecSeedBuilder().build_seed(bundle, source_records=[source], evidence_items=[item])

    assert seed.backend_hint == "qlib"
    assert "point-in-time OHLCV" in seed.required_data
    assert "forward_return" in seed.label_hints
    assert "source_governance_blocks_direct_execution" in seed.risk_notes
    assert seed.confidence == 0.73


def test_seed_promotion_builds_lineage_edge_without_registry_write() -> None:
    seed = StrategySpecSeedBuilder().build_seed(
        _bundle(),
        source_records=[_source()],
        evidence_items=[_item()],
        created_at="2026-05-16T07:15:00Z",
    )

    edge = build_strategy_spec_seed_lineage_edge(
        seed,
        strategy_spec_id="strat-tw-lightgbm-alpha-v1",
        created_by="Claude",
        created_at="2026-05-16T07:20:00Z",
    )

    assert edge["edge_type"] == "strategy_spec_seed_promoted"
    assert edge["from_id"] == seed.seed_id
    assert edge["to_id"] == "strat-tw-lightgbm-alpha-v1"
    assert edge["evidence_bundle_id"] == "evbundle-alpha-001"
    assert edge["source_ids"] == ["src-paper-alpha-001"]
    assert edge["citation_refs"] == ["alpha-paper#abstract"]
    assert edge["metadata"]["registry_write_authority"] == "registry_service_only"
    assert edge["metadata"]["promotion_requires_review"] is True


def _source(status: str = "normalized") -> SourceRecord:
    return SourceRecord(
        source_id="src-paper-alpha-001",
        connector_id="conn-openalex-papers",
        source_type="paper",
        title="A LightGBM cross-sectional alpha paper",
        content_ref="https://doi.org/10.1000/alpha",
        status=status,
        trace_id="trace-alpha-source",
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
            "allowed_use": ["research", "search_index", "strategy_seed"],
            "governance": {
                "direct_execution_allowed": False,
                "canonical_sink": "SourceRecord/EvidenceBundle",
            },
        },
    )


def _item() -> EvidenceItem:
    return EvidenceItem(
        evidence_item_id="evi-alpha-001",
        source_id="src-paper-alpha-001",
        item_type="abstract",
        content_ref="https://doi.org/10.1000/alpha#abstract",
        citation_label="alpha-paper#abstract",
        body=(
            "LightGBM ranks TWSE equities with momentum, volatility, and liquidity "
            "features using daily OHLCV and 5-day forward return labels."
        ),
        confidence=0.82,
        access_scope=["research"],
        trace_refs=["trace-alpha-evidence"],
    )


def _bundle() -> EvidenceBundle:
    return EvidenceBundle(
        evidence_bundle_id="evbundle-alpha-001",
        source_ids=["src-paper-alpha-001"],
        evidence_item_ids=["evi-alpha-001"],
        summary="Evidence for a LightGBM TW equity cross-sectional alpha.",
        citation_refs=["alpha-paper#abstract"],
        confidence=0.9,
        license_scope="open",
        access_scope=["research"],
        created_by="Codex",
        trace_refs=["trace-alpha-bundle"],
    )
