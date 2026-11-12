"""The adapter routes persona_own_agent turns to the persona's own agent."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

_ADAPTER_DIR = Path(__file__).resolve().parents[2] / "openclaw-gateway-adapter"
if str(_ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(_ADAPTER_DIR))

from consultation_provider import (  # noqa: E402
    CONSULTATION_CONTRIBUTION_PATH,
    REQUIRED_OUTPUT,
    consult_agent_id,
    create_app,
)  # noqa: E402

from services.consultation.tests.test_current_provider_handoff import (  # noqa: E402
    _default_contribution,
)

TENANT = "t-own"
TOKEN = "tok"
ACTOR = "consultation-workflow-executor"


DENY_ALL = {
    "tools": {"allow": [], "deny": ["*"], "exec": {"security": "deny", "ask": "always"}},
    "skills": [],
    "memorySearch": {"enabled": False, "sources": []},
    "contextInjection": "never",
}


class _Fake:
    def __init__(
        self,
        *,
        answer_as: str,
        agent_id: str | None = None,
        policy: dict[str, Any] | None = None,
        provisioned: bool = True,
    ) -> None:
        self.answer_as = answer_as
        self.agent_id = agent_id
        self.policy = DENY_ALL if policy is None else policy
        self.provisioned = provisioned
        self.prompts: list[str] = []
        self.invoked: list[str | None] = []
        self.structured = 0

    def _gateway_call(self, method: str, **kwargs: Any) -> Any:
        assert method == "config.get"
        entries = (
            [{"id": consult_agent_id(TENANT, self.answer_as), **self.policy}]
            if self.provisioned
            else []
        )
        return {"valid": True, "config": {"agents": {"list": entries}}}

    def gateway_agents_list(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [{"id": consult_agent_id(TENANT, self.answer_as)}] if self.provisioned else []
        self.invoked: list[str | None] = []
        self.structured = 0

    def invoke_structured(self, prompt: str, **kwargs: Any) -> Any:
        self.structured += 1
        raise AssertionError("persona turn must not use structured-extraction")

    def invoke(self, prompt: str, **kwargs: Any) -> Any:
        self.invoked.append(kwargs.get("agent_id"))
        self.prompts.append(prompt)
        contribution = _default_contribution(TENANT, "req-own")
        contribution.update(
            participant_type="persona",
            participant_ref=self.answer_as,
            author_type="persona",
        )
        return SimpleNamespace(
            status="completed",
            output={
                "agent_id": self.agent_id or kwargs.get("agent_id"),
                "json_events": [{"item": {"text": json.dumps(contribution)}}],
            },
        )


def _post(fake: _Fake, tmp_path: Path, persona: str, memory: Any = None) -> Any:
    app = create_app(
        committee_memory_fetcher=memory or (lambda *a: None),
        provider=fake,
        provider_token=TOKEN,
        expected_service_actor=ACTOR,
        replay_path=tmp_path / "r.sqlite3",
    )
    return TestClient(app).post(
        CONSULTATION_CONTRIBUTION_PATH,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "X-Pantheon-Service-Actor": ACTOR,
            "X-Pantheon-Tenant-Id": TENANT,
            "Idempotency-Key": "k1",
        },
        json={
            "tenant_id": TENANT,
            "request_id": "req-own",
            "required_output": REQUIRED_OUTPUT,
            "request": {
                "tenant_id": TENANT,
                "request_id": "req-own",
                "participant": {
                    "persona_id": persona,
                    "role": "risk_reviewer",
                    "agent": "persona_own_agent",
                },
            },
        },
    )


def test_persona_turn_runs_on_the_personas_own_agent(tmp_path: Path) -> None:
    fake = _Fake(answer_as="persona-risk-officer")
    resp = _post(fake, tmp_path, "persona-risk-officer")
    assert resp.status_code == 200, resp.text
    assert fake.invoked == [consult_agent_id(TENANT, "persona-risk-officer")]
    assert fake.structured == 0
    assert resp.json()["contribution"]["participant_ref"] == "persona-risk-officer"


def test_persona_turn_from_another_agent_is_refused(tmp_path: Path) -> None:
    fake = _Fake(answer_as="persona-risk-officer", agent_id="structured-extraction")
    assert _post(fake, tmp_path, "persona-risk-officer").status_code >= 400


def test_persona_turn_attribution_must_match_asked_persona(tmp_path: Path) -> None:
    fake = _Fake(answer_as="persona-someone-else")
    assert _post(fake, tmp_path, "persona-risk-officer").status_code >= 400


def test_default_main_agent_is_never_dispatched(tmp_path: Path) -> None:
    for persona in ("main", "structured-extraction", "consult-abc-persona-x", ""):
        fake = _Fake(answer_as=persona or "x")
        resp = _post(fake, tmp_path, persona)
        assert resp.status_code in (403, 422), (persona, resp.text)
        assert not fake.invoked


def test_unprovisioned_consult_agent_is_refused(tmp_path: Path) -> None:
    fake = _Fake(answer_as="persona-risk-officer", provisioned=False)
    assert _post(fake, tmp_path, "persona-risk-officer").status_code == 403
    assert not fake.invoked


def test_read_and_proposal_tool_policy_is_refused_before_dispatch(tmp_path: Path) -> None:
    from integrations.openclaw.persona_agent_sync import persona_agent_runtime_policy

    fake = _Fake(
        answer_as="persona-risk-officer", policy=persona_agent_runtime_policy()
    )
    assert _post(fake, tmp_path, "persona-risk-officer").status_code == 403
    assert not fake.invoked


def test_each_deny_all_field_is_required(tmp_path: Path) -> None:
    for key, bad in (
        ("skills", ["x"]),
        ("contextInjection", "always"),
        ("memorySearch", {"enabled": True, "sources": ["memory"]}),
    ):
        fake = _Fake(answer_as="persona-risk-officer", policy={**DENY_ALL, key: bad})
        assert _post(fake, tmp_path, "persona-risk-officer").status_code == 403, key
        assert not fake.invoked


def test_other_tenant_agent_is_not_admitted(tmp_path: Path) -> None:
    fake = _Fake(answer_as="persona-risk-officer")
    fake._gateway_call = lambda method, **kw: {  # type: ignore[assignment]
        "valid": True,
        "config": {"agents": {"list": [
            {"id": consult_agent_id("t-other", "persona-risk-officer"), **DENY_ALL}
        ]}},
    }
    assert _post(fake, tmp_path, "persona-risk-officer").status_code == 403
    assert not fake.invoked


def _hit_persona(mid: str, persona: str, scope: str, summary: str) -> dict[str, Any]:
    return {"type": "persona", "relevance_score": 1.0, "entry": {
        "memory_id": mid, "persona_id": persona, "relevance_scope": scope,
        "memory_type": "lesson", "content": {"summary": summary}}}


def test_committee_memory_excludes_private_and_includes_committee(tmp_path: Path) -> None:
    retrieval = {"hits": [
        _hit_persona("m1", "persona-risk-officer", "persona_private", "PRIVATE-SENTINEL"),
        _hit_persona("m2", "persona-risk-officer", "persona_and_committee", "COMMITTEE-SENTINEL"),
        _hit_persona("m3", "persona-other", "persona_and_committee", "OTHER-PERSONA-SENTINEL"),
        {"type": "institutional", "relevance_score": 1.0, "entry": {
            "entry_id": "i1", "knowledge_type": "obs",
            "content": {"headline": "INSTITUTIONAL-SENTINEL", "body": "b"}}},
    ]}
    fake = _Fake(answer_as="persona-risk-officer")
    resp = _post(fake, tmp_path, "persona-risk-officer", memory=lambda *a: retrieval)
    assert resp.status_code == 200, resp.text
    prompt = fake.prompts[0]
    assert "COMMITTEE-SENTINEL" in prompt
    assert "INSTITUTIONAL-SENTINEL" in prompt
    assert "PRIVATE-SENTINEL" not in prompt
    assert "OTHER-PERSONA-SENTINEL" not in prompt


def test_memory_fetch_failure_degrades_without_leaking(tmp_path: Path) -> None:
    def boom(*a: Any) -> Any:
        raise OSError("memory down")

    fake = _Fake(answer_as="persona-risk-officer")
    resp = _post(fake, tmp_path, "persona-risk-officer", memory=boom)
    assert resp.status_code == 200, resp.text
    assert "Committee-visible memory JSON: []" in fake.prompts[0]
