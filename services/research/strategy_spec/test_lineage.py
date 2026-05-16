"""Tests for StrategySpec evidence/code lineage refs."""

from __future__ import annotations

import pytest

from services.knowledge.evidence.models import EvidenceItem
from services.research.strategy_spec.lineage import (
    StrategySpecLineageError,
    attach_lineage_refs_to_strategy_spec_payload,
    build_strategy_spec_lineage_refs,
)
from services.research.strategy_spec.models import StrategySpec, validate_strategy_spec_payload
from services.source_ingestion.connectors import SourceRecord
from services.source_ingestion.strategy_seed_builder import StrategySpecSeed


def test_builds_strategy_spec_lineage_refs_from_seed_evidence_and_repo_code() -> None:
    refs = build_strategy_spec_lineage_refs(
        _seed(),
        source_records=[_paper_source(), _repo_source()],
        evidence_items=[_paper_item(), _repo_item()],
    )

    patch = refs.to_strategy_spec_patch()

    assert patch["evidence_refs"][0] == {
        "ref_type": "evidence_bundle",
        "ref_id": "evbundle-alpha-001",
        "association": "source_evidence",
    }
    assert patch["evidence_refs"][1]["ref_id"] == "evi-paper-alpha-001"
    assert patch["evidence_refs"][1]["citation_ref"] == "alpha-paper#abstract"
    assert patch["code_refs"] == [
        {
            "repo_ref": "https://github.com/example/alpha",
            "path": "examples/lightgbm_alpha.py",
            "source_id": "src-repo-alpha-001",
            "commit": "abc123",
            "symbol": "build_alpha_features",
            "line_start": 12,
            "line_end": 48,
            "url": "https://github.com/example/alpha/blob/abc123/examples/lightgbm_alpha.py#L12-L48",
            "association": "strategy_code_reference",
        }
    ]

    edge = refs.to_lineage_edge(
        strategy_spec_id="strat-alpha-v1",
        actor_ref="Codex",
        created_at="2026-05-16T07:30:00Z",
    )

    assert edge["edge_type"] == "strategy_spec_evidence_code_linked"
    assert edge["from_type"] == "evidence_bundle"
    assert edge["from_id"] == "evbundle-alpha-001"
    assert edge["to_id"] == "strat-alpha-v1"
    assert edge["actor_ref"] == "Codex"
    assert edge["trace_id"] == "trace-seed-alpha"
    assert edge["evidence_refs"] == patch["evidence_refs"]
    assert edge["code_refs"] == patch["code_refs"]


def test_lineage_helpers_are_exported_from_strategy_spec_package() -> None:
    from services.research.strategy_spec import (
        StrategySpecLineageError as ExportedError,
        StrategySpecLineageRefs,
        attach_lineage_refs_to_strategy_spec_payload as exported_attach,
        build_strategy_spec_lineage_refs as exported_build,
    )

    refs = exported_build(
        _seed(),
        source_records=[_paper_source(), _repo_source()],
        evidence_items=[_paper_item(), _repo_item()],
    )

    assert isinstance(refs, StrategySpecLineageRefs)
    assert ExportedError is StrategySpecLineageError
    assert exported_attach(_strategy_spec_payload(), refs)["code_refs"][0]["path"] == "examples/lightgbm_alpha.py"


def test_attach_lineage_refs_to_strategy_spec_payload_round_trips_contract() -> None:
    refs = build_strategy_spec_lineage_refs(
        _seed(),
        source_records=[_paper_source(), _repo_source()],
        evidence_items=[_paper_item(), _repo_item()],
    )

    payload = attach_lineage_refs_to_strategy_spec_payload(_strategy_spec_payload(), refs)

    assert payload["provenance"]["source_refs"] == [
        "src-paper-alpha-001",
        "src-repo-alpha-001",
    ]
    assert payload["evidence_refs"][0]["ref_type"] == "evidence_bundle"
    assert payload["code_refs"][0]["path"] == "examples/lightgbm_alpha.py"
    validate_strategy_spec_payload(payload)
    assert StrategySpec.from_dict(payload).code_refs[0].line_start == 12


def test_lineage_refs_reject_source_outside_seed_lineage() -> None:
    outside = SourceRecord(
        source_id="src-repo-outside",
        connector_id="conn-github-allowlist",
        source_type="repo",
        title="Outside alpha repo",
        content_ref="https://github.com/example/outside",
        metadata={"path": "outside.py"},
    )

    with pytest.raises(StrategySpecLineageError, match="outside StrategySpecSeed"):
        build_strategy_spec_lineage_refs(
            _seed(),
            source_records=[outside],
            evidence_items=[_paper_item()],
        )


def test_builds_code_refs_from_repo_fallback_and_evidence_item_metadata() -> None:
    repo_source = SourceRecord(
        source_id="src-repo-alpha-001",
        connector_id="conn-github-allowlist",
        source_type="repo",
        title="LightGBM alpha implementation",
        content_ref="https://github.com/example/alpha",
        metadata={
            "path": "src/fallback_alpha.py",
            "commit": "def456",
            "symbol": "fallback_alpha",
            "line_start": 3,
            "line_end": 19,
        },
    )
    repo_item = EvidenceItem(
        evidence_item_id="evi-repo-alpha-001",
        source_id="src-repo-alpha-001",
        item_type="code_snippet",
        content_ref="https://github.com/example/alpha/blob/abc123/examples/lightgbm_alpha.py#L12-L48",
        citation_label="alpha-repo#example",
        body="build_alpha_features computes rolling momentum and volatility factors.",
        metadata={
            "code_ref": {
                "repo_ref": "https://github.com/example/alpha",
                "path": "examples/item_alpha.py",
                "commit_sha": "abc123",
                "function": "build_alpha_features",
                "start_line": 12,
                "end_line": 48,
            }
        },
    )

    refs = build_strategy_spec_lineage_refs(
        _seed(),
        source_records=[_paper_source(), repo_source],
        evidence_items=[_paper_item(), repo_item],
    )

    assert [ref.path for ref in refs.code_refs] == [
        "src/fallback_alpha.py",
        "examples/item_alpha.py",
    ]
    assert refs.code_refs[0].repo_ref == "https://github.com/example/alpha"
    assert refs.code_refs[1].symbol == "build_alpha_features"


def test_lineage_refs_reject_evidence_item_outside_seed_lineage() -> None:
    outside = EvidenceItem(
        evidence_item_id="evi-outside",
        source_id="src-paper-alpha-001",
        item_type="abstract",
        content_ref="https://doi.org/10.1000/outside#abstract",
        citation_label="outside#abstract",
        body="Outside evidence should not attach to this seed.",
    )

    with pytest.raises(StrategySpecLineageError, match="outside StrategySpecSeed"):
        build_strategy_spec_lineage_refs(
            _seed(),
            source_records=[_paper_source()],
            evidence_items=[outside],
        )


def _seed() -> StrategySpecSeed:
    return StrategySpecSeed(
        seed_id="seed-alpha-001",
        source_id="src-paper-alpha-001",
        evidence_bundle_id="evbundle-alpha-001",
        hypothesis="LightGBM TW equity factors can rank 5-day forward returns.",
        asset_class=["equity"],
        market_scope=["Taiwan", "TWSE", "TPEx"],
        holding_period="5 trading days",
        required_data=["point-in-time daily OHLCV"],
        backend_hint="qlib",
        feature_hints=["momentum", "volatility"],
        label_hints=["5_day_forward_return"],
        risk_notes=["survivorship bias check"],
        confidence=0.82,
        source_ids=["src-paper-alpha-001", "src-repo-alpha-001"],
        evidence_item_ids=["evi-paper-alpha-001", "evi-repo-alpha-001"],
        citation_refs=["alpha-paper#abstract", "alpha-repo#example"],
        trace_refs=["trace-seed-alpha"],
        lineage={
            "created_from": "evidence_bundle",
            "evidence_bundle_id": "evbundle-alpha-001",
            "trace_refs": ["trace-bundle-alpha"],
        },
    )


def _paper_source() -> SourceRecord:
    return SourceRecord(
        source_id="src-paper-alpha-001",
        connector_id="conn-openalex-papers",
        source_type="paper",
        title="A LightGBM cross-sectional alpha paper",
        content_ref="https://doi.org/10.1000/alpha",
        trace_id="trace-paper-source",
        metadata={"license_scope": "open", "access_scope": ["research"]},
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
            ]
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


def _strategy_spec_payload() -> dict:
    return {
        "spec_version": "1.0",
        "strategy_id": "strat-alpha-v1",
        "title": "LightGBM Alpha",
        "hypothesis": "LightGBM TW equity factors can rank 5-day forward returns.",
        "objective": "Evaluate source-backed alpha under governed research gates.",
        "market_scope": {
            "symbols": ["RESEARCH_UNIVERSE"],
            "asset_classes": ["equity"],
            "venues": ["TWSE", "TPEx"],
            "frequency": "1d",
        },
        "data_dependencies": [
            {"ref": "src-paper-alpha-001", "kind": "source_record"},
            {"ref": "src-repo-alpha-001", "kind": "source_record"},
        ],
        "execution_profile": {
            "signal_schema_version": "1.0",
            "quantity_type": "PERCENT_PORTFOLIO",
            "execution_mode_hint": "research",
        },
        "evaluation_plan": {
            "metrics": ["sharpe_ratio", "max_drawdown"],
            "candidate_gate": "Schema and evidence lineage review pass.",
        },
        "governance": {
            "approval_required": True,
            "risk_profile": "research_only",
        },
        "provenance": {
            "source_kind": "workflow",
            "created_at": "2026-05-16T07:30:00Z",
        },
    }
