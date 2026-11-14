"""Exercise the deployed persona lifespan and persistent owner CAS path."""
import importlib.util
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.persona.consult_triggers import PANEL_PERSONA_SPECS
from services.persona.write_owner import (
    create_app, PersistentPersonaOwner, PatchPersonaRequest, PersonaOwnerError,
    AdvancePersonaLifecycleRequest,
)


def load_persona_main():
    path = Path(__file__).with_name("main.py")
    spec = importlib.util.spec_from_file_location("panel_bootstrap_persona_main", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def test_startup_and_second_replica_are_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_DEFAULT_TENANT_ID", "tenant-panel")
    monkeypatch.setenv("PERSONA_DAILY_CYCLE_ENABLED", "false")
    main = load_persona_main()
    path = tmp_path / "personas.json"
    owner = PersistentPersonaOwner.from_json_path(path)
    monkeypatch.setattr(main, "PERSONA_OWNER_API", create_app(owner))
    with TestClient(main.app) as client:
        status = client.get("/healthz").json()["details"]["panel_personas"]
        assert status["status"] == "ready"
        assert set(status["personas"].values()) == {"created"}
    before = path.read_bytes()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(r["status"] == "ready" for r in pool.map(lambda _: main._bootstrap_panel_personas(main.app), range(2)))
    assert path.read_bytes() == before
    assert len(owner.list()) == 2
    from persona_policy_resolver import PersonaPolicyResolver
    from types import SimpleNamespace
    from services.persona.persona_cycle_controller import ELIGIBLE_STATES
    from services.control_plane.bff.personas.fleet_classification import is_non_trading_persona
    from services.control_plane.bff.personas.service import _pm12_quarterly_ranking_items, paper_advisory_ineligibility
    assert _pm12_quarterly_ranking_items(owner.list_json(), quarter_window={"quarter": "2026-Q4"}) == []
    for row in owner.list_json():
        assert is_non_trading_persona(row)
        assert paper_advisory_ineligibility(row) == "persona_not_active"
        assert row["lifecycle_state"] == "draft" and row["lifecycle_state"] not in ELIGIBLE_STATES
        policy = PersonaPolicyResolver().resolve(SimpleNamespace(**row))
        assert policy.consultable and not policy.effective_tools and not policy.effective_workflows
        for patch in ({"tool_profile_id": "trader"}, {"route_policy_id": "trade"},
                      {"metadata": {"trading": True}}, {"metadata": {"persona_class": "trader"}}):
            with pytest.raises(PersonaOwnerError, match="panel personas"):
                owner.patch(row["persona_id"], PatchPersonaRequest(actor_id="test", **patch))
        with pytest.raises(PersonaOwnerError, match="panel personas"):
            owner.advance_lifecycle(row["persona_id"], AdvancePersonaLifecycleRequest(
                actor_id="test", target_state="research_only"), expected_from_state="draft")


def test_bootstrap_failure_is_visible_and_retried(tmp_path, monkeypatch):
    main = load_persona_main()
    owner = PersistentPersonaOwner.from_json_path(tmp_path / "personas.json")
    monkeypatch.setattr(main, "PERSONA_OWNER_API", create_app(owner))
    monkeypatch.delenv("PERSONA_DEFAULT_TENANT_ID", raising=False)
    monkeypatch.delenv("PANTHEON_TENANT_ID", raising=False)
    assert main._bootstrap_panel_personas(main.app)["status"] == "blocked"
    assert not owner.list()
    monkeypatch.setenv("PERSONA_DEFAULT_TENANT_ID", "tenant-panel")
    assert main._bootstrap_panel_personas(main.app)["status"] == "ready"
