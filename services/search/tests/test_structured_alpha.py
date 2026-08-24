"""Tests for Structured Alpha AST validation, query execution, snapshot receipts, and API endpoints."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft7Validator

from services.search import (
    AlphaDatasetSchema,
    AlphaFieldDef,
    AlphaRecord,
    AlphaSortSpec,
    SearchAccessContext,
    SearchGateway,
    SearchPolicyError,
    SearchRequest,
    StructuredAlphaEngine,
    StructuredAlphaQuery,
)
from services.search.main import create_app


ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = ROOT / "docs" / "contracts" / "alpha_rule_query.schema.json"


def _validate_schema(payload: dict) -> list[str]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft7Validator(schema)
    return sorted(error.message for error in validator.iter_errors(payload))


def _build_test_engine() -> StructuredAlphaEngine:
    engine = StructuredAlphaEngine()
    now = datetime.now(timezone.utc)

    # Register custom alpha dataset
    schema = AlphaDatasetSchema(
        dataset_ref="alpha-momentum-quality-v1",
        fields={
            "quality_score": AlphaFieldDef(name="quality_score", field_type="float", unit="score"),
            "momentum_20d": AlphaFieldDef(name="momentum_20d", field_type="float", unit="ratio"),
            "volatility_60d": AlphaFieldDef(name="volatility_60d", field_type="float", unit="ratio"),
            "market_cap_usd": AlphaFieldDef(name="market_cap_usd", field_type="float", unit="usd"),
            "sector": AlphaFieldDef(name="sector", field_type="str"),
            "is_sp500": AlphaFieldDef(name="is_sp500", field_type="bool"),
        },
        license_scope="internal",
        access_scope=("research", "operator"),
        entitlement_tags=("entitlement:alpha-quant",),
        allowed_universes=("US_EQUITY", "GLOBAL_MACRO"),
        default_citations=("alpha-db:factors-v1",),
    )
    engine.register_schema(schema)

    # Add records
    records = [
        AlphaRecord(
            entity_id="AAPL",
            dataset_ref="alpha-momentum-quality-v1",
            universe="US_EQUITY",
            values={
                "quality_score": 0.92,
                "momentum_20d": 0.08,
                "volatility_60d": 0.18,
                "market_cap_usd": 3000000000000.0,
                "sector": "technology",
                "is_sp500": True,
            },
            event_time=now - timedelta(days=2),
            available_time=now - timedelta(days=1),
            citations=["sec:10-k/aapl"],
        ),
        AlphaRecord(
            entity_id="MSFT",
            dataset_ref="alpha-momentum-quality-v1",
            universe="US_EQUITY",
            values={
                "quality_score": 0.88,
                "momentum_20d": 0.05,
                "volatility_60d": 0.15,
                "market_cap_usd": 2800000000000.0,
                "sector": "technology",
                "is_sp500": True,
            },
            event_time=now - timedelta(days=2),
            available_time=now - timedelta(days=1),
            citations=["sec:10-k/msft"],
        ),
        AlphaRecord(
            entity_id="JNJ",
            dataset_ref="alpha-momentum-quality-v1",
            universe="US_EQUITY",
            values={
                "quality_score": 0.75,
                "momentum_20d": -0.02,
                "volatility_60d": 0.11,
                "market_cap_usd": 400000000000.0,
                "sector": "healthcare",
                "is_sp500": True,
            },
            event_time=now - timedelta(days=2),
            available_time=now - timedelta(days=1),
            citations=["sec:10-k/jnj"],
        ),
        # Record with future available_time (should not match if query as_of <= now)
        AlphaRecord(
            entity_id="FUTURE_CO",
            dataset_ref="alpha-momentum-quality-v1",
            universe="US_EQUITY",
            values={
                "quality_score": 0.99,
                "momentum_20d": 0.50,
                "volatility_60d": 0.10,
                "market_cap_usd": 100000000000.0,
                "sector": "technology",
                "is_sp500": True,
            },
            event_time=now,
            available_time=now + timedelta(days=1),
            citations=["sec:future"],
        ),
    ]
    engine.add_records("alpha-momentum-quality-v1", records)
    return engine


def _default_context() -> SearchAccessContext:
    return SearchAccessContext(
        persona_id="persona-quant",
        workspace_id="workspace-alpha",
        role_refs=["researcher"],
        environment="paper",
        access_scopes=["research", "operator", "public"],
        license_scopes=["internal", "open"],
        entitlements=["entitlement:alpha-quant"],
    )


# ---------------------------------------------------------------------------
# 1. Contract Schema Validation
# ---------------------------------------------------------------------------

def test_alpha_rule_query_schema_validation() -> None:
    valid_query = {
        "schema_version": "alpha_rule_query.v1",
        "dataset_ref": "alpha-momentum-quality-v1",
        "universe": ["US_EQUITY"],
        "as_of": "2026-06-01T00:00:00Z",
        "rule": {
            "op": "and",
            "args": [
                {"op": "gte", "field": "quality_score", "value": 0.8},
                {"op": "gt", "field": "momentum_20d", "value": 0.0},
            ],
        },
        "sort": [{"field": "quality_score", "direction": "desc"}],
        "limit": 50,
    }
    assert _validate_schema(valid_query) == []


# ---------------------------------------------------------------------------
# 2. Query Execution & Operators
# ---------------------------------------------------------------------------

def test_structured_alpha_execution_and_operators() -> None:
    engine = _build_test_engine()
    ctx = _default_context()

    query = StructuredAlphaQuery(
        dataset_ref="alpha-momentum-quality-v1",
        universe=["US_EQUITY"],
        rule={
            "op": "and",
            "args": [
                {"op": "gte", "field": "quality_score", "value": 0.85},
                {"op": "gt", "field": "momentum_20d", "value": 0.0},
                {"op": "eq", "field": "sector", "value": "technology"},
                {"op": "between", "field": "volatility_60d", "value": [0.10, 0.20]},
            ],
        },
        sort=[AlphaSortSpec(field="quality_score", direction="desc")],
        limit=10,
    )

    snapshot = engine.execute(query, ctx)

    # AAPL (0.92) and MSFT (0.88) match; JNJ (0.75) and FUTURE_CO (future available_time) do not
    assert snapshot.matched_entity_ids == ["AAPL", "MSFT"]
    assert len(snapshot.matched_records) == 2
    assert snapshot.citations == ["alpha-db:factors-v1", "sec:10-k/aapl", "sec:10-k/msft"]
    assert snapshot.license_scope == "internal"
    assert "query_fingerprint" in snapshot.to_dict()
    assert "dataset_fingerprint" in snapshot.to_dict()
    assert snapshot.quota_receipt["units_consumed"] == 1
    assert snapshot.cost_receipt["currency"] == "USD"


def test_structured_alpha_or_and_not_operators() -> None:
    engine = _build_test_engine()
    ctx = _default_context()

    query = StructuredAlphaQuery(
        dataset_ref="alpha-momentum-quality-v1",
        universe=["US_EQUITY"],
        rule={
            "op": "or",
            "args": [
                {"op": "eq", "field": "sector", "value": "healthcare"},
                {"op": "gt", "field": "momentum_20d", "value": 0.06},
            ],
        },
        sort=[AlphaSortSpec(field="quality_score", direction="desc")],
        limit=10,
    )

    snapshot = engine.execute(query, ctx)
    # AAPL (momentum 0.08) and JNJ (healthcare) match
    assert snapshot.matched_entity_ids == ["AAPL", "JNJ"]


# ---------------------------------------------------------------------------
# 3. Acceptance 6: Negative Rejection Tests (AST, Fields, Types, Complexity, Future)
# ---------------------------------------------------------------------------

def test_reject_unknown_field() -> None:
    engine = _build_test_engine()
    ctx = _default_context()

    query = StructuredAlphaQuery(
        dataset_ref="alpha-momentum-quality-v1",
        universe=["US_EQUITY"],
        rule={"op": "gt", "field": "non_existent_factor", "value": 1.0},
    )
    with pytest.raises(SearchPolicyError, match="Unknown or missing field"):
        engine.execute(query, ctx)


def test_reject_unknown_operator() -> None:
    engine = _build_test_engine()
    ctx = _default_context()

    query = StructuredAlphaQuery(
        dataset_ref="alpha-momentum-quality-v1",
        universe=["US_EQUITY"],
        rule={"op": "$eval", "field": "quality_score", "value": "1+1"},
    )
    with pytest.raises(SearchPolicyError, match="Unknown or forbidden operator"):
        engine.execute(query, ctx)


def test_reject_type_mismatch() -> None:
    engine = _build_test_engine()
    ctx = _default_context()

    # Pass string to numeric float field
    query = StructuredAlphaQuery(
        dataset_ref="alpha-momentum-quality-v1",
        universe=["US_EQUITY"],
        rule={"op": "gte", "field": "quality_score", "value": "high_score"},
    )
    with pytest.raises(SearchPolicyError, match="Type mismatch"):
        engine.execute(query, ctx)


def test_reject_future_as_of_time() -> None:
    engine = _build_test_engine()
    ctx = _default_context()
    future_time = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()

    query = StructuredAlphaQuery(
        dataset_ref="alpha-momentum-quality-v1",
        universe=["US_EQUITY"],
        as_of=future_time,
        rule={"op": "gte", "field": "quality_score", "value": 0.5},
    )
    with pytest.raises(SearchPolicyError, match="is in the future; lookahead not allowed"):
        engine.execute(query, ctx)


def test_reject_excess_ast_depth() -> None:
    engine = _build_test_engine()
    ctx = _default_context()

    # Build deeply nested AST (> 5 levels)
    deep_rule = {"op": "gte", "field": "quality_score", "value": 0.5}
    for _ in range(6):
        deep_rule = {"op": "and", "args": [deep_rule]}

    query = StructuredAlphaQuery(
        dataset_ref="alpha-momentum-quality-v1",
        universe=["US_EQUITY"],
        rule=deep_rule,
    )
    with pytest.raises(SearchPolicyError, match="AST nesting depth exceeds maximum allowed"):
        engine.execute(query, ctx)


def test_reject_unentitled_dataset() -> None:
    engine = _build_test_engine()
    # Context lacking entitlement:alpha-quant
    unentitled_ctx = SearchAccessContext(
        persona_id="persona-quant",
        workspace_id="workspace-alpha",
        role_refs=["researcher"],
        access_scopes=["research", "operator"],
        license_scopes=["internal"],
        entitlements=[],
    )

    query = StructuredAlphaQuery(
        dataset_ref="alpha-momentum-quality-v1",
        universe=["US_EQUITY"],
        rule={"op": "gte", "field": "quality_score", "value": 0.5},
    )
    with pytest.raises(SearchPolicyError, match="Required entitlement"):
        engine.execute(query, unentitled_ctx)


def test_reject_unbounded_limit() -> None:
    with pytest.raises(SearchPolicyError, match="limit must be between 1 and 1000"):
        StructuredAlphaQuery(
            dataset_ref="alpha-momentum-quality-v1",
            universe=["US_EQUITY"],
            rule={"op": "gte", "field": "quality_score", "value": 0.5},
            limit=5000,
        )


# ---------------------------------------------------------------------------
# 4. HTTP API & Gateway Integration
# ---------------------------------------------------------------------------

def test_http_api_v2_structured_alpha_query(tmp_path: Path) -> None:
    engine = _build_test_engine()
    app = create_app(
        index_store_path=tmp_path / "index.jsonl",
        evidence_store_path=tmp_path / "evidence.jsonl",
        materialize_store_path=tmp_path / "materialize.jsonl",
        pipeline_store_path=tmp_path / "pipeline.jsonl",
        alpha_engine=engine,
    )
    client = TestClient(app)

    response = client.post(
        "/api/search/v2/alpha-query",
        json={
            "schema_version": "alpha_rule_query.v1",
            "dataset_ref": "alpha-momentum-quality-v1",
            "universe": ["US_EQUITY"],
            "rule": {
                "op": "and",
                "args": [
                    {"op": "gte", "field": "quality_score", "value": 0.90},
                ],
            },
            "sort": [{"field": "quality_score", "direction": "desc"}],
            "limit": 10,
            "access_context": {
                "persona_id": "persona-quant",
                "workspace_id": "workspace-alpha",
                "role_refs": ["researcher"],
                "access_scopes": ["research", "operator"],
                "license_scopes": ["internal"],
                "entitlements": ["entitlement:alpha-quant"],
            },
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["retrieval_mode"] == "structured_alpha"
    assert len(data["results"]) == 1
    assert data["results"][0]["result_id"] == "alpha:alpha-momentum-quality-v1:AAPL"
    assert "fingerprints" in data
    assert "query_fingerprint" in data["fingerprints"]
    assert "cost_receipt" in data["fingerprints"]
