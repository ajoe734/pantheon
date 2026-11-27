"""envelope_publication consultation and publish calls of the pool cycle's BFF port (no network)."""
from __future__ import annotations

from types import SimpleNamespace

from services.persona import consult_triggers as ct
from services.persona.pool_autonomy_cycle import HttpPoolCyclePorts
from services.persona.pool_publish_port import envelope_publication_requirement, membership_digest

PERSONA = {"persona_id": "p1", "tenant_id": "t1"}


def test_only_own_pending_decisions_for_confirmed_membership_are_superseded():
    import copy
    current = pool(m("a", weight=1.0), version=6)
    confirmed = {"status": "published", "members": current["members"], "pool_version": 2,
                 "envelope_artifact_id": "artifact-persona-pool-test-v2"}
    own = {"decision_id": "old", "created_by_id": "persona-svc", "tenant_id": "t1", "persona_id": "p1",
           "decision_state": "proposed", "metadata": {"decision_kind": "pool_envelope_publication", "paper_only": True,
                                                       "membership_digest": membership_digest(current)}}
    others = []
    for field, value in (("tenant_id", "foreign"), ("persona_id", "other"), ("created_by_id", "operator"),
                         ("decision_state", "approved")):
        others.append({**copy.deepcopy(own), field: value})
    others.append({**own, "metadata": {**own["metadata"], "membership_digest": "other-membership"}})
    writes = []
    def transport(method, service, path, tenant, body=None):
        if method == "GET":
            assert "decision_state=proposed%2Creviewed" in path and tenant == "t1"
            return [own, *others]
        writes.append((path, body))
        own["decision_state"] = "superseded"
        return own
    ports = HttpPoolCyclePorts(transport, pool_store=SimpleNamespace(), env={})
    assert ports.supersede_publication_operator_decisions(PERSONA, current, confirmed) == ["old"]
    assert writes[0][0] == "/api/evolution/proposals/old/supersede"
    assert writes[0][1]["note"] == "publication_membership_already_confirmed"
    assert ports.supersede_publication_operator_decisions(PERSONA, current, confirmed) == []
    changed = pool(m("a"), m("b"))
    assert ports.supersede_publication_operator_decisions(PERSONA, changed, confirmed) == []
    assert len(writes) == 1


def m(ref, stage="active", weight=.5):
    return {"artifact_ref": ref, "checksum": "sha256:" + "a" * 64, "stage": stage, "risk_weight": weight}


def pool(*members, version=3):
    return {"persona_id": "p1", "tenant_id": "t1", "pool_version": version, "members": list(members)}


def test_requirement_follows_the_unmodified_consult_rules_and_binds_membership_not_weights():
    req = envelope_publication_requirement("t1", "p1", pool(m("a"), version=3), 3)
    assert req.decision_type == "envelope_publication" and req.risk_persona_id == ct.RISK_PERSONA_ID
    assert membership_digest(pool(m("a", weight=.1))) == membership_digest(pool(m("a", weight=.9)))
    assert membership_digest(pool(m("a"))) != membership_digest(pool(m("a"), m("b")))
    assert envelope_publication_requirement("t1", "p1", pool(m("a")), 4).decision_id != req.decision_id


def test_consultation_opens_once_per_membership_then_verifies_published_memo():
    opened, requests, memos, calls = [], [], [], []

    class Client:
        def list_requests(self, **kw):
            assert kw["target_type"] == "envelope_publication"
            return list(requests)
        def list_memos(self, **kw): return list(memos)
        def open_persona_panel(self, requirement, **kw):
            opened.append(requirement)
            requests.append({"request_id": kw["request_id"], "status": "submitted", "from_persona_id": kw["persona_id"],
                             "metadata": {"panel": requirement.to_panel_metadata()}})
            return {"request_id": kw["request_id"]}

    ports = HttpPoolCyclePorts(lambda *a, **k: calls.append(a) or {}, consultation_factory=lambda t, a=None: Client(),
                               pool_store=SimpleNamespace(), env={})
    first = ports.envelope_publication_consultation(PERSONA, pool(m("a"), version=3), "pk-1")
    # the daily re-weight appended a version with the same live membership: same consultation, no duplicate
    again = ports.envelope_publication_consultation(PERSONA, pool(m("a", weight=.4), version=4), "pk-2")
    assert first["state"] == "opened" and again["state"] == "pending" and len(opened) == 1
    changed = ports.envelope_publication_consultation(PERSONA, pool(m("a"), m("b"), version=5), "pk-3")
    assert changed["state"] == "opened" and len(opened) == 2
    requests.pop()
    requests[0]["status"] = "published"
    assert ports.envelope_publication_consultation(PERSONA, pool(m("a"), version=4), "pk-4")["state"] == "pending"
    memos.append({"memo_id": "memo-1"})
    original = ct.verify_published_memo
    try:
        ct.verify_published_memo = lambda *a, **k: ct.MemoCheck(False, "risk_persona_requires_operator")
        assert ports.envelope_publication_consultation(PERSONA, pool(m("a"), version=4), "pk-5") == {
            "state": "operator", "reason": "risk_persona_requires_operator", "memo_id": "memo-1", "pool_version": 3,
            "decision_id": opened[0].decision_id, "request_id": requests[0]["request_id"]}
        ct.verify_published_memo = lambda *a, **k: ct.MemoCheck(True)
        ok = ports.envelope_publication_consultation(PERSONA, pool(m("a"), version=4), "pk-6")
    finally:
        ct.verify_published_memo = original
    assert ok["state"] == "verified" and ok["memo_id"] == "memo-1"
    requests[0]["status"] = "rejected"
    assert ports.envelope_publication_consultation(PERSONA, pool(m("a"), version=4), "pk-7")["state"] == "operator"
    assert calls == []


def test_publish_and_history_go_through_the_bff_publish_routes():
    calls = []
    def transport(method, service, path, tenant, body=None):
        calls.append((method, service, path, tenant, body))
        return {"data": {"status": "published"}}
    ports = HttpPoolCyclePorts(transport, pool_store=SimpleNamespace(), env={})
    assert ports.publish_pool(PERSONA, 7) == {"status": "published"}
    assert ports.publication_state(PERSONA) == {"status": "published"}
    assert calls[0] == ("POST", "bff", "/bff/personas/p1/strategy-pool/publish", "t1",
                        {"pool_version": 7, "stage": "paper", "defer_confirmation": True})
    assert calls[1][:3] == ("GET", "bff", "/bff/personas/p1/strategy-pool/publish-history")


def test_bff_credential_unconfigured_fails_closed():
    import pytest
    ports = HttpPoolCyclePorts(env={})
    with pytest.raises(RuntimeError, match="owner_credential_unconfigured:bff"):
        ports.publish_pool(PERSONA, 1)


def test_dead_lettered_consultation_is_named_failed_and_replaced_once_per_failure():
    opened, requests = [], []

    class Client:
        def list_requests(self, **kw): return list(requests)
        def list_memos(self, **kw): return []
        def open_persona_panel(self, requirement, **kw):
            opened.append(kw["request_id"])
            requests.append({"request_id": kw["request_id"], "status": "submitted", "from_persona_id": kw["persona_id"],
                             "metadata": {"panel": requirement.to_panel_metadata()}})
            return {"request_id": kw["request_id"]}

    ports = HttpPoolCyclePorts(lambda *a, **k: {}, consultation_factory=lambda t, a=None: Client(),
                               pool_store=SimpleNamespace(), env={})
    p = pool(m("a"), version=3)
    assert ports.envelope_publication_consultation(PERSONA, p, "k1")["state"] == "opened"
    for failure in range(1, ct and 3):
        requests[-1]["status"] = "failed"  # the executor named it failed after the recovery budget
        out = ports.envelope_publication_consultation(PERSONA, p, f"k{failure + 1}")
        assert out["state"] == "failed" and out["reason"] == "consultation_dead_lettered"
        assert out["replacement_opened"] and out["request_id"] == opened[-1] and out["failed_request_id"] != out["request_id"]
        # the next cycle waits on the live replacement, not on the failed one
        assert ports.envelope_publication_consultation(PERSONA, p, "again")["state"] == "pending"
    assert len(opened) == 3 and len(set(opened)) == 3
    requests[-1]["status"] = "failed"
    out = ports.envelope_publication_consultation(PERSONA, p, "k-last")
    assert out["state"] == "operator" and out["reason"] == "consultation_dead_lettered_retries_exhausted"
    assert len(opened) == 3
    # other terminal states are unchanged
    requests[:] = [dict(requests[0], status="cancelled")]
    assert ports.envelope_publication_consultation(PERSONA, p, "k-c")["state"] == "operator"


def test_operator_decision_id_binds_the_consultation_and_survives_daily_reweights():
    """One queue entry per persona per escalated membership + pool version; the read checks the binding."""
    import pytest
    from services.persona.pool_publish_port import PoolPublishPort

    calls, store = [], {}

    class NotFound(Exception):
        code = 404

    def transport(method, service, path, tenant, body=None):
        calls.append((method, service, path))
        assert service == "evolution"
        if method == "POST":
            store[body["decision_id"]] = {**body, "decision_state": "proposed", "review_chain": []}
            return store[body["decision_id"]]
        decision_id = path.rsplit("/", 1)[1]
        if decision_id not in store:
            raise NotFound()
        return store[decision_id]

    ports = HttpPoolCyclePorts(transport, pool_store=SimpleNamespace(), env={})
    p = pool(m("a"), m("b"), version=7)
    consult = {"state": "operator", "reason": "risk_persona_requires_operator", "memo_id": "memo-9",
               "decision_id": envelope_publication_requirement("t1", "p1", p, 5).decision_id, "request_id": "r-5",
               "pool_version": 5}
    decision_id = PoolPublishPort.publication_operator_decision_id(consult)
    assert decision_id.startswith("pool-publication-op-")
    assert ports.publication_operator_decision(PERSONA, consult) is None
    assert ports.queue_publication_operator_decision(PERSONA, p, consult) == {"decision_id": decision_id, "status": "queued"}
    body = store[decision_id]
    assert body["target_version"] == "5" and body["metadata"]["membership_digest"] == membership_digest(p)
    assert body["metadata"]["consultation_memo_id"] == "memo-9" and body["metadata"]["paper_only"] is True
    read = ports.publication_operator_decision(PERSONA, consult)
    assert read["decision_id"] == decision_id and read["decision_state"] == "proposed"
    # a different membership (or pool version) is a different consultation, hence a different queue entry
    other = {**consult, "decision_id": envelope_publication_requirement("t1", "p1", pool(m("a")), 5).decision_id}
    assert PoolPublishPort.publication_operator_decision_id(other) != decision_id
    store[decision_id]["persona_id"] = "p2"
    with pytest.raises(ValueError, match="operator_decision_binding_mismatch"):
        ports.publication_operator_decision(PERSONA, consult)
    assert [c[0] for c in calls] == ["GET", "POST", "GET", "GET"]
