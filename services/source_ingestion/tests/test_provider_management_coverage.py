"""Comprehensive provider management, coverage matrix, and schema reconciliation tests.

Validates SD-SRCM-05 §7.1-§7.6 invariants:
- Catalog entry -> Config template -> Connector definition -> Provider adapter -> Normalized schema
- Fail on unmapped templates, missing adapters, token conflicts, or missing disabled reasons
- Prevent social sources from ever being projected as news
- Verify TDCC and TAIFEX PIT watermarks and official reference truth
- Verify External Alpha DB contract (alpha_signal_record.v1) and fixture-only isolation of example-alpha-db
"""

from __future__ import annotations

import json
from pathlib import Path
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
        "body_hash": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
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
