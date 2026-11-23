"""Read-only change probe controls; financial terms are local fixtures."""
import importlib.machinery
import importlib.util
from copy import deepcopy
from pathlib import Path

from acceptance.test_paper_risk_policy_binding import evidence, NOW

path = Path(__file__).parent / "probes/persona-risk-profile-change"
loader = importlib.machinery.SourceFileLoader("persona_risk_change_probe", str(path))
spec = importlib.util.spec_from_loader(loader.name, loader)
probe = importlib.util.module_from_spec(spec)
loader.exec_module(probe)


def fixture(pending=False):
    payload = evidence()
    persona = payload["/bff/personas/fixture-persona"]["data"]
    persona.update(legacyPaperCapitalPoolId="fixture-pool", risk="high" if pending else "low",
                   riskPolicyChange={"requestedRisk": "high" if pending else "low", "effectiveRisk": "low",
                                     "status": "pending_approval" if pending else "effective",
                                     "policyVersion": "fixture-v1", "approvalId": "fixture-owner-approval"})
    pool = payload["/bff/capital-pools/fixture-pool"]["data"]
    pool.update(single_runtime_enforced=True)
    pool["metadata"].update(execution_context="paper", requested_risk_profile=persona["risk"],
                            risk_profile_change={"requested_profile": persona["risk"], "request_id": "fixture-key",
                                                 "requested_by": "fixture-operator", "revision": 1})
    return payload


def check(payload, **kwargs):
    return probe.run(lambda path: payload[path], ("fixture-persona",), now=NOW,
                     owner_read=lambda *args: {"allow_new_or_increased_risk": False, "allow_risk_reducing_actions": True},
                     **kwargs)


def test_effective_and_pending_both_prove_truthful_requested_vs_effective():
    for pending in (False, True):
        result = check(fixture(pending))
        assert result["passed"], result


def test_pending_null_is_truthful_only_without_a_verified_mapping():
    payload = fixture(True)
    payload["/api/v1/approval-decisions"]["data"] = []
    payload["/bff/personas/fixture-persona"]["data"]["riskPolicyChange"].update(
        effectiveRisk=None, approvalId=None, policyVersion=None,
    )
    assert check(payload)["passed"]


def test_stale_persona_only_edit_and_unverified_effective_fail():
    payload = fixture()
    payload["/bff/capital-pools/fixture-pool"]["data"]["metadata"]["requested_risk_profile"] = "high"
    assert not check(payload)["passed"]
    payload = fixture()
    payload["/api/v1/approval-decisions"]["data"][0]["decision"] = "rejected"
    assert not check(payload)["passed"]


def test_refetch_must_agree_and_probe_requires_explicit_personas(monkeypatch):
    payload = fixture()
    called = 0
    def fetch(path):
        nonlocal called
        body = deepcopy(payload[path])
        if path == "/bff/personas/fixture-persona":
            called += 1
            if called == 2:
                body["data"]["riskPolicyChange"]["requestedRisk"] = "high"
        return body
    report = probe.run(fetch, ("fixture-persona",), now=NOW, owner_read=lambda *args: {})
    assert not report["passed"]
    monkeypatch.delenv("ACCEPTANCE_EXPECTED_PERSONA_IDS", raising=False)
    assert probe.main() == 77


def test_explicit_persona_without_viewer_credentials_skips(monkeypatch):
    monkeypatch.setenv("ACCEPTANCE_EXPECTED_PERSONA_IDS", "fixture-persona")
    for name in probe.transport.REQUIRED:
        monkeypatch.delenv(name, raising=False)
    assert probe.main() == 77
