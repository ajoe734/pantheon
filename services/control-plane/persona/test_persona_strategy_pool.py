"""Tests for persona_strategy_pool.v1: store rules and the real persona app routes.

Run: python3 -m pytest services/control-plane/persona/test_persona_strategy_pool.py -v
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.foundation.reliable_delivery import AtomicJsonRecordStore
from services.persona.write_owner import CreatePersonaRequest
from services.runtime_auth_inbound import encode_jwt_hs256

_DIR = Path(__file__).resolve().parent
_SECRET = "pool-test-secret"
CHECKSUM = "sha256:" + "a" * 64


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _DIR / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(_DIR))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def _auth(roles, tenant="tenant-a", actor="op-1"):
    claims = {"sub": actor, "roles": roles, "exp": 4102444800, "tenant_id": tenant, "allowed_tenants": [tenant]}
    return {"Authorization": "Bearer " + encode_jwt_hs256(claims, secret=_SECRET), "X-Tenant-Id": tenant}


WRITE = ["persona.admin"]
READ = ["viewer"]


class FakeRegistry:
    """Stands in for the registry owner HTTP boundary (entries keyed by id)."""

    def __init__(self):
        self.entries = {}

    def add(self, rid, *, state="approved", checksum=CHECKSUM, persona=None, baseline="baseline-1"):
        self.entries[rid] = {
            "registry_id": rid, "artifact_state": state, "checksum": checksum,
            "approval_decision_id": baseline, "rollback_target": "rollback-no-trade", "approved_at": "2026-11-01T00:00:00+00:00",
            "owner_tenant": "tenant-a",
            "metadata": {"persona_id": persona, "execution_context": "paper",
                         "persona_capital_binding_id": "pcb-1"} if persona else {},
        }

    def read(self, rid, tenant):
        return self.entries.get(rid, {})

    def lister(self, persona_id, tenant):
        return [e for e in self.entries.values()
                if e["metadata"].get("persona_id") == persona_id and e["artifact_state"] == "approved"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    for k, v in {
        "PERSONA_AUTH_MODE": "strict", "PERSONA_JWT_SECRET": _SECRET, "PANTHEON_BFF_JWT_SECRET": _SECRET,
        "PERSONA_STORE_PATH": str(tmp_path / "personas.json"),
        "PERSONA_CAPABILITY_STORE_PATH": str(tmp_path / "caps.json"),
        "PERSONA_POOL_STORE_PATH": str(tmp_path / "pools.json"),
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("PANTHEON_PERSONA_SERVICE_TOKEN", raising=False)
    module = _load("persona_main_pool_test_module")
    registry = FakeRegistry()
    from persona_strategy_pool import PersonaStrategyPoolStore
    module.POOL_STORE = PersonaStrategyPoolStore(
        AtomicJsonRecordStore(tmp_path / "pools.json"),
        registry_reader=registry.read, owner_lister=registry.lister,
    )
    owner = module.PERSONA_OWNER_API.state.persona_owner
    for pid, tenant in (("persona-1", "tenant-a"), ("persona-2", "tenant-b")):
        owner.create(CreatePersonaRequest(
            actor_id="op", persona_id=pid, name=pid, mandate="m", tenant_id=tenant,
        ))
    return TestClient(module.app), registry, owner


def _member(ref, **kw):
    base = {"artifact_ref": ref, "checksum": CHECKSUM, "stage": "active", "risk_weight": 0.5,
            "baseline_ref": "b-1", "joined_at": "2026-11-12T00:00:00+00:00"}
    base.update(kw)
    return base


def _post(client, members, expected, pid="persona-1", tenant="tenant-a"):
    return client.post(
        f"/api/personas/{pid}/strategy-pool/versions",
        json={"expected_version": expected, "members": members, "change_reason": "test"},
        headers=_auth(WRITE, tenant),
    )


def _decision(client, version, action="reweight", **kw):
    return client.post("/api/personas/persona-1/strategy-pool/decisions",
        json={"expected_version": version, "execution_context": "paper", "action": action, **kw},
        headers=_auth(WRITE))


def _observed_book(checksum, better=False):
    # Test-only book input at the authenticated owner boundary. Production
    # has no fallback books or prices; the OOS replay below uses recorded TWSE.
    from datetime import date, timedelta
    return {"checksum": checksum, "source_ref": "test:virtual-book-owner", "since_inception": True,
            "basis": "underlying_exposure_fraction_net_taiwan_costs_equal_notional",
            "dates": [(date(2026, 9, 1) + timedelta(days=i)).isoformat() for i in range(30)],
            "returns": ([.002, -.0005] if better else [.001, -.0005]) * 15}


def _risk_observations(books, notional=20):
    return [{"date": day, "source_ref": "test:risk-owner", "gross_notional": notional,
             "gross_limit": 100, "drawdown": .01, "drawdown_limit": .1}
            for day in next(iter(books.values()))["dates"][-20:]]


def test_decision_routes_admit_reweight_replace_refuse_and_read_version_history(env):
    """Real HTTP -> store -> policy -> recorded-data replay, no patched evaluator.

    Registry is the existing FakeRegistry boundary. Its test-only constant-long
    approval proof is not represented as a real gate approval or hosted proof.
    """
    import copy
    from services.registry.strategy_artifact import strategy_artifact_checksum
    from services.research.tests.test_pool_admission import entries_and_members, recorded_dataset

    client, reg, owner = env
    entries, one, two = entries_and_members()
    current = entries[two["artifact_ref"]]
    candidate = copy.deepcopy(entries[one["artifact_ref"]])
    artifact = candidate["metadata"]["strategy_artifact"]
    artifact["strategy_logic"].update(positive_action="BUY", non_positive_action="BUY")
    artifact["parameters"]["zero_momentum_action"] = "BUY"
    candidate["checksum"] = strategy_artifact_checksum(artifact)
    candidate["replication_proof"]["owner_verified_binding"]["artifact_checksum"] = candidate["checksum"]
    reg.entries = {"current": {**current, "registry_id": "current"},
                   "candidate": {**candidate, "registry_id": "candidate"},
                   "stronger": {**candidate, "registry_id": "stronger"},
                   "weak": {**entries[one["artifact_ref"]], "registry_id": "weak"}}
    assert _post(client, [_member("current", checksum=current["checksum"], risk_weight=.7)], 0).status_code == 201
    lifecycle = owner.get("persona-1").lifecycle_state
    books = {ref: _observed_book(entry["checksum"], better=ref in {"candidate", "stronger"})
             for ref, entry in reg.entries.items()}
    common = {"books": books, "dataset": recorded_dataset(), "observations": _risk_observations(books)}
    admitted = _decision(client, 1, "admit", candidate={"artifact_ref": "candidate", "checksum": candidate["checksum"]}, **common)
    assert admitted.status_code == 201, admitted.text
    assert admitted.json()["decision"]["status"] == "applied", admitted.text
    trial = admitted.json()["members"][-1]
    assert trial["stage"] == "trial" and trial["risk_weight"] <= .1
    weighted = _decision(client, 2, **common)
    assert weighted.status_code == 201 and weighted.json()["decision"]["status"] == "applied", weighted.text
    assert sum(m["risk_weight"] for m in weighted.json()["members"]) == pytest.approx(1)
    replaced = _decision(client, 3, "admit", candidate={"artifact_ref": "stronger", "checksum": candidate["checksum"]}, **common)
    assert replaced.status_code == 201 and replaced.json()["decision"]["status"] == "applied", replaced.text
    assert replaced.json()["decision"]["replaced_artifact_ref"] == "current"
    assert replaced.json()["members"][0]["stage"] == "removed"
    refused = _decision(client, 4, "admit", candidate={"artifact_ref": "weak", "checksum": reg.entries["weak"]["checksum"]}, **common)
    assert refused.status_code == 201 and refused.json()["decision"]["status"] == "refused", refused.text
    assert refused.json()["members"] == replaced.json()["members"]
    assert refused.json()["decision"]["persona_trial_count"] == 3
    history = client.get("/api/personas/persona-1/strategy-pool/versions", headers=_auth(READ)).json()["versions"]
    assert [v["pool_version"] for v in history] == [1, 2, 3, 4, 5]
    assert all(b["previous_digest"] == a["digest"] for a, b in zip(history, history[1:]))
    assert owner.get("persona-1").lifecycle_state == lifecycle
    assert all(v["decision"]["execution_status"] == "requires_approved_envelope" for v in history[1:])


def test_missing_books_record_refusal_and_read_health_unknown(env):
    client, reg, _ = env
    reg.add("current")
    _post(client, [_member("current")], 0)
    response = _decision(client, 1)
    assert response.status_code == 201
    decision = response.json()["decision"]
    assert decision["status"] == "refused" and decision["reasons"] == ["virtual_book_source_missing"]
    assert response.json()["members"][0]["risk_weight"] == .5
    result = client.post("/api/personas/persona-1/strategy-pool/health", json={}, headers=_auth(READ))
    assert result.status_code == 200 and result.json()["gross_utilisation"]["status"] == "unknown"
    assert _decision(client, 1).status_code == 409


def test_decisions_fail_auth_tenant_and_paper_scope(env):
    client, _, _ = env
    url = "/api/personas/persona-1/strategy-pool/decisions"
    body = {"expected_version": 1, "execution_context": "paper", "action": "reweight"}
    assert client.post(url, json=body).status_code == 401
    assert client.post(url, json=body, headers=_auth(READ)).status_code == 403
    assert client.post(url.replace("persona-1", "persona-2"), json=body, headers=_auth(WRITE)).status_code == 403
    assert client.post(url, json={**body, "execution_context": "live"}, headers=_auth(WRITE)).status_code == 422
    assert client.post(url, json={**body, "persona_trial_count": 0}, headers=_auth(WRITE)).status_code == 422


def test_safety_ceiling_enforced_by_existing_record_route(env):
    client, reg, _ = env
    for i in range(21):
        reg.add(str(i))
    response = _post(client, [_member(str(i), risk_weight=.01) for i in range(21)], 0)
    assert response.status_code == 422 and "safety ceiling" in response.text


def test_reweight_haircut_is_persisted_and_idempotent(env):
    client, reg, _ = env
    reg.add("current")
    _post(client, [_member("current")], 0)
    book = _observed_book(CHECKSUM)
    book.update(returns=[.01, .02, -.05], dates=book["dates"][:3])
    first = _decision(client, 1, books={"current": book})
    second = _decision(client, 2, books={"current": book})
    assert first.status_code == second.status_code == 201
    assert first.json()["members"][0]["risk_weight"] == second.json()["members"][0]["risk_weight"] == .25


def test_idle_budget_never_loosens_gate_and_persona_trials_survive_refusals(env):
    from services.research.tests.test_pool_admission import entries_and_members, recorded_dataset
    client, reg, _ = env
    entries, one, two = entries_and_members()
    reg.entries = entries
    _post(client, [_member(two["artifact_ref"], checksum=two["checksum"], risk_weight=.3)], 0)
    books = {ref: _observed_book(entry["checksum"]) for ref, entry in entries.items()}
    # Same candidate and recorded bars, first with idle capital, then higher
    # measured utilisation. Neither changes the policy or correlation cut-off.
    outcomes = []
    for version, notional in ((1, 0), (2, 70)):
        rows = _risk_observations(books, notional)
        response = _decision(client, version, "admit", candidate={"artifact_ref": one["artifact_ref"], "checksum": one["checksum"]},
                             books=books, observations=rows, dataset=recorded_dataset())
        assert response.status_code == 201
        outcomes.append(response.json())
    assert all(result["decision"]["status"] == "refused" and result["members"][0]["risk_weight"] == .3 for result in outcomes)
    assert outcomes[0]["decision"]["backtest"]["correlations"] == outcomes[1]["decision"]["backtest"]["correlations"]
    assert outcomes[0]["decision"]["policy_digest"] == outcomes[1]["decision"]["policy_digest"]
    assert [result["decision"]["persona_trial_count"] for result in outcomes] == [1, 2]
    assert outcomes[1]["decision"]["backtest"]["required_candidate_sharpe"] > outcomes[0]["decision"]["backtest"]["required_candidate_sharpe"]


@pytest.mark.parametrize("failure", ["missing_proof", "checksum", "tenant", "not_passed"])
def test_admission_requires_candidates_own_owner_verified_gate_proof(env, failure):
    from services.research.tests.test_pool_admission import entries_and_members
    client, reg, _ = env
    entries, one, two = entries_and_members()
    reg.entries = entries
    _post(client, [_member(two["artifact_ref"], checksum=two["checksum"], risk_weight=.5)], 0)
    candidate = entries[one["artifact_ref"]]
    if failure == "missing_proof":
        candidate.pop("replication_proof")
    elif failure == "checksum":
        candidate["checksum"] = "wrong"
    elif failure == "tenant":
        candidate["owner_tenant"] = "other"
    else:
        candidate["replication_success"] = False
    response = _decision(client, 1, "admit", candidate={"artifact_ref": one["artifact_ref"], "checksum": one["checksum"]})
    assert response.status_code == 201 and response.json()["decision"]["status"] == "refused"
    assert response.json()["decision"]["reasons"] and len(response.json()["members"]) == 1


def test_full_budget_rejects_candidate_that_is_not_stronger_and_ceiling_is_tunable(env, monkeypatch):
    from services.research.tests.test_pool_admission import entries_and_members
    import pool_policy
    client, reg, _ = env
    entries, one, two = entries_and_members()
    reg.entries = entries
    _post(client, [_member(two["artifact_ref"], checksum=two["checksum"], risk_weight=1)], 0)
    books = {ref: _observed_book(entry["checksum"]) for ref, entry in entries.items()}
    candidate = {"artifact_ref": one["artifact_ref"], "checksum": one["checksum"]}
    response = _decision(client, 1, "admit", candidate=candidate, books=books, observations=_risk_observations(books))
    assert response.json()["decision"]["reasons"] == ["full_budget_candidate_not_stronger_than_weakest"]
    # Direct risk reductions remain possible; additions/increases must use policy.
    assert _post(client, [_member(two["artifact_ref"], checksum=two["checksum"], risk_weight=.5)], 2).status_code == 201
    # Even with allocation below 100%, actual full utilisation requires replacement.
    response = _decision(client, 3, "admit", candidate=candidate, books=books, observations=_risk_observations(books, 100))
    assert response.json()["decision"]["reasons"] == ["full_budget_candidate_not_stronger_than_weakest"]
    policy = pool_policy.load_policy().model_copy(update={"member_ceiling": 1})
    monkeypatch.setattr(pool_policy, "load_policy", lambda: policy)
    response = _decision(client, 4, "admit", candidate=candidate, books=books, observations=_risk_observations(books))
    assert response.json()["decision"]["reasons"] == ["pool_member_safety_ceiling"]


def test_admission_missing_budget_data_does_not_assume_spare_capacity(env):
    from services.research.tests.test_pool_admission import entries_and_members
    client, reg, _ = env
    entries, one, two = entries_and_members()
    reg.entries = entries
    _post(client, [_member(two["artifact_ref"], checksum=two["checksum"], risk_weight=.5)], 0)
    books = {ref: _observed_book(entry["checksum"]) for ref, entry in entries.items()}
    response = _decision(client, 1, "admit", candidate={"artifact_ref": one["artifact_ref"], "checksum": one["checksum"]}, books=books)
    assert response.status_code == 201
    assert response.json()["decision"]["reasons"] == ["risk_budget_observations_missing"]


def test_registry_outage_is_not_an_admission_verdict(env):
    import sys
    from persona_strategy_pool import RegistryUnavailable
    client, reg, _ = env
    reg.add("current")
    _post(client, [_member("current")], 0)
    def unavailable(ref, tenant):
        raise RegistryUnavailable("registry unavailable")
    sys.modules["persona_main_pool_test_module"].POOL_STORE._reader = unavailable
    response = _decision(client, 1, "admit", candidate={"artifact_ref": "new", "checksum": CHECKSUM})
    assert response.status_code == 503
    history = client.get("/api/personas/persona-1/strategy-pool/versions", headers=_auth(READ)).json()
    assert len(history["versions"]) == 1


def test_no_candidate_records_idle_budget_without_inventing_trials(env):
    client, reg, _ = env
    reg.add("current")
    _post(client, [_member("current", risk_weight=.2)], 0)
    response = _decision(client, 1, "admit")
    assert response.status_code == 201
    result = response.json()
    assert result["members"][0]["risk_weight"] == .2
    assert result["decision"]["reasons"] == ["no_qualifying_candidate_budget_left_unused"]
    assert result["decision"]["persona_trial_count"] == 0


def test_create_append_read_through_real_routes(env):
    client, reg, owner = env
    reg.add("art-1"); reg.add("art-2")
    assert _post(client, [_member("art-1")], 0).status_code == 201
    assert _post(client, [_member("art-1"), _member("art-2", stage="trial", risk_weight=0.1, baseline_ref=None)], 1).status_code == 422
    r = _post(client, [_member("art-1", risk_weight=.4)], 1)
    assert r.status_code == 201 and r.json()["pool_version"] == 2
    cur = client.get("/api/personas/persona-1/strategy-pool", headers=_auth(READ)).json()
    assert cur["pool_version"] == 2 and cur["schema_version"] == "persona_strategy_pool.v1"
    hist = client.get("/api/personas/persona-1/strategy-pool/versions", headers=_auth(READ)).json()
    assert [v["pool_version"] for v in hist["versions"]] == [1, 2]
    assert hist["versions"][1]["previous_digest"] == hist["versions"][0]["digest"]
    v1 = client.get("/api/personas/persona-1/strategy-pool/versions/1", headers=_auth(READ)).json()
    assert len(v1["members"]) == 1


def test_removal_keeps_history_and_persona_lifecycle(env):
    client, reg, owner = env
    reg.add("art-1")
    before = owner.get("persona-1").lifecycle_state
    _post(client, [_member("art-1")], 0)
    removed = _member("art-1", stage="removed", risk_weight=0.0, removal_reason="decayed")
    assert _post(client, [removed], 1).status_code == 201
    assert owner.get("persona-1").lifecycle_state == before
    hist = client.get("/api/personas/persona-1/strategy-pool/versions", headers=_auth(READ)).json()
    assert hist["versions"][0]["members"][0]["stage"] == "active"
    assert hist["versions"][1]["members"][0]["stage"] == "removed"
    # dropping a member outright is refused
    assert _post(client, [], 2).status_code == 422


def test_unapproved_or_mismatched_artifact_refused_with_recorded_reason(env):
    client, reg, _ = env
    reg.add("cand", state="candidate"); reg.add("bad-sum", checksum="sha256:" + "b" * 64)
    for ref, text in (("cand", "not approved"), ("bad-sum", "checksum"), ("ghost", "not a registry entry")):
        r = _post(client, [_member(ref)], 0)
        assert r.status_code == 422 and text in r.json()["detail"]
    assert client.get("/api/personas/persona-1/strategy-pool", headers=_auth(READ)).status_code == 404
    # a refused create leaves no current pool, but every refusal is recorded and readable
    body = client.get("/api/personas/persona-1/strategy-pool/versions", headers=_auth(READ)).json()
    assert body["versions"] == [] and [r["artifact_ref"] for r in body["refusals"]] == ["cand", "bad-sum", "ghost"]


def test_refusal_recorded_on_existing_pool(env):
    client, reg, _ = env
    reg.add("art-1"); reg.add("cand", state="candidate")
    _post(client, [_member("art-1")], 0)
    assert _post(client, [_member("art-1"), _member("cand", risk_weight=0.1)], 1).status_code == 422
    body = client.get("/api/personas/persona-1/strategy-pool/versions", headers=_auth(READ)).json()
    assert len(body["versions"]) == 1
    assert body["refusals"][0]["artifact_ref"] == "cand" and "not approved" in body["refusals"][0]["reason"]


def test_cross_tenant_write_refused_and_read_hidden(env):
    client, reg, _ = env
    reg.add("art-1")
    assert _post(client, [_member("art-1")], 0, pid="persona-2", tenant="tenant-a").status_code == 403
    _post(client, [_member("art-1")], 0, pid="persona-2", tenant="tenant-b")
    assert client.get("/api/personas/persona-2/strategy-pool", headers=_auth(READ, "tenant-a")).status_code == 404
    assert client.post("/api/personas/persona-1/strategy-pool/versions",
                       json={"expected_version": 0, "members": [_member("art-1")], "change_reason": "x"}).status_code in (401, 403)
    # viewers cannot write
    r = client.post("/api/personas/persona-1/strategy-pool/versions",
                    json={"expected_version": 0, "members": [_member("art-1")], "change_reason": "x"},
                    headers=_auth(READ))
    assert r.status_code in (401, 403)


def test_stale_expected_version_conflicts(env):
    client, reg, _ = env
    reg.add("art-1")
    _post(client, [_member("art-1")], 0)
    assert _post(client, [_member("art-1")], 0).status_code == 409


def test_first_member_derived_from_owner_records(env):
    client, reg, _ = env
    reg.add("any-id-xyz", persona="persona-1", baseline="base-9")
    r = client.post("/api/personas/persona-1/strategy-pool/seed", headers=_auth(WRITE))
    assert r.status_code == 201
    m = r.json()["members"][0]
    assert (m["artifact_ref"], m["stage"], m["baseline_ref"], m["checksum"]) == ("any-id-xyz", "active", "base-9", CHECKSUM)
    assert client.post("/api/personas/persona-1/strategy-pool/seed", headers=_auth(WRITE)).status_code == 409


def test_seed_fails_closed_without_or_with_ambiguous_owner_records(env):
    client, reg, _ = env
    assert client.post("/api/personas/persona-1/strategy-pool/seed", headers=_auth(WRITE)).status_code == 422
    reg.add("a", persona="persona-1"); reg.add("b", persona="persona-1")
    assert client.post("/api/personas/persona-1/strategy-pool/seed", headers=_auth(WRITE)).status_code == 422
    reg.entries.pop("b"); reg.entries["a"]["approval_decision_id"] = None
    assert client.post("/api/personas/persona-1/strategy-pool/seed", headers=_auth(WRITE)).status_code == 422


def test_registry_unavailable_fails_closed_without_write(env, monkeypatch):
    client, reg, _ = env
    from persona_strategy_pool import PersonaStrategyPoolStore, RegistryUnavailable
    import sys as _s
    mod = _s.modules["persona_main_pool_test_module"]
    def boom(rid, tenant): raise RegistryUnavailable("down")
    mod.POOL_STORE._reader = boom
    assert _post(client, [_member("art-1")], 0).status_code == 503
    assert client.get("/api/personas/persona-1/strategy-pool", headers=_auth(READ)).status_code == 404


def test_seed_baseline_is_gate_decision_not_rollback_target(env):
    client, reg, _ = env
    reg.add("run-1", persona="persona-1", baseline="governance-gate-verdict")
    r = client.post("/api/personas/persona-1/strategy-pool/seed", headers=_auth(WRITE))
    assert r.status_code == 201
    assert r.json()["members"][0]["baseline_ref"] == "governance-gate-verdict"


def _provisioning_entries(persona="persona-1"):
    meta = {"persona_id": persona, "execution_context": "paper", "persona_capital_binding_id": "pcb-1"}
    base = {"registry_id": "baseline-0", "artifact_state": "approved", "checksum": CHECKSUM,
            "owner_tenant": "tenant-a", "rollback_target": None,
            "metadata": dict(meta, fail_closed_baseline=True)}
    run = {"registry_id": "run-1", "artifact_state": "approved", "checksum": CHECKSUM,
           "owner_tenant": "tenant-a", "rollback_target": "baseline-0", "approval_decision_id": "gate-1",
           "approved_at": "2026-11-01T00:00:00+00:00",
           "metadata": dict(meta, fail_closed_baseline=False, rollback_target_registry_id="baseline-0")}
    return base, run


def test_real_http_lister_excludes_rollback_baseline_and_superseded(env, monkeypatch):
    import persona_strategy_pool as psp
    base, run = _provisioning_entries()
    old = dict(run, registry_id="run-0", rollback_target="baseline-0", superseded_by="run-1")
    entries = {e["registry_id"]: e for e in (base, run, old)}

    def fake_http(path, tenant):
        if "strategy-artifacts?" in path:
            return {"items": [{"registry_id": k} for k in entries], "next_page_token": None}
        return {"entry": entries[path.rsplit("/", 1)[1]]}

    monkeypatch.setattr(psp, "_http_json", fake_http)
    found = psp.http_owner_artifact_lister("persona-1", "tenant-a")
    assert [e["registry_id"] for e in found] == ["run-1"]


def test_seed_through_real_lister_with_provisioning_shape(env, monkeypatch):
    import persona_strategy_pool as psp
    client, reg, _ = env
    base, run = _provisioning_entries()
    entries = {e["registry_id"]: e for e in (base, run)}
    monkeypatch.setattr(psp, "_http_json", lambda path, t: (
        {"items": [{"registry_id": k} for k in entries], "next_page_token": None}
        if "strategy-artifacts?" in path else {"entry": entries[path.rsplit("/", 1)[1]]}))
    import sys as _s
    mod = _s.modules["persona_main_pool_test_module"]
    mod.POOL_STORE._lister = psp.http_owner_artifact_lister
    mod.POOL_STORE._reader = lambda rid, t: entries.get(rid, {})
    r = client.post("/api/personas/persona-1/strategy-pool/seed", headers=_auth(WRITE))
    assert r.status_code == 201, r.text
    m = r.json()["members"][0]
    assert (m["artifact_ref"], m["baseline_ref"]) == ("run-1", "gate-1")


def test_unchanged_stage_is_revalidated_on_append(env):
    client, reg, _ = env
    reg.add("art-1")
    assert _post(client, [_member("art-1")], 0).status_code == 201
    reg.entries["art-1"]["artifact_state"] = "retired"
    r = _post(client, [_member("art-1", risk_weight=0.8)], 1)
    assert r.status_code == 422
    # historical removal of a now-retired member stays allowed
    removed = _member("art-1", stage="removed", removal_reason="retired")
    assert _post(client, [removed], 1).status_code == 201
    reg.add("art-2"); 
    reg.entries["art-2"]["checksum"] = "sha256:" + "b" * 64
    assert _post(client, [removed, _member("art-2")], 2).status_code == 422


def test_concurrent_refusals_are_all_recorded(env):
    import threading
    client, reg, _ = env
    import sys as _s
    store = _s.modules["persona_main_pool_test_module"].POOL_STORE
    from persona_strategy_pool import AppendPoolVersionRequest, PoolMember, PoolMemberRefused
    barrier = threading.Barrier(2)
    orig = store._reader

    def slow(rid, t):
        barrier.wait(timeout=5)
        return orig(rid, t)
    store._reader = slow
    results = []

    def go(ref):
        req = AppendPoolVersionRequest(expected_version=0, change_reason="c", members=[PoolMember(**_member(ref))])
        try:
            store.append("persona-1", "tenant-a", req, actor_id="op")
        except PoolMemberRefused:
            results.append(ref)
    ts = [threading.Thread(target=go, args=(r,)) for r in ("x-1", "x-2")]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(results) == ["x-1", "x-2"]
    assert sorted(r["artifact_ref"] for r in store.refusals("persona-1", "tenant-a")) == ["x-1", "x-2"]


def test_member_baselines_resolve_each_member_from_its_own_gate_proof(env, tmp_path):
    client, reg, owner = env
    from persona_strategy_pool import PersonaStrategyPoolStore
    from services.evolution.test_approved_baselines import genuine_entry

    proof = genuine_entry(tenant="tenant-a")  # real gate on recorded Taiwan backtest output
    reg.entries[proof["registry_id"]] = proof
    reg.add("art-2")
    assert _post(client, [
        _member(proof["registry_id"], checksum=proof["checksum"], risk_weight=0.5),
        _member("art-2", stage="trial", risk_weight=0.1, baseline_ref=None, parent_ref=proof["registry_id"]),
    ], 0).status_code == 201
    store = PersonaStrategyPoolStore(AtomicJsonRecordStore(tmp_path / "pools.json"), registry_reader=reg.read)
    rows = store.member_baselines("persona-1", "tenant-a")
    by_ref = {r["baseline"]["baseline_key"].split("/")[2]: r for r in rows}
    assert by_ref[proof["registry_id"]]["baseline"]["status"] == "approved"
    assert by_ref[proof["registry_id"]]["baseline"]["checksum"] == proof["checksum"]
    # the trial child has no pass of its own: it never inherits the parent's proof
    assert by_ref["art-2"]["baseline"]["reason"] == "strategy_artifact_missing"
    assert by_ref["art-2"]["stage"] == "trial" and by_ref["art-2"]["pool_version"] == 1


def _admission_fixture(env):
    import copy
    from services.registry.strategy_artifact import strategy_artifact_checksum
    from services.research.tests.test_pool_admission import entries_and_members, recorded_dataset

    client, reg, owner = env
    entries, one, two = entries_and_members()
    current = entries[two["artifact_ref"]]
    candidate = copy.deepcopy(entries[one["artifact_ref"]])
    artifact = candidate["metadata"]["strategy_artifact"]
    artifact["strategy_logic"].update(positive_action="BUY", non_positive_action="BUY")
    artifact["parameters"]["zero_momentum_action"] = "BUY"
    candidate["checksum"] = strategy_artifact_checksum(artifact)
    candidate["replication_proof"]["owner_verified_binding"]["artifact_checksum"] = candidate["checksum"]
    reg.entries = {"current": {**current, "registry_id": "current"}, "candidate": {**candidate, "registry_id": "candidate"}}
    assert _post(client, [_member("current", checksum=current["checksum"], risk_weight=.7)], 0).status_code == 201
    books = {ref: _observed_book(e["checksum"], better=ref == "candidate") for ref, e in reg.entries.items()}
    return client, {"books": books, "dataset": recorded_dataset(), "observations": _risk_observations(books),
                    "candidate": {"artifact_ref": "candidate", "checksum": candidate["checksum"]}}


def test_persona_initiated_admission_needs_verified_consultation_memo_and_links_it(env, monkeypatch):
    """AC5: a persona-initiated paper admission is refused without a verified memo; an applied one links the memo id."""
    import persona_strategy_pool as pool_module
    from services.persona.consult_triggers import MemoCheck
    client, body = _admission_fixture(env)
    store = sys.modules["persona_main_pool_test_module"].POOL_STORE
    verified = []
    store._verify_memo = lambda tenant, persona, candidate, version, memo: verified.append((tenant, persona, candidate.artifact_ref, version, memo)) or MemoCheck(True)

    missing = _decision(client, 1, "admit", initiated_by="persona", **body)
    assert missing.json()["decision"]["status"] == "refused" and missing.json()["decision"]["reasons"] == ["consultation_memo_required"]
    assert len(missing.json()["members"]) == 1 and verified == []

    store._verify_memo = lambda *a: pool_module.MemoCheck(False, "risk_persona_requires_operator") if hasattr(pool_module, "MemoCheck") else MemoCheck(False, "risk_persona_requires_operator")
    opposed = _decision(client, 2, "admit", initiated_by="persona", consultation_memo_id="memo-x", **body)
    assert opposed.json()["decision"]["status"] == "refused"
    assert opposed.json()["decision"]["reasons"] == ["consultation_memo_not_verified:risk_persona_requires_operator"]
    assert "evidence_refs" not in opposed.json()["decision"]

    def unavailable(*a):
        raise RuntimeError("consultation down")
    store._verify_memo = unavailable
    down = _decision(client, 3, "admit", initiated_by="persona", consultation_memo_id="memo-x", **body)
    assert down.json()["decision"]["reasons"] == ["consultation_unavailable:RuntimeError"] and len(down.json()["members"]) == 1

    store._verify_memo = lambda tenant, persona, candidate, version, memo: verified.append((tenant, persona, candidate.artifact_ref, version, memo)) or MemoCheck(True)
    # the refusals above appended versions; the consultation was opened at version 1 with the same live membership
    applied = _decision(client, 4, "admit", initiated_by="persona", consultation_memo_id="memo-ok",
                        consultation_pool_version=1, **body)
    decision = applied.json()["decision"]
    assert decision["status"] == "applied", decision
    assert decision["consultation_memo_id"] == "memo-ok"
    assert decision["evidence_refs"] == [{"ref_type": "consultation_memo", "ref_id": "memo-ok"}]
    assert verified == [("tenant-a", "persona-1", "candidate", 1, "memo-ok")]  # bound to the pool version of the consultation


def test_persona_cycle_actor_cannot_skip_the_memo_and_operator_admission_keeps_its_path(env):
    """The cycle's own actor is persona-initiated even without the flag; an operator admission needs no memo."""
    client, body = _admission_fixture(env)
    store = sys.modules["persona_main_pool_test_module"].POOL_STORE
    from persona_strategy_pool import PoolDecisionRequest
    request = PoolDecisionRequest.model_validate({"expected_version": 1, "execution_context": "paper", "action": "admit", **body})
    store._verify_memo = lambda *a: (_ for _ in ()).throw(AssertionError("operator path must not consult"))
    refused = store.decide("persona-1", "tenant-a", request, actor_id="persona-pool-cycle")
    assert refused["decision"]["reasons"] == ["consultation_memo_required"]
    operator = _decision(client, 2, "admit", **body)
    assert operator.json()["decision"]["status"] == "applied" and "consultation_memo_id" not in operator.json()["decision"]
    # A consultation opened before the live membership changed no longer covers this pool.
    store._verify_memo = lambda *a: (_ for _ in ()).throw(AssertionError("stale memo must not be verified"))
    stale = _decision(client, 3, "admit", initiated_by="persona", consultation_memo_id="memo-old", consultation_pool_version=1, **body)
    assert stale.json()["decision"]["reasons"] == ["consultation_stale_pool_membership_changed"]
