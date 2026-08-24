"""Comprehensive provider management, coverage matrix, and schema reconciliation tests.

Validates SD-SRCM-05 §7.1-§7.6 invariants:
- Catalog entry -> Config template -> Connector definition -> Provider adapter -> Normalized schema
- Fail on unmapped templates, missing adapters, token conflicts, or missing disabled reasons
- Prevent social sources from ever being projected as news
- Verify TDCC and TAIFEX PIT watermarks and official reference truth
- Verify External Alpha DB contract (alpha_signal_record.v1) and fixture-only isolation of example-alpha-db
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

import pytest
import jsonschema

from services.source_ingestion.connector_definitions import (
    _CANONICAL_DEFINITIONS,
    _DEFINITIONS_BY_ADAPTER,
    _DEFINITIONS_BY_ID,
    ConnectorDefinition,
    DefinitionState,
    get_connector_definition,
)
from services.source_ingestion.connectors import (
    ALPHA_DB_VENDOR_CONNECTOR_ID,
    ALPHA_SIGNAL_RECORD_SCHEMA_VERSION,
    ALPHA_SIGNAL_SCHEMA_HASH,
    AlphaSignalRecord,
    ExternalAlphaDbAdapter,
    SOCIAL_ADMITTED_CONNECTOR_ID,
    SOCIAL_ADMITTED_SCHEMA_HASH,
    SourceEvidenceError,
    SourceRecord,
    SourceType,
    TAIFEX_DERIVATIVES_CONNECTOR_ID,
    TAIFEX_FUTURES_CHIP_SCHEMA_HASH,
    TAIFEX_OPTIONS_CHIP_SCHEMA_HASH,
    TDCC_SHAREHOLDING_CONNECTOR_ID,
    TDCC_SHAREHOLDING_SCHEMA_HASH,
    AdmittedSocialMediaAdapter,
    TaifexDerivativesChipAdapter,
    TdccShareholdingDistributionAdapter,
)
from services.source_ingestion.financial_source_catalog import (
    initial_financial_data_source_config_templates,
    initial_financial_data_source_entries,
)
from services.source_ingestion.provider_adapters import (
    ALLOWED_PROVIDER_ADAPTERS,
    PROVIDER_ADAPTER_ALIASES,
    execute_provider_owned_adapter,
    provider_adapter_tokens,
)


def _load_schema(relative_path: str) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[3]
    schema_path = repo_root / relative_path
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema contract not found: {schema_path}")
    with schema_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _sample_payload_for_adapter(adapter_token: str, dataset: str | None = None) -> list[dict[str, Any]]:
    """Return a minimal valid raw payload for adapter normalized schema verification."""
    if "TaifexDerivativesChipAdapter" in adapter_token:
        if dataset == "taifex_options_chip":
            return [{
                "Date": "2026-08-24",
                "Contract": "TXO",
                "CallVolume": 1000,
                "PutVolume": 1200,
                "CallOpenInterest": 5000,
                "PutOpenInterest": 6000,
                "PutCallRatio": 120.0,
            }]
        return [{
            "Date": "2026-08-24",
            "Contract": "TX",
            "ParticipantGroup": "foreign_investors",
            "LongVolume": 15000,
            "ShortVolume": 12000,
            "NetVolume": 3000,
            "LongOpenInterest": 45000,
            "ShortOpenInterest": 50000,
            "NetOpenInterest": -5000,
        }]
    if "TdccShareholdingDistributionAdapter" in adapter_token:
        return [{
            "Date": "2026-08-21",
            "Code": "2330",
            "HoldLevel": 15,
            "HoldingRange": "1,000,001以上",
            "PeopleCount": 1500,
            "Shares": 20000000000,
            "Percentage": 77.12,
        }]
    if "AdmittedSocialMediaAdapter" in adapter_token:
        return [{
            "id": 12345,
            "body": "$2330 bullish report",
            "created_at": "2026-08-24T12:00:00Z",
            "symbols": ["2330"],
            "user": {"id": 999},
        }]
    if "ExternalAlphaDbAdapter" in adapter_token:
        return [{
            "symbol": "AAPL",
            "date": "2026-08-24 16:00:00",
            "rsi": 58.2,
        }]
    return []


def validate_catalog_template_adapter_normalized_schema(tmpl: dict[str, Any]) -> None:
    """Reconcile template expected_fields with provider adapter normalized output (SD-SRCM-05 §7.1)."""
    fetch = tmpl.get("fetch") or {}
    adapter_token = fetch.get("adapter")
    expected_fields_decl = fetch.get("expected_fields")
    if not expected_fields_decl or not adapter_token:
        return

    connector_id = tmpl.get("connector_id")
    defn = _DEFINITIONS_BY_ID.get(connector_id) if connector_id else None
    if connector_id and not defn:
        raise AssertionError(f"Template {tmpl.get('template_id')} references unknown connector_id '{connector_id}'")

    if defn:
        tmpl_source_type = tmpl.get("source_type")
        if tmpl_source_type:
            assert (
                tmpl_source_type in defn.source_types
                or tmpl_source_type in defn.source_classes
            ), (
                f"Template {tmpl['template_id']} source_type '{tmpl_source_type}' not in definition "
                f"source_types {defn.source_types} or source_classes {defn.source_classes}"
            )

    # Map dataset -> list of expected field names
    dataset_expected_map: dict[str, list[str]] = {}
    if isinstance(expected_fields_decl, dict):
        dataset_expected_map = expected_fields_decl
    elif isinstance(expected_fields_decl, (list, tuple)):
        ds_name = fetch.get("dataset") or (defn.datasets[0] if defn and defn.datasets else "default")
        dataset_expected_map = {ds_name: list(expected_fields_decl)}

    for dataset_name, expected_fields in dataset_expected_map.items():
        if defn and defn.datasets:
            assert dataset_name in defn.datasets, (
                f"Template {tmpl['template_id']} declares expected_fields for dataset '{dataset_name}' "
                f"which is not in definition datasets: {defn.datasets}"
            )

        # Normalize sample payload and verify every expected field is present in normalized row
        sample_payload = _sample_payload_for_adapter(adapter_token, dataset_name)
        if not sample_payload:
            raise AssertionError(f"No sample payload defined for adapter '{adapter_token}' and dataset '{dataset_name}'")

        if "TaifexDerivativesChipAdapter" in adapter_token:
            adapter = TaifexDerivativesChipAdapter()
            rows = adapter.normalized_rows_from_payload(sample_payload, dataset=dataset_name)
            assert len(rows) > 0, f"No normalized rows returned for {dataset_name}"
            row = rows[0]
            for field_name in expected_fields:
                assert field_name in row, (
                    f"Template {tmpl['template_id']} dataset '{dataset_name}' declares expected_field '{field_name}' "
                    f"missing from TAIFEX normalized row keys: {sorted(row.keys())}"
                )
        elif "TdccShareholdingDistributionAdapter" in adapter_token:
            adapter = TdccShareholdingDistributionAdapter()
            rows = adapter.normalized_rows_from_payload(sample_payload)
            assert len(rows) > 0, "No normalized rows returned for TDCC"
            row = rows[0]
            for field_name in expected_fields:
                assert field_name in row, (
                    f"Template {tmpl['template_id']} declares expected_field '{field_name}' "
                    f"missing from TDCC normalized row keys: {sorted(row.keys())}"
                )
        elif "AdmittedSocialMediaAdapter" in adapter_token:
            adapter = AdmittedSocialMediaAdapter()
            rows = adapter.normalized_rows_from_payload(sample_payload, platform="stocktwits")
            assert len(rows) > 0, "No normalized rows returned for Social"
            row = rows[0]
            for field_name in expected_fields:
                assert field_name in row, (
                    f"Template {tmpl['template_id']} declares expected_field '{field_name}' "
                    f"missing from Social normalized row keys: {sorted(row.keys())}"
                )
        elif "ExternalAlphaDbAdapter" in adapter_token:
            adapter = ExternalAlphaDbAdapter()
            records = adapter.normalized_rows_from_payload(sample_payload)
            assert len(records) > 0, "No normalized records returned for Alpha DB"
            rec_dict = records[0].to_dict()
            for field_name in expected_fields:
                assert field_name in rec_dict, (
                    f"Template {tmpl['template_id']} declares expected_field '{field_name}' "
                    f"missing from Alpha DB normalized record fields: {sorted(rec_dict.keys())}"
                )
        else:
            raise AssertionError(f"Unhandled adapter token '{adapter_token}' in normalized schema validator")


def test_catalog_templates_join_connector_definitions_and_allowed_adapters() -> None:
    """Every candidate/supported catalog template must map to a definition and allowlisted adapter."""
    entries_by_id = {entry.data_source_id: entry for entry in initial_financial_data_source_entries()}
    templates = initial_financial_data_source_config_templates()
    allowed_tokens = set(ALLOWED_PROVIDER_ADAPTERS.keys()) | set(PROVIDER_ADAPTER_ALIASES.keys())

    assert len(templates) >= 15

    for tmpl in templates:
        template_id = tmpl["template_id"]
        data_source_id = tmpl["data_source_id"]
        connector_id = tmpl.get("connector_id")
        lifecycle_state = tmpl.get("lifecycle_state")

        # 1. Template must reference a known data_source_id in the catalog
        assert data_source_id in entries_by_id, f"Template {template_id} references unknown data_source_id: {data_source_id}"
        entry = entries_by_id[data_source_id]

        # 2. Template must be listed in the catalog entry's config_template_ids
        config_template_ids = entry.metadata.get("config_template_ids", [])
        assert template_id in config_template_ids, f"Template {template_id} not in entry {data_source_id} config_template_ids: {config_template_ids}"

        # 3. Connector ID must have a canonical ConnectorDefinition
        if connector_id:
            assert connector_id in _DEFINITIONS_BY_ID, f"Connector {connector_id} for {template_id} has no ConnectorDefinition"
            defn = _DEFINITIONS_BY_ID[connector_id]
            assert defn.definition_state in (DefinitionState.SUPPORTED, DefinitionState.EXPERIMENTAL, DefinitionState.DISABLED_BY_BUILD), (
                f"Connector {connector_id} definition state is {defn.definition_state}"
            )

        # 4. If fetch mode is provider_owned_adapter, verify adapter token exists and matches definition
        fetch = tmpl.get("fetch") or {}
        if fetch.get("mode") == "provider_owned_adapter":
            adapter_token = fetch.get("adapter")
            assert adapter_token, f"Template {template_id} specifies provider_owned_adapter without adapter token"
            assert adapter_token in allowed_tokens, f"Template {template_id} references unallowed adapter: {adapter_token}"

            # Verify definition adapter_token matches
            if connector_id and connector_id in _DEFINITIONS_BY_ID:
                canonical_adapter = _DEFINITIONS_BY_ID[connector_id].adapter_token
                resolved_adapter = PROVIDER_ADAPTER_ALIASES.get(adapter_token, adapter_token)
                resolved_canonical = PROVIDER_ADAPTER_ALIASES.get(canonical_adapter, canonical_adapter)
                assert resolved_adapter == resolved_canonical, (
                    f"Template {template_id} adapter {adapter_token} does not match definition {canonical_adapter}"
                )

        # 5. Reconcile declared expected_fields with provider adapter normalized schema (SD §7.1)
        validate_catalog_template_adapter_normalized_schema(tmpl)


def test_disabled_templates_require_non_empty_disabled_reason() -> None:
    """Any disabled template must provide an explicit disabled_reason."""
    templates = initial_financial_data_source_config_templates()
    for tmpl in templates:
        if tmpl.get("lifecycle_state") == "disabled":
            reason = str(tmpl.get("disabled_reason") or "").strip()
            assert reason, f"Disabled template {tmpl['template_id']} is missing disabled_reason"


def test_operator_facing_adapters_have_connector_definitions() -> None:
    """Every allowlisted adapter must be bound to at least one valid canonical definition."""
    definitions_by_token = {defn.adapter_token: defn for defn in _CANONICAL_DEFINITIONS}

    for token, spec in ALLOWED_PROVIDER_ADAPTERS.items():
        assert token in definitions_by_token, f"Adapter {token} has no corresponding ConnectorDefinition"
        defn = definitions_by_token[token]
        assert defn.adapter_token == token
        assert defn.definition_state in (DefinitionState.SUPPORTED, DefinitionState.DISABLED_BY_BUILD)
        assert defn.fingerprint, f"Definition {defn.definition_id} fingerprint is empty"


def test_social_sources_never_projected_as_news() -> None:
    """Strict SD-SRCM-05 §7.4 invariant: social feeds must have dedicated SOCIAL source class and never NEWS."""
    # 1. Social Connector Definition
    social_defn = get_connector_definition(SOCIAL_ADMITTED_CONNECTOR_ID)
    assert social_defn is not None
    assert "social" in social_defn.source_types
    assert "news" not in social_defn.source_types
    assert "social" in social_defn.source_classes
    assert "news" not in social_defn.source_classes

    # 2. Social Catalog Entry
    entries = {entry.data_source_id: entry for entry in initial_financial_data_source_entries()}
    social_entry = entries["ds-admitted-social-sentiment"]
    assert social_entry.source_class.value == "social"
    assert social_entry.source_class.value != "news"
    for ds in social_entry.datasets:
        dataset_class = ds["dataset_class"] if isinstance(ds, dict) else getattr(ds, "dataset_class", "")
        assert dataset_class == "social"
        assert dataset_class != "news"

    # 3. Social Config Template
    templates = {t["template_id"]: t for t in initial_financial_data_source_config_templates()}
    social_tmpl = templates["template-social-admitted-market-discussion"]
    assert social_tmpl["source_type"] == "social"
    assert social_tmpl["source_type"] != "news"

    # 4. Social Adapter
    adapter = AdmittedSocialMediaAdapter()
    conn = adapter.connector()
    assert conn.source_type == SourceType.SOCIAL
    assert conn.source_type != SourceType.NEWS
    assert conn.metadata["source_class"] == "social"


def test_alpha_signal_record_schema_contract_conformance() -> None:
    """Verify Draft-07 JSON Schema conformance for alpha_signal_record.v1 (SD §7.5)."""
    schema = _load_schema("docs/contracts/alpha_signal_record.schema.json")
    jsonschema.Draft7Validator.check_schema(schema)

    valid_record = {
        "schema_version": "alpha_signal_record.v1",
        "alpha_vendor_id": "composite-alpha-research",
        "signal_id": "momentum_quality_factor",
        "signal_version": "v1.0.0",
        "field_schema_version": "v1",
        "universe": ["US_EQUITY", "TW_EQUITY"],
        "entity_id": "2330.TWSE",
        "event_time": "2026-06-10T13:30:00Z",
        "as_of_time": "2026-06-10T13:30:00Z",
        "available_time": "2026-06-10T14:00:00Z",
        "ingest_time": "2026-06-10T14:05:00Z",
        "values": {"factor_score": 1.45, "z_score": 2.10},
        "units": {"factor_score": "score", "z_score": "standard_deviations"},
        "currency": "TWD",
        "corporate_action_policy": "provider_adjusted",
        "survivorship_policy": "point_in_time",
        "license_scope": "vendor",
        "allowed_use": ["research", "experiment"],
        "entitlement_tags": ["alpha_db-research"],
        "provider_record_ref": "ref://composite/mom_qual/2330.TWSE/20260610",
        "body_hash": "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90",
    }
    jsonschema.validate(instance=valid_record, schema=schema)

    # Test that invalid corporate action policy fails schema validation
    invalid_record = dict(valid_record, corporate_action_policy="unsupported_adjustment")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=invalid_record, schema=schema)


def test_example_alpha_db_is_fixture_only_and_never_live() -> None:
    """Verify example-alpha-db cannot be marked configured or live (SD-SRCM-05 §7.5)."""
    # 1. Instantiation with example-alpha-db connector_id must fail
    with pytest.raises(SourceEvidenceError, match="test fixture only"):
        ExternalAlphaDbAdapter(connector_id="example-alpha-db")

    # 2. Parsing payload claiming example-alpha-db must fail
    adapter = ExternalAlphaDbAdapter(connector_id=ALPHA_DB_VENDOR_CONNECTOR_ID)
    with pytest.raises(SourceEvidenceError, match="test fixture only"):
        adapter.records_from_payload([{"entity_id": "2330"}], alpha_vendor_id="example-alpha-db")


def test_tdcc_and_taifex_publication_pit_watermark_and_canary_evidence() -> None:
    """Verify TDCC and TAIFEX publication PIT watermark and bounded canary truth (SD-SRCM-05 §7.2-§7.3)."""
    # TDCC
    tdcc_adapter = TdccShareholdingDistributionAdapter(max_records=5)
    tdcc_conn = tdcc_adapter.connector()
    assert tdcc_conn.connector_id == TDCC_SHAREHOLDING_CONNECTOR_ID
    assert tdcc_conn.metadata["official_reference_truth"] is True
    assert tdcc_conn.metadata["dataset_schema_hash"] == TDCC_SHAREHOLDING_SCHEMA_HASH

    tdcc_sample = [
        {
            "Date": "2026-06-12",
            "Code": "2330",
            "HoldLevel": 15,
            "HoldingRange": "1,000,001以上",
            "PeopleCount": 1500,
            "Shares": 20000000000,
            "Percentage": 77.12,
        }
    ]
    tdcc_records = tdcc_adapter.records_from_payload(tdcc_sample, available_time="2026-06-12T19:00:00Z")
    assert len(tdcc_records) == 1
    assert tdcc_records[0].metadata["available_time"] == "2026-06-12T19:00:00Z"
    assert tdcc_records[0].metadata["normalized_row"]["shares"] == 20000000000

    # TAIFEX
    taifex_adapter = TaifexDerivativesChipAdapter(max_records=5)
    taifex_conn = taifex_adapter.connector()
    assert taifex_conn.connector_id == TAIFEX_DERIVATIVES_CONNECTOR_ID
    assert taifex_conn.metadata["official_reference_truth"] is True
    assert taifex_conn.metadata["dataset_schema_hash"] == TAIFEX_FUTURES_CHIP_SCHEMA_HASH

    taifex_sample = [
        {
            "Date": "2026-06-10",
            "Contract": "TX",
            "ParticipantGroup": "foreign_investors",
            "LongVolume": 15000,
            "ShortVolume": 12000,
            "NetVolume": 3000,
            "LongOpenInterest": 45000,
            "ShortOpenInterest": 50000,
            "NetOpenInterest": -5000,
        }
    ]
    taifex_records = taifex_adapter.records_from_payload(
        taifex_sample,
        dataset="taifex_futures_chip",
        available_time="2026-06-10T16:30:00Z",
    )
    assert len(taifex_records) == 1
    assert taifex_records[0].metadata["available_time"] == "2026-06-10T16:30:00Z"
    assert taifex_records[0].metadata["normalized_row"]["net_open_interest"] == -5000


def test_stocktwits_and_alpha_db_evidence_and_search_canary_readback(tmp_path) -> None:
    """Verify durable source->evidence->search canary readback for StockTwits and External Alpha DB."""
    from services.knowledge.evidence import (
        EvidenceBundleBuilder,
        EvidenceItem,
        JsonlEvidenceRepository,
    )
    from services.search.main import create_app as create_search_app
    from fastapi.testclient import TestClient

    # 1. Social Record (StockTwits)
    social_adapter = AdmittedSocialMediaAdapter()
    social_payload = [
        {
            "id": "st-msg-9901",
            "user": {"id": 12345, "username": "alpha_trader", "official": True},
            "body": "$2330 reported outstanding Q2 earnings and raised full year guidance.",
            "symbols": ["2330", "TSM"],
            "created_at": "2026-06-10T11:00:00Z",
            "available_time": "2026-06-10T11:00:30Z",
            "trust_score": 0.95,
            "sentiment": {"label": "positive", "score": 0.88, "model_version": "fin-bert-sentiment.v1"},
        }
    ]
    social_records = social_adapter.records_from_payload(social_payload, platform="stocktwits")
    assert len(social_records) == 1
    assert social_records[0].metadata["platform"] == "stocktwits"
    assert social_records[0].metadata["trust_score"] == 0.95
    assert social_records[0].metadata["author_id_hash"] != "alpha_trader"  # Hashed

    # 2. Alpha DB Record (FMP / Factor signals)
    alpha_adapter = ExternalAlphaDbAdapter()
    alpha_payload = [
        {
            "alpha_vendor_id": "fmp-alpha-factors",
            "signal_id": "momentum_factor_v1",
            "signal_version": "v1.0",
            "field_schema_version": "v1",
            "universe": ["US_EQUITY", "TW_EQUITY"],
            "entity_id": "2330.TWSE",
            "event_time": "2026-06-10T13:30:00Z",
            "as_of_time": "2026-06-10T13:30:00Z",
            "available_time": "2026-06-10T14:00:00Z",
            "values": {"momentum_score": 2.15, "volatility_score": 0.45},
            "units": {"momentum_score": "z_score", "volatility_score": "standard_deviation"},
            "corporate_action_policy": "provider_adjusted",
            "survivorship_policy": "point_in_time",
            "license_scope": "vendor",
            "allowed_use": ["research", "experiment"],
            "entitlement_tags": ["alpha_db-research"],
            "provider_record_ref": "ref://fmp/mom/2330.TWSE/20260610",
        }
    ]
    alpha_records = alpha_adapter.records_from_payload(
        alpha_payload,
        alpha_vendor_id="fmp-alpha-factors",
        signal_id="momentum_factor_v1",
    )
    assert len(alpha_records) == 1
    assert alpha_records[0].metadata["signal_id"] == "momentum_factor_v1"
    assert alpha_records[0].metadata["governance"]["direct_execution_allowed"] is False

    # 3. Build Evidence Bundles and persist to durable store
    evidence_path = Path(tmp_path) / "source_evidence.jsonl"
    repo = JsonlEvidenceRepository(evidence_path)
    builder = EvidenceBundleBuilder(repo)

    social_item = EvidenceItem(
        evidence_item_id="evi-st-2330-001",
        source_id=social_records[0].source_id,
        item_type="social_post",
        content_ref=social_records[0].content_ref,
        citation_label="StockTwits Discussion 2330 2026-06-10",
        body=str(social_records[0].metadata["body"]),
        event_time="2026-06-10T11:00:00Z",
        available_time="2026-06-10T11:00:30Z",
        confidence=0.95,
        access_scope=("research",),
        metadata={"entitlement_tags": ["social-research"]},
    )
    social_bundle = builder.build_bundle(
        source_records=[social_records[0]],
        evidence_items=[social_item],
        summary="StockTwits sentiment discussion for 2330",
        created_by="source-ingest",
        evidence_bundle_id="evbundle-st-2330-001",
    )
    builder.build_knowledge_object(
        knowledge_object_id="kobj-st-2330-001",
        source_record=social_records[0],
        evidence_item=social_item,
        evidence_bundle=social_bundle,
        title=social_records[0].title,
        text=social_item.body,
        source_type="social",
        keywords=["StockTwits", "2330", "TSM", "earnings", "guidance"],
    )

    alpha_item = EvidenceItem(
        evidence_item_id="evi-fmp-mom-2330-001",
        source_id=alpha_records[0].source_id,
        item_type="alpha_signal",
        content_ref=alpha_records[0].content_ref,
        citation_label="FMP Momentum Signal 2330.TWSE 2026-06-10",
        body="FMP factor signal momentum z-score 2.15 on 2026-06-10.",
        event_time="2026-06-10T13:30:00Z",
        available_time="2026-06-10T14:00:00Z",
        confidence=1.0,
        access_scope=("research",),
        metadata={"entitlement_tags": ["alpha_db-research"]},
    )
    alpha_bundle = builder.build_bundle(
        source_records=[alpha_records[0]],
        evidence_items=[alpha_item],
        summary="FMP momentum alpha signal for 2330.TWSE",
        created_by="source-ingest",
        evidence_bundle_id="evbundle-fmp-mom-2330-001",
    )
    builder.build_knowledge_object(
        knowledge_object_id="kobj-fmp-mom-2330-001",
        source_record=alpha_records[0],
        evidence_item=alpha_item,
        evidence_bundle=alpha_bundle,
        title=alpha_records[0].title,
        text=alpha_item.body,
        source_type="alpha_db",
        keywords=["FMP", "momentum", "alpha", "factor", "2330.TWSE"],
    )

    # 4. Refresh search index and query SearchGateway
    search_app = create_search_app(
        index_store_path=Path(tmp_path) / "search-index.jsonl",
        evidence_store_path=evidence_path,
        materialize_store_path=Path(tmp_path) / "search-materialize.jsonl",
        pipeline_store_path=Path(tmp_path) / "search-pipeline.jsonl",
        freshness_sla_seconds=60,
    )
    search_client = TestClient(search_app)

    refresh_resp = search_client.post("/api/search/index/refresh", json={"triggered_by": "canary_test"})
    assert refresh_resp.status_code == 200

    # Query Social
    st_query = search_client.post(
        "/api/search/query",
        json={
            "request_id": "req-social-canary",
            "query": "StockTwits outstanding earnings guidance",
            "persona_id": "operator-workbench",
            "workspace_id": "research-workbench",
            "source_types": ["social"],
            "access_context": {
                "persona_id": "operator-workbench",
                "workspace_id": "research-workbench",
                "environment": "paper",
                "access_scopes": ["research"],
                "license_scopes": ["community_admitted"],
            },
            "top_k": 5,
        },
    )
    assert st_query.status_code == 200
    st_res = st_query.json()
    assert st_res["index_adapter"]["adapter_state"] == "durable"
    assert len(st_res["results"]) >= 1

    # Query Alpha DB
    alpha_query = search_client.post(
        "/api/search/query",
        json={
            "request_id": "req-alpha-canary",
            "query": "FMP momentum factor signal z-score",
            "persona_id": "operator-workbench",
            "workspace_id": "research-workbench",
            "source_types": ["alpha_db"],
            "access_context": {
                "persona_id": "operator-workbench",
                "workspace_id": "research-workbench",
                "environment": "paper",
                "access_scopes": ["research"],
                "license_scopes": ["vendor"],
            },
            "top_k": 5,
        },
    )
    assert alpha_query.status_code == 200
    alpha_res = alpha_query.json()
    assert alpha_res["index_adapter"]["adapter_state"] == "durable"
    assert len(alpha_res["results"]) >= 1


def test_provider_owned_adapters_execute_bounded_fetches_when_payload_omitted(monkeypatch) -> None:
    """Validate that provider_owned_adapter handlers execute bounded fetches without requiring synthetic payloads (SD-SRCM-05 §7)."""
    monkeypatch.setenv("PANTHEON_EXTERNAL_EGRESS", "allowlist")
    monkeypatch.setenv(
        "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS",
        "smart.tdcc.com.tw,openapi.tdcc.com.tw,openapi.taifex.com.tw,api.stocktwits.com,financialmodelingprep.com",
    )

    # 1. TDCC adapter bounded fetch
    tdcc_adapter = TdccShareholdingDistributionAdapter(max_records=5)
    records = execute_provider_owned_adapter(
        connector=tdcc_adapter.connector(),
        fetch=tdcc_adapter.fetch_config(),
        trace_id="test-tdcc-fetch",
    )
    assert len(records) > 0
    assert records[0].metadata["provider"] == "TDCC"
    assert records[0].metadata["schema_hash"] == TDCC_SHAREHOLDING_SCHEMA_HASH

    # 2. TAIFEX adapter bounded fetch
    taifex_adapter = TaifexDerivativesChipAdapter(max_records=5)
    taifex_records = execute_provider_owned_adapter(
        connector=taifex_adapter.connector(),
        fetch=taifex_adapter.fetch_config(),
        trace_id="test-taifex-fetch",
    )
    assert len(taifex_records) > 0
    assert taifex_records[0].metadata["provider"] == "TAIFEX"
    assert taifex_records[0].metadata["schema_hash"] == TAIFEX_FUTURES_CHIP_SCHEMA_HASH

    # 3. StockTwits adapter bounded fetch (public symbol stream)
    st_adapter = AdmittedSocialMediaAdapter(max_records=5)
    st_records = execute_provider_owned_adapter(
        connector=st_adapter.connector(),
        fetch=st_adapter.fetch_config(),
        trace_id="test-st-fetch",
    )
    assert len(st_records) > 0
    assert st_records[0].metadata["source_class"] == "social"
    assert st_records[0].metadata["schema_hash"] == SOCIAL_ADMITTED_SCHEMA_HASH

    # 4. FMP External Alpha DB adapter without secret ref must raise SourceEvidenceError
    monkeypatch.delenv("ALPHA_DB_API_KEY", raising=False)
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    monkeypatch.delenv("FINANCIAL_MODELING_PREP_API_KEY", raising=False)

    alpha_adapter = ExternalAlphaDbAdapter(max_records=5)
    with pytest.raises(SourceEvidenceError, match="env://ALPHA_DB_API_KEY"):
        execute_provider_owned_adapter(
            connector=alpha_adapter.connector(),
            fetch=alpha_adapter.fetch_config(),
            trace_id="test-alpha-unauth",
        )

    # 5. FMP External Alpha DB adapter with secret resolved internally
    from contextlib import contextmanager
    from unittest.mock import MagicMock

    @contextmanager
    def mock_open_external_url(req, *args, **kwargs):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps([
            {
                "symbol": "AAPL",
                "date": "2026-06-10 00:00:00",
                "rsi": 58.2,
                "event_time": "2026-06-10T13:30:00Z",
                "available_time": "2026-06-10T14:00:00Z",
                "universe": ["US_EQUITY"],
                "values": {"rsi": 58.2},
                "units": {"rsi": "index"},
            }
        ]).encode("utf-8")
        yield mock_resp

    monkeypatch.setenv("ALPHA_DB_API_KEY", "test_key_123")
    monkeypatch.setattr("services.source_ingestion.connectors.alpha_db.open_external_url", mock_open_external_url)

    alpha_records = execute_provider_owned_adapter(
        connector=alpha_adapter.connector(),
        fetch=alpha_adapter.fetch_config(),
        trace_id="test-alpha-auth",
    )
    assert len(alpha_records) > 0
    assert alpha_records[0].metadata["source_class"] == "alpha_signal"


def test_alpha_signal_record_rfc3339_validation_and_universe_normalization() -> None:
    """Validate SD §7.5 AlphaSignalRecord RFC3339 enforcement, string universe normalization, and factor values."""
    # 1. String universe must normalize to single-element tuple, not characters
    rec = AlphaSignalRecord(
        alpha_vendor_id="fmp-alpha-factors",
        signal_id="technical_rsi_14d",
        signal_version="v1",
        field_schema_version="v1",
        universe="US_EQUITY",
        entity_id="AAPL",
        event_time="2026-08-24T16:00:00Z",
        as_of_time="2026-08-24T16:00:00Z",
        available_time="2026-08-24T16:05:00Z",
        values={"rsi": 58.2},
        units={"rsi": "index"},
    )
    assert rec.universe == ("US_EQUITY",)
    assert rec.universe != tuple("US_EQUITY")

    # 2. Invalid timestamps must fail validation
    with pytest.raises(SourceEvidenceError, match="RFC3339"):
        AlphaSignalRecord(
            alpha_vendor_id="fmp-alpha-factors",
            signal_id="technical_rsi_14d",
            signal_version="v1",
            field_schema_version="v1",
            universe=["US_EQUITY"],
            entity_id="AAPL",
            event_time="2026-08-24 16:00:00",  # Non-RFC3339
            as_of_time="2026-08-24T16:00:00Z",
            available_time="2026-08-24T16:05:00Z",
            values={"rsi": 58.2},
            units={"rsi": "index"},
        )

    # 3. FMP adapter normalizes non-RFC3339 date strings and excludes date from values
    adapter = ExternalAlphaDbAdapter()
    fmp_raw = [
        {
            "symbol": "AAPL",
            "date": "2026-08-24 16:00:00",
            "rsi": 58.2,
        }
    ]
    records = adapter.records_from_payload(fmp_raw, signal_id="technical_rsi_14d")
    assert len(records) == 1
    assert records[0].metadata["event_time"] == "2026-08-24T16:00:00Z"
    assert records[0].metadata["signal_id"] == "technical_rsi_14d"
    assert "date" not in records[0].metadata["values"]
    assert "Date" not in records[0].metadata["values"]
    assert records[0].metadata["values"] == {"rsi": 58.2}


def test_stocktwits_payload_normalization_and_platform_sentiment() -> None:
    """Validate StockTwits symbol dict extraction, author id hashing, and platform sentiment."""
    adapter = AdmittedSocialMediaAdapter()

    # 1. StockTwits real payload shape with symbol dicts, user object, and platform sentiment
    st_raw = [
        {
            "id": 55667788,
            "body": "$AAPL breaking out above 230 on heavy volume!",
            "created_at": "2026-08-24T15:30:00Z",
            "user": {
                "id": 998877,
                "username": "breakout_trader",
                "name": "Alex",
                "followers": 1250,
            },
            "symbols": [
                {"id": 686, "symbol": "AAPL", "title": "Apple Inc."},
                {"id": 888, "symbol": "TSM", "title": "Taiwan Semiconductor"},
            ],
            "entities": {
                "sentiment": {
                    "basic": "Bullish",
                }
            },
        }
    ]

    records = adapter.records_from_payload(st_raw, platform="stocktwits")
    assert len(records) == 1
    rec = records[0]

    # Symbols extracted as uppercase strings
    assert rec.metadata["symbols"] == ["AAPL", "TSM"]

    # Author hash based on stable user ID, not mutable full user object
    expected_author_hash = hashlib.sha256(b"998877").hexdigest()[:16]
    assert rec.metadata["author_id_hash"] == expected_author_hash

    # Platform sentiment accurately tagged
    sentiment = rec.metadata["sentiment"]
    assert sentiment["label"] == "bullish"
    assert sentiment["score"] == 1.0
    assert sentiment["model_version"] == "stocktwits_platform_sentiment.v1"
    assert sentiment["is_derived"] is False

    # 2. Neutral post without platform sentiment or NLP model
    st_unlabeled = [
        {
            "id": 55667789,
            "body": "Watching $AAPL into the close.",
            "created_at": "2026-08-24T15:45:00Z",
            "user": {"id": 112233, "username": "watcher"},
            "symbols": ["AAPL"],
        }
    ]
    unlabeled_records = adapter.records_from_payload(st_unlabeled, platform="stocktwits")
    assert unlabeled_records[0].metadata["sentiment"]["label"] == "neutral"
    assert unlabeled_records[0].metadata["sentiment"]["model_version"] == "unspecified"


def test_tdcc_and_taifex_bounded_streaming_and_provenance_urls() -> None:
    """Validate TDCC and TAIFEX bounded reading, early stopping, and real smart/OpenAPI provenance URLs."""
    # 1. TDCC records cite real smart.tdcc endpoint
    tdcc_adapter = TdccShareholdingDistributionAdapter(max_records=5)
    tdcc_records = tdcc_adapter.records_from_payload(
        [{"Date": "2026-08-21", "Code": "2330", "HoldLevel": 15, "PeopleCount": 1500, "Shares": 20000000000, "Percentage": 77.12}]
    )
    assert tdcc_records[0].metadata["api_endpoint"] == "https://smart.tdcc.com.tw/opendata/getOD.ashx?id=1-5"

    # 2. TAIFEX records cite real openapi endpoints
    taifex_adapter = TaifexDerivativesChipAdapter(max_records=5)
    fut_records = taifex_adapter.records_from_payload(
        [{"Date": "2026-08-24", "Contract": "TX", "ParticipantGroup": "foreign_investors", "LongVolume": 1000}],
        dataset="taifex_futures_chip",
    )
    assert fut_records[0].metadata["api_endpoint"] == "https://openapi.taifex.com.tw/v1/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate"

    opt_records = taifex_adapter.records_from_payload(
        [{"Date": "2026-08-24", "Contract": "TXO", "CallVolume": 1000, "PutVolume": 1200}],
        dataset="taifex_options_chip",
    )
    assert opt_records[0].metadata["api_endpoint"] == "https://openapi.taifex.com.tw/v1/PutCallRatio"


def test_social_platform_restriction_and_tombstone_sanitization() -> None:
    """Validate social platform restriction to admitted StockTwits and tombstone privacy sanitization."""
    adapter = AdmittedSocialMediaAdapter()

    # 1. Unadmitted platform must fail closed
    with pytest.raises(SourceEvidenceError, match="not an admitted social provider"):
        adapter.records_from_payload([{"id": "1", "body": "test"}], platform="twitter")

    with pytest.raises(SourceEvidenceError, match="not an admitted social provider"):
        adapter.records_from_payload([{"id": "1", "body": "test"}], platform="reddit")

    # 2. Tombstone record must never retain deleted body or raw user identity
    tombstone_raw = [
        {
            "id": 998811,
            "body": "This was a deleted post containing sensitive text that must be purged",
            "deleted": True,
            "created_at": "2026-08-24T12:00:00Z",
            "user": {
                "id": 445566,
                "username": "secret_user",
                "email": "user@example.com",
            },
        }
    ]
    tombstone_records = adapter.records_from_payload(tombstone_raw, platform="stocktwits")
    assert len(tombstone_records) == 1
    t_rec = tombstone_records[0]
    assert t_rec.metadata["is_tombstone"] is True
    assert t_rec.metadata["body"] == ""  # Purged body
    assert t_rec.metadata["sentiment"]["model_version"] == "tombstone"
    assert "body" not in t_rec.metadata["raw_row"] or t_rec.metadata["raw_row"].get("body") == ""
    assert "user" not in t_rec.metadata["raw_row"]
    assert "secret_user" not in str(t_rec.metadata["raw_row"])
    assert t_rec.metadata["author_id_hash"] == hashlib.sha256(b"445566").hexdigest()[:16]


def test_alpha_signal_calendar_validation_and_schema_pattern() -> None:
    """Validate real RFC3339 calendar date checking and Draft-07 SHA-256 pattern conformance."""
    schema = _load_schema("docs/contracts/alpha_signal_record.schema.json")

    # 1. Non-existent calendar date (e.g. Feb 31) must fail validation
    with pytest.raises(SourceEvidenceError, match="valid calendar date"):
        AlphaSignalRecord(
            alpha_vendor_id="fmp-alpha-factors",
            signal_id="technical_rsi_14d",
            signal_version="v1",
            field_schema_version="v1",
            universe=["US_EQUITY"],
            entity_id="AAPL",
            event_time="2026-02-31T00:00:00Z",  # Invalid calendar date
            as_of_time="2026-08-24T16:00:00Z",
            available_time="2026-08-24T16:05:00Z",
            values={"rsi": 50.0},
            units={"rsi": "index"},
        )

    # 2. Comma-separated universe string normalizes to multi-element tuple
    rec = AlphaSignalRecord(
        alpha_vendor_id="fmp-alpha-factors",
        signal_id="technical_rsi_14d",
        signal_version="v1",
        field_schema_version="v1",
        universe="US_EQUITY, TW_EQUITY",
        entity_id="AAPL",
        event_time="2026-08-24T16:00:00Z",
        as_of_time="2026-08-24T16:00:00Z",
        available_time="2026-08-24T16:05:00Z",
        values={"rsi": 50.0},
        units={"rsi": "index"},
    )
    assert rec.universe == ("US_EQUITY", "TW_EQUITY")
    assert len(rec.body_hash) == 64

    # 3. Conformance to JSON Schema Draft-07 with 64-char body_hash pattern
    jsonschema.validate(instance=rec.to_dict(), schema=schema)


def test_alpha_db_signal_id_resolution_and_unsupported_rejection(monkeypatch) -> None:
    """Validate FMP indicator resolution and rejection of unsupported signal_ids."""
    from services.source_ingestion.connectors.alpha_db import _resolve_fmp_indicator

    # Supported mappings
    ind, period = _resolve_fmp_indicator("technical_sma_50d")
    assert ind == "sma"
    assert period == 50

    ind, period = _resolve_fmp_indicator("technical_rsi_14d")
    assert ind == "rsi"
    assert period == 14

    ind, period = _resolve_fmp_indicator("ema")
    assert ind == "ema"
    assert period == 20

    ind, period = _resolve_fmp_indicator("technical_wma")
    assert ind == "wma"
    assert period == 20

    ind, period = _resolve_fmp_indicator("technical_standarddeviation_20d")
    assert ind == "standarddeviation"
    assert period == 20

    # Unsupported signal_ids must raise SourceEvidenceError (fail-closed, no substring matching)
    with pytest.raises(SourceEvidenceError, match="Unsupported signal_id"):
        _resolve_fmp_indicator("unsupported_sma_garbage")

    with pytest.raises(SourceEvidenceError, match="Unsupported signal_id"):
        _resolve_fmp_indicator("unsupported_random_signal_xyz")

    # Clear any host FMP credentials
    monkeypatch.delenv("ALPHA_DB_API_KEY", raising=False)
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    monkeypatch.delenv("FINANCIAL_MODELING_PREP_API_KEY", raising=False)

    adapter = ExternalAlphaDbAdapter()

    # Signal validation must happen before credential lookup: invalid signal raises Unsupported signal_id
    with pytest.raises(SourceEvidenceError, match="Unsupported signal_id"):
        adapter.fetch_payload(signal_id="unsupported_sma_garbage")

    with pytest.raises(SourceEvidenceError, match="Unsupported signal_id"):
        adapter.fetch_payload(signal_id="unsupported_random_signal_xyz")

    # Valid signal_id with missing credentials raises secret_ref error
    with pytest.raises(SourceEvidenceError, match="env://ALPHA_DB_API_KEY"):
        adapter.fetch_payload(signal_id="technical_rsi_14d")


def test_twse_10mb_limit_and_finmind_bulk_backfill_alias() -> None:
    """Validate TWSE 10MB definition bound and FinMind broker bulk backfill alias resolution."""
    # 1. TWSE definition default limit is 10MB (10485760 bytes)
    twse_defn = get_connector_definition("tw-twse-tpex-official-market")
    assert twse_defn is not None
    assert twse_defn.default_limits["max_bytes"] == 10485760

    # 2. FinMind broker bulk backfill alias resolves to tw-finmind-broker-bulk-parquet
    finmind_alias_defn = get_connector_definition("tw-finmind-broker-bulk-backfill")
    assert finmind_alias_defn is not None
    assert finmind_alias_defn.definition_id == "tw-finmind-broker-bulk-parquet"


def test_taifex_futures_normalization_bounds_and_roll_day() -> None:
    """Validate TAIFEX normalization bounds and contract roll day calculation."""
    adapter = TaifexDerivativesChipAdapter(max_records=2)

    # 10 rows in payload, max_records=2 must bound normalized rows
    payload = [
        {"Date": "2026-08-24", "Contract": f"TX{i}", "ParticipantGroup": "foreign_investors", "LongVolume": 100}
        for i in range(10)
    ]
    rows = adapter.normalized_rows_from_payload(payload, max_records=2)
    assert len(rows) == 2

    # 3rd Wednesday check
    # 2026-08-19 is the 3rd Wednesday of August 2026
    assert adapter.is_contract_roll_day("2026-08-19") is True
    # 2026-08-20 (Thursday) is not
    assert adapter.is_contract_roll_day("2026-08-20") is False
    # Invalid date string returns False without raising
    assert adapter.is_contract_roll_day("invalid-date") is False


def test_catalog_template_adapter_normalized_schema_reconciliation() -> None:
    """Verify SD-SRCM-05 §7.1 reconciliation across all templates declaring expected_fields."""
    templates = initial_financial_data_source_config_templates()
    templates_with_expected_fields = [
        tmpl for tmpl in templates
        if (tmpl.get("fetch") or {}).get("expected_fields")
    ]
    assert len(templates_with_expected_fields) >= 2

    for tmpl in templates_with_expected_fields:
        validate_catalog_template_adapter_normalized_schema(tmpl)


def test_normalized_schema_reconciliation_fails_on_mismatches() -> None:
    """Verify that catalog/template/adapter normalized schema mismatches fail reconciliation (SD-SRCM-05 §7.1)."""
    # 1. TAIFEX futures mismatch: declaring legacy volume / open_interest (instead of long/short/net fields)
    corrupted_futures_tmpl = {
        "template_id": "template-corrupted-taifex-futures",
        "connector_id": "tw-taifex-futures-options-chip",
        "fetch": {
            "mode": "provider_owned_adapter",
            "adapter": "TaifexDerivativesChipAdapter.records_from_payload",
            "datasets": ["taifex_futures_chip"],
            "expected_fields": {
                "taifex_futures_chip": ["contract", "date", "participant_group", "volume", "open_interest"],
            },
        },
    }
    with pytest.raises(AssertionError, match="missing from TAIFEX normalized row keys"):
        validate_catalog_template_adapter_normalized_schema(corrupted_futures_tmpl)

    # 2. TAIFEX options mismatch: declaring participant_group (which options rows do not produce)
    corrupted_options_tmpl = {
        "template_id": "template-corrupted-taifex-options",
        "connector_id": "tw-taifex-futures-options-chip",
        "fetch": {
            "mode": "provider_owned_adapter",
            "adapter": "TaifexDerivativesChipAdapter.records_from_payload",
            "datasets": ["taifex_options_chip"],
            "expected_fields": {
                "taifex_options_chip": ["contract", "date", "participant_group", "call_volume", "put_volume"],
            },
        },
    }
    with pytest.raises(AssertionError, match="missing from TAIFEX normalized row keys"):
        validate_catalog_template_adapter_normalized_schema(corrupted_options_tmpl)

    # 3. TDCC mismatch: declaring non-existent field
    corrupted_tdcc_tmpl = {
        "template_id": "template-corrupted-tdcc",
        "connector_id": "tw-tdcc-shareholding-distribution",
        "fetch": {
            "mode": "provider_owned_adapter",
            "adapter": "TdccShareholdingDistributionAdapter.records_from_payload",
            "dataset": "tdcc_shareholding_distribution",
            "expected_fields": ["holder_level", "people_count", "shares", "non_existent_field_xyz"],
        },
    }
    with pytest.raises(AssertionError, match="missing from TDCC normalized row keys"):
        validate_catalog_template_adapter_normalized_schema(corrupted_tdcc_tmpl)

    # 4. Unknown dataset declaration in template expected_fields
    corrupted_dataset_tmpl = {
        "template_id": "template-corrupted-unknown-ds",
        "connector_id": "tw-taifex-futures-options-chip",
        "fetch": {
            "mode": "provider_owned_adapter",
            "adapter": "TaifexDerivativesChipAdapter.records_from_payload",
            "expected_fields": {
                "unknown_derivatives_dataset": ["contract", "date"],
            },
        },
    }
    with pytest.raises(AssertionError, match="not in definition datasets"):
        validate_catalog_template_adapter_normalized_schema(corrupted_dataset_tmpl)

    # 5. Social mismatch: declaring nonexistent social field
    corrupted_social_tmpl = {
        "template_id": "template-corrupted-social",
        "connector_id": "social-admitted-market-discussion",
        "source_type": "social",
        "fetch": {
            "mode": "provider_owned_adapter",
            "adapter": "AdmittedSocialMediaAdapter.records_from_payload",
            "dataset": "social_admitted_post",
            "expected_fields": ["post_id", "author_id_hash", "nonexistent_social_field"],
        },
    }
    with pytest.raises(AssertionError, match="missing from Social normalized row keys"):
        validate_catalog_template_adapter_normalized_schema(corrupted_social_tmpl)

    # 6. Alpha DB mismatch: declaring nonexistent alpha field
    corrupted_alpha_tmpl = {
        "template_id": "template-corrupted-alpha",
        "connector_id": "alpha-db-vendor-signals",
        "source_type": "alpha_db",
        "fetch": {
            "mode": "provider_owned_adapter",
            "adapter": "ExternalAlphaDbAdapter.records_from_payload",
            "dataset": "alpha_signal_record",
            "expected_fields": ["alpha_vendor_id", "signal_id", "nonexistent_alpha_field"],
        },
    }
    with pytest.raises(AssertionError, match="missing from Alpha DB normalized record fields"):
        validate_catalog_template_adapter_normalized_schema(corrupted_alpha_tmpl)

    # 7. Unhandled adapter token
    corrupted_unhandled_adapter_tmpl = {
        "template_id": "template-corrupted-unhandled-adapter",
        "connector_id": "tw-tdcc-shareholding-distribution",
        "fetch": {
            "mode": "provider_owned_adapter",
            "adapter": "NonExistentAdapter.records_from_payload",
            "dataset": "tdcc_shareholding_distribution",
            "expected_fields": ["holder_level"],
        },
    }
    with pytest.raises(AssertionError, match="No sample payload defined|Unhandled adapter"):
        validate_catalog_template_adapter_normalized_schema(corrupted_unhandled_adapter_tmpl)


def test_alpha_db_rejects_unvalued_signal_rows() -> None:
    """Verify that external alpha DB drops unvalued signal rows instead of fabricating factor_score=0.0."""
    adapter = ExternalAlphaDbAdapter()

    # 1. Payload with no numerical signal fields (only entity / date metadata)
    empty_factor_payload = [
        {"symbol": "AAPL", "date": "2026-08-24 16:00:00"},
        {"symbol": "MSFT", "date": "2026-08-24 16:00:00", "currency": "USD"},
    ]
    rows = adapter.normalized_rows_from_payload(empty_factor_payload)
    assert len(rows) == 0, "Expected empty signal rows to be rejected without fabrication"

    records = adapter.records_from_payload(empty_factor_payload)
    assert len(records) == 0, "Expected empty signal records to be rejected without fabrication"

    # 2. Payload with empty values mapping
    empty_dict_payload = [
        {"symbol": "AAPL", "date": "2026-08-24 16:00:00", "values": {}},
    ]
    assert len(adapter.normalized_rows_from_payload(empty_dict_payload)) == 0

    # 3. Mixed payload: only valid row with numeric factor is kept
    mixed_payload = [
        {"symbol": "AAPL", "date": "2026-08-24 16:00:00", "rsi": 62.5},
        {"symbol": "GOOG", "date": "2026-08-24 16:00:00"},  # no factors -> dropped
    ]
    mixed_records = adapter.records_from_payload(mixed_payload)
    assert len(mixed_records) == 1
    assert mixed_records[0].source_id.startswith("alpha_db:fmp-alpha-factors:technical_rsi_14d:AAPL:")


def test_tdcc_and_taifex_emit_all_declared_pit_fields() -> None:
    """Verify that TDCC and TAIFEX adapters emit valid RFC3339 PIT fields in both records and normalized rows."""
    rfc3339_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")

    # TDCC
    tdcc_adapter = TdccShareholdingDistributionAdapter()
    tdcc_raw = [
        {"Date": "2026-08-21", "Code": "2330", "HoldLevel": 15, "HoldingRange": "1,000,001以上", "PeopleCount": 1500, "Shares": 20000000000, "Percentage": 77.12}
    ]
    tdcc_rows = tdcc_adapter.normalized_rows_from_payload(tdcc_raw)
    assert len(tdcc_rows) == 1
    assert rfc3339_pattern.match(tdcc_rows[0]["event_time"])
    assert rfc3339_pattern.match(tdcc_rows[0]["available_time"])
    assert rfc3339_pattern.match(tdcc_rows[0]["ingest_time"])
    assert tdcc_rows[0]["available_time"] == "2026-08-21T19:00:00Z"

    tdcc_records = tdcc_adapter.records_from_payload(tdcc_raw)
    assert len(tdcc_records) == 1
    assert rfc3339_pattern.match(tdcc_records[0].metadata["event_time"])
    assert rfc3339_pattern.match(tdcc_records[0].metadata["available_time"])
    assert rfc3339_pattern.match(tdcc_records[0].metadata["ingest_time"])

    # TAIFEX Futures
    taifex_adapter = TaifexDerivativesChipAdapter()
    taifex_fut_raw = [
        {"Date": "2026-08-21", "Contract": "TX", "ParticipantGroup": "foreign_investors", "LongVolume": 1000, "ShortVolume": 800, "LongOpenInterest": 5000, "ShortOpenInterest": 4000}
    ]
    fut_rows = taifex_adapter.normalized_rows_from_payload(taifex_fut_raw, dataset="taifex_futures_chip")
    assert len(fut_rows) == 1
    assert rfc3339_pattern.match(fut_rows[0]["event_time"])
    assert rfc3339_pattern.match(fut_rows[0]["available_time"])
    assert rfc3339_pattern.match(fut_rows[0]["ingest_time"])
    assert fut_rows[0]["available_time"] == "2026-08-21T16:30:00Z"

    fut_records = taifex_adapter.records_from_payload(taifex_fut_raw, dataset="taifex_futures_chip")
    assert len(fut_records) == 1
    assert rfc3339_pattern.match(fut_records[0].metadata["event_time"])
    assert rfc3339_pattern.match(fut_records[0].metadata["available_time"])
    assert rfc3339_pattern.match(fut_records[0].metadata["ingest_time"])

    # TAIFEX Options
    taifex_opt_raw = [
        {"Date": "2026-08-21", "Contract": "TXO", "CallVolume": 500, "PutVolume": 600, "CallOpenInterest": 2000, "PutOpenInterest": 2200, "PutCallRatio": 110.0}
    ]
    opt_rows = taifex_adapter.normalized_rows_from_payload(taifex_opt_raw, dataset="taifex_options_chip")
    assert len(opt_rows) == 1
    assert rfc3339_pattern.match(opt_rows[0]["event_time"])
    assert rfc3339_pattern.match(opt_rows[0]["available_time"])
    assert rfc3339_pattern.match(opt_rows[0]["ingest_time"])
    assert opt_rows[0]["available_time"] == "2026-08-21T16:30:00Z"

    opt_records = taifex_adapter.records_from_payload(taifex_opt_raw, dataset="taifex_options_chip")
    assert len(opt_records) == 1
    assert rfc3339_pattern.match(opt_records[0].metadata["event_time"])
    assert rfc3339_pattern.match(opt_records[0].metadata["available_time"])
    assert rfc3339_pattern.match(opt_records[0].metadata["ingest_time"])


def test_taifex_unsupported_dataset_fails_closed():
    """Verify that unsupported TAIFEX datasets are strictly rejected rather than defaulted."""
    adapter = TaifexDerivativesChipAdapter()
    sample_payload = [{"Date": "2026-08-21", "Contract": "TX", "ParticipantGroup": "foreign_investors"}]

    with pytest.raises(SourceEvidenceError, match="unsupported TAIFEX dataset"):
        adapter.fetch_payload(dataset="unsupported_taifex_dataset")

    with pytest.raises(SourceEvidenceError, match="unsupported TAIFEX dataset"):
        adapter.records_from_payload(sample_payload, dataset="unsupported_taifex_dataset")

    with pytest.raises(SourceEvidenceError, match="unsupported TAIFEX dataset"):
        adapter.normalized_rows_from_payload(sample_payload, dataset="unsupported_taifex_dataset")


def test_invalid_rfc3339_pit_fails_closed_across_providers():
    """Verify that invalid RFC3339 timestamps (e.g. malformed or invalid calendar dates) fail closed."""
    # TDCC with invalid available_time
    tdcc_adapter = TdccShareholdingDistributionAdapter()
    invalid_tdcc_raw = [
        {"Date": "2026-08-21", "Code": "2330", "available_time": "not-rfc3339", "HoldLevel": 15, "HoldingRange": "1,000,001以上", "PeopleCount": 1500, "Shares": 20000000000, "Percentage": 77.12}
    ]
    with pytest.raises(SourceEvidenceError, match="must be a valid RFC3339 timestamp"):
        tdcc_adapter.records_from_payload(invalid_tdcc_raw)

    invalid_tdcc_date = [
        {"Date": "2026-02-31", "Code": "2330", "HoldLevel": 15, "HoldingRange": "1,000,001以上", "PeopleCount": 1500, "Shares": 20000000000, "Percentage": 77.12}
    ]
    with pytest.raises(SourceEvidenceError, match="valid RFC3339 timestamp"):
        tdcc_adapter.records_from_payload(invalid_tdcc_date)

    # TAIFEX with invalid available_time
    taifex_adapter = TaifexDerivativesChipAdapter()
    invalid_taifex_raw = [
        {"Date": "2026-08-21", "Contract": "TX", "available_time": "not-rfc3339", "ParticipantGroup": "foreign_investors", "LongVolume": 1000}
    ]
    with pytest.raises(SourceEvidenceError, match="must be a valid RFC3339 timestamp"):
        taifex_adapter.records_from_payload(invalid_taifex_raw, dataset="taifex_futures_chip")

    # Social with invalid available_time
    social_adapter = AdmittedSocialMediaAdapter()
    invalid_social_raw = {
        "messages": [
            {"id": 12345, "body": "test AAPL bullish", "available_time": "invalid-time", "user": {"id": 101}}
        ]
    }
    with pytest.raises(SourceEvidenceError, match="must be a valid RFC3339 timestamp"):
        social_adapter.records_from_payload(invalid_social_raw)

    # Alpha DB with invalid available_time
    alpha_adapter = ExternalAlphaDbAdapter()
    invalid_alpha_raw = {
        "signals": [
            {"entity_id": "AAPL", "available_time": "bad-date-time", "rsi": 65.4}
        ]
    }
    with pytest.raises(SourceEvidenceError, match="must be a valid RFC3339 timestamp"):
        alpha_adapter.records_from_payload(invalid_alpha_raw)


def test_custom_env_secret_ref_resolution(monkeypatch):
    """Verify that configured env secret refs (e.g. env://MY_VAR) are correctly resolved."""
    monkeypatch.setenv("CUSTOM_STOCKTWITS_TOKEN", "st_secret_token_123")
    monkeypatch.setenv("CUSTOM_FMP_TOKEN", "fmp_secret_token_456")

    social_adapter = AdmittedSocialMediaAdapter(secret_ref_id="env://CUSTOM_STOCKTWITS_TOKEN")
    assert social_adapter.resolve_api_key() == "st_secret_token_123"

    alpha_adapter = ExternalAlphaDbAdapter(secret_ref_id="env://CUSTOM_FMP_TOKEN")
    assert alpha_adapter.resolve_api_key() == "fmp_secret_token_456"


def test_management_materialization_config_reconciliation_end_to_end(tmp_path):
    """Verify SD-SRCM-05 §7.1 config reconciliation end-to-end through management command engine."""
    from services.source_ingestion.configured import JsonlConfiguredConnectorStore, JsonlConnectorScheduleStore
    from services.source_ingestion.source_management_store import JsonlSourceManagementStore
    from services.source_ingestion.source_management_commands import SourceCommandEngine
    from services.source_ingestion.source_management_models import (
        SourceManagementCommand,
        CommandType,
        DesiredLifecycleState,
    )
    from services.source_ingestion.provider_adapters import execute_provider_owned_adapter

    mg_store = JsonlSourceManagementStore(tmp_path / "mgmt")
    conn_store = JsonlConfiguredConnectorStore(tmp_path / "conn")
    sched_store = JsonlConnectorScheduleStore(tmp_path / "sched")

    engine = SourceCommandEngine(
        store=mg_store,
        connector_store=conn_store,
        schedule_config_store=sched_store,
    )

    # 1. TDCC reconciliation test
    cmd_tdcc = SourceManagementCommand(
        command_id="cmd-tdcc-1",
        idempotency_key="idem-tdcc-1",
        command_type=CommandType.CREATE,
        expected_revision=None,
        actor={"actor_type": "operator", "actor_id": "op-1", "roles": ["operator"]},
        source_instance_id="tdcc-configured-inst",
        reason="Register TDCC source",
        parameters={
            "definition_id": "tw-tdcc-shareholding-distribution",
            "connector_id": "tw-tdcc-shareholding-distribution",
            "schedule": {"cadence": "0 19 * * 5"},
            "connector_config": {
                "public": {
                    "symbols": ["2330", "2317"],
                    "source_dataset": "TDCC_OD_1-5",
                    "max_records": 50,
                }
            },
            "limits": {"max_records": 50, "max_bytes": 5242880, "timeout_seconds": 20},
        },
    )
    receipt_tdcc = engine.execute_command(cmd_tdcc)
    assert receipt_tdcc.status.value == "succeeded"

    cfg_tdcc = conn_store.get_config("tw-tdcc-shareholding-distribution")
    assert cfg_tdcc is not None
    conn_tdcc = cfg_tdcc.connector
    fetch_tdcc = cfg_tdcc.fetch
    assert fetch_tdcc["adapter_config"]["symbols"] == ["2330", "2317"]
    assert fetch_tdcc["adapter_config"]["source_dataset"] == "TDCC_OD_1-5"
    assert fetch_tdcc["request"]["symbols"] == ["2330", "2317"]

    # Execute TDCC with payload and verify filtering by reconciled symbols
    tdcc_payload = [
        {"Date": "2026-08-21", "Code": "2330", "HoldLevel": 15, "HoldingRange": "1,000,001以上", "PeopleCount": 1500, "Shares": 20000000000, "Percentage": 77.12},
        {"Date": "2026-08-21", "Code": "2454", "HoldLevel": 15, "HoldingRange": "1,000,001以上", "PeopleCount": 500, "Shares": 5000000000, "Percentage": 55.0},
    ]
    records_tdcc = execute_provider_owned_adapter(
        connector=conn_tdcc,
        fetch={**fetch_tdcc, "request": {**fetch_tdcc["request"], "payload": tdcc_payload}},
        trace_id="trace-tdcc",
    )
    assert len(records_tdcc) == 1
    assert "2330" in records_tdcc[0].title

    # 2. TAIFEX reconciliation test
    cmd_taifex = SourceManagementCommand(
        command_id="cmd-taifex-1",
        idempotency_key="idem-taifex-1",
        command_type=CommandType.CREATE,
        expected_revision=None,
        actor={"actor_type": "operator", "actor_id": "op-1", "roles": ["operator"]},
        source_instance_id="taifex-configured-inst",
        reason="Register TAIFEX source",
        parameters={
            "definition_id": "tw-taifex-futures-options-chip",
            "connector_id": "tw-taifex-futures-options-chip",
            "schedule": {"cadence": "0 16 * * 1-5"},
            "connector_config": {
                "public": {
                    "dataset": "taifex_options_chip",
                    "contracts": ["TXO"],
                    "max_records": 25,
                }
            },
            "limits": {"max_records": 25, "max_bytes": 2097152, "timeout_seconds": 20},
        },
    )
    receipt_taifex = engine.execute_command(cmd_taifex)
    assert receipt_taifex.status.value == "succeeded"

    cfg_taifex = conn_store.get_config("tw-taifex-futures-options-chip")
    assert cfg_taifex is not None
    conn_taifex = cfg_taifex.connector
    fetch_taifex = cfg_taifex.fetch
    assert fetch_taifex["adapter_config"]["dataset"] == "taifex_options_chip"
    assert fetch_taifex["adapter_config"]["contracts"] == ["TXO"]
    assert fetch_taifex["request"]["dataset"] == "taifex_options_chip"

    taifex_payload = [
        {"Date": "2026-08-21", "Contract": "TXO", "CallVolume": 500, "PutVolume": 600, "CallOpenInterest": 2000, "PutOpenInterest": 2200, "PutCallRatio": 110.0},
        {"Date": "2026-08-21", "Contract": "TEO", "CallVolume": 50, "PutVolume": 60, "CallOpenInterest": 200, "PutOpenInterest": 220, "PutCallRatio": 100.0},
    ]
    records_taifex = execute_provider_owned_adapter(
        connector=conn_taifex,
        fetch={**fetch_taifex, "request": {**fetch_taifex["request"], "payload": taifex_payload}},
        trace_id="trace-taifex",
    )
    assert len(records_taifex) == 1
    assert "TXO" in records_taifex[0].title

    # 3. Social reconciliation test
    cmd_social = SourceManagementCommand(
        command_id="cmd-social-1",
        idempotency_key="idem-social-1",
        command_type=CommandType.CREATE,
        expected_revision=None,
        actor={"actor_type": "operator", "actor_id": "op-1", "roles": ["operator"]},
        source_instance_id="social-configured-inst",
        reason="Register Social source",
        parameters={
            "definition_id": "social-admitted-market-discussion",
            "connector_id": "social-admitted-market-discussion",
            "schedule": {"cadence": "0 * * * *"},
            "connector_config": {
                "secret_ref_id": "env://CUSTOM_STOCKTWITS_KEY",
                "public": {
                    "platform": "stocktwits",
                    "symbols": ["AAPL"],
                    "max_records": 10,
                }
            },
            "limits": {"max_records": 10, "max_bytes": 2097152, "timeout_seconds": 20},
        },
    )
    receipt_social = engine.execute_command(cmd_social)
    assert receipt_social.status.value == "succeeded"

    cfg_social = conn_store.get_config("social-admitted-market-discussion")
    assert cfg_social is not None
    conn_social = cfg_social.connector
    fetch_social = cfg_social.fetch
    assert fetch_social["adapter_config"]["secret_ref_id"] == "env://CUSTOM_STOCKTWITS_KEY"
    assert fetch_social["adapter_config"]["symbols"] == ["AAPL"]

    social_payload = {
        "messages": [
            {"id": 1001, "body": "bullish on AAPL!", "symbols": [{"symbol": "AAPL"}], "user": {"id": 1}},
            {"id": 1002, "body": "bearish on TSLA!", "symbols": [{"symbol": "TSLA"}], "user": {"id": 2}},
        ]
    }
    records_social = execute_provider_owned_adapter(
        connector=conn_social,
        fetch={**fetch_social, "request": {**fetch_social["request"], "payload": social_payload}},
        trace_id="trace-social",
    )
    assert len(records_social) == 1
    assert records_social[0].metadata["post_id"] == "1001"

    # 4. Alpha DB reconciliation test
    cmd_alpha = SourceManagementCommand(
        command_id="cmd-alpha-1",
        idempotency_key="idem-alpha-1",
        command_type=CommandType.CREATE,
        expected_revision=None,
        actor={"actor_type": "operator", "actor_id": "op-1", "roles": ["operator"]},
        source_instance_id="alpha-configured-inst",
        reason="Register Alpha DB source",
        parameters={
            "definition_id": "alpha-db-vendor-signals",
            "connector_id": "alpha-db-vendor-signals",
            "schedule": {"cadence": "0 0 * * *"},
            "connector_config": {
                "secret_ref_id": "env://CUSTOM_ALPHA_KEY",
                "public": {
                    "alpha_vendor_id": "fmp-alpha-factors",
                    "signal_id": "technical_sma_20d",
                    "signal_version": "v2",
                    "field_schema_version": "v2",
                    "universe": ["US_MEGA"],
                    "max_records": 15,
                }
            },
            "limits": {"max_records": 15, "max_bytes": 4194304, "timeout_seconds": 25},
        },
    )
    receipt_alpha = engine.execute_command(cmd_alpha)
    assert receipt_alpha.status.value == "succeeded"

    cfg_alpha = conn_store.get_config("alpha-db-vendor-signals")
    assert cfg_alpha is not None
    conn_alpha = cfg_alpha.connector
    fetch_alpha = cfg_alpha.fetch
    assert fetch_alpha["adapter_config"]["secret_ref_id"] == "env://CUSTOM_ALPHA_KEY"
    assert fetch_alpha["adapter_config"]["signal_id"] == "technical_sma_20d"
    assert fetch_alpha["adapter_config"]["universe"] == ["US_MEGA"]

    alpha_payload = {
        "signals": [
            {"entity_id": "AAPL", "sma": 180.5},
            {"entity_id": "MSFT", "sma": 420.0},
        ]
    }
    records_alpha = execute_provider_owned_adapter(
        connector=conn_alpha,
        fetch={**fetch_alpha, "request": {**fetch_alpha["request"], "payload": alpha_payload}},
        trace_id="trace-alpha",
    )
    assert len(records_alpha) == 2
    assert records_alpha[0].metadata["signal_id"] == "technical_sma_20d"
    assert records_alpha[0].metadata["universe"] == ["US_MEGA"]


def test_create_fails_closed_on_unsupported_source_class_type_or_dataset(tmp_path):
    """Verify that CREATE reconciles requested source_class/source_type/dataset against definition and fails closed."""
    from services.source_ingestion.configured import JsonlConfiguredConnectorStore, JsonlConnectorScheduleStore
    from services.source_ingestion.source_management_store import JsonlSourceManagementStore
    from services.source_ingestion.source_management_commands import SourceCommandEngine
    from services.source_ingestion.source_management_models import (
        SourceManagementCommand,
        CommandType,
        SourceManagementContractError,
    )

    mg_store = JsonlSourceManagementStore(tmp_path / "mgmt")
    conn_store = JsonlConfiguredConnectorStore(tmp_path / "conn")
    sched_store = JsonlConnectorScheduleStore(tmp_path / "sched")
    engine = SourceCommandEngine(
        store=mg_store,
        connector_store=conn_store,
        schedule_config_store=sched_store,
    )

    # 1. Negative test: social-admitted-market-discussion with source_class="news" must fail closed
    cmd_bad_social_class = SourceManagementCommand(
        command_id="cmd-bad-soc-class",
        idempotency_key="idem-bad-soc-class",
        command_type=CommandType.CREATE,
        expected_revision=None,
        actor={"actor_type": "operator", "actor_id": "op-1", "roles": ["operator"]},
        source_instance_id="bad-soc-class-inst",
        reason="Register with invalid source_class",
        parameters={
            "definition_id": "social-admitted-market-discussion",
            "source_class": "news",  # Invalid! Definition only supports "social"
        },
    )
    with pytest.raises(SourceManagementContractError, match="Requested source_class 'news' is not supported by definition 'social-admitted-market-discussion'"):
        engine.execute_command(cmd_bad_social_class)

    # 2. Negative test: twse market with source_type="social" must fail closed
    cmd_bad_twse_type = SourceManagementCommand(
        command_id="cmd-bad-twse-type",
        idempotency_key="idem-bad-twse-type",
        command_type=CommandType.CREATE,
        expected_revision=None,
        actor={"actor_type": "operator", "actor_id": "op-1", "roles": ["operator"]},
        source_instance_id="bad-twse-type-inst",
        reason="Register with invalid source_type",
        parameters={
            "definition_id": "tw-twse-tpex-official-market",
            "source_type": "social",  # Invalid! Definition only supports "market"
        },
    )
    with pytest.raises(SourceManagementContractError, match="Requested source_type 'social' is not supported by definition 'tw-twse-tpex-official-market'"):
        engine.execute_command(cmd_bad_twse_type)

    # 3. Negative test: twse market with invalid dataset must fail closed
    cmd_bad_dataset = SourceManagementCommand(
        command_id="cmd-bad-twse-ds",
        idempotency_key="idem-bad-twse-ds",
        command_type=CommandType.CREATE,
        expected_revision=None,
        actor={"actor_type": "operator", "actor_id": "op-1", "roles": ["operator"]},
        source_instance_id="bad-twse-ds-inst",
        reason="Register with invalid dataset",
        parameters={
            "definition_id": "tw-twse-tpex-official-market",
            "datasets": ["unsupported_crypto_dataset"],
        },
    )
    with pytest.raises(SourceManagementContractError, match="Dataset 'unsupported_crypto_dataset' is not supported"):
        engine.execute_command(cmd_bad_dataset)


def test_create_rejects_example_alpha_db_at_management_creation(tmp_path):
    """Verify that example-alpha-db is rejected immediately at management CREATE."""
    from services.source_ingestion.configured import JsonlConfiguredConnectorStore, JsonlConnectorScheduleStore
    from services.source_ingestion.source_management_store import JsonlSourceManagementStore
    from services.source_ingestion.source_management_commands import SourceCommandEngine
    from services.source_ingestion.source_management_models import (
        SourceManagementCommand,
        CommandType,
        SourceManagementContractError,
    )

    mg_store = JsonlSourceManagementStore(tmp_path / "mgmt")
    conn_store = JsonlConfiguredConnectorStore(tmp_path / "conn")
    sched_store = JsonlConnectorScheduleStore(tmp_path / "sched")
    engine = SourceCommandEngine(
        store=mg_store,
        connector_store=conn_store,
        schedule_config_store=sched_store,
    )

    # 1. Reject source_instance_id="example-alpha-db"
    cmd_id_rejection = SourceManagementCommand(
        command_id="cmd-ex-alpha-1",
        idempotency_key="idem-ex-alpha-1",
        command_type=CommandType.CREATE,
        expected_revision=None,
        actor={"actor_type": "operator", "actor_id": "op-1", "roles": ["operator"]},
        source_instance_id="example-alpha-db",
        reason="Attempting to register example-alpha-db instance",
        parameters={
            "definition_id": "alpha-db-vendor-signals",
            "connector_id": "valid-alpha-id",
        },
    )
    with pytest.raises(SourceManagementContractError, match="example-alpha-db"):
        engine.execute_command(cmd_id_rejection)

    # 2. Reject connector_id="example-alpha-db"
    cmd_conn_rejection = SourceManagementCommand(
        command_id="cmd-ex-alpha-2",
        idempotency_key="idem-ex-alpha-2",
        command_type=CommandType.CREATE,
        expected_revision=None,
        actor={"actor_type": "operator", "actor_id": "op-1", "roles": ["operator"]},
        source_instance_id="inst-alpha-2",
        reason="Attempting to register connector with example-alpha-db id",
        parameters={
            "definition_id": "alpha-db-vendor-signals",
            "connector_id": "example-alpha-db",
        },
    )
    with pytest.raises(SourceManagementContractError, match="example-alpha-db"):
        engine.execute_command(cmd_conn_rejection)

    # 3. Reject alpha_vendor_id="example-alpha-db"
    cmd_vendor_rejection = SourceManagementCommand(
        command_id="cmd-ex-alpha-3",
        idempotency_key="idem-ex-alpha-3",
        command_type=CommandType.CREATE,
        expected_revision=None,
        actor={"actor_type": "operator", "actor_id": "op-1", "roles": ["operator"]},
        source_instance_id="inst-alpha-3",
        reason="Attempting to register with example-alpha-db vendor",
        parameters={
            "definition_id": "alpha-db-vendor-signals",
            "alpha_vendor_id": "example-alpha-db",
        },
    )
    with pytest.raises(SourceManagementContractError, match="example-alpha-db"):
        engine.execute_command(cmd_vendor_rejection)


def test_materialize_connector_runtime_derives_source_type_from_definition(tmp_path):
    """Verify that _materialize_connector_runtime derives source_type from definition (e.g. social, alpha_db)."""
    from services.source_ingestion.configured import JsonlConfiguredConnectorStore, JsonlConnectorScheduleStore
    from services.source_ingestion.source_management_store import JsonlSourceManagementStore
    from services.source_ingestion.source_management_commands import SourceCommandEngine
    from services.source_ingestion.source_management_models import (
        SourceManagementCommand,
        CommandType,
    )

    mg_store = JsonlSourceManagementStore(tmp_path / "mgmt")
    conn_store = JsonlConfiguredConnectorStore(tmp_path / "conn")
    sched_store = JsonlConnectorScheduleStore(tmp_path / "sched")
    engine = SourceCommandEngine(
        store=mg_store,
        connector_store=conn_store,
        schedule_config_store=sched_store,
    )

    # 1. Social connector materialization derives source_type="social"
    cmd_social = SourceManagementCommand(
        command_id="cmd-mat-soc",
        idempotency_key="idem-mat-soc",
        command_type=CommandType.CREATE,
        expected_revision=None,
        actor={"actor_type": "operator", "actor_id": "op-1", "roles": ["operator"]},
        source_instance_id="mat-soc-inst",
        reason="Create social source",
        parameters={
            "definition_id": "social-admitted-market-discussion",
            "connector_id": "mat-soc-conn",
            "connector_config": {
                "public": {
                    "platform": "stocktwits",
                    "symbols": ["AAPL"],
                }
            },
        },
    )
    engine.execute_command(cmd_social)
    cfg_soc = conn_store.get_config("mat-soc-conn")
    assert cfg_soc is not None
    assert cfg_soc.connector.source_type.value == "social"

    # 2. Alpha DB connector materialization derives source_type="alpha_db"
    cmd_alpha = SourceManagementCommand(
        command_id="cmd-mat-alpha",
        idempotency_key="idem-mat-alpha",
        command_type=CommandType.CREATE,
        expected_revision=None,
        actor={"actor_type": "operator", "actor_id": "op-1", "roles": ["operator"]},
        source_instance_id="mat-alpha-inst",
        reason="Create alpha DB source",
        parameters={
            "definition_id": "alpha-db-vendor-signals",
            "connector_id": "mat-alpha-conn",
            "connector_config": {
                "secret_ref_id": "env://ALPHA_KEY",
                "public": {
                    "alpha_vendor_id": "fmp-alpha-factors",
                    "signal_id": "technical_sma_20d",
                }
            },
        },
    )
    engine.execute_command(cmd_alpha)
    cfg_alpha = conn_store.get_config("mat-alpha-conn")
    assert cfg_alpha is not None
    assert cfg_alpha.connector.source_type.value == "alpha_db"


def test_strict_config_key_reconciliation_and_declarations(tmp_path):
    """Verify that all definition config schemas declare necessary adapter keys and reject undeclared keys."""
    from services.source_ingestion.connector_definitions import (
        DEPLOYED_CONNECTOR_DEFINITIONS,
        get_connector_definition,
    )
    from services.source_ingestion.configured import JsonlConfiguredConnectorStore, JsonlConnectorScheduleStore
    from services.source_ingestion.source_management_store import JsonlSourceManagementStore
    from services.source_ingestion.source_management_commands import SourceCommandEngine
    from services.source_ingestion.source_management_models import (
        SourceManagementCommand,
        CommandType,
        SourceManagementContractError,
    )

    # 1. Verify TDCC definition config_schema declares source_dataset
    tdcc_defn = get_connector_definition("tw-tdcc-shareholding-distribution")
    assert tdcc_defn is not None
    assert "source_dataset" in tdcc_defn.config_schema.get("properties", {})

    # 2. Verify Alpha DB definition config_schema declares signal_version and field_schema_version
    alpha_defn = get_connector_definition("alpha-db-vendor-signals")
    assert alpha_defn is not None
    assert "signal_version" in alpha_defn.config_schema.get("properties", {})
    assert "field_schema_version" in alpha_defn.config_schema.get("properties", {})

    # 3. Verify all deployed definitions have clean property mappings
    for defn in DEPLOYED_CONNECTOR_DEFINITIONS:
        props = defn.config_schema.get("properties", {})
        assert isinstance(props, dict)

    mg_store = JsonlSourceManagementStore(tmp_path / "mgmt")
    conn_store = JsonlConfiguredConnectorStore(tmp_path / "conn")
    sched_store = JsonlConnectorScheduleStore(tmp_path / "sched")
    engine = SourceCommandEngine(
        store=mg_store,
        connector_store=conn_store,
        schedule_config_store=sched_store,
    )

    # 4. Negative test: Undeclared config key in connector_config must fail closed on CREATE
    cmd_undeclared_key = SourceManagementCommand(
        command_id="cmd-undec-1",
        idempotency_key="idem-undec-1",
        command_type=CommandType.CREATE,
        expected_revision=None,
        actor={"actor_type": "operator", "actor_id": "op-1", "roles": ["operator"]},
        source_instance_id="undec-inst-1",
        reason="Attempting to register undeclared config key",
        parameters={
            "definition_id": "tw-tdcc-shareholding-distribution",
            "connector_config": {
                "public": {
                    "undeclared_malicious_key": "unsupported_value",
                }
            },
        },
    )
    with pytest.raises(SourceManagementContractError, match="Config key 'undeclared_malicious_key' is not declared in definition"):
        engine.execute_command(cmd_undeclared_key)



