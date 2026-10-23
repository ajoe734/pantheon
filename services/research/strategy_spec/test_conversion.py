"""Tests for Source/Evidence -> StrategySpec conversion."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.knowledge.evidence.models import EvidenceBundle, EvidenceItem
from services.registry.service import app as registry_app
from services.governance.test_approval_authority import configure_registry_unit_auth, registry_unit_headers
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


def test_converted_registry_payload_registers_and_advances_to_candidate(monkeypatch) -> None:
    configure_registry_unit_auth(monkeypatch)
    monkeypatch.setenv("REGISTRY_STORE_BACKEND", "memory")
    reset_store()
    try:
        result = StrategySpecConversionService().convert_source_material(
            _bundle(),
            source_records=[_paper_source(), _repo_source()],
            evidence_items=[_paper_item(), _repo_item()],
            created_at="2026-05-16T08:00:00Z",
        )
        client = TestClient(registry_app, headers=registry_unit_headers())

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
            json={
                **result.candidate_advance_request,
                "expected_artifact_state": created["entry"]["artifact_state"],
                "expected_version": created["entry"]["version"],
                "expected_updated_at": created["entry"]["updated_at"],
            },
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


_DATA_SOURCE = "source-ingest:normalized/taiwanstockprice/finmind-daily"


def _recipe(**overrides) -> dict:
    recipe = {
        "kind": "close_to_close_momentum",
        "symbols": ["2330.TW"],
        "bar_frequency": "1d",
        "data_source": _DATA_SOURCE,
        "lookback_bars": 2,
        "momentum_threshold": 0.01,
        "positive_action": "BUY",
        "non_positive_action": "SELL",
        "order_quantity": 1,
        "quantity_type": "SHARES",
    }
    recipe.update(overrides)
    return recipe


def _convert_with_recipe(recipe: dict | None, **kwargs):
    source = _paper_source()
    source.metadata["strategy_seed"]["required_data"] = [_DATA_SOURCE]
    if recipe is not None:
        source.metadata["strategy_seed"]["execution_recipe"] = recipe
    return StrategySpecConversionService().convert_source_material(
        _bundle(),
        source_records=[source, _repo_source()],
        evidence_items=[_paper_item(), _repo_item()],
        created_at="2026-05-16T08:00:00Z",
        **kwargs,
    )


def test_explicit_recipe_roundtrips_into_checksummed_spec_and_drives_execution_artifact() -> None:
    from services.registry.strategy_artifact import (
        build_execution_artifact_registration,
        build_strategy_artifact_registry_payload,
        evaluate_strategy_action,
        strategy_spec_payload_checksum,
    )

    result = _convert_with_recipe(_recipe())
    spec = result.strategy_spec.to_dict()
    payload = result.registry_payload

    assert spec["metadata"]["execution_recipe"] == _recipe()
    assert spec["market_scope"]["symbols"] == ["2330.TW"]
    assert spec["execution_profile"]["quantity_type"] == "SHARES"
    assert payload["checksum"] == strategy_spec_payload_checksum(payload["strategy_spec"])

    spec_entry = {
        "registry_id": "spec-reg-1",
        "version": payload["version"],
        "checksum": payload["checksum"],
        "lineage": payload["lineage"],
        "metadata": {"strategy_spec": payload["strategy_spec"]},
    }
    registration = build_execution_artifact_registration(
        spec_entry, artifact_id="exec-art-1", algorithm_commit="a" * 40
    )
    artifact = registration["strategy_artifact"]
    assert registration["registry_id"] != spec_entry["registry_id"]
    assert artifact["strategy_id"] == spec["strategy_id"]
    assert artifact["lineage"]["source_strategy_spec_id"] == "spec-reg-1"
    assert artifact["lineage"]["source_run_ids"] == payload["lineage"]["source_run_ids"]
    build_strategy_artifact_registry_payload(registration)

    # Known closes: +2% (BUY), -2% (SELL); threshold boundary is non-positive.
    assert evaluate_strategy_action(artifact, [100.0, 102.0]) == "BUY"
    assert evaluate_strategy_action(artifact, [100.0, 98.0]) == "SELL"
    assert evaluate_strategy_action(artifact, [100.0, 101.0]) == "SELL"
    assert artifact["parameters"]["order_quantity"] == 1
    assert artifact["parameters"]["quantity_type"] == "SHARES"


def test_source_without_recipe_stays_valid_research_and_not_executable() -> None:
    from services.registry.strategy_artifact import (
        StrategyArtifactValidationError,
        build_execution_artifact_registration,
    )

    result = _convert_with_recipe(None)
    payload = result.registry_payload
    assert "execution_recipe" not in payload["strategy_spec"]["metadata"]
    with pytest.raises(StrategyArtifactValidationError, match="research-only"):
        build_execution_artifact_registration(
            {
                "registry_id": "spec-reg-1",
                "version": payload["version"],
                "checksum": payload["checksum"],
                "lineage": payload["lineage"],
                "metadata": {"strategy_spec": payload["strategy_spec"]},
            },
            artifact_id="exec-art-1",
            algorithm_commit="a" * 40,
        )


@pytest.mark.parametrize(
    "recipe, reason",
    [
        (_recipe(kind="pairs_zscore"), "unsupported execution_recipe kind"),
        ({k: v for k, v in _recipe().items() if k != "momentum_threshold"}, "missing explicit fields"),
        (_recipe(positive_action="LONG"), "positive_action"),
        (_recipe(order_quantity=0), "order_quantity is not positive"),
        (_recipe(data_source="source-ingest:other"), "not a StrategySpec data dependency"),
    ],
)
def test_unsupported_or_contradictory_recipe_is_rejected_with_reason(recipe, reason) -> None:
    with pytest.raises(StrategySpecConversionError, match=reason):
        _convert_with_recipe(recipe)


def test_recipe_symbols_contradicting_spec_symbols_are_rejected() -> None:
    with pytest.raises(StrategySpecConversionError, match="contradict StrategySpec market_scope.symbols"):
        _convert_with_recipe(_recipe(), metadata={"symbols": ["AAPL"]})
