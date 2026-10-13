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


class Runtime:
    """Fake Runtime owner: one paper binding per case plan."""

    def __init__(self, keys, retired=()):
        self.bindings = {
            f"plan-{k}": {
                "binding_id": f"bind-{k}", "plan_id": f"plan-{k}",
                "deployment_mode": "paper", "metadata": {"tenant_id": "tenant-dev"},
                "status": "retired" if k in retired else "active",
            }
            for k in keys
        }
        self.retired = []

    def list_by_plan(self, plan_id):
        b = self.bindings.get(plan_id)
        return [dict(b)] if b else []

    def retire(self, binding_id):
        for b in self.bindings.values():
            if b["binding_id"] == binding_id:
                b["status"] = "retired"
                self.retired.append(binding_id)
                return dict(b)
        raise RuntimeError("not found")


def _setup(monkeypatch, keys, retired=()):
    recs = [Rec(k, f"2026-10-{i + 1:02d}") for i, k in enumerate(keys)]
    monkeypatch.setattr(retire, "case_plan_id", lambda r: f"plan-{r.idempotency_key}")
    return Store(recs), Runtime(keys, retired)


def _run(store, owner, **kw):
    kw.setdefault("dry_run", False)
    return retire.execute(
        store=store, runtime=owner, run_id="9", timeout_seconds=30, **kw,
    )


def _ids(keys):
    return sorted(f"bind-{k}" for k in keys)


KEYS = [f"dev-paper-release-{n}-1" for n in (1, 2, 3, 4)]


def test_accepted_retires_previous_and_keeps_new(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)
    out = _run(store, owner, outcome="accepted", new_case_key=KEYS[3])
    assert out["served_case_key"] == KEYS[3]
    assert sorted(owner.retired) == _ids(KEYS[:3])
    assert owner.bindings["plan-" + KEYS[3]]["status"] == "active"
    assert out["status"] == "retired"


def test_compensated_retires_only_new_case(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)
    out = _run(store, owner, outcome="compensated", new_case_key=KEYS[3])
    assert owner.retired == ["bind-" + KEYS[3]]
    assert out["retire"] == [KEYS[3]]
    assert all(owner.bindings["plan-" + k]["status"] == "active" for k in KEYS[:3])


def test_compensated_never_touches_older_live_cases(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS, retired=[KEYS[2]])
    out = _run(store, owner, outcome="compensated", new_case_key=KEYS[3])
    assert owner.retired == ["bind-" + KEYS[3]]
    assert out["status"] == "retired"


def test_compensated_replay_is_idempotent(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)
    _run(store, owner, outcome="compensated", new_case_key=KEYS[3])
    out = _run(store, owner, outcome="compensated", new_case_key=KEYS[3])
    assert owner.retired == ["bind-" + KEYS[3]]
    assert out["retire"] == []


def test_replay_is_idempotent(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)
    _run(store, owner, outcome="accepted", new_case_key=KEYS[3])
    first = list(owner.retired)
    out = _run(store, owner, outcome="accepted", new_case_key=KEYS[3])
    assert owner.retired == first
    assert out["retire"] == []
    assert out["status"] == "retired"


def test_response_loss_is_reconciled_by_readback(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)
    real = owner.retire

    def lossy(binding_id):
        real(binding_id)
        raise TimeoutError("response lost")

    owner.retire = lossy
    out = _run(store, owner, outcome="compensated", new_case_key=KEYS[3])
    assert out["status"] == "retired"


def test_dry_run_is_read_only_and_lists_cases(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)
    out = _run(store, owner, outcome="accepted", new_case_key="", dry_run=True)
    assert owner.retired == []
    assert out["status"] == "dry_run"
    assert sorted(out["retire"]) == sorted(KEYS)


def test_non_release_and_non_succeeded_cases_are_ignored(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)
    store.records.append(Rec("dev-paper-bootstrap-x", "2026-10-09"))
    store.records.append(Rec("dev-paper-release-5-1", "2026-10-09", state="compensated"))
    out = _run(store, owner, outcome="accepted", new_case_key=KEYS[3])
    assert out["observed_cases"] == 4


def test_owner_read_failure_is_recorded_not_raised(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)

    def boom(plan_id):
        raise RuntimeError("owner down")

    owner.list_by_plan = boom
    out = _run(store, owner, outcome="accepted", new_case_key=KEYS[3])
    assert out["errors"] and owner.retired == []


@pytest.mark.parametrize("field,value", [
    ("plan_id", "other-plan"),
    ("deployment_mode", "live"),
    ("metadata", {"tenant_id": "tenant-other"}),
    ("metadata", {}),
])
def test_binding_identity_mismatch_writes_nothing(monkeypatch, field, value):
    store, owner = _setup(monkeypatch, KEYS)
    owner.bindings["plan-" + KEYS[0]][field] = value
    out = _run(store, owner, outcome="accepted", new_case_key=KEYS[3])
    assert KEYS[0] in out["errors"]
    assert "bind-" + KEYS[0] not in owner.retired


def test_deadline_bounds_work(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)
    out = retire.execute(
        store=store, runtime=owner, run_id="9", dry_run=False,
        outcome="accepted", new_case_key=KEYS[3], timeout_seconds=1,
        monotonic=iter([0, 5, 5, 5, 5, 5, 5]).__next__,
    )
    assert set(out["results"].values()) == {"not_attempted_deadline"}
    assert owner.retired == []


def test_invalid_outcome_rejected():
    with pytest.raises(retire.RetireError):
        retire.plan_retirement([], {}, outcome="bogus", new_case_key="x")


@pytest.mark.parametrize("new_key", ["", "dev-paper-release-99-1"])
def test_accepted_with_missing_or_unknown_served_identity_refuses(monkeypatch, new_key):
    store, owner = _setup(monkeypatch, KEYS)
    out = _run(store, owner, outcome="accepted", new_case_key=new_key)
    assert owner.retired == []
    assert out["status"] == "refused"
    assert out["retire"] == []


def test_accepted_with_unreadable_or_inactive_served_case_refuses(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS, retired=[KEYS[3]])
    out = _run(store, owner, outcome="accepted", new_case_key=KEYS[3])
    assert owner.retired == [] and out["status"] == "refused"

    store, owner = _setup(monkeypatch, KEYS)
    real = owner.list_by_plan

    def lbp(plan_id):
        if plan_id == "plan-" + KEYS[3]:
            raise RuntimeError("owner down")
        return real(plan_id)

    owner.list_by_plan = lbp
    out = _run(store, owner, outcome="accepted", new_case_key=KEYS[3])
    assert owner.retired == [] and out["status"] == "refused"
