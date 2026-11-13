import json
from pathlib import Path

import pytest

from services.memory.evaluation.hindsight_comparison import export_real_events as ex


class FakeClient:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, path, params=None):
        self.calls.append(path)
        v = self.routes[path]
        if isinstance(v, Exception):
            raise v
        return v

    def describe(self, path):
        return "https://owner.invalid" + path


# Shapes below mirror the real owner contracts (incident.Postmortem, evolution DecisionResponse,
# persona_strategy_pool versions {members, decision}, research_demand tried_families).
PM = [
    {"postmortem_id": "pm-1", "title": "t", "root_cause": "Gamma squeeze broke the short-vol book", "status": "published",
     "created_at": "2026-10-01T00:00:00Z"},           # real rows carry no persona -> unmapped
]
EVO = [
    {"decision_id": "ev-1", "decision_state": "approved", "rationale": "調整動能策略權重", "persona_id": "bob",
     "created_at": "2026-10-02T00:00:00Z", "metadata": {"strategy_family": "momentum", "underlyings": ["TXF"]}},
    {"decision_id": "ev-2", "decision_state": "proposed", "rationale": "pending thing", "persona_id": "bob",
     "created_at": "2026-10-02T00:00:00Z", "metadata": {"strategy_family": "momentum"}},
]
MEMOS = [
    {"memo_id": "m1", "status": "published", "author_type": "persona", "author_ref": "alice",
     "summary": "Short vol needs a hedge", "published_at": "2026-10-03T00:00:00Z", "created_at": "2026-10-03T00:00:00Z"},
    {"memo_id": "m2", "status": "draft", "author_type": "persona", "author_ref": "alice", "summary": "x"},
]
DEMANDS = {"demands": [{"demand_id": "d1", "persona_id": "alice", "tried_families": ["short_vol"],
                        "missing_datasets": ["vix_term"]}], "count": 1}
M = lambda ref, w=0.1, removed=None, demand=None: {
    "artifact_ref": ref, "checksum": "c", "stage": "trial", "risk_weight": w, "joined_at": "2026-10-01T00:00:00Z",
    "demand_ref": demand, "removed_at": removed}
POOL = {"versions": [
    {"persona_id": "alice", "pool_version": 1, "members": [M("a1", demand="d1")], "decision": None},
    {"persona_id": "alice", "pool_version": 2, "members": [M("a1", 0.3, demand="d1"), M("a2", demand="d1")],
     "decision": {"action": "admit", "status": "applied"}},
    {"persona_id": "alice", "pool_version": 3, "members": [M("a1", 0.3, removed="2026-10-05", demand="d1"), M("a2", demand="d1")],
     "decision": {"action": "reweight", "status": "applied"}},
], "refusals": []}


def clients():
    return {"incident": FakeClient({"/api/postmortems": PM}),
            "evolution": FakeClient({"/api/evolution/proposals": EVO}),
            "consultation": FakeClient({"/api/consult/memos": MEMOS}),
            "research": FakeClient({"/api/research/persona-demands": DEMANDS}),
            "persona": FakeClient({"/api/personas/alice/strategy-pool/versions": POOL})}


def _load(tmp_path, name):
    return [json.loads(l) for l in (tmp_path / name).read_text().splitlines() if l.strip()]


def test_export_uses_production_writer_and_manifest(tmp_path):
    m = ex.export(clients(), tmp_path, tenant_id="t1", persona_ids=["alice"])
    assert m["hosted_written"] is False
    rows = _load(tmp_path, ex.ENTRIES_NAME)
    assert {r["entry"]["source_event_id"] for r in rows} == {"ev-1", "m1"}
    assert all("learn_feedback" in r["entry"]["content"]["tags"] for r in rows)   # production writer tag
    assert any("family:momentum" in r["entry"]["content"]["tags"] for r in rows)
    unmapped = json.loads((tmp_path / ex.UNMAPPED_NAME).read_text())
    reasons = {u["reason"] for u in unmapped}
    assert "persona_id_absent" in reasons                                 # real postmortem has no persona
    assert any(r.startswith("evolution_decision_not_approved:proposed") for r in reasons)  # never manufactured
    assert "consultation_memo_not_published" in reasons
    assert any(r.startswith("no_owner_read_endpoint") for r in reasons)   # research findings: no route
    decisions = _load(tmp_path, ex.DECISIONS_NAME)
    by_type = {}
    for d in decisions:
        by_type.setdefault(d["decision_type"], []).append(d)
    assert len(by_type["evolution_proposal"]) == 2
    assert [d["strategy_family"] for d in by_type["research_demand"]] == ["short_vol"]   # tried_families
    assert len(by_type["trial_admission"]) == 2 and len(by_type["weight_change"]) == 1 \
        and len(by_type["member_removal"]) == 1
    assert all(d["strategy_family"] == "short_vol" for d in by_type["trial_admission"])
    ex.validate_manifest(tmp_path)
    assert "Bearer" not in json.dumps(m)


def test_owner_paths_are_real_routes():
    paths = {p for _, p, _ in ex.SOURCES.values()}
    assert "/api/postmortems" in paths and "/api/consult/memos" in paths
    assert not any("/api/v1/" in p or p.endswith("research/findings") for p in paths)


def test_proposed_evolution_never_becomes_approved_event():
    with pytest.raises(ex.Unmappable):
        ex.map_evolution({"decision_id": "e", "decision_state": "proposed", "rationale": "r", "persona_id": "p"}, {})
    with pytest.raises(ex.Unmappable, match="decision_state_absent"):
        ex.map_evolution({"decision_id": "e", "rationale": "r", "persona_id": "p"}, {})


def test_manifest_required_and_tamper_detected(tmp_path):
    with pytest.raises(ValueError, match="manifest_missing"):
        ex.validate_manifest(tmp_path)
    ex.export(clients(), tmp_path)
    (tmp_path / ex.DECISIONS_NAME).write_text("tampered\n")
    with pytest.raises(ValueError, match="manifest_sha_mismatch"):
        ex.validate_manifest(tmp_path)


def test_empty_or_partial_hash_set_rejected(tmp_path):
    ex.export(clients(), tmp_path)
    mp = tmp_path / ex.MANIFEST_NAME
    m = json.loads(mp.read_text())
    (tmp_path / ex.ENTRIES_NAME).write_text("tampered\n")
    for bad in ({}, {ex.DECISIONS_NAME: m["sha256"][ex.DECISIONS_NAME]}):
        mp.write_text(json.dumps({**m, "sha256": bad}))
        with pytest.raises(ValueError, match="manifest_sha_set_mismatch"):
            ex.validate_manifest(tmp_path)
    mp.write_text(json.dumps({**m, "counts": "x"}))
    with pytest.raises(ValueError, match="manifest_field_type_invalid"):
        ex.validate_manifest(tmp_path)


def test_owner_failure_listed_not_fatal(tmp_path):
    c = clients()
    c["incident"] = FakeClient({"/api/postmortems": OSError("down")})
    ex.export(c, tmp_path)
    assert any(u["reason"].startswith("owner_unreachable") for u in json.loads((tmp_path / ex.UNMAPPED_NAME).read_text()))


def test_client_is_get_only():
    import inspect
    assert "method=\"GET\"" in inspect.getsource(ex.ReadOnlyClient.get)
    assert not hasattr(ex.ReadOnlyClient, "post")
