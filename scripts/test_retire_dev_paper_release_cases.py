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


class Owner:
    """Fake Deployment owner: the retire POST finalizes like the consumer."""

    def __init__(self, ids, statuses=None):
        self.ids = ids  # saga_id -> plan_id
        self.status = {s: "completed" for s in ids}
        self.status.update(statuses or {})
        self.posts = []
        self.reasons = {}

    def get(self, owner, path):
        saga_id = path.rsplit("/", 1)[1]
        if saga_id not in self.ids:
            return None
        return {"saga_id": saga_id, "plan_id": self.ids[saga_id], "status": self.status[saga_id]}

    def post(self, owner, path, payload):
        assert path.endswith("/retire"), path
        saga_id = path.split("/")[-2]
        assert set(payload) == {"reason"}
        self.posts.append(saga_id)
        self.reasons[saga_id] = payload["reason"]
        self.status[saga_id] = "retired"
        return {}


def _setup(monkeypatch, keys, statuses=None):
    recs = [Rec(k, f"2026-10-{i + 1:02d}") for i, k in enumerate(keys)]
    ids = {f"saga-{k}": f"plan-{k}" for k in keys}
    monkeypatch.setattr(
        retire, "case_ids", lambda r: (f"plan-{r.idempotency_key}", f"saga-{r.idempotency_key}")
    )
    owner = Owner(ids, {f"saga-{k}": v for k, v in (statuses or {}).items()})
    return Store(recs), owner


def _run(store, owner, **kw):
    kw.setdefault("dry_run", False)
    return retire.execute(
        store=store, transport_factory=lambda t: owner, run_id="9",
        timeout_seconds=30, poll_seconds=0, sleep=lambda s: None, **kw,
    )


KEYS = [f"dev-paper-release-{n}-1" for n in (1, 2, 3, 4)]


def test_accepted_retires_every_case_but_this_runs(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)
    out = _run(store, owner, outcome="accepted", new_case_key=KEYS[3])
    assert sorted(owner.posts) == sorted(f"saga-{k}" for k in KEYS[:3])
    assert "saga-" + KEYS[3] not in owner.posts
    assert out["status"] == "retired"


def test_accepted_is_independent_of_recency(monkeypatch):
    # This run's case is the oldest by created_at; it is still the only one kept.
    store, owner = _setup(monkeypatch, KEYS)
    out = _run(store, owner, outcome="accepted", new_case_key=KEYS[0])
    assert sorted(owner.posts) == sorted(f"saga-{k}" for k in KEYS[1:])
    assert out["status"] == "retired"


def test_compensated_retires_only_this_runs_case(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)
    out = _run(store, owner, outcome="compensated", new_case_key=KEYS[3])
    assert owner.posts == ["saga-" + KEYS[3]]
    assert out["status"] == "retired"


def test_compensated_with_unprovisioned_case_touches_nothing(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)
    out = _run(store, owner, outcome="compensated", new_case_key="dev-paper-release-9-1")
    assert owner.posts == []
    assert out["retire"] == []


def test_compensated_without_key_touches_nothing(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)
    out = _run(store, owner, outcome="compensated", new_case_key="")
    assert owner.posts == [] and out["retire"] == []


def test_replay_is_idempotent_and_reason_is_stable(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)
    _run(store, owner, outcome="accepted", new_case_key=KEYS[3])
    first, reasons = list(owner.posts), dict(owner.reasons)
    out = _run(store, owner, outcome="accepted", new_case_key=KEYS[3])
    assert owner.posts == first and owner.reasons == reasons
    assert out["retire"] == [] and out["status"] == "retired"
    assert all("run" not in r for r in reasons.values())


def test_non_completed_sagas_are_skipped_not_posted(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS, statuses={KEYS[0]: "failed", KEYS[1]: "compensating"})
    out = _run(store, owner, outcome="accepted", new_case_key=KEYS[3])
    assert owner.posts == ["saga-" + KEYS[2]]
    assert sorted(out["skipped"]) == sorted(KEYS[:2])


def test_dry_run_is_read_only_and_lists_states(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS, statuses={KEYS[0]: "retired"})
    out = _run(store, owner, outcome="accepted", new_case_key="", dry_run=True)
    assert owner.posts == []
    assert out["status"] == "dry_run"
    assert sorted(out["retire"]) == sorted(KEYS[1:])
    assert out["case_states"][KEYS[0]] == "retired"
    assert out["case_states"][KEYS[1]] == "active"


def test_non_release_and_non_succeeded_cases_are_ignored(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)
    store.records.append(Rec("dev-paper-bootstrap-x", "2026-10-09"))
    store.records.append(Rec("dev-paper-release-5-1", "2026-10-09", state="compensated"))
    out = _run(store, owner, outcome="accepted", new_case_key=KEYS[3])
    assert out["observed_cases"] == 4


def test_readback_failure_is_recorded_not_raised(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)

    def boom(o, p):
        raise RuntimeError("owner down")

    owner.get = boom
    out = _run(store, owner, outcome="accepted", new_case_key=KEYS[3])
    assert out["errors"] and owner.posts == []


def test_wrong_identity_readback_is_refused(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)
    real_get = owner.get
    owner.get = lambda o, p: {**real_get(o, p), "plan_id": "other"}
    out = _run(store, owner, outcome="accepted", new_case_key=KEYS[3])
    assert owner.posts == [] and out["errors"]


def test_deadline_bounds_work(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)
    out = retire.execute(
        store=store, transport_factory=lambda t: owner, run_id="9", dry_run=False,
        outcome="accepted", new_case_key=KEYS[3], timeout_seconds=1,
        monotonic=iter([0, 5, 5, 5, 5, 5, 5, 5, 5]).__next__, sleep=lambda s: None,
    )
    assert set(out["results"].values()) == {"not_attempted_deadline"}
    assert owner.posts == []


def test_unknown_outcome_is_rejected():
    with pytest.raises(retire.RetireError):
        retire.plan_retirement([], {}, outcome="bogus", new_case_key="x")


def test_script_uses_only_the_retire_action():
    code = open(retire.__file__, encoding="utf-8").read().split('"""', 2)[2]
    assert '/retire"' in code
    assert "/failure" not in code and "/api/runtime-bindings" not in code
    assert "psycopg" not in code
