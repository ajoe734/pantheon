"""memory-svc persona reads: roles that governance allows are admitted; viewers are not."""
import pytest
from fastapi import HTTPException

from services.governance.authz import evaluate_authz_request
from services.memory import main as memory_main


def _decide(roles, **kw):
    return evaluate_authz_request(
        action="memory.retrieve", actor_id="alice", actor_roles=roles,
        resource={"scope": "persona", "persona_id": "p1"}, context={"session_id": "s"})


@pytest.mark.parametrize("role", ["operator", "reviewer", "auditor"])
def test_governance_allows_operator_reviewer_auditor_persona_read(role):
    assert _decide([role])["allowed"] is True


def test_governance_denies_viewer_persona_read():
    d = _decide(["viewer"])
    assert d["allowed"] is False and d["reason"] == "persona_memory_role_denied"


@pytest.mark.parametrize("role", ["reviewer", "auditor"])
def test_memory_svc_admits_roles_governance_allows(role):
    assert role in memory_main._MEMORY_READ_ROLES + memory_main._POLICY_READ_ONLY_ROLES


def test_memory_svc_does_not_admit_viewer():
    assert "viewer" not in memory_main._MEMORY_READ_ROLES + memory_main._POLICY_READ_ONLY_ROLES


def test_actor_mismatch_and_role_escalation_refused(monkeypatch):
    class Ctx:
        actor_id = "alice"
        roles = {"operator"}
        claims = {"tenant_id": "t"}
    monkeypatch.setattr(memory_main, "_authenticate_memory_request", lambda *a, **k: Ctx())
    monkeypatch.setenv("PANTHEON_MEMORY_AUTHZ_MODE", "local")
    kw = dict(session_id="s", scope="persona", persona_id="p1", session_persona_id=None)
    with pytest.raises(HTTPException) as e:
        memory_main._authorize_memory_read("Bearer x", actor_id="bob", actor_roles=[], **kw)
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        memory_main._authorize_memory_read("Bearer x", actor_id="alice", actor_roles=["admin"], **kw)
    assert e.value.status_code == 403
    out = memory_main._authorize_memory_read("Bearer x", actor_id="alice", actor_roles=["operator"], **kw)
    assert out[1] == ["operator"]
