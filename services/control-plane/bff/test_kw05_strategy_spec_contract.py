from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

_MODULE_DIR = Path(__file__).resolve().parent


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


bff_main = _load_module("bff_main_kw05_test_module", _MODULE_DIR / "main.py")
read_store_module = _load_module("bff_read_store_kw05_test_module", _MODULE_DIR / "read_store.py")
ReadSurfaceStore = read_store_module.ReadSurfaceStore


OPERATOR_TOKEN = "Bearer op-2:operator"
FALLBACK_STRATEGY_ID = "strat-0a1b2c3d-9f8e-7d6c-5b4a-3f2e1d0c9b8a"
SERVICE_STRATEGY_ID = "strat-11111111-2222-3333-4444-555555555555"
RETIRED_STRATEGY_ID = "strat-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
DRAFT_STRATEGY_ID = "strat-99999999-8888-7777-6666-555555555555"


@contextmanager
def _seeded_client():
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        client = TestClient(bff_main.app)
        try:
            yield client
        finally:
            bff_main.read_store = original_store


@contextmanager
def _service_backed_client():
    tracked_env = {
        "PANTHEON_BFF_STRATEGY_SPEC_STORE": os.environ.get("PANTHEON_BFF_STRATEGY_SPEC_STORE"),
    }
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

        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        client = TestClient(bff_main.app)
        try:
            yield client
        finally:
            bff_main.read_store = original_store
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
        assert duplicate_response.json()["error"]["code"] == "INVALID_PARAMS"

        noncomparable_response = client.get(
            f"/api/v1/knowledge/strategy-specs/{DRAFT_STRATEGY_ID}/compare?left_version=v1&right_version=v2",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert noncomparable_response.status_code == 422, noncomparable_response.text
        payload = noncomparable_response.json()
        assert payload["error"]["code"] == "INVALID_STATE"
        assert payload["error"]["details"]["precondition_failed"] == "lifecycle_state"
