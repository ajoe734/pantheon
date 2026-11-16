"""Integration and contract tests for the governed Agora platform source connector (AGORA-PLATFORM-SOURCE-20261116).

Covers:
1. Internal service-token read route on the Agora owner returning decision locks (C4 / A4),
   settled arms, decision journal entries, and servant insights (AgoraInsight) with all fields
   incrementally by cursor, and verifying no other owner reads the Agora database directly.
2. Governed source registration (agora-platform-source) with provenance `agora` and PIT timestamps
   (`event_time`, `available_time`, `ingest_time`) reaching the knowledge base, L2 hypothesis
   extraction / distillation, attribution owner, meeting case files, and imitation contracts
   (`CorrectionTrace`, `PreferenceExample`) through existing paths.
3. Preservation of trader identity (`tenant_id`, `agora_user_id`, `user_id`), `account_id`,
   `broker_ids`, and `raw_text`, with access following existing operator and owner roles.
4. Validation-gate enforcement ensuring no record becomes a strategy, rule, or lesson without
   existing validation gates.
5. Production pull execution against the real Agora owner route with recorded Agora rows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
import urllib.parse

from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft7Validator
import pytest

from services.agora.store import AgoraStore
from services.control_plane.bff.agora.platform_source_router import (
    AgoraPlatformSourceOwner,
    create_platform_source_router,
)
from services.knowledge.evidence import (
    EvidenceBundleBuilder,
    JsonlEvidenceRepository,
)
from services.source_ingestion.configured import (
    ConfiguredConnectorFetcher,
    JsonlConfiguredConnectorStore,
    JsonlConnectorScheduleStore,
)
from services.source_ingestion.connector_definitions import (
    compute_definition_fingerprint,
    deployed_connector_definitions,
    get_connector_definition,
    get_connector_definition_by_adapter,
    validate_connector_definition,
)
from services.source_ingestion.connectors.agora_owner import (
    AGORA_OWNER_ADAPTER_TOKEN,
    AGORA_PLATFORM_SOURCE_CONNECTOR_ID,
    AGORA_PLATFORM_SOURCE_DATASET,
    AGORA_PLATFORM_SOURCE_SCHEMA_HASH,
    AgoraOwnerAdapter,
    check_record_access,
    enforce_validation_gate_before_promotion,
    ingest_agora_records_to_knowledge_base,
    load_agora_platform_source_desired_state,
    project_agora_records_for_attribution,
    project_agora_records_for_imitation,
    project_agora_records_for_meeting_case_file,
)
from services.source_ingestion.connectors.base import SourceEvidenceError
from services.source_ingestion.distillation_worker import (
    DistillationJobQueue,
    DistillationWorker,
)
from services.source_ingestion.strategy_seed_builder import StrategySpecSeedStatus
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore
from services.source_ingestion.ingest_manager import IngestManager
from services.source_ingestion.persona_source_reconciler import SourceProvisioningReconciler
from services.source_ingestion.provider_adapters import execute_provider_owned_adapter

REPO_ROOT = Path(__file__).resolve().parents[3]
TEST_SERVICE_TOKEN = "test-agora-platform-source-service-token"


def _seed_recorded_agora_rows(owner: AgoraPlatformSourceOwner) -> Dict[str, Dict[str, Any]]:
    """Seed recorded Agora rows across all four governed record kinds (C4 / A4 / SD §6 / §11)."""
    # 1. C4 Decision Lock (with 4 comparison arms + unavailable_arms + full trader/account/broker/raw_text fields)
    decision_lock = owner.record_decision_lock(
        {
            "decision_id": "dec-lock-20261116-001",
            "tenant_id": "tenant-alpha",
            "agora_user_id": "trader-alice",
            "user_id": "trader-alice",
            "account_id": "acct-tw-margin-01",
            "broker_ids": ["fubon-sec", "kgi-sec"],
            "decision_time": "2026-11-16T01:15:00Z",
            "data_cutoff": "2026-11-16T01:14:55Z",
            "raw_text": "Trimming 2330.TW into morning gap-up because foreign futures net long contracted.",
            "arms": {
                "human_actual": {
                    "action": "reduce",
                    "instrument": "2330.TW",
                    "quantity": 2000,
                    "rationale": "Trimming 2330.TW into morning gap-up because foreign futures net long contracted.",
                },
                "private_servant": {
                    "servant_id": "servant-alice-01",
                    "suggestion": "hold",
                    "confidence": 0.68,
                    "rationale": "Hold core semiconductor exposure while trend remains intact.",
                },
                "base_strategy": {
                    "strategy_ref": "strat-tw-semis-momentum-v2",
                    "suggestion": "enter",
                    "suggested_size": 4000,
                },
                "committee_variant": None,
            },
            "unavailable_arms": {
                "committee_variant": "not_invoked_for_single_trader_lock",
            },
            "created_at": "2026-11-16T01:15:05Z",
            "available_time": "2026-11-16T01:15:05Z",
        }
    )

    # 2. Settled Arm (A4 broker_settled with outcome + raw_text + account_id + broker_ids)
    settled_arm = owner.record_settled_arm(
        {
            "settled_arm_id": "settled-dec-lock-20261116-001-human",
            "decision_id": "dec-lock-20261116-001",
            "tenant_id": "tenant-alpha",
            "agora_user_id": "trader-alice",
            "user_id": "trader-alice",
            "account_id": "acct-tw-margin-01",
            "broker_ids": ["fubon-sec"],
            "arm_name": "human_actual",
            "execution_observation_kind": "broker_settled",
            "settlement_source": "broker_fill",
            "raw_text": "Filled 2,000 shares reduce on 2330.TW via Fubon; avoided afternoon pullback.",
            "outcome": {
                "instrument": "2330.TW",
                "realized_pnl_bps": 42.5,
                "max_adverse_excursion_bps": -8.0,
                "fill_price": 1045.0,
            },
            "decision_time": "2026-11-16T01:15:00Z",
            "settled_at": "2026-11-16T06:00:00Z",
            "data_cutoff": "2026-11-16T06:00:00Z",
            "available_time": "2026-11-16T06:00:02Z",
        }
    )

    # 3. Decision Journal Entry (trader reflection with raw_text, account_id, broker_ids)
    journal_entry = owner.record_decision_journal_entry(
        {
            "entry_id": "journal-20261116-001",
            "decision_id": "dec-lock-20261116-001",
            "tenant_id": "tenant-alpha",
            "agora_user_id": "trader-alice",
            "user_id": "trader-alice",
            "account_id": "acct-tw-margin-01",
            "broker_ids": ["fubon-sec", "kgi-sec"],
            "title": "Post-close review on 2330.TW futures divergence",
            "raw_text": "When TAIFEX foreign net open interest drops >3,000 contracts while spot gaps up >1.5%, reducing size by half protected gains.",
            "tags": ["2330.TW", "taifex_divergence", "risk_trim"],
            "outcome": {"status": "validated_in_post_close_review", "saved_drawdown_bps": 35.0},
            "event_time": "2026-11-16T06:30:00Z",
            "available_time": "2026-11-16T06:30:05Z",
        }
    )

    # 4. Servant Insight (AgoraInsight from AgoraStore + enriched trader/account/broker fields)
    servant_insight = owner.record_servant_insight(
        {
            "insight_id": "ins-20261116-001",
            "tenant_id": "tenant-alpha",
            "agora_user_id": "trader-alice",
            "user_id": "trader-alice",
            "persona_id": "persona-alice-servant",
            "account_id": "acct-tw-margin-01",
            "broker_ids": ["fubon-sec"],
            "category": "risk_alert",
            "title": "Semiconductor gap-up vs futures institutional flow divergence",
            "summary": "Trader Alice consistently outperforms base momentum when trimming into spot-futures divergence.",
            "raw_text": "Servant observation: Alice trimmed 2330.TW on 3 of 3 recent spot-futures divergence mornings with +38 bps average alpha vs base strategy.",
            "confidence": 0.84,
            "impact": "high",
            "tags": ["2330.TW", "servant_insight", "imitation_candidate"],
            "linked_strategy_ids": ["strat-tw-semis-momentum-v2"],
            "linked_persona_ids": ["persona-alice-servant"],
            "context_refs": [{"kind": "decision_lock", "id": "dec-lock-20261116-001"}],
            "created_at": "2026-11-16T07:00:00Z",
            "available_time": "2026-11-16T07:00:03Z",
        }
    )

    # Also seed an AgoraInsight for Bob to verify multi-user and store-backed insight merges seamlessly
    owner.record_servant_insight(
        {
            "insight_id": "ins-20261116-002",
            "tenant_id": "tenant-alpha",
            "agora_user_id": "trader-bob",
            "user_id": "trader-bob",
            "persona_id": "persona-bob-servant",
            "category": "execution_pattern",
            "title": "Bob afternoon liquidity patience",
            "summary": "Waits for 13:00 volume confirmation before adding.",
            "raw_text": "Bob waited for 13:00 volume confirmation on 2317.TW across acct-tw-cash-02.",
            "account_id": "acct-tw-cash-02",
            "broker_ids": ["yuanta-sec"],
            "confidence": 0.76,
            "impact": "medium",
            "tags": ["2317.TW", "liquidity"],
            "linked_strategy_ids": ["strat-tw-largecap-v1"],
            "linked_persona_ids": ["persona-bob-servant"],
            "context_refs": ["acct-tw-cash-02", "yuanta-sec"],
            "created_at": "2026-11-16T07:15:00Z",
            "available_time": "2026-11-16T07:15:02Z",
        }
    )

    return {
        "decision_lock": decision_lock,
        "settled_arm": settled_arm,
        "decision_journal": journal_entry,
        "servant_insight": servant_insight,
    }


def _build_test_owner_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[FastAPI, AgoraPlatformSourceOwner, TestClient]:
    """Create a FastAPI app mounting the real Agora platform-source router backed by AgoraPlatformSourceOwner."""
    monkeypatch.setenv("AGORA_PLATFORM_SOURCE_SERVICE_TOKEN", TEST_SERVICE_TOKEN)
    monkeypatch.setenv(
        "AGORA_PLATFORM_SOURCE_ALLOWED_SERVICE_ACTORS",
        "source-ingest,source-ingest-scheduler,agora-owner-connector",
    )
    monkeypatch.setenv("AGORA_PLATFORM_SOURCE_SERVICE_TENANTS", "*")
    monkeypatch.setenv("SOURCE_INGEST_DATA_DIR", str(tmp_path / "source_ingest_data"))

    owner = AgoraPlatformSourceOwner(
        data_dir=tmp_path / "agora_platform_source",
        backend="file",
    )
    app = FastAPI()
    app.include_router(create_platform_source_router(owner=owner))
    client = TestClient(app)
    return app, owner, client


def _make_testclient_transport(client: TestClient):
    """Build a transport callable for AgoraOwnerAdapter that routes HTTP pulls to the real FastAPI route."""

    def _transport(
        *,
        url: str,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        kind: Optional[str] = None,
        tenant_id: Optional[str] = None,
        headers: Optional[Mapping[str, str]] = None,
        **_kwargs: Any,
    ) -> Mapping[str, Any]:
        parsed = urllib.parse.urlsplit(url)
        query_params: Dict[str, str] = dict(urllib.parse.parse_qsl(parsed.query))
        if tenant_id:
            query_params["tenant_id"] = str(tenant_id)
        if limit is not None:
            query_params["limit"] = str(limit)
        if cursor:
            query_params["cursor"] = str(cursor)
        if kind:
            query_params["kind"] = str(kind)
        query_string = urllib.parse.urlencode(query_params)
        path_with_query = parsed.path + (f"?{query_string}" if query_string else "")
        response = client.get(path_with_query, headers=dict(headers or {}))
        if response.status_code != 200:
            raise SourceEvidenceError(
                f"Agora owner route returned status {response.status_code}: {response.text}"
            )
        return response.json()

    return _transport


# ==============================================================================
# 1. Internal Service-Token Route, Cursor Pagination & Single-Owner DB Boundary
# ==============================================================================


def test_internal_service_token_route_auth_and_incremental_cursor_pagination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify service-token auth (401/403/503/200) and cursor pagination across all 4 record kinds."""
    _, owner, client = _build_test_owner_app(tmp_path, monkeypatch)
    _seed_recorded_agora_rows(owner)

    # 1. Missing bearer token -> 401
    resp_unauth = client.get("/internal/agora/platform-source/records?tenant_id=tenant-alpha")
    assert resp_unauth.status_code == 401

    # 2. Invalid bearer token -> 401
    resp_bad_token = client.get(
        "/internal/agora/platform-source/records?tenant_id=tenant-alpha",
        headers={
            "Authorization": "Bearer wrong-token",
            "X-Pantheon-Service-Actor": "source-ingest",
        },
    )
    assert resp_bad_token.status_code == 401

    # 3. Unallowed service actor -> 403
    resp_bad_actor = client.get(
        "/internal/agora/platform-source/records?tenant_id=tenant-alpha",
        headers={
            "Authorization": f"Bearer {TEST_SERVICE_TOKEN}",
            "X-Pantheon-Service-Actor": "unauthorized-worker",
        },
    )
    assert resp_bad_actor.status_code == 403

    # 4. Incremental cursor pagination with valid service token (5 total records in tenant-alpha)
    service_headers = {
        "Authorization": f"Bearer {TEST_SERVICE_TOKEN}",
        "X-Pantheon-Service-Actor": "source-ingest",
    }
    page1 = client.get(
        "/internal/agora/platform-source/records?tenant_id=tenant-alpha&limit=2",
        headers=service_headers,
    )
    assert page1.status_code == 200
    body1 = page1.json()
    assert body1["provenance"] == "agora"
    assert body1["count"] == 2
    assert body1["has_more"] is True
    assert body1["next_cursor"]

    page2 = client.get(
        f"/internal/agora/platform-source/records?tenant_id=tenant-alpha&limit=2&cursor={urllib.parse.quote(body1['next_cursor'])}",
        headers=service_headers,
    )
    assert page2.status_code == 200
    body2 = page2.json()
    assert body2["count"] == 2
    assert body2["has_more"] is True
    assert body2["next_cursor"] != body1["next_cursor"]

    page3 = client.get(
        f"/internal/agora/platform-source/records?tenant_id=tenant-alpha&limit=2&cursor={urllib.parse.quote(body2['next_cursor'])}",
        headers=service_headers,
    )
    assert page3.status_code == 200
    body3 = page3.json()
    assert body3["count"] == 1
    assert body3["has_more"] is False

    # Subsequent pull from final cursor returns 0 records and preserves watermark
    page4 = client.get(
        f"/internal/agora/platform-source/records?tenant_id=tenant-alpha&limit=2&cursor={urllib.parse.quote(body3['next_cursor'])}",
        headers=service_headers,
    )
    assert page4.status_code == 200
    body4 = page4.json()
    assert body4["count"] == 0
    assert body4["items"] == []
    assert body4["next_cursor"] == body3["next_cursor"]

    # Verify all 4 record kinds and all required fields were returned without loss
    all_items = body1["items"] + body2["items"] + body3["items"]
    kinds = {item["record_kind"] for item in all_items}
    assert kinds == {"decision_lock", "settled_arm", "decision_journal", "servant_insight"}

    for item in all_items:
        assert item["provenance"] == "agora"
        assert item["tenant_id"] == "tenant-alpha"
        assert item["agora_user_id"] in {"trader-alice", "trader-bob"}
        assert item["user_id"] == item["agora_user_id"]
        assert item["trader_identity"]["agora_user_id"] == item["agora_user_id"]
        assert item["account_id"] in {"acct-tw-margin-01", "acct-tw-cash-02"}
        assert isinstance(item["broker_ids"], list) and len(item["broker_ids"]) >= 1
        assert isinstance(item["raw_text"], str) and len(item["raw_text"]) > 10
        assert item["event_time"].endswith("Z")
        assert item["available_time"].endswith("Z")
        assert item["cursor"]

    lock_item = next(i for i in all_items if i["record_kind"] == "decision_lock")
    assert set(lock_item["arms"].keys()) == {
        "human_actual",
        "private_servant",
        "base_strategy",
        "committee_variant",
    }
    assert lock_item["unavailable_arms"] == [
        {
            "arm": "committee_variant",
            "reason": "not_invoked_for_single_trader_lock",
        }
    ]


def test_role_scoped_bff_route_and_record_access_follow_operator_and_owner_roles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acceptance 3: Access follows existing operator and owner roles (traders read own, operators read tenant)."""
    _, owner, client = _build_test_owner_app(tmp_path, monkeypatch)
    _seed_recorded_agora_rows(owner)

    # 1. Trader Alice reads her own records via BFF route
    alice_resp = client.get(
        "/bff/agora/platform-source/records",
        headers={
            "X-Tenant-Id": "tenant-alpha",
            "X-User-Id": "trader-alice",
            "X-Pantheon-Role": "trader",
        },
    )
    assert alice_resp.status_code == 200
    alice_items = alice_resp.json()["items"]
    assert len(alice_items) == 4
    assert {item["agora_user_id"] for item in alice_items} == {"trader-alice"}

    # 2. Trader Alice attempting to read Trader Bob's records -> 403 Forbidden
    cross_user_resp = client.get(
        "/bff/agora/platform-source/records?user_id=trader-bob",
        headers={
            "X-Tenant-Id": "tenant-alpha",
            "X-User-Id": "trader-alice",
            "X-Pantheon-Role": "trader",
        },
    )
    assert cross_user_resp.status_code == 403

    # 3. Operator role reads all records in tenant-alpha (both Alice and Bob)
    operator_resp = client.get(
        "/bff/agora/platform-source/records",
        headers={
            "X-Tenant-Id": "tenant-alpha",
            "X-User-Id": "op-supervisor-01",
            "X-Pantheon-Role": "operator",
        },
    )
    assert operator_resp.status_code == 200
    op_items = operator_resp.json()["items"]
    assert len(op_items) == 5
    assert {item["agora_user_id"] for item in op_items} == {"trader-alice", "trader-bob"}


def test_no_other_owner_reads_agora_database_directly() -> None:
    """Acceptance 1: Verify source_ingestion and other owners do not import services.agora or query agora tables directly."""
    source_ingest_root = REPO_ROOT / "services" / "source_ingestion"
    for py_file in source_ingest_root.rglob("*.py"):
        if "tests" in py_file.parts:
            continue
        source_text = py_file.read_text(encoding="utf-8")
        assert "from services.agora" not in source_text, f"{py_file} imports services.agora directly"
        assert "import services.agora" not in source_text, f"{py_file} imports services.agora directly"
        assert "AGORA_WORKSHOP_STORE_DSN" not in source_text, f"{py_file} references Agora DB DSN directly"
        assert "FROM agora." not in source_text, f"{py_file} queries agora schema directly"


# ==============================================================================
# 2. Governed Source Definition, Desired State, & Production Pull Integration
# ==============================================================================


def test_agora_platform_source_definition_and_desired_state_registration(
    tmp_path: Path,
) -> None:
    """Acceptance 2: Source ingestion registers one governed source with provenance `agora` and PIT fields."""
    defn = get_connector_definition(AGORA_PLATFORM_SOURCE_CONNECTOR_ID)
    assert defn is not None
    validate_connector_definition(defn)
    assert defn.adapter_token == AGORA_OWNER_ADAPTER_TOKEN
    assert defn.datasets == (AGORA_PLATFORM_SOURCE_DATASET,)
    assert defn.required_pit_fields == ("event_time", "available_time", "ingest_time")
    assert defn.metadata["provenance"] == "agora"
    assert defn.metadata["validation_gates_required"] is True
    assert defn.fingerprint == compute_definition_fingerprint(defn._to_raw_dict_without_fp())
    assert get_connector_definition_by_adapter(AGORA_OWNER_ADAPTER_TOKEN) == defn
    assert AGORA_PLATFORM_SOURCE_CONNECTOR_ID in deployed_connector_definitions()

    # Verify default_desired_state.json declares the governed platform source and reconciles cleanly
    desired_personas = load_agora_platform_source_desired_state()
    assert len(desired_personas) == 1
    assert desired_personas[0]["provenance"] == "agora"
    req = desired_personas[0]["required_data_sources"][0]
    assert req["connector_candidates"] == [AGORA_PLATFORM_SOURCE_CONNECTOR_ID]
    assert req["dataset"] == AGORA_PLATFORM_SOURCE_DATASET
    assert req["source_class"] == "live_pull"

    connector_store = JsonlConfiguredConnectorStore(tmp_path / "connectors.jsonl")
    schedule_store = JsonlConnectorScheduleStore(tmp_path / "schedules.jsonl")
    reconciler = SourceProvisioningReconciler(
        manager=IngestManager(),
        connector_store=connector_store,
        schedule_store=schedule_store,
    )
    result = reconciler.reconcile_persona(desired_personas[0])
    assert result.summary["mutated"] == 1
    assert result.actions[0].connector_id == AGORA_PLATFORM_SOURCE_CONNECTOR_ID
    assert connector_store.get_config(AGORA_PLATFORM_SOURCE_CONNECTOR_ID) is not None
    assert schedule_store.get_schedule(AGORA_PLATFORM_SOURCE_CONNECTOR_ID) is not None


def test_production_pull_against_real_owner_route_and_downstream_routing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acceptance 2, 3, 4, 5: Run production pull against real owner route and route to KB, L2, attribution, meeting case files, and imitation."""
    _, owner, client = _build_test_owner_app(tmp_path, monkeypatch)
    _seed_recorded_agora_rows(owner)

    cursor_file = tmp_path / "agora_cursor_state.json"
    transport = _make_testclient_transport(client)
    adapter = AgoraOwnerAdapter(
        connector_id=AGORA_PLATFORM_SOURCE_CONNECTOR_ID,
        api_url="http://operator-bff:8001/internal/agora/platform-source/records",
        tenant_id="tenant-alpha",
        max_records=50,
        cursor_state_path=cursor_file,
        transport=transport,
    )

    # 1. Execute production pull via execute_provider_owned_adapter
    connector = adapter.connector()
    fetch_cfg = adapter.fetch_config()
    fetch_cfg["adapter_config"]["transport"] = transport

    records = execute_provider_owned_adapter(
        connector=connector,
        fetch=fetch_cfg,
        trace_id="trace-prod-pull-001",
    )
    assert len(records) == 5

    # Verify cursor state was persisted and a subsequent incremental pull only returns newly added rows
    assert cursor_file.exists()
    empty_incremental = adapter.pull(tenant_id="tenant-alpha", trace_id="trace-prod-pull-002")
    assert len(empty_incremental.records) == 0

    # Record a new trader-reported settled arm and verify incremental cursor pull picks up only the new row
    owner.record_settled_arm(
        {
            "settled_arm_id": "settled-dec-lock-20261116-002-trader",
            "decision_id": "dec-lock-20261116-001",
            "tenant_id": "tenant-alpha",
            "agora_user_id": "trader-alice",
            "account_id": "acct-tw-margin-01",
            "broker_ids": ["fubon-sec"],
            "arm_name": "private_servant",
            "execution_observation_kind": "trader_reported",
            "settlement_source": "manual_note",
            "raw_text": "Counterfactual servant hold arm would have given back 35 bps in afternoon session.",
            "outcome": {"instrument": "2330.TW", "counterfactual_pnl_bps": 7.5},
            "decision_time": "2026-11-16T01:15:00Z",
            "settled_at": "2026-11-16T08:00:00Z",
            "available_time": "2026-11-16T08:00:05Z",
        }
    )
    delta_pull = adapter.pull(tenant_id="tenant-alpha", trace_id="trace-prod-pull-003")
    assert len(delta_pull.records) == 1
    assert delta_pull.records[0].metadata["record_id"] == "settled-dec-lock-20261116-002-trader"
    assert (
        delta_pull.records[0].metadata["execution_observation_kind"]
        == "trader_reported"
    )

    all_records = list(records) + list(delta_pull.records)

    # 2. Verify PIT timestamps, provenance `agora`, trader identity, account_id, broker_ids, and raw_text on all records
    for rec in all_records:
        meta = rec.metadata
        assert meta["provenance"] == "agora"
        assert meta["dataset"] == AGORA_PLATFORM_SOURCE_DATASET
        assert meta["schema_hash"] == AGORA_PLATFORM_SOURCE_SCHEMA_HASH
        assert meta["pit"]["validated"] is True
        assert meta["event_time"].endswith("Z")
        assert meta["available_time"].endswith("Z")
        assert meta["ingest_time"].endswith("Z")
        assert meta["tenant_id"] == "tenant-alpha"
        assert meta["agora_user_id"] in {"trader-alice", "trader-bob"}
        assert meta["user_id"] == meta["agora_user_id"]
        assert meta["account_id"] in {"acct-tw-margin-01", "acct-tw-cash-02"}
        assert len(meta["broker_ids"]) >= 1
        assert len(meta["raw_text"]) > 10
        assert meta["governance"]["direct_execution_allowed"] is False
        assert meta["governance"]["validation_gates_required"] is True

        # Verify role-based access check on normalized SourceRecord
        assert check_record_access(
            rec,
            tenant_id="tenant-alpha",
            user_id="op-01",
            roles=["operator"],
        ) is True
        assert check_record_access(
            rec,
            tenant_id="tenant-alpha",
            user_id=meta["agora_user_id"],
            roles=["trader"],
        ) is True
        assert check_record_access(
            rec,
            tenant_id="tenant-alpha",
            user_id="other-trader",
            roles=["trader"],
        ) is False
        assert check_record_access(
            rec,
            tenant_id="other-tenant",
            user_id="op-01",
            roles=["operator"],
        ) is False

    # 3. Route into Knowledge Base (EvidenceBundle + KnowledgeObject) and L2 Hypothesis Extraction / Distillation Queue
    manager = IngestManager()
    evidence_repo = JsonlEvidenceRepository(tmp_path / "source_evidence.jsonl")
    distill_queue = DistillationJobQueue(tmp_path / "distill_jobs.jsonl")
    kb_summary = ingest_agora_records_to_knowledge_base(
        all_records,
        manager=manager,
        evidence_repository=evidence_repo,
        distillation_job_queue=distill_queue,
        evidence_builder=EvidenceBundleBuilder(evidence_repo),
        ingest_run_id="run-agora-prod-001",
        trace_id="trace-agora-kb-001",
    )
    assert str(kb_summary["evidence_bundle_id"]).startswith("evbundle-run-agora-prod-001")
    assert len(kb_summary["evidence_item_ids"]) == len(all_records)
    assert len(kb_summary["knowledge_object_ids"]) == len(all_records)
    assert len(kb_summary["distillation_admissions"]) == len(all_records)

    # Run L2 DistillationWorker to materialize StrategySpecSeeds from queued Agora records
    seed_store = StrategySpecSeedStore(tmp_path / "strategy_seeds.jsonl")
    distill_worker = DistillationWorker(
        job_queue=distill_queue,
        seed_store=seed_store,
        worker_id="test-distill-worker",
    )
    distill_result = distill_worker.run_pending(limit=10)
    assert distill_result.processed == len(all_records)
    assert distill_result.created == len(all_records)
    assert distill_result.failed == 0
    seeds = seed_store.list_all()
    assert len(seeds) == len(all_records)
    for seed in seeds:
        assert seed.status in {
            StrategySpecSeedStatus.DRAFT,
            StrategySpecSeedStatus.NEEDS_MORE_EVIDENCE,
            StrategySpecSeedStatus.DRAFT.value,
            StrategySpecSeedStatus.NEEDS_MORE_EVIDENCE.value,
        }

    # 4. Route to Attribution Owner
    attr_projection = project_agora_records_for_attribution(all_records)
    assert len(attr_projection["decision_locks"]) == 1
    assert len(attr_projection["settled_arms"]) == 2
    assert len(attr_projection["decision_journal_entries"]) == 1
    assert len(attr_projection["servant_insights"]) == 2
    assert attr_projection["decision_locks"][0]["arms"]["human_actual"]["action"] == "reduce"
    assert {a["execution_observation_kind"] for a in attr_projection["settled_arms"]} == {
        "broker_settled",
        "trader_reported",
    }

    # 5. Route to Meeting Case Files (MT-4 attribution / review meetings)
    alice_case_file = project_agora_records_for_meeting_case_file(
        all_records,
        subject_id="trader-alice",
        tenant_id="tenant-alpha",
    )
    assert len(alice_case_file) == 5
    for cf_item in alice_case_file:
        assert cf_item["provenance"] == "agora"
        assert cf_item["agora_user_id"] == "trader-alice"
        assert cf_item["account_id"] == "acct-tw-margin-01"
        assert "fubon-sec" in cf_item["broker_ids"]
        assert cf_item["raw_text"]
        assert cf_item["fact_only"] is True
        assert cf_item["private_memory_included"] is False
        assert cf_item["validation_gates_required"] is True

    # 6. Route to Imitation Contracts (CorrectionTrace & PreferenceExample) and validate Draft-07 schemas
    from services.research.imitation.preference_models import (
        load_correction_trace_schema,
        load_preference_example_schema,
    )

    imitation = project_agora_records_for_imitation(all_records)
    assert len(imitation["correction_traces"]) == 1
    assert len(imitation["preference_examples"]) == 1

    trace_schema = load_correction_trace_schema()
    pref_schema = load_preference_example_schema()
    trace_dict = imitation["correction_traces"][0].to_dict()
    pref_dict = imitation["preference_examples"][0].to_dict()
    assert not list(Draft7Validator(trace_schema).iter_errors(trace_dict))
    assert not list(Draft7Validator(pref_schema).iter_errors(pref_dict))
    assert trace_dict["actor_id"] == "trader-alice"
    assert trace_dict["decision_ref"] == "dec-lock-20261116-001"
    assert trace_dict["metadata"]["account_id"] == "acct-tw-margin-01"
    assert trace_dict["metadata"]["broker_ids"] == ["fubon-sec", "kgi-sec"]
    assert pref_dict["context"]["source_channel"] == "agora"


def test_validation_gates_prevent_unvalidated_promotion_to_strategy_rule_or_lesson(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acceptance 4: No record becomes a strategy or rule or lesson without existing validation gates."""
    _, owner, client = _build_test_owner_app(tmp_path, monkeypatch)
    _seed_recorded_agora_rows(owner)
    adapter = AgoraOwnerAdapter(
        transport=_make_testclient_transport(client),
        tenant_id="tenant-alpha",
    )
    record = adapter.pull(tenant_id="tenant-alpha").records[0]

    # Unvalidated promotion attempts must fail closed for strategy, rule, and lesson
    for target_kind in ("strategy", "rule", "lesson"):
        with pytest.raises(SourceEvidenceError, match="without passing existing validation gates"):
            enforce_validation_gate_before_promotion(record, target_kind=target_kind, validation_evidence={})

    # Partial gates still fail closed
    with pytest.raises(SourceEvidenceError, match="trial_admission_panel_approved"):
        enforce_validation_gate_before_promotion(
            record,
            target_kind="strategy",
            validation_evidence={"validation_passed": True},
        )

    # Full validation gate evidence succeeds
    strat_gate = enforce_validation_gate_before_promotion(
        record,
        target_kind="strategy",
        validation_evidence={
            "validation_passed": True,
            "trial_admission_panel_approved": True,
        },
    )
    assert strat_gate["status"] == "validation_gates_satisfied"

    lesson_gate = enforce_validation_gate_before_promotion(
        record,
        target_kind="lesson",
        validation_evidence={
            "risk_review_approved": True,
            "paper_endorsed": True,
        },
    )
    assert lesson_gate["status"] == "validation_gates_satisfied"

    rule_gate = enforce_validation_gate_before_promotion(
        record,
        target_kind="rule",
        validation_evidence={
            "risk_review_approved": True,
            "paper_endorsed": True,
            "harness_panel_approved": True,
        },
    )
    assert rule_gate["status"] == "validation_gates_satisfied"


def test_configured_connector_fetcher_live_http_pull_and_shadow_mode_c4_compatibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acceptance 1 & 5: Run production ConfiguredConnectorFetcher over live loopback HTTP against real owner route."""
    import http.server
    import threading

    _, owner, client = _build_test_owner_app(tmp_path, monkeypatch)
    _seed_recorded_agora_rows(owner)

    # Also record an AgoraHumanDecisionLock.v1 shape (parallel task AGORA-SHADOW-MODE-20261116 compatibility)
    owner.record_decision_lock(
        {
            "schema_version": "AgoraHumanDecisionLock.v1",
            "human_decision_id": "hdl-20261116-shadow-001",
            "tenant_id": "tenant-alpha",
            "trader_identity": {"tenant_id": "tenant-alpha", "user_id": "trader-alice"},
            "account_id": "acct-tw-margin-01",
            "broker_ids": ["fubon-sec"],
            "target_instrument": "2454.TW",
            "decision": "enter",
            "requested_quantity": 1000,
            "raw_rationale": "Entering 2454.TW on MediaTek edge AI breakout.",
            "decision_timestamp": "2026-11-16T02:00:00Z",
            "available_data_cutoff": "2026-11-16T01:59:58Z",
            "execution_observation_kind": "trader_reported",
            "comparison_arms": {
                "human_actual": {"action": "enter", "quantity": 1000},
                "private_servant": {"available": True, "suggested_action": "enter", "persona_id": "persona-alice-servant"},
                "base_strategy": {"available": True, "suggested_action": "hold", "strategy_version_id": "strat-mtk-v1"},
                "institutional_view": {"available": False, "unavailable_reason": "no_committee_session"},
            },
            "created_at": "2026-11-16T02:00:05Z",
        }
    )

    class _FastAPIBridgeHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            fwd_headers = {
                k: v
                for k, v in self.headers.items()
                if k.lower() in {"authorization", "x-pantheon-service-actor", "x-tenant-id"}
            }
            resp = client.get(self.path, headers=fwd_headers)
            body = resp.content
            self.send_response(resp.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FastAPIBridgeHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        live_url = f"http://127.0.0.1:{port}/internal/agora/platform-source/records"
        monkeypatch.setenv("AGORA_PLATFORM_SOURCE_URL", live_url)

        connector_store = JsonlConfiguredConnectorStore(tmp_path / "live_connectors.jsonl")
        schedule_store = JsonlConnectorScheduleStore(tmp_path / "live_schedules.jsonl")
        reconciler = SourceProvisioningReconciler(
            manager=IngestManager(),
            connector_store=connector_store,
            schedule_store=schedule_store,
        )
        desired_personas = load_agora_platform_source_desired_state()
        reconcile_res = reconciler.reconcile_persona(desired_personas[0])
        assert reconcile_res.summary["mutated"] == 1

        fetcher = ConfiguredConnectorFetcher(connector_store)
        batch = fetcher.fetch_batch(
            AGORA_PLATFORM_SOURCE_CONNECTOR_ID,
            None,
            trace_id="trace-live-http-pull",
            job_parameters={"tenant_id": "tenant-alpha"},
        )
        assert len(batch.records) == 6
        shadow_rec = next(
            r for r in batch.records if r.metadata.get("record_id") == "hdl-20261116-shadow-001"
        )
        assert shadow_rec.metadata["provenance"] == "agora"
        assert shadow_rec.metadata["agora_user_id"] == "trader-alice"
        assert shadow_rec.metadata["account_id"] == "acct-tw-margin-01"
        assert shadow_rec.metadata["broker_ids"] == ["fubon-sec"]
        assert shadow_rec.metadata["raw_text"] == "Entering 2454.TW on MediaTek edge AI breakout."
        assert shadow_rec.metadata["unavailable_arms"] == [
            {"arm": "committee_variant", "reason": "no_committee_session"}
        ]
    finally:
        server.shutdown()
        server.server_close()

