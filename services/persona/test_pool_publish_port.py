"""envelope_publication consultation and publish calls of the pool cycle's BFF port (no network)."""
from __future__ import annotations

from types import SimpleNamespace

from services.persona import consult_triggers as ct
from services.persona.pool_autonomy_cycle import HttpPoolCyclePorts
from services.persona.pool_publish_port import envelope_publication_requirement, membership_digest

PERSONA = {"persona_id": "p1", "tenant_id": "t1"}


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
            "state": "operator", "reason": "risk_persona_requires_operator",
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
    assert calls[0] == ("POST", "bff", "/bff/personas/p1/strategy-pool/publish", "t1", {"pool_version": 7, "stage": "paper"})
    assert calls[1][:3] == ("GET", "bff", "/bff/personas/p1/strategy-pool/publish-history")


def test_bff_credential_unconfigured_fails_closed():
    import pytest
    ports = HttpPoolCyclePorts(env={})
    with pytest.raises(RuntimeError, match="owner_credential_unconfigured:bff"):
        ports.publish_pool(PERSONA, 1)
