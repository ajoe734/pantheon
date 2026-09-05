from fastapi.testclient import TestClient

from services.control_plane.bff import main as bff_main


HEADERS = {"Authorization": "Bearer ppl-alloc-003:operator"}


def test_capital_pool_rows_include_persona_binding_summaries(monkeypatch) -> None:
    monkeypatch.setattr(bff_main.read_store, "list_capital_pools", lambda **_: [{"pool_id": "pool-parent"}])
    monkeypatch.setattr(
        bff_main.read_store,
        "list_bindings",
        lambda **_: [
            {"binding_id": "b-a", "persona_id": "persona-a", "capital_pool_id": "pool-parent", "sleeve_id": "sleeve-a", "current_weight": 0.1, "target_weight": 0.12, "status": "active"},
            {"binding_id": "b-b", "persona_id": "persona-b", "capital_pool_id": "pool-parent", "sleeve_id": "sleeve-b", "current_weight": 0.2, "target_weight": 0.18, "status": "active"},
        ],
    )
    with TestClient(bff_main.app) as client:
        response = client.get("/bff/capital-pools", headers=HEADERS)
    assert response.status_code == 200
    row = response.json()["data"][0]
    assert row["persona_binding_count"] == 2
    assert {item["capital_sleeve_id"] for item in row["persona_binding_summaries"]} == {"sleeve-a", "sleeve-b"}
    assert row["persona_binding_summaries"][0]["current_weight"] == 0.1


def test_stage_aware_binding_projection_keeps_paper_pool_as_trace_only() -> None:
    paper = bff_main._persona_fleet_capital_binding_projection(
        persona_id="persona-paper", capital_mode="paper", deployment_stage="paper",
        paper_ledger_id="ledger-paper", live_pool_id=None,
        binding={"capital_pool_id": "legacy-paper-pool", "status": "active"}, runtime={},
        league_entry={}, raw_metadata={}, context_metadata={},
    )
    assert paper["capital_scope"] == "paper_ledger"
    assert paper["capital_scope_id"] == "ledger-paper"
    assert paper["capital_sleeve_id"] is None
    assert paper["capital_binding"]["capital_pool_id"] is None

    canary = bff_main._persona_fleet_capital_binding_projection(
        persona_id="persona-canary", capital_mode="canary", deployment_stage="canary",
        paper_ledger_id=None, live_pool_id="pool-parent",
        binding={"sleeve_id": "sleeve-canary", "current_weight": "0.03", "target_weight": 0.05, "validity": "active"},
        runtime={}, league_entry={}, raw_metadata={}, context_metadata={},
    )
    assert canary["capital_scope"] == "canary_sleeve"
    assert canary["capital_sleeve_id"] == "sleeve-canary"
    assert canary["current_weight"] == 0.03
    assert canary["target_weight"] == 0.05
    assert canary["binding_state"] == "active"


def test_missing_binding_projects_explicit_unbound_state() -> None:
    row = bff_main._persona_fleet_capital_binding_projection(
        persona_id="persona-unbound", capital_mode="none", deployment_stage="none",
        paper_ledger_id=None, live_pool_id=None, binding={}, runtime={}, league_entry={},
        raw_metadata={}, context_metadata={},
    )
    assert row["capital_scope"] == "unbound"
    assert row["capital_scope_id"] is None
    assert row["binding_state"] == "missing"
