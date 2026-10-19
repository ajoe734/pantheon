from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.tests.knowledge_read_port_fixtures import (
    create_environment_knowledge_read_ports,
    create_seeded_knowledge_read_ports,
)
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.research.router import create_research_router
from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.models import utc_now


def _build_test_app(read_store: Any) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(
        create_research_router(
            read_surface=read_store,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
            utc_now=utc_now,
        )
    )
    return app


OPERATOR_TOKEN = "Bearer op-2:operator"
FALLBACK_STRATEGY_ID = "strat-0a1b2c3d-9f8e-7d6c-5b4a-3f2e1d0c9b8a"
SERVICE_STRATEGY_ID = "strat-11111111-2222-3333-4444-555555555555"
RETIRED_STRATEGY_ID = "strat-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
DRAFT_STRATEGY_ID = "strat-99999999-8888-7777-6666-555555555555"


@contextmanager
def _seeded_client():
    tracked_env = {
        "PANTHEON_BFF_AUTH_STUB": os.environ.get("PANTHEON_BFF_AUTH_STUB"),
        "PANTHEON_BFF_AUTH_MODE": os.environ.get("PANTHEON_BFF_AUTH_MODE"),
    }
    os.environ["PANTHEON_BFF_AUTH_STUB"] = "1"
    os.environ["PANTHEON_BFF_AUTH_MODE"] = "permissive"
    app = _build_test_app(create_seeded_knowledge_read_ports())
    try:
        with TestClient(app) as client:
            yield client
    finally:
        for key, value in tracked_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def _service_backed_client():
    tracked_env = {
        "PANTHEON_BFF_STRATEGY_SPEC_STORE": os.environ.get("PANTHEON_BFF_STRATEGY_SPEC_STORE"),
        "PANTHEON_BFF_AUTH_STUB": os.environ.get("PANTHEON_BFF_AUTH_STUB"),
        "PANTHEON_BFF_AUTH_MODE": os.environ.get("PANTHEON_BFF_AUTH_MODE"),
    }
    os.environ["PANTHEON_BFF_AUTH_STUB"] = "1"
    os.environ["PANTHEON_BFF_AUTH_MODE"] = "permissive"
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        strategy_store = root / "strategy_specs.json"
        strategy_store.write_text(
            json.dumps(
                {
                    SERVICE_STRATEGY_ID: {
                        "strategy_id": SERVICE_STRATEGY_ID,
                        "current_spec_version_id": "specver-11111111-0003-0003-0003-000000000003",
                        "title": "Cross-Session Momentum Rotation",
                        "source_kind": "workflow",
                        "persona_ids": ["persona-HAWK-001"],
                        "updated_at": "2026-04-21T11:30:00Z",
                        "versions": [
                            {
                                "spec_version_id": "specver-11111111-0001-0001-0001-000000000001",
                                "spec_version": "v1",
                                "lifecycle_state": "retired",
                                "title": "Cross-Session Momentum Rotation v1",
                                "hypothesis": "Baseline cross-session rotation captures the Asia-to-US continuation effect.",
                                "objective": "Establish baseline cross-session rotation behavior.",
                                "market_scope": {"symbols": ["ES", "NQ"], "frequency": "daily"},
                                "execution_profile": {
                                    "signal_schema_version": "1.0",
                                    "quantity_type": "PERCENT_PORTFOLIO",
                                    "execution_mode_hint": "research",
                                },
                                "evaluation_plan": {"metrics": ["sharpe_ratio"]},
                                "governance": {"approval_required": True},
                                "citation_bundle": {"evidence_refs": [], "memory_anchors": [], "insight_citations": []},
                                "parent_spec_version_id": None,
                                "derived_from_source_refs": ["note-alpha"],
                                "created_at": "2026-03-01T08:00:00Z",
                                "created_by": "Operator: Alice Chen",
                            },
                            {
                                "spec_version_id": "specver-11111111-0002-0002-0002-000000000002",
                                "spec_version": "v2",
                                "lifecycle_state": "candidate",
                                "title": "Cross-Session Momentum Rotation v2",
                                "hypothesis": "Shorter rebalance windows improve cross-session decay handling.",
                                "objective": "Reduce latency after Asia-session divergence.",
                                "market_scope": {"symbols": ["ES", "NQ"], "frequency": "daily"},
                                "execution_profile": {
                                    "signal_schema_version": "1.1",
                                    "quantity_type": "PERCENT_PORTFOLIO",
                                    "execution_mode_hint": "paper",
                                },
                                "evaluation_plan": {
                                    "metrics": ["sharpe_ratio", "max_drawdown"],
                                    "paper_gate": "Sharpe >= 1.0 over 30d paper run",
                                },
                                "governance": {"approval_required": True, "risk_profile": "medium"},
                                "citation_bundle": {
                                    "evidence_refs": [
                                        {
                                            "ref_id": "evref-10000000-1111-2222-3333-444444444444",
                                            "source_document_title": "Volatility Regime Analysis Q1 2026",
                                            "link_type": "supporting_evidence",
                                            "credibility_tier": "primary",
                                            "association": "evaluation",
                                            "resolved_link": {
                                                "availability": "available",
                                                "route_href": "/knowledge/evidence/evref-10000000-1111-2222-3333-444444444444",
                                                "display_label": "View evidence reference",
                                                "open_in_new_tab": False,
                                            },
                                        }
                                    ],
                                    "memory_anchors": [
                                        {
                                            "entry_id": "mem-11111111-2222-3333-4444-555555555555",
                                            "knowledge_type": "regime_pattern",
                                            "content_headline": "Asia-session divergence pattern",
                                            "route_href": "/knowledge/memory/mem-11111111-2222-3333-4444-555555555555",
                                        }
                                    ],
                                    "insight_citations": [],
                                },
                                "parent_spec_version_id": "specver-11111111-0001-0001-0001-000000000001",
                                "derived_from_source_refs": ["note-alpha", "analysis-beta"],
                                "created_at": "2026-04-10T08:00:00Z",
                                "created_by": "Persona: Momentum-alpha",
                            },
                            {
                                "spec_version_id": "specver-11111111-0003-0003-0003-000000000003",
                                "spec_version": "v3",
                                "lifecycle_state": "approved",
                                "title": "Cross-Session Momentum Rotation v3",
                                "hypothesis": "Stricter paper gates and paper-mode execution improve regime hand-off stability.",
                                "objective": "Promote the refined cross-session rotation into governed paper deployment.",
                                "market_scope": {"symbols": ["ES", "NQ"], "frequency": "daily"},
                                "execution_profile": {
                                    "signal_schema_version": "1.2",
                                    "quantity_type": "PERCENT_PORTFOLIO",
                                    "execution_mode_hint": "paper",
                                },
                                "evaluation_plan": {
                                    "metrics": ["sharpe_ratio", "max_drawdown"],
                                    "paper_gate": "Sharpe >= 1.1 over 30d paper run",
                                    "live_gate": "Sharpe >= 1.25 over 60d paper run",
                                },
                                "governance": {
                                    "approval_required": True,
                                    "policy_id": "gov-policy-cross-session-001",
                                    "risk_profile": "medium",
                                },
                                "citation_bundle": {
                                    "evidence_refs": [
                                        {
                                            "ref_id": "evref-10000000-1111-2222-3333-444444444444",
                                            "source_document_title": "Volatility Regime Analysis Q1 2026",
                                            "link_type": "supporting_evidence",
                                            "credibility_tier": "primary",
                                            "association": "evaluation",
                                            "resolved_link": {
                                                "availability": "available",
                                                "route_href": "/knowledge/evidence/evref-10000000-1111-2222-3333-444444444444",
                                                "display_label": "View evidence reference",
                                                "open_in_new_tab": False,
                                            },
                                        },
                                        {
                                            "ref_id": "evref-20000000-1111-2222-3333-444444444444",
                                            "source_document_title": "Execution slippage histogram",
                                            "link_type": "citation",
                                            "credibility_tier": "secondary",
                                            "association": "background",
                                            "resolved_link": {
                                                "availability": "available",
                                                "route_href": "/research/artifacts/artifact-abc123",
                                                "display_label": "Open experiment artifact",
                                                "open_in_new_tab": False,
                                            },
                                        },
                                    ],
                                    "memory_anchors": [
                                        {
                                            "entry_id": "mem-11111111-2222-3333-4444-555555555555",
                                            "knowledge_type": "regime_pattern",
                                            "content_headline": "Asia-session divergence pattern",
                                            "route_href": "/knowledge/memory/mem-11111111-2222-3333-4444-555555555555",
                                        }
                                    ],
                                    "insight_citations": [
                                        {
                                            "insight_id": "ins-11111111-2222-3333-4444-555555555555",
                                            "summary": "Cross-session decay stabilizes after stricter paper gates.",
                                            "route_href": "/knowledge/insights/ins-11111111-2222-3333-4444-555555555555",
                                        }
                                    ],
                                },
                                "parent_spec_version_id": "specver-11111111-0002-0002-0002-000000000002",
                                "derived_from_source_refs": ["note-alpha", "analysis-beta", "analysis-gamma"],
                                "created_at": "2026-04-21T11:30:00Z",
                                "created_by": "Operator: Alice Chen",
                            },
                        ],
                    },
                    RETIRED_STRATEGY_ID: {
                        "strategy_id": RETIRED_STRATEGY_ID,
                        "current_spec_version_id": "specver-aaaaaaaa-0001-0001-0001-000000000001",
                        "title": "Retired Legacy Mean Reversion",
                        "source_kind": "manual",
                        "persona_ids": ["persona-OWL-001"],
                        "updated_at": "2026-02-15T10:00:00Z",
                        "versions": [
                            {
                                "spec_version_id": "specver-aaaaaaaa-0001-0001-0001-000000000001",
                                "spec_version": "v1",
                                "lifecycle_state": "retired",
                                "title": "Retired Legacy Mean Reversion v1",
                                "hypothesis": "Legacy mean reversion baseline.",
                                "objective": "Retained for lineage only.",
                                "market_scope": {"symbols": ["SPY"], "frequency": "daily"},
                                "execution_profile": {"signal_schema_version": "1.0", "quantity_type": "PERCENT_PORTFOLIO"},
                                "evaluation_plan": {"metrics": ["sharpe_ratio"]},
                                "governance": {"approval_required": True},
                                "citation_bundle": {"evidence_refs": [], "memory_anchors": [], "insight_citations": []},
                                "parent_spec_version_id": None,
                                "derived_from_source_refs": ["legacy-note"],
                                "created_at": "2026-02-15T10:00:00Z",
                                "created_by": "Operator: Alice Chen",
                            }
                        ],
                    },
                    DRAFT_STRATEGY_ID: {
                        "strategy_id": DRAFT_STRATEGY_ID,
                        "current_spec_version_id": "specver-99999999-0002-0002-0002-000000000002",
                        "title": "Draft Opening-Auction Response",
                        "source_kind": "workflow",
                        "persona_ids": ["persona-HAWK-001"],
                        "updated_at": "2026-04-20T09:00:00Z",
                        "versions": [
                            {
                                "spec_version_id": "specver-99999999-0001-0001-0001-000000000001",
                                "spec_version": "v1",
                                "lifecycle_state": "draft",
                                "title": "Draft Opening-Auction Response v1",
                                "hypothesis": "Opening-auction divergence can be captured with faster decay.",
                                "objective": "Explore a draft-only opening-auction strategy.",
                                "market_scope": {"symbols": ["NQ"], "frequency": "daily"},
                                "execution_profile": {"signal_schema_version": "0.9", "quantity_type": "PERCENT_PORTFOLIO"},
                                "evaluation_plan": {"metrics": ["sharpe_ratio"]},
                                "governance": {"approval_required": True},
                                "citation_bundle": {"evidence_refs": [], "memory_anchors": [], "insight_citations": []},
                                "parent_spec_version_id": None,
                                "derived_from_source_refs": ["draft-note"],
                                "created_at": "2026-04-18T07:00:00Z",
                                "created_by": "Persona: Drafting-agent",
                            },
                            {
                                "spec_version_id": "specver-99999999-0002-0002-0002-000000000002",
                                "spec_version": "v2",
                                "lifecycle_state": "candidate",
                                "title": "Draft Opening-Auction Response v2",
                                "hypothesis": "Candidate version keeps the same thesis but tightens metrics.",
                                "objective": "Candidate for later approval.",
                                "market_scope": {"symbols": ["NQ"], "frequency": "daily"},
                                "execution_profile": {
                                    "signal_schema_version": "1.0",
                                    "quantity_type": "PERCENT_PORTFOLIO",
                                    "execution_mode_hint": "paper",
                                },
                                "evaluation_plan": {"metrics": ["sharpe_ratio", "max_drawdown"]},
                                "governance": {"approval_required": True},
                                "citation_bundle": {"evidence_refs": [], "memory_anchors": [], "insight_citations": []},
                                "parent_spec_version_id": "specver-99999999-0001-0001-0001-000000000001",
                                "derived_from_source_refs": ["draft-note", "candidate-note"],
                                "created_at": "2026-04-20T09:00:00Z",
                                "created_by": "Persona: Drafting-agent",
                            },
                        ],
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        os.environ["PANTHEON_BFF_STRATEGY_SPEC_STORE"] = str(strategy_store)

        app = _build_test_app(create_environment_knowledge_read_ports())
        client = TestClient(app)
        try:
            yield client
        finally:
            for key, value in tracked_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def test_kw05_seeded_routes_return_contract_shape_with_degraded_surface() -> None:
    with _seeded_client() as client:
        list_response = client.get(
            "/api/v1/knowledge/strategy-specs?lifecycle_state=approved",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert list_response.status_code == 200, list_response.text
        payload = list_response.json()

        assert payload["meta"]["surfaces"] == {"strategy_spec_list": "degraded"}
        assert payload["page_info"] == {
            "next_page_token": None,
            "page_size": 20,
            "has_more": False,
        }
        assert payload["items"][0]["strategy_id"] == FALLBACK_STRATEGY_ID
        assert payload["items"][0]["current_spec_version"] == "v3"
        assert payload["items"][0]["version_count"] == 3

        detail_response = client.get(
            f"/api/v1/knowledge/strategy-specs/{FALLBACK_STRATEGY_ID}?version=v2",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert detail_response.status_code == 200, detail_response.text
        detail = detail_response.json()

        assert detail["spec_version"] == "v2"
        assert detail["parent_spec_version_id"] == "specver-0a1b2c3d-0001-0001-0001-000000000001"
        assert detail["citation_bundle"]["memory_anchors"][0]["entry_id"] == "mem-e5f6a7b8-c9d0-1234-efab-234567890123"
        assert detail["allowedActions"] == {
            "canSubmitForApproval": False,
            "canRetire": True,
            "canCompare": True,
        }
        assert detail["meta"]["surfaces"] == {
            "strategy_spec_detail": "degraded",
            "citation_bundle": "degraded",
            "version_ancestry": "degraded",
        }

        versions_response = client.get(
            f"/api/v1/knowledge/strategy-specs/{FALLBACK_STRATEGY_ID}/versions",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert versions_response.status_code == 200, versions_response.text
        versions_payload = versions_response.json()

        assert [version["spec_version"] for version in versions_payload["versions"]] == ["v3", "v2", "v1"]

        compare_response = client.get(
            f"/api/v1/knowledge/strategy-specs/{FALLBACK_STRATEGY_ID}/compare?left_version=v2&right_version=v3",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert compare_response.status_code == 200, compare_response.text
        compare_payload = compare_response.json()

        assert compare_payload["left_spec_version_id"] == "specver-0a1b2c3d-0002-0002-0002-000000000002"
        assert compare_payload["right_spec_version_id"] == "specver-0a1b2c3d-0003-0003-0003-000000000003"
        assert "evref-a1b2c3d4-e5f6-7890-abcd-ef1234567890" in compare_payload["evidence_refs"]
        assert compare_payload["meta"]["surfaces"] == {"strategy_spec_compare": "degraded"}


def test_kw05_service_backed_routes_apply_filters_and_preserve_compare_semantics() -> None:
    with _service_backed_client() as client:
        list_response = client.get(
            "/api/v1/knowledge/strategy-specs?lifecycle_state=approved&source_kind=workflow&persona_id=persona-HAWK-001",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert list_response.status_code == 200, list_response.text
        payload = list_response.json()

        assert payload["meta"]["surfaces"] == {"strategy_spec_list": "ok"}
        assert [item["strategy_id"] for item in payload["items"]] == [SERVICE_STRATEGY_ID]

        hidden_retired = client.get(
            "/api/v1/knowledge/strategy-specs",
            headers={"Authorization": OPERATOR_TOKEN},
        ).json()
        assert [item["strategy_id"] for item in hidden_retired["items"]] == [
            SERVICE_STRATEGY_ID,
            DRAFT_STRATEGY_ID,
        ]

        visible_retired = client.get(
            "/api/v1/knowledge/strategy-specs?include_retired=true",
            headers={"Authorization": OPERATOR_TOKEN},
        ).json()
        assert [item["strategy_id"] for item in visible_retired["items"]] == [
            SERVICE_STRATEGY_ID,
            DRAFT_STRATEGY_ID,
            RETIRED_STRATEGY_ID,
        ]

        detail_response = client.get(
            f"/api/v1/knowledge/strategy-specs/{SERVICE_STRATEGY_ID}",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert detail_response.status_code == 200, detail_response.text
        detail = detail_response.json()

        assert detail["spec_version"] == "v3"
        assert len(detail["citation_bundle"]["evidence_refs"]) == 2
        assert detail["citation_bundle"]["insight_citations"][0]["insight_id"] == "ins-11111111-2222-3333-4444-555555555555"
        assert detail["meta"]["surfaces"] == {
            "strategy_spec_detail": "ok",
            "citation_bundle": "ok",
            "version_ancestry": "ok",
        }

        versions_response = client.get(
            f"/api/v1/knowledge/strategy-specs/{SERVICE_STRATEGY_ID}/versions",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert versions_response.status_code == 200, versions_response.text
        versions_payload = versions_response.json()

        assert versions_payload["versions"][0]["route_href"] == (
            f"/knowledge/strategy-specs/{SERVICE_STRATEGY_ID}?version=specver-11111111-0003-0003-0003-000000000003"
        )
        assert versions_payload["meta"]["surfaces"] == {"version_history": "ok"}

        compare_response = client.get(
            f"/api/v1/knowledge/strategy-specs/{SERVICE_STRATEGY_ID}/compare?base_version=v2&target_version=v3",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert compare_response.status_code == 200, compare_response.text
        compare_payload = compare_response.json()

        assert compare_payload["strategy_id"] == SERVICE_STRATEGY_ID
        assert compare_payload["changed_sections"]
        assert compare_payload["breaking_changes"] == [
            {
                "section": "execution_profile",
                "summary": "Execution profile changed from v2 to v3.",
                "severity": "breaking",
            }
        ]
        assert compare_payload["evidence_refs"] == [
            "evref-10000000-1111-2222-3333-444444444444",
            "evref-20000000-1111-2222-3333-444444444444",
        ]
        assert compare_payload["meta"]["surfaces"] == {"strategy_spec_compare": "ok"}


def test_kw05_compare_rejects_missing_duplicate_and_noncomparable_versions() -> None:
    with _service_backed_client() as client:
        missing_response = client.get(
            f"/api/v1/knowledge/strategy-specs/{SERVICE_STRATEGY_ID}/compare?left_version=v2",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert missing_response.status_code == 400, missing_response.text
        assert missing_response.json()["error"]["details"]["precondition_failed"] == "left_version"

        duplicate_response = client.get(
            f"/api/v1/knowledge/strategy-specs/{SERVICE_STRATEGY_ID}/compare?left_version=v2&right_version=v2",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert duplicate_response.status_code == 422, duplicate_response.text
        assert duplicate_response.json()["error"]["code"] == "VALIDATION_FAILED"

        noncomparable_response = client.get(
            f"/api/v1/knowledge/strategy-specs/{DRAFT_STRATEGY_ID}/compare?left_version=v1&right_version=v2",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert noncomparable_response.status_code == 422, noncomparable_response.text
        payload = noncomparable_response.json()
        assert payload["error"]["code"] == "OPERATION_NOT_ALLOWED"
        assert payload["error"]["details"]["precondition_failed"] == "lifecycle_state"


def test_kw05_and_strategies_service_registry_http_owner_read_contract(monkeypatch) -> None:
    import urllib.error
    import pytest
    from fastapi import HTTPException
    from services.control_plane.bff.command_adapters import base as adapter_base
    from services.control_plane.bff.core import owner_reads
    from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware
    from services.control_plane.bff.ports import ReadSurfacePorts
    from services.control_plane.bff.ports.research_knowledge_source import DefaultResearchKnowledgeSourcePort
    from services.control_plane.bff.strategies.service import StrategiesService

    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "1")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.internal")

    strategy_id = "strategy-persona-aa060d53ff712cbba704"
    registry_id = "reg-strategy-spec-persona-aa060d53ff712cbba704"
    tenant_id = "tenant-dev"

    def make_entry(owner_tenant: str = tenant_id) -> dict:
        return {
            "registry_id": registry_id,
            "artifact_type": "strategy_spec",
            "artifact_state": "approved",
            "version": "1.0.0",
            "strategy_id": strategy_id,
            "owner_tenant": owner_tenant,
            "lineage": {
                "source_strategy_spec_id": strategy_id,
                "parent_registry_ids": ["reg-strategy-spec-persona-baseline-aa060d53ff712cbba704"],
            },
            "evaluation_summary": {"risk_level": "medium"},
            "metadata": {
                "title": "Original Workshop Strategy Spec",
                "persona_id": "persona-132519698e6199e80326",
                "strategy_spec": {
                    "strategy_id": strategy_id,
                    "spec_version": "1.0",
                    "lifecycle_state": "candidate",
                    "title": "Original Workshop Strategy Spec",
                    "hypothesis": "Cross-session orderflow imbalance mean-reverts.",
                    "objective": "Capture intraday reversion with bounded drawdown.",
                    "market_scope": {"symbols": ["BTCUSDT"], "frequency": "1h"},
                    "execution_profile": {"signal_schema_version": "1.0", "execution_mode_hint": "paper"},
                    "evaluation_plan": {"metrics": ["sharpe_ratio"]},
                    "governance": {"approval_required": True},
                    "metadata": {"tenant_id": owner_tenant, "persona_id": "persona-132519698e6199e80326"},
                },
            },
            "created_at": "2026-10-18T10:00:00Z",
            "updated_at": "2026-10-18T12:00:00Z",
            "last_actor": {"actor_id": "op-workshop"},
        }

    port = DefaultResearchKnowledgeSourcePort()
    assert port._strategy_specs == {}
    read_ports = ReadSurfacePorts(research_knowledge_source=port)
    app = _build_test_app(read_ports)
    app.add_middleware(OwnerReadContextMiddleware)

    tenant_token = f"Bearer op-2:operator:{tenant_id}"
    headers = {"Authorization": tenant_token, "X-Tenant-Id": tenant_id}
    with TestClient(app, raise_server_exceptions=False) as client:
        monkeypatch.setattr(adapter_base, "http_request_json", lambda *a, **k: [make_entry(tenant_id)])

        # 1. Detail by registry_id selector and default current selector
        by_reg_resp = client.get(
            f"/api/v1/knowledge/strategy-specs/{strategy_id}?version={registry_id}",
            headers=headers,
        )
        assert by_reg_resp.status_code == 200, by_reg_resp.text
        detail = by_reg_resp.json()
        assert detail["strategy_id"] == strategy_id
        assert detail["spec_version_id"] == registry_id
        assert detail["spec_version"] == "1.0.0"
        assert detail["lifecycle_state"] == "approved"
        assert detail["parent_spec_version_id"] == "reg-strategy-spec-persona-baseline-aa060d53ff712cbba704"

        versions_resp = client.get(
            f"/api/v1/knowledge/strategy-specs/{strategy_id}/versions",
            headers=headers,
        )
        assert versions_resp.status_code == 200, versions_resp.text
        assert versions_resp.json()["versions"][0]["spec_version_id"] == registry_id

        # 2. Missing entry (200 []) or missing version selector -> 404
        missing_ver_resp = client.get(
            f"/api/v1/knowledge/strategy-specs/{strategy_id}?version=nonexistent-ver",
            headers=headers,
        )
        assert missing_ver_resp.status_code == 404, missing_ver_resp.text

        monkeypatch.setattr(adapter_base, "http_request_json", lambda *a, **k: [])
        missing_resp = client.get(
            f"/api/v1/knowledge/strategy-specs/{strategy_id}",
            headers=headers,
        )
        assert missing_resp.status_code == 404, missing_resp.text

        # 3. Foreign tenant -> 403
        monkeypatch.setattr(adapter_base, "http_request_json", lambda *a, **k: [make_entry("tenant-other")])
        forbidden_resp = client.get(
            f"/api/v1/knowledge/strategy-specs/{strategy_id}",
            headers=headers,
        )
        assert forbidden_resp.status_code == 403, forbidden_resp.text

        # 4. Unavailable Registry (503 or untyped 404 route error) -> 503
        def raise_404(*a, **k):
            raise urllib.error.HTTPError("http://registry.internal", 404, "Not Found", hdrs=None, fp=None)

        def raise_503(*a, **k):
            raise urllib.error.HTTPError("http://registry.internal", 503, "Unavailable", hdrs=None, fp=None)

        monkeypatch.setattr(adapter_base, "http_request_json", raise_404)
        http_404_resp = client.get(
            f"/api/v1/knowledge/strategy-specs/{strategy_id}",
            headers=headers,
        )
        assert http_404_resp.status_code == 503, http_404_resp.text

        monkeypatch.setattr(adapter_base, "http_request_json", raise_503)
        unavail_resp = client.get(
            f"/api/v1/knowledge/strategy-specs/{strategy_id}",
            headers=headers,
        )
        assert unavail_resp.status_code == 503, unavail_resp.text

    # 5. StrategiesService read methods propagate owner reads, 404, 403, and 503
    #    even when strategy_write_owner is injected (matching production app_factory).
    class _PoisonedLocalWriteOwner:
        def get_strategy(self, sid: str):
            return {"strategy_id": sid, "title": "Poisoned Local Writer Fallback"}

    port._strategy_specs[strategy_id] = {"strategy_id": strategy_id, "title": "Poisoned Local Cache"}
    svc = StrategiesService(
        read_surface=read_ports,
        strategy_write_owner=lambda: _PoisonedLocalWriteOwner(),
        list_strategy_summaries=port.list_strategy_specs,
        bff_error=auth_policy.bff_error,
        utc_now=utc_now,
    )
    tok_auth = owner_reads.authorization.set(tenant_token)
    tok_tenant = owner_reads.selected_tenant.set(tenant_id)
    try:
        monkeypatch.setattr(adapter_base, "http_request_json", lambda *a, **k: [make_entry(tenant_id)])
        spec_detail = svc.get_strategy_spec_detail(strategy_id, version_selector=registry_id)
        assert spec_detail is not None
        assert spec_detail["spec_version_id"] == registry_id
        assert spec_detail["strategy_spec_registry_id"] == registry_id
        assert spec_detail["spec_version"] == "1.0.0"

        strat = svc.get_strategy(strategy_id)
        assert strat is not None
        assert strat["strategy_id"] == strategy_id
        assert strat["strategy_spec_registry_id"] == registry_id

        monkeypatch.setattr(adapter_base, "http_request_json", lambda *a, **k: [])
        assert svc.get_strategy(strategy_id) is None
        with pytest.raises(HTTPException) as not_found_exc:
            svc.ensure_strategy_exists(strategy_id)
        assert not_found_exc.value.status_code == 404

        monkeypatch.setattr(adapter_base, "http_request_json", lambda *a, **k: [make_entry("tenant-other")])
        with pytest.raises(HTTPException) as svc_403_exc:
            svc.get_strategy(strategy_id)
        assert svc_403_exc.value.status_code == 403

        monkeypatch.setattr(adapter_base, "http_request_json", raise_404)
        with pytest.raises(HTTPException) as svc_404_exc:
            svc.get_strategy(strategy_id)
        assert svc_404_exc.value.status_code == 503

        monkeypatch.setattr(adapter_base, "http_request_json", raise_503)
        with pytest.raises(HTTPException) as svc_503_exc:
            svc.get_strategy(strategy_id)
        assert svc_503_exc.value.status_code == 503

        # Missing Registry URL or caller authorization with injected strategy_write_owner
        # must fail closed with 503 DEPENDENCY_UNAVAILABLE (never local fallback or None/[]).
        monkeypatch.delenv("PANTHEON_REGISTRY_API_URL", raising=False)
        monkeypatch.delenv("PANTHEON_REGISTRY_URL", raising=False)
        for fn in (
            lambda: svc.get_strategy(strategy_id),
            lambda: svc.get_strategy_spec_detail(strategy_id),
            lambda: svc.list_strategy_spec_versions(strategy_id),
            lambda: svc.list_strategy_summaries(),
        ):
            with pytest.raises(HTTPException) as missing_cfg_exc:
                fn()
            assert missing_cfg_exc.value.status_code == 503

        monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.internal")
        tok_no_auth = owner_reads.authorization.set(None)
        try:
            for fn in (
                lambda: svc.get_strategy(strategy_id),
                lambda: svc.get_strategy_spec_detail(strategy_id),
                lambda: svc.list_strategy_spec_versions(strategy_id),
                lambda: svc.list_strategy_summaries(),
            ):
                with pytest.raises(HTTPException) as missing_auth_exc:
                    fn()
                assert missing_auth_exc.value.status_code == 503
        finally:
            owner_reads.authorization.reset(tok_no_auth)
    finally:
        owner_reads.authorization.reset(tok_auth)
        owner_reads.selected_tenant.reset(tok_tenant)


def test_bff_strategies_and_kw05_list_against_real_registry_app(monkeypatch) -> None:
    import urllib.error
    from urllib.parse import urlparse
    from services.control_plane.bff.command_adapters import base as adapter_base
    from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware
    from services.control_plane.bff.ports import ReadSurfacePorts
    from services.control_plane.bff.ports.research_knowledge_source import DefaultResearchKnowledgeSourcePort
    from services.control_plane.bff.strategies.router import create_strategies_router
    from services.registry import service as reg_service
    from services.registry import storage as reg_storage

    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "1")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.internal")
    monkeypatch.setenv("PANTHEON_REGISTRY_AUTH_MODE", "permissive")

    remote_store = reg_storage.RegistryStore()
    remote_reg_svc = reg_service.RegistryService(store=remote_store)
    monkeypatch.setattr(reg_service, "get_registry_service", lambda: remote_reg_svc)
    monkeypatch.setattr(reg_service, "get_store", lambda: remote_store)

    class _PoisonedLocalStore:
        def list_all_entries(self):
            raise AssertionError("BFF must not read process-local services.registry.storage.get_store()")

    monkeypatch.setattr(reg_storage, "get_store", lambda: _PoisonedLocalStore())

    reg_client = TestClient(reg_service.app)

    strategy_id = "strategy-persona-aa060d53ff712cbba704"
    reg_v1 = "reg-strategy-spec-persona-aa060d53ff712cbba704"
    reg_v2 = "reg-strategy-spec-persona-aa060d53ff712cbba704-v2"
    persona_id = "persona-132519698e6199e80326"

    spec_v1 = {
        "strategy_id": strategy_id,
        "spec_version": "1.0",
        "title": "Original Workshop Strategy Spec",
        "hypothesis": "Cross-session orderflow imbalance mean-reverts.",
        "objective": "Capture intraday reversion with bounded drawdown.",
        "market_scope": {"symbols": ["BTCUSDT"], "frequency": "1h"},
        "data_dependencies": [{"ref": "seed-workshop-1", "kind": "note"}],
        "execution_profile": {
            "signal_schema_version": "research-signal-v1",
            "quantity_type": "PERCENT_PORTFOLIO",
            "execution_mode_hint": "paper",
        },
        "evaluation_plan": {"metrics": ["sharpe_ratio"], "candidate_gate": "schema-valid"},
        "governance": {"approval_required": True, "risk_profile": "research"},
        "provenance": {
            "source_kind": "workflow",
            "created_at": "2026-10-18T10:00:00Z",
            "source_refs": ["seed-workshop-1"],
            "created_by": "op-workshop",
        },
    }
    r1 = reg_client.post(
        "/api/registry/strategy-specs",
        json={
            "registry_id": reg_v1,
            "strategy_id": strategy_id,
            "version": "1.0.0",
            "source_seed_id": "seed-workshop-1",
            "metadata": {"tenant_id": "tenant-dev", "persona_id": persona_id, "title": "Original Workshop Strategy Spec"},
            "strategy_spec": spec_v1,
        },
        headers={"Authorization": "Bearer svc-bff:admin,service"},
    )
    assert r1.status_code == 200, r1.text
    remote_store._entries[reg_v1].owner_tenant = "tenant-dev"

    spec_v2 = json.loads(json.dumps(spec_v1))
    spec_v2["title"] = "Original Workshop Strategy Spec v2"
    r2 = reg_client.post(
        "/api/registry/strategy-specs",
        json={
            "registry_id": reg_v2,
            "strategy_id": strategy_id,
            "version": "1.1.0",
            "artifact_state": "candidate",
            "lineage": {"parent_registry_ids": [reg_v1], "source_strategy_spec_id": reg_v1},
            "metadata": {"tenant_id": "tenant-dev", "persona_id": persona_id, "title": "Original Workshop Strategy Spec v2"},
            "strategy_spec": spec_v2,
        },
        headers={"Authorization": "Bearer svc-bff:admin,service"},
    )
    assert r2.status_code == 200, r2.text
    remote_store._entries[reg_v2].owner_tenant = "tenant-dev"

    spec_other = json.loads(json.dumps(spec_v1))
    spec_other["strategy_id"] = "strategy-other-tenant"
    r_other = reg_client.post(
        "/api/registry/strategy-specs",
        json={
            "registry_id": "reg-strategy-spec-other-tenant",
            "strategy_id": "strategy-other-tenant",
            "version": "1.0.0",
            "source_seed_id": "seed-other",
            "metadata": {"tenant_id": "tenant-other"},
            "strategy_spec": spec_other,
        },
        headers={"Authorization": "Bearer svc-bff:admin,service"},
    )
    assert r_other.status_code == 200, r_other.text
    remote_store._entries["reg-strategy-spec-other-tenant"].owner_tenant = "tenant-other"

    http_paths: list[str] = []

    def bridge_http_request_json(url: str, *, method: str = "GET", payload=None, auth_token=None, tenant_id=None, **kwargs):
        parsed = urlparse(url)
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        http_paths.append(path)
        hdrs = {}
        if auth_token:
            hdrs["Authorization"] = "Bearer svc-bff:admin,service"
        if tenant_id:
            hdrs["X-Tenant-Id"] = tenant_id
        resp = reg_client.request(method, path, json=payload, headers=hdrs)
        if resp.status_code >= 400:
            raise urllib.error.HTTPError(url, resp.status_code, resp.text, hdrs=None, fp=None)
        return resp.json()

    monkeypatch.setattr(adapter_base, "http_request_json", bridge_http_request_json)

    port = DefaultResearchKnowledgeSourcePort()
    read_ports = ReadSurfacePorts(research_knowledge_source=port)
    app = _build_test_app(read_ports)
    app.include_router(
        create_strategies_router(
            read_surface=read_ports,
            list_strategy_summaries=port.list_strategy_specs,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
            utc_now=utc_now,
        )
    )
    app.add_middleware(OwnerReadContextMiddleware)

    dev_headers = {"Authorization": "Bearer op-2:operator:tenant-dev", "X-Tenant-Id": "tenant-dev"}
    empty_headers = {"Authorization": "Bearer op-2:operator:tenant-empty", "X-Tenant-Id": "tenant-empty"}

    with TestClient(app, raise_server_exceptions=False) as client:
        # 1. GET /bff/strategies and GET /api/v1/knowledge/strategy-specs return canonical tenant-dev strategy
        http_paths.clear()
        bff_list = client.get("/bff/strategies", headers=dev_headers)
        assert bff_list.status_code == 200, bff_list.text
        bff_payload = bff_list.json()
        assert bff_payload["page_info"]["total"] == 1
        assert bff_payload["items"][0]["id"] == strategy_id
        assert bff_payload["items"][0]["name"] == "Original Workshop Strategy Spec v2"
        assert bff_payload["items"][0]["state"] == "candidate"
        assert bff_payload["items"][0]["personaIds"] == [persona_id]
        # Zero per-row detail fanout: only /api/registry/strategy-specs was called
        assert http_paths == ["/api/registry/strategy-specs"]

        kw05_list = client.get("/api/v1/knowledge/strategy-specs", headers=dev_headers)
        assert kw05_list.status_code == 200, kw05_list.text
        kw05_items = kw05_list.json()["items"]
        assert len(kw05_items) == 1
        assert kw05_items[0]["strategy_id"] == strategy_id
        assert kw05_items[0]["current_spec_version_id"] == reg_v2
        assert kw05_items[0]["current_spec_version"] == "1.1.0"
        assert kw05_items[0]["version_count"] == 2
        assert kw05_items[0]["lifecycle_state"] == "candidate"

        # 2. Filters on GET /bff/strategies and GET /api/v1/knowledge/strategy-specs
        assert client.get(f"/bff/strategies?persona_id={persona_id}", headers=dev_headers).json()["page_info"]["total"] == 1
        assert client.get("/bff/strategies?persona_id=persona-none", headers=dev_headers).json()["page_info"]["total"] == 0
        assert client.get("/bff/strategies?state=candidate", headers=dev_headers).json()["page_info"]["total"] == 1
        assert client.get("/bff/strategies?state=approved", headers=dev_headers).json()["page_info"]["total"] == 0

        # 3. Legitimate empty tenant returns 200 [] / total=0
        empty_bff = client.get("/bff/strategies", headers=empty_headers)
        assert empty_bff.status_code == 200, empty_bff.text
        assert empty_bff.json()["items"] == []
        assert empty_bff.json()["page_info"]["total"] == 0

        empty_kw05 = client.get("/api/v1/knowledge/strategy-specs", headers=empty_headers)
        assert empty_kw05.status_code == 200, empty_kw05.text
        assert empty_kw05.json()["items"] == []

        # 4. Foreign tenant or unavailable/malformed Registry fails closed on /bff/strategies and /api/v1/knowledge/strategy-specs
        monkeypatch.setattr(
            adapter_base,
            "http_request_json",
            lambda *a, **k: [{"registry_id": "reg-foreign", "artifact_type": "strategy_spec", "strategy_id": "s-1", "version": "1.0.0", "owner_tenant": "tenant-foreign"}],
        )
        assert client.get("/bff/strategies", headers=dev_headers).status_code == 403
        assert client.get("/api/v1/knowledge/strategy-specs", headers=dev_headers).status_code == 403

        monkeypatch.setattr(adapter_base, "http_request_json", lambda *a, **k: {"malformed": True})
        assert client.get("/bff/strategies", headers=dev_headers).status_code == 503
        assert client.get("/api/v1/knowledge/strategy-specs", headers=dev_headers).status_code == 503

        monkeypatch.delenv("PANTHEON_REGISTRY_API_URL", raising=False)
        monkeypatch.delenv("PANTHEON_REGISTRY_URL", raising=False)
        assert client.get("/bff/strategies", headers=dev_headers).status_code == 503
        assert client.get("/api/v1/knowledge/strategy-specs", headers=dev_headers).status_code == 503

