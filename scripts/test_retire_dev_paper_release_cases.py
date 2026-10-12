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
    """Fake Deployment owner: failure POST compensates like the consumer."""

    def __init__(self, ids, retired=()):
        self.ids = ids  # saga_id -> plan_id
        self.status = {s: ("failed", "compensated") if s in retired else ("completed", "runtime_active") for s in ids}
        self.posts = []

    def get(self, owner, path):
        saga_id = path.rsplit("/", 1)[1]
        if saga_id not in self.ids:
            return None
        status, step = self.status[saga_id]
        return {"saga_id": saga_id, "plan_id": self.ids[saga_id], "status": status, "current_step": step}

    def post(self, owner, path, payload):
        saga_id = path.split("/")[-2]
        assert path.endswith("/failure")
        assert payload["failed_step"] == "runtime_load_requested"
        self.posts.append(saga_id)
        self.status[saga_id] = ("failed", "compensated")
        return {}


def _setup(monkeypatch, keys, retired=()):
    recs = [Rec(k, f"2026-10-{i + 1:02d}") for i, k in enumerate(keys)]
    ids = {}
    key_by_saga = {}
    for r in recs:
        plan, saga = f"plan-{r.idempotency_key}", f"saga-{r.idempotency_key}"
        ids[saga] = plan
        key_by_saga[saga] = r.idempotency_key
    monkeypatch.setattr(retire, "case_ids", lambda r: (f"plan-{r.idempotency_key}", f"saga-{r.idempotency_key}"))
    owner = Owner(ids, retired={f"saga-{k}" for k in retired})
    return Store(recs), owner


def _run(store, owner, **kw):
    kw.setdefault("dry_run", False)
    return retire.execute(
        store=store, transport_factory=lambda t: owner, run_id="9",
        timeout_seconds=30, poll_seconds=0, sleep=lambda s: None, **kw,
    )


KEYS = [f"dev-paper-release-{n}-1" for n in (1, 2, 3, 4)]


def test_accepted_retires_previous_and_keeps_new(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)
    out = _run(store, owner, outcome="accepted", new_case_key=KEYS[3])
    assert out["served_case_key"] == KEYS[3]
    assert sorted(owner.posts) == sorted(f"saga-{k}" for k in KEYS[:3])
    assert "saga-" + KEYS[3] not in owner.posts
    assert out["status"] == "retired"


def test_compensated_retires_new_and_keeps_previous_served(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)
    out = _run(store, owner, outcome="compensated", new_case_key=KEYS[3])
    assert out["served_case_key"] == KEYS[2]
    assert sorted(owner.posts) == sorted(f"saga-{k}" for k in (KEYS[0], KEYS[1], KEYS[3]))
    assert "saga-" + KEYS[2] not in owner.posts


def test_compensated_skips_already_retired_when_choosing_served(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS, retired=[KEYS[2]])
    out = _run(store, owner, outcome="compensated", new_case_key=KEYS[3])
    assert out["served_case_key"] == KEYS[1]
    assert "saga-" + KEYS[1] not in owner.posts


def test_replay_is_idempotent(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)
    _run(store, owner, outcome="accepted", new_case_key=KEYS[3])
    first = list(owner.posts)
    out = _run(store, owner, outcome="accepted", new_case_key=KEYS[3])
    assert owner.posts == first
    assert out["retire"] == []
    assert out["status"] == "retired"


def test_dry_run_is_read_only_and_lists_cases(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)
    out = _run(store, owner, outcome="accepted", new_case_key="", dry_run=True)
    assert owner.posts == []
    assert out["status"] == "dry_run"
    assert sorted(out["retire"]) == sorted(KEYS)


def test_non_release_and_non_succeeded_cases_are_ignored(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)
    store.records.append(Rec("dev-paper-bootstrap-x", "2026-10-09"))
    store.records.append(Rec("dev-paper-release-5-1", "2026-10-09", state="compensated"))
    out = _run(store, owner, outcome="accepted", new_case_key=KEYS[3])
    assert out["observed_cases"] == 4


def test_failure_is_recorded_not_raised(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)

    def boom(o, p):
        raise RuntimeError("owner down")

    owner.get = boom
    out = _run(store, owner, outcome="accepted", new_case_key=KEYS[3])
    assert out["errors"] and owner.posts == []


def test_deadline_bounds_work(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)
    out = retire.execute(
        store=store, transport_factory=lambda t: owner, run_id="9", dry_run=False,
        outcome="accepted", new_case_key=KEYS[3], timeout_seconds=1,
        monotonic=iter([0, 5, 5, 5, 5, 5, 5]).__next__, sleep=lambda s: None,
    )
    assert set(out["results"].values()) == {"not_attempted_deadline"}
    assert owner.posts == []


def test_served_case_is_never_posted(monkeypatch):
    store, owner = _setup(monkeypatch, KEYS)
    with pytest.raises(retire.RetireError):
        retire.plan_retirement([], {}, outcome="bogus", new_case_key="x")
