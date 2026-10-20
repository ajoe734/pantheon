from __future__ import annotations

from dataclasses import dataclass

import pytest

from scripts import retire_dev_paper_release_cases as retire


@dataclass
class Rec:
    idempotency_key: str
    created_at: str
    state: str = "succeeded"
    tenant_id: str = "tenant-dev"
    persona_id: str = ""


class Store:
    def __init__(self, records):
        self.records = records

    def list_all(self):
        return list(self.records)


class Coordinator:
    """Fake retire_case entrypoint: retires like the real coordinator."""

    def __init__(self, pending=(), boom=()):
        self.calls = []
        self.reasons = {}
        self.pending = set(pending)
        self.boom = set(boom)

    def __call__(self, tenant_id, persona_id, *, reason):
        self.calls.append(persona_id)
        self.reasons[persona_id] = reason
        if persona_id in self.boom:
            raise RuntimeError("coordinator down")
        if persona_id in self.pending:
            return {"persona_id": persona_id, "state": "provisioning",
                    "retirement": {"status": "pending", "pending_step": "saga"}}
        return {"persona_id": persona_id, "state": "retired",
                "retirement": {"status": "retired"}}


KEYS = [f"dev-paper-release-{n}-1" for n in (1, 2, 3, 4)]


def _store(states=None):
    states = states or {}
    return Store([
        Rec(k, f"2026-10-{i + 1:02d}", state=states.get(k, "succeeded"),
            persona_id=f"p-{i + 1}")
        for i, k in enumerate(KEYS)
    ])


def _run(store, coord, **kw):
    kw.setdefault("dry_run", False)
    return retire.execute(
        store=store, retire_case=coord, run_id="9", timeout_seconds=30, **kw
    )


def test_accepted_retires_every_case_but_this_runs():
    coord = Coordinator()
    out = _run(_store(), coord, outcome="accepted", new_case_key=KEYS[3])
    assert sorted(coord.calls) == ["p-1", "p-2", "p-3"]
    assert out["status"] == "retired"


def test_accepted_is_independent_of_recency():
    coord = Coordinator()
    _run(_store(), coord, outcome="accepted", new_case_key=KEYS[0])
    assert sorted(coord.calls) == ["p-2", "p-3", "p-4"]


@pytest.mark.parametrize("state", ["reserved", "provisioning", "succeeded", "failed", "compensated"])
def test_every_retirable_case_state_is_selected(state):
    coord = Coordinator()
    out = _run(_store({KEYS[0]: state}), coord, outcome="accepted", new_case_key=KEYS[3])
    assert "p-1" in coord.calls
    assert out["case_states"][KEYS[0]] == state


def test_retired_case_is_reported_not_called_again():
    coord = Coordinator()
    out = _run(_store({KEYS[0]: "retired"}), coord, outcome="accepted", new_case_key=KEYS[3])
    assert "p-1" not in coord.calls
    assert out["already_retired"] == [KEYS[0]]


def test_compensated_retires_only_this_runs_case():
    coord = Coordinator()
    out = _run(_store(), coord, outcome="compensated", new_case_key=KEYS[3])
    assert coord.calls == ["p-4"] and out["status"] == "retired"


def test_compensated_with_unprovisioned_or_missing_key_touches_nothing():
    for key in ("dev-paper-release-9-1", ""):
        coord = Coordinator()
        out = _run(_store(), coord, outcome="compensated", new_case_key=key)
        assert coord.calls == [] and out["retire"] == []


def test_replay_reason_is_stable_and_run_independent():
    coord = Coordinator()
    _run(_store(), coord, outcome="accepted", new_case_key=KEYS[3])
    assert coord.reasons["p-1"] == f"retire dev paper release case {KEYS[0]}"


def test_unconverged_case_is_partial_evidence():
    coord = Coordinator(pending={"p-2"})
    out = _run(_store(), coord, outcome="accepted", new_case_key=KEYS[3])
    assert out["status"] == "partial"
    assert out["results"][KEYS[1]] == "pending"
    assert out["results"][KEYS[0]] == "retired"


def test_coordinator_error_is_recorded_and_others_continue():
    coord = Coordinator(boom={"p-1"})
    out = _run(_store(), coord, outcome="accepted", new_case_key=KEYS[3])
    assert KEYS[0] in out["errors"] and out["status"] == "partial"
    assert "p-2" in coord.calls and "p-3" in coord.calls


def test_dry_run_is_read_only():
    coord = Coordinator()
    out = _run(_store(), coord, outcome="accepted", new_case_key="", dry_run=True)
    assert coord.calls == [] and out["status"] == "dry_run"
    assert sorted(out["retire"]) == sorted(KEYS)
    assert out["executable"] is False and out["refusal"]


def test_non_release_and_pending_cases_are_ignored():
    store = _store()
    store.records.append(Rec("dev-paper-bootstrap-x", "2026-10-09"))
    store.records.append(Rec("dev-paper-release-5-1", "2026-10-09", state="weird"))
    out = _run(store, Coordinator(), outcome="accepted", new_case_key=KEYS[3])
    assert out["observed_cases"] == 4


def test_deadline_bounds_work():
    coord = Coordinator()
    out = retire.execute(
        store=_store(), retire_case=coord, run_id="9", dry_run=False,
        outcome="accepted", new_case_key=KEYS[3], timeout_seconds=1,
        monotonic=iter([0, 5, 5, 5, 5, 5]).__next__,
    )
    assert set(out["results"].values()) == {"not_attempted_deadline"}
    assert coord.calls == []


def test_unknown_outcome_is_rejected():
    with pytest.raises(retire.RetireError):
        retire.plan_retirement([], outcome="bogus", new_case_key="x")


@pytest.mark.parametrize("kind", ["missing", "unknown", "not_succeeded", "retired"])
def test_accepted_refuses_all_writes_without_a_succeeded_served_case(kind):
    key = KEYS[3]
    states = {}
    if kind == "missing":
        key = ""
    elif kind == "unknown":
        key = "dev-paper-release-9-1"
    elif kind == "not_succeeded":
        states = {KEYS[3]: "provisioning"}
    else:
        states = {KEYS[3]: "retired"}
    coord = Coordinator()
    out = _run(_store(states), coord, outcome="accepted", new_case_key=key)
    assert coord.calls == []
    assert out["status"] == "refused" and out["refusal"]


def test_served_case_is_never_retired():
    coord = Coordinator()
    _run(_store(), coord, outcome="accepted", new_case_key=KEYS[3])
    assert "p-4" not in coord.calls


def test_script_never_touches_saga_or_database_itself():
    code = open(retire.__file__, encoding="utf-8").read().split('"""', 2)[2]
    for banned in ("/api/deployment", "/failure", "/api/runtime-bindings",
                   "psycopg", "classify_saga", "transport.post"):
        assert banned not in code


def test_unavailable_retire_case_is_evidence_without_fallback(monkeypatch, capsys):
    def unavailable():
        raise retire.RetireCaseUnavailable("no retire_case")

    monkeypatch.setattr(retire, "_build_runtime", unavailable)
    rc = retire.main(["--outcome", "accepted", "--new-case-key", KEYS[3]])
    out = capsys.readouterr().out
    assert rc == 1 and '"status": "retire_case_unavailable"' in out
    assert '"schema_version": "pantheon.dev-paper-release-case-retire.v2"' in out


def test_build_runtime_reports_unavailable_when_image_lacks_entrypoint(monkeypatch):
    from services.control_plane.bff.personas import service

    monkeypatch.delattr(service, "retire_persona_provisioning_case", raising=False)
    with pytest.raises(retire.RetireCaseUnavailable):
        retire._build_runtime()


def test_build_runtime_composes_persona_service_via_app_factory(monkeypatch):
    from services.control_plane.bff.personas import service

    monkeypatch.setattr(service, "_composed_persona_service", None)
    monkeypatch.setenv("RANKING_STORE_DSN", "postgresql://test:test@localhost:5432/test")
    store, retire_case = retire._build_runtime()
    assert service._composed_persona_service is not None
    assert callable(retire_case) and store is not None
    # the write owner the coordinator needs to archive the final Persona resolves
    assert service._get_active_write_owner() is not None
