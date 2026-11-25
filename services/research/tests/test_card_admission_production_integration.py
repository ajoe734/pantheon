"""CARD-ADMISSION-PRODUCTION-WIRING-20261115: the deployed worker admits a card spec through real owners.

The compose service entry point (``replication_controller.main``) runs against the real Governance
application (``services.governance.main:app``) with the real issued dev paper grant file and the real
issued principals (``scripts/issue_dev_paper_principals``), the real Registry application, the real Research
application (open persona demands) and the real Persona owner application (the demand's persona for the
existing discovery scorer). Neighbours are reached over their real HTTP routes through one in-process
transport; only the Governance decision store is the in-memory mirror of ``execute_command`` that the
existing operator tests use (no database in the sandbox) and the gate's backtest protocol is the recorded
governed evaluation the other card tests use. No owner is replaced by a hand-written fake.
"""
from __future__ import annotations

import hashlib
import io
import json
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from scripts import issue_dev_paper_principals as issuer
from services.governance import approval_authority, paper_approval_scope
from services.governance import main as gov_main
from services.governance.approval_authority import ApprovalReader
from services.governance.test_approval_authority_postgres import OperatorMemoryStore
from services.persona.write_owner import PersistentPersonaOwner, create_app as create_persona_app
from services.registry.service import get_registry_service
from services.research.alpha_replication import replication_controller as controller
from services.research.alpha_replication.admission import ReplicationAdmissionStore
from services.research.tests.test_candidate_registration import (
    BARS_FIXTURE,
    OwnerEnvironment,
    _governed_passing_evaluation_for_artifact,
)
from services.research.tests.test_hypothesis_card_gate_candidate import (  # noqa: F401  (autouse fixture)
    _attested_market,
    _distill,
    _restore_process_globals,
)
from services.research.tests.test_persona_demand_routes_auth import AUDIENCE, ISSUER, SECRET, TENANT

PERSONA = "persona-card-alpha"
WORKER = "alpha-replication-worker"


FITTING_PERSONA = {"preferred_markets": ["TWSE listed"], "asset_classes": ["equity"], "holding_period": "1m"}
UNFITTING_PERSONA = {"preferred_markets": ["NASDAQ"], "asset_classes": ["crypto"], "holding_period": "1s"}


def _persona_create_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "actor_id": "operator-persona", "persona_id": PERSONA, "name": "Card Alpha", "mandate": "TW equity",
        "strategy_family": "factor",
        # Persona profile fields the existing discovery scorer reads (market, asset class, cadence).
        "metadata": dict(FITTING_PERSONA),
        "required_data_sources": [{"dataset": "tw_price_daily", "market": "TW", "cadence": "daily",
                                   "source_class": "seed_only"}],
    }
    payload.update(overrides)
    return payload


def _persona_headers(actor_id: str, *roles: str) -> dict[str, str]:
    import time

    from services.runtime_auth_inbound import encode_jwt_hs256

    claims = {"sub": actor_id, "roles": list(roles), "tenant_id": TENANT, "iss": ISSUER, "aud": AUDIENCE,
              "exp": int(time.time()) + 900}
    return {"Authorization": "Bearer " + encode_jwt_hs256(claims, secret=SECRET)}


class _Response(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200) -> None:
        super().__init__(body)
        self.status = status
        self.headers = type("H", (), {"get_content_type": staticmethod(lambda: "application/json")})()

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class Transport:
    """One in-process HTTP transport: host -> the neighbouring owner's real application."""

    def __init__(self) -> None:
        self.apps: dict[str, TestClient] = {}
        self.calls: list[tuple[str, str, str]] = []

    def _send(self, request: Any) -> _Response:
        parts = urllib.parse.urlsplit(request.full_url)
        client = self.apps[parts.hostname]
        path = parts.path + ("?" + parts.query if parts.query else "")
        self.calls.append((parts.hostname, request.get_method(), parts.path))
        headers = {key: value for key, value in request.header_items()}
        body = json.loads(request.data.decode()) if request.data else None
        response = client.request(request.get_method(), path, json=body, headers=headers)
        if response.status_code >= 400:
            raise urllib.error.HTTPError(request.full_url, response.status_code, response.text, {},
                                         io.BytesIO(response.content))
        return _Response(response.content, response.status_code)

    def urlopen(self, request: Any, timeout: float | None = None) -> _Response:
        return self._send(request)

    def open(self, request: Any, timeout: float | None = None) -> _Response:
        return self._send(request)


class World:
    """The deployed environment of the alpha replication worker, with every neighbour a real application."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, dataset: dict[str, Any],
                 persona_metadata: dict[str, Any] | None = None) -> None:
        self.tmp, self.mp = tmp_path, monkeypatch
        self.owners = OwnerEnvironment(tmp_path, monkeypatch)  # real Research + Registry applications
        self.transport = Transport()

        # Real issued principals and the real grant file.
        principals = tmp_path / "principals"
        issuing = {
            "PANTHEON_ENV": "dev", "PANTHEON_DEV_BFF_TENANT_ID": TENANT,
            "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "true", "PANTHEON_DEV_BFF_JWT_SECRET": SECRET,
            "PANTHEON_DEV_BFF_JWT_ISSUER": ISSUER, "PANTHEON_DEV_BFF_JWT_AUDIENCE": AUDIENCE,
        }
        issuer.refresh_files(principals, issuing)
        self.issued = issuer.issue_environment(issuing)
        self.grant_file = principals / "governance" / "grant"

        # Real Governance application (decision store: the in-memory mirror of execute_command).
        for name, value in {"JWT_SECRET": SECRET, "JWT_ISSUER": ISSUER, "JWT_AUDIENCE": AUDIENCE}.items():
            monkeypatch.setenv(f"PANTHEON_GOVERNANCE_{name}", value)
        monkeypatch.setenv("GOVERNANCE_DEV_PAPER_GRANT_FILE", str(self.grant_file))
        monkeypatch.setenv("GOVERNANCE_DEV_PAPER_APPROVAL_ENABLED", "true")
        monkeypatch.setenv("PANTHEON_ENV", "dev")
        monkeypatch.setenv("GOVERNANCE_REGISTRY_BASE_URL", "http://registry.test")
        monkeypatch.setenv("GOVERNANCE_REGISTRY_SERVICE_TOKEN", self.issued["GOVERNANCE_REGISTRY_SERVICE_TOKEN"])
        monkeypatch.setattr(gov_main, "store", OperatorMemoryStore())
        self.store = gov_main.store
        self.governance = TestClient(gov_main.app)

        # Real Persona owner application.
        monkeypatch.setenv("PERSONA_AUTH_MODE", "strict")
        monkeypatch.setenv("PERSONA_JWT_SECRET", SECRET)
        monkeypatch.setenv("PERSONA_JWT_ISSUER", ISSUER)
        monkeypatch.setenv("PERSONA_JWT_AUDIENCE", AUDIENCE)
        persona_owner = PersistentPersonaOwner.from_json_path(tmp_path / "personas.json")
        self.persona = TestClient(create_persona_app(persona_owner))
        created = self.persona.post(
            "/api/personas", json=_persona_create_payload(persona_id=PERSONA, tenant_id=TENANT, mandate="TW equity",
                                    metadata=dict(persona_metadata or FITTING_PERSONA)),
            headers=_persona_headers("operator-persona", "persona.admin"))
        assert created.status_code == 201, created.text

        self.transport.apps.update({
            "registry.test": self.owners.registry_client, "gov.test": self.governance,
            "research.test": self.owners.research_client, "persona.test": self.persona,
        })
        monkeypatch.setattr("urllib.request.urlopen", self.transport.urlopen)
        monkeypatch.setattr(approval_authority, "build_opener", lambda *a, **k: self.transport)
        monkeypatch.setattr(paper_approval_scope, "build_opener", lambda *a, **k: self.transport)
        # The Registry reads decisions from the real Governance application, not from OwnerEnvironment's harness.
        reader = ApprovalReader(base_url="http://gov.test", service_token=self.issued["REGISTRY_GOVERNANCE_SERVICE_TOKEN"])
        get_registry_service().approval_reader = reader
        monkeypatch.setattr(approval_authority, "configured_approval_reader", lambda *a, **k: reader)

        # The deployed worker's environment: compose names, mounted credential files.
        data_dir = self.owners.data_dir
        # The governed market dataset reads the stored daily bars only: no persona authority file exists.
        from services.research import governed_market_dataset as gmd
        from services.research.tests import test_hypothesis_card_gate_candidate as card_candidate
        from services.research.tests.test_governed_market_dataset import store_from_records

        store_root = store_from_records(tmp_path / "market_data_store", dataset["records"])
        monkeypatch.setattr(gmd, "_as_of", lambda now: datetime(2026, 11, 2, tzinfo=timezone.utc))
        # the card binds to the governed market reference (as the distillation env does), not a persona dataset
        monkeypatch.setattr(card_candidate, "_dataset", lambda: dataset)
        env = {
            "ALPHA_REPLICATION_DATA_DIR": str(data_dir), "ALPHA_REPLICATION_CONTROLLER_MAX_TICKS": "1",
            "ALPHA_REPLICATION_CONTROLLER_INTERVAL_SECONDS": "60", "PANTHEON_TENANT_ID": TENANT,
            "PANTHEON_ENV": "dev", "GIT_SHA": "card-admission-test",
            "STRATEGY_SPEC_SEED_STORE_PATH": str(tmp_path / "no-seeds.jsonl"),
            "PANTHEON_REGISTRY_URL": "http://registry.test", "PANTHEON_GOVERNANCE_URL": "http://gov.test",
            "RESEARCH_API_URL": "http://research.test", "RESEARCH_ORCHESTRATOR_URL": "http://research.test",
            "PERSONA_API_URL": "http://persona.test",
            "RESEARCH_GOVERNANCE_PROPOSER_ID": self.issued["RESEARCH_GOVERNANCE_PROPOSER_ID"],
            "RESEARCH_GOVERNANCE_GATE_ID": self.issued["RESEARCH_GOVERNANCE_GATE_ID"],
            "GOVERNED_MARKET_STORE_ROOT": str(store_root),
            "ALPHA_REPLICATION_CARD_ADMISSION_ENABLED": "1",
        }
        for variable in issuer.CONSUMER_FILES[WORKER]:
            env[variable + "_FILE"] = str(principals / WORKER / variable)
        self.worker_env = env
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        for name in ("DATABASE_URL",):
            monkeypatch.delenv(name, raising=False)

    def run_worker_entry_point(self) -> dict[str, Any]:
        """Start the real compose entry point (``python -m ...replication_controller``) for one tick."""
        assert controller.main() == 0
        state = json.loads((self.owners.data_dir / "controller_state.json").read_text())["state"]
        assert state.get("last_failure_reason") in (None, ""), state["last_failure_reason"]
        return state

    def open_demand(self) -> dict[str, Any]:
        return self.owners.research.research_demand_store.open(TENANT, {
            "persona_id": PERSONA, "mandate": "TW equity", "missing_datasets": [], "tried_families": [],
            "short_measure": "effective_bets", "pool_version": 0})


def _dataset() -> dict[str, Any]:
    """Recorded official bars, bound to the governed market reference the way a card's binding names it."""
    from services.research.alpha_replication.card_production_wiring import GOVERNED_MARKET_DATASET_ID

    return {**json.loads(BARS_FIXTURE.read_text(encoding="utf-8"))["dataset"], "dataset_id": GOVERNED_MARKET_DATASET_ID}


def _store_card_spec(world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """The card -> StrategySpec the distillation controller produced, stored in the real Registry."""
    (tmp_path / "d").mkdir()
    (payload,) = _distill(tmp_path / "d", monkeypatch, predictions=True)
    payload["metadata"]["tenant_id"] = TENANT
    posted = world.owners.registry_client.post("/api/registry/strategy-specs", json=payload,
                                               headers=world.owners.registry_headers(TENANT))
    assert posted.status_code in (200, 201), posted.text
    return payload["registry_id"]


def _record_passing_protocol(monkeypatch: pytest.MonkeyPatch, dataset: dict[str, Any]) -> None:
    def recorded_protocol(**kw: Any) -> dict[str, Any]:
        evaluation = _governed_passing_evaluation_for_artifact(kw["strategy_artifact"], dataset, run_id=kw["run_id"])
        if kw["run_id"].endswith("-entry-only"):
            evaluation["out_of_sample"] = {**evaluation["out_of_sample"],
                                           "sharpe_ratio": evaluation["out_of_sample"]["sharpe_ratio"] - 1.0}
        return evaluation

    for target in ("services.research.vectorbt.adapter.vectorbt_adapter.run_replication_backtest_protocol",
                   "services.research.replication.gate.run_replication_backtest_protocol"):
        monkeypatch.setattr(target, recorded_protocol, raising=False)


def _gate_outcomes(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    from services.research.replication.gate import ReplicationGate

    real_eval = ReplicationGate.evaluate_candidate
    seen: list[Any] = []
    monkeypatch.setattr(ReplicationGate, "evaluate_candidate",
                        lambda self, request: seen.append(real_eval(self, request)) or seen[-1])
    return seen


def _admitted_spec(world: World, registry_id: str, demand: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Assert the admission end to end through the real owners; returns the approved Registry entry."""
    reconcile = state["reconcile"]
    assert reconcile["card_path"]["enabled"] is True and reconcile["card_path"]["error"] is None, reconcile
    (result,) = [a for a in reconcile["card_admissions"] if a["registry_id"] == registry_id]
    assert result["status"] == "admitted", result
    admission = ReplicationAdmissionStore(world.owners.data_dir).get_admission(TENANT, registry_id)
    assert admission["demand_ref"] == demand["demand_id"] and admission["persona_id"] == PERSONA
    assert admission["requested_by"] == issuer.CARD_PROPOSER_SUBJECT
    assert admission["approver"] == issuer.PAPER_SUBJECT != admission["requested_by"]

    # The real Governance decision: proposed by the research principal, decided by the paper gate.
    decision = world.store.get(admission["approval_decision_id"]).to_dict()
    assert decision["decision"] == "approved" and decision["decision_state"] == "decided"
    assert decision["owner_user_id"] == issuer.CARD_PROPOSER_SUBJECT and decision["proposal_id"] == demand["demand_id"]
    assert decision["authorization_scope"]["allowed_target_stages"] == ["paper"]
    assert any(ref["ref_id"].startswith("dev-paper-grant:card-admission-paper-research:")
               for ref in decision["evidence_refs"]), decision["evidence_refs"]
    entry = world.owners.registry_client.get(f"/api/registry/strategy-specs/{registry_id}",
                                             headers=world.owners.registry_headers(TENANT)).json()["entry"]
    assert entry["artifact_state"] == "approved" and entry["approval_decision_id"] == admission["approval_decision_id"]
    hosts = {host for host, _method, _path in world.transport.calls}
    assert {"registry.test", "gov.test", "research.test", "persona.test"} <= hosts
    return entry


def test_matched_spec_is_admitted_and_gated_but_a_screened_spec_is_refused_without_the_governed_market(
        tmp_path, monkeypatch):
    """Criteria 5 and 6 with the production resolver: the governed dataset authority supplies daily bars only."""
    dataset = _dataset()
    world = World(tmp_path, monkeypatch, dataset=dataset)
    registry_id = _store_card_spec(world, tmp_path, monkeypatch)
    demand = world.open_demand()
    _record_passing_protocol(monkeypatch, dataset)
    gate = _gate_outcomes(monkeypatch)

    state = world.run_worker_entry_point()

    entry = _admitted_spec(world, registry_id, demand, state)
    failed = [r for response in gate for r in response.results if not r.passed]
    assert [r.criterion_id for r in failed] == ["real_backtest_validation"], failed
    # The resolver cannot attest delisted/suspended coverage: the dataset it returns carries no
    # candidate_pool_market, so the screened spec is refused (named input, never a guessed pass).
    assert failed[0].details["status"] == "screen_contribution_unavailable"
    assert failed[0].details["reason"] == "screen_contribution_inputs_missing"
    assert state["reconcile"]["card_registrations"] == []
    artifact_id = entry["metadata"]["strategy_spec"]["metadata"]["strategy_artifact"]["artifact_id"]
    pooled = world.owners.registry_client.get(f"/api/registry/strategy-artifacts/{artifact_id}",
                                              headers=world.owners.registry_headers(TENANT))
    assert pooled.status_code == 404 or pooled.json()["entry"]["artifact_state"] != "approved"


def test_a_pass_registers_the_pool_candidate_when_the_governed_market_attests_survivorship(tmp_path, monkeypatch):
    """Criterion 5 pass path: only the attested market is supplied by the test (as in the card gate test)."""
    from services.research.alpha_replication import card_production_wiring as wiring

    dataset = _dataset()
    world = World(tmp_path, monkeypatch, dataset=dataset)
    registry_id = _store_card_spec(world, tmp_path, monkeypatch)
    demand = world.open_demand()
    _record_passing_protocol(monkeypatch, dataset)

    real_builder = wiring.build_dataset_resolver

    def attested_resolver(env, now=None):
        real = real_builder(env, now)
        return lambda binding: {**real(binding), "candidate_pool_market": _attested_market()}

    monkeypatch.setattr(wiring, "build_dataset_resolver", attested_resolver)

    state = world.run_worker_entry_point()

    approved = _admitted_spec(world, registry_id, demand, state)
    artifact = approved["metadata"]["strategy_spec"]["metadata"]["strategy_artifact"]
    (registration,) = state["reconcile"]["card_registrations"]
    assert registration.get("reason") is None, registration
    assert registration["status"] == "approved" and registration["demand_ref"] == demand["demand_id"]
    assert registration["registry_id"] == artifact["artifact_id"]
    got = world.owners.registry_client.get(f"/api/registry/strategy-artifacts/{artifact['artifact_id']}",
                                           headers=world.owners.registry_headers(TENANT)).json()["entry"]
    assert got["artifact_state"] == "approved" and got["replication_success"] is True
    # The pool candidate's own Governance decision: same research proposer, decided by the same paper gate.
    (bundle_decision,) = [d.to_dict() for d in (world.store.get(k) for k in list(world.store.rows))
                          if d.to_dict()["target_id"] == artifact["artifact_id"]]
    assert bundle_decision["owner_user_id"] == issuer.CARD_PROPOSER_SUBJECT and bundle_decision["decision"] == "approved"
    assert bundle_decision["authorization_scope"]["allowed_target_stages"] == ["paper"]
    assert bundle_decision["proposal_id"] == demand["demand_id"] and bundle_decision["persona_id"] == PERSONA


def test_an_unmatched_spec_stays_a_draft_with_no_admission_and_no_governance_decision(tmp_path, monkeypatch):
    dataset = _dataset()
    world = World(tmp_path, monkeypatch, dataset=dataset, persona_metadata=UNFITTING_PERSONA)
    registry_id = _store_card_spec(world, tmp_path, monkeypatch)
    world.open_demand()

    state = world.run_worker_entry_point()

    assert state["reconcile"]["card_admissions"] == [
        {"status": "draft", "reason": "no_open_demand_match", "registry_id": registry_id}]
    assert ReplicationAdmissionStore(world.owners.data_dir).list_admissions() == []
    assert world.store.rows == {}
    entry = world.owners.registry_client.get(f"/api/registry/strategy-specs/{registry_id}",
                                             headers=world.owners.registry_headers(TENANT)).json()["entry"]
    assert entry["artifact_state"] == "draft"
    assert state["reconcile"]["created_run_ids"] == [] and state["reconcile"]["card_registrations"] == []


def test_a_grant_without_the_card_widening_leaves_the_spec_unadmitted_with_a_readable_reason(tmp_path, monkeypatch):
    """Criterion 3 reproduction through the real application: today's grant denies the research proposer."""
    dataset = _dataset()
    world = World(tmp_path, monkeypatch, dataset=dataset)
    world.grant_file.write_text("enabled;persona_lifecycle=paper_ladder;proposer=" + issuer.ENROLLMENT_PROPOSER_SUBJECT)
    world.grant_file.chmod(0o600)
    registry_id = _store_card_spec(world, tmp_path, monkeypatch)
    world.open_demand()

    state = world.run_worker_entry_point()

    (result,) = state["reconcile"]["card_admissions"]
    assert result["status"] == "error" and result["registry_id"] == registry_id
    assert "review_card_approval" in result["reason"] and "403" in result["reason"], result
    assert ReplicationAdmissionStore(world.owners.data_dir).list_admissions() == []
    entry = world.owners.registry_client.get(f"/api/registry/strategy-specs/{registry_id}",
                                             headers=world.owners.registry_headers(TENANT)).json()["entry"]
    assert entry["artifact_state"] == "candidate"  # advanced for review, never approved


def test_missing_credential_disables_the_card_path_with_a_readable_reason_on_the_health_file(tmp_path, monkeypatch):
    dataset = _dataset()
    world = World(tmp_path, monkeypatch, dataset=dataset)
    registry_id = _store_card_spec(world, tmp_path, monkeypatch)
    world.open_demand()
    monkeypatch.delenv("RESEARCH_GOVERNANCE_GATE_TOKEN_FILE")

    state = world.run_worker_entry_point()

    card = state["reconcile"]["card_path"]
    assert card["enabled"] is False and "RESEARCH_GOVERNANCE_GATE_TOKEN" in card["reason"], card
    assert state["reconcile"]["card_admissions"] == []
    entry = world.owners.registry_client.get(f"/api/registry/strategy-specs/{registry_id}",
                                             headers=world.owners.registry_headers(TENANT)).json()["entry"]
    assert entry["artifact_state"] == "draft" and world.store.rows == {}
