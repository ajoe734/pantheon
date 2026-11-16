"""Tests for deliberative persona_meeting rounds at the OpenClaw consultation provider boundary."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

_ADAPTER_DIR = Path(__file__).resolve().parents[1]
if str(_ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(_ADAPTER_DIR))

from consultation_provider import (  # noqa: E402
    CONSULTATION_CONTRIBUTION_PATH,
    REQUIRED_OUTPUT,
    consult_agent_id,
    create_app,
)

TENANT = "tenant-meeting-rounds"
TOKEN = "meeting-provider-token"
ACTOR = "consultation-workflow-executor"

DENY_ALL = {
    "tools": {"allow": [], "deny": ["*"], "exec": {"security": "deny", "ask": "always"}},
    "skills": [],
    "memorySearch": {"enabled": False, "sources": []},
    "contextInjection": "never",
}


class _ProviderDouble:
    def __init__(
        self,
        *,
        personas: tuple[str, ...] = ("persona-alpha", "persona-red-team", "persona-risk-officer"),
        policy: dict[str, Any] | None = None,
        provisioned: bool = True,
        event_content_override: dict[str, Any] | None = None,
    ) -> None:
        self.personas = personas
        self.policy = DENY_ALL if policy is None else policy
        self.provisioned = provisioned
        self.event_content_override = event_content_override
        self.prompts: list[str] = []
        self.invoked_agents: list[str | None] = []

    def _gateway_call(self, method: str, **kwargs: Any) -> Any:
        assert method == "config.get"
        entries = (
            [{"id": consult_agent_id(TENANT, pid), **self.policy} for pid in self.personas]
            if self.provisioned
            else []
        )
        return {"valid": True, "config": {"agents": {"list": entries}}}

    def gateway_agents_list(self, **kwargs: Any) -> list[dict[str, Any]]:
        return (
            [{"id": consult_agent_id(TENANT, pid)} for pid in self.personas]
            if self.provisioned
            else []
        )

    def invoke_structured(self, prompt: str, **kwargs: Any) -> Any:
        raise AssertionError("persona_meeting turns must not use structured-extraction")

    def invoke(self, prompt: str, **kwargs: Any) -> Any:
        agent_id = kwargs.get("agent_id")
        self.invoked_agents.append(agent_id)
        self.prompts.append(prompt)
        persona_id = next(
            (pid for pid in self.personas if consult_agent_id(TENANT, pid) == agent_id),
            self.personas[0],
        )
        event_content = self.event_content_override or {
            "stance": "approve_with_conditions",
            "confidence": 0.82,
            "citations": ["facts:item:1"],
            "claims": [{"claim": f"{persona_id} claim", "evidence_refs": ["facts:item:1"]}],
            "summary_evidence_refs": ["facts:item:1"],
            "accepted": ["role:chair|style:momentum#1"],
            "disputed": ["sizing assumption"],
            "changed": True,
        }
        contribution = {
            "contribution_id": f"contrib-mtg-{persona_id}-{len(self.prompts)}",
            "tenant_id": TENANT,
            "request_id": "mtg-1",
            "participant_type": "persona",
            "participant_ref": persona_id,
            "author_type": "persona",
            "summary": f"{persona_id} round contribution",
            "recommendation": "approve_with_conditions",
            "confidence": 0.82,
            "event_type": "meeting_round_position",
            "event_content": event_content,
            "evidence": [
                {
                    "id": "facts:item:1",
                    "evidence_type": "meeting_evidence",
                    "link": "artifact://mtg-1/facts:item:1",
                }
            ],
            "findings": [],
        }
        return SimpleNamespace(
            status="completed",
            output={
                "agent_id": agent_id,
                "json_events": [{"item": {"text": json.dumps(contribution)}}],
            },
        )


def _post_turn(
    double: _ProviderDouble,
    tmp_path: Path,
    *,
    persona_id: str = "persona-alpha",
    role: str = "chair",
    meeting: dict[str, Any],
    case_file: dict[str, Any] | None = None,
    memory_fetcher: Any = None,
    idem_key: str = "idem-1",
) -> Any:
    app = create_app(
        committee_memory_fetcher=memory_fetcher or (lambda *a: None),
        provider=double,
        provider_token=TOKEN,
        expected_service_actor=ACTOR,
        replay_path=tmp_path / "replays.sqlite3",
    )
    cf = case_file or {
        "groups": {
            "facts": {
                "status": "ok",
                "items": [{"item_id": "facts:item:1", "excerpt": "Pool Sharpe 1.4"}],
            }
        }
    }
    return TestClient(app).post(
        CONSULTATION_CONTRIBUTION_PATH,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "X-Pantheon-Service-Actor": ACTOR,
            "X-Pantheon-Tenant-Id": TENANT,
            "Idempotency-Key": idem_key,
        },
        json={
            "tenant_id": TENANT,
            "request_id": "mtg-1",
            "required_output": REQUIRED_OUTPUT,
            "request": {
                "tenant_id": TENANT,
                "request_id": "mtg-1",
                "consultation_type": "persona_meeting",
                "case_file": cf,
                "participant": {
                    "persona_id": persona_id,
                    "role": role,
                    "style": "momentum",
                    "agent": "persona_own_agent",
                },
                "meeting": meeting,
            },
        },
    )


def test_round_1_independent_position_and_12_committee_memory_limit(tmp_path: Path) -> None:
    hits = [
        {
            "type": "persona",
            "relevance_score": 1.0,
            "entry": {
                "memory_id": f"m-{i}",
                "tenant_id": TENANT,
                "persona_id": "persona-alpha",
                "relevance_scope": "persona_and_committee",
                "memory_type": "lesson",
                "content": {"summary": f"COMMITTEE-FULL-{i}"},
            },
        }
        for i in range(15)
    ]
    hits.append(
        {
            "type": "persona",
            "relevance_score": 1.0,
            "entry": {
                "memory_id": "m-priv",
                "tenant_id": TENANT,
                "persona_id": "persona-alpha",
                "relevance_scope": "persona_private",
                "memory_type": "lesson",
                "content": {"summary": "PRIVATE-LEAK-SENTINEL"},
            },
        }
    )
    double = _ProviderDouble()
    resp = _post_turn(
        double,
        tmp_path,
        persona_id="persona-alpha",
        meeting={"meeting_id": "mtg-1", "meeting_type": "MT-1", "round": 1},
        memory_fetcher=lambda *a: {"hits": hits},
    )
    assert resp.status_code == 200, resp.text
    assert double.invoked_agents == [consult_agent_id(TENANT, "persona-alpha")]
    prompt = double.prompts[0]
    assert "Deliberative meeting MT-1 round 1 (independent position)" in prompt
    assert "COMMITTEE-FULL-0" in prompt
    assert "COMMITTEE-FULL-11" in prompt
    assert "COMMITTEE-FULL-12" not in prompt
    assert "PRIVATE-LEAK-SENTINEL" not in prompt


def test_round_1_rejects_prior_round_positions(tmp_path: Path) -> None:
    double = _ProviderDouble()
    resp = _post_turn(
        double,
        tmp_path,
        meeting={
            "meeting_id": "mtg-1",
            "meeting_type": "MT-1",
            "round": 1,
            "prior_round_positions": [
                {"label": "role:chair|style:momentum#1", "role": "chair", "style": "momentum", "stance": "approve"}
            ],
        },
    )
    assert resp.status_code == 502
    assert "round 1 must be independent" in resp.json()["reason"]
    assert not double.invoked_agents


def test_round_2_accepts_anonymised_positions_and_rejects_persona_id_leak(tmp_path: Path) -> None:
    double = _ProviderDouble()
    # Leaking persona_id in prior_round_positions is rejected
    bad_resp = _post_turn(
        double,
        tmp_path,
        idem_key="r2-bad",
        meeting={
            "meeting_id": "mtg-1",
            "meeting_type": "MT-1",
            "round": 2,
            "prior_round_positions": [
                {
                    "label": "role:chair|style:momentum#1",
                    "persona_id": "persona-alpha",
                    "role": "chair",
                    "style": "momentum",
                    "stance": "approve",
                }
            ],
        },
    )
    assert bad_resp.status_code == 502
    assert "anonymised by role and style only" in bad_resp.json()["reason"]
    assert not double.invoked_agents

    # Properly anonymised Round 2 positions succeed
    ok_resp = _post_turn(
        double,
        tmp_path,
        idem_key="r2-ok",
        meeting={
            "meeting_id": "mtg-1",
            "meeting_type": "MT-1",
            "round": 2,
            "prior_round_positions": [
                {
                    "label": "role:chair|style:momentum#1",
                    "role": "chair",
                    "style": "momentum",
                    "stance": "approve",
                    "confidence": 0.8,
                    "citations": ["facts:item:1"],
                }
            ],
        },
    )
    assert ok_resp.status_code == 200, ok_resp.text
    content = ok_resp.json()["contribution"]["event_content"]
    assert content["changed"] is True
    assert content["accepted"] == ["role:chair|style:momentum#1"]
    assert content["disputed"] == ["sizing assumption"]


def test_mt4_red_team_receives_luck_or_small_sample_or_confounder_mandate(tmp_path: Path) -> None:
    double = _ProviderDouble()
    resp = _post_turn(
        double,
        tmp_path,
        persona_id="persona-red-team",
        role="red_team",
        meeting={
            "meeting_id": "mtg-1",
            "meeting_type": "MT-4",
            "round": 1,
            "assigned_case": "luck_or_small_sample_or_confounder",
        },
    )
    assert resp.status_code == 200, resp.text
    assert "MUST argue luck, small sample size, or a confounder" in double.prompts[0]


def test_round_3_requires_chair_new_evidence(tmp_path: Path) -> None:
    double = _ProviderDouble()
    no_ev = _post_turn(
        double,
        tmp_path,
        idem_key="r3-no-ev",
        meeting={
            "meeting_id": "mtg-1",
            "meeting_type": "MT-1",
            "round": 3,
            "prior_round_positions": [
                {"label": "role:chair|style:momentum#1", "role": "chair", "style": "momentum", "stance": "approve"}
            ],
        },
    )
    assert no_ev.status_code == 502
    assert "round 3 requires new evidence" in no_ev.json()["reason"]

    with_ev = _post_turn(
        double,
        tmp_path,
        idem_key="r3-with-ev",
        meeting={
            "meeting_id": "mtg-1",
            "meeting_type": "MT-1",
            "round": 3,
            "prior_round_positions": [
                {"label": "role:chair|style:momentum#1", "role": "chair", "style": "momentum", "stance": "approve"}
            ],
            "new_evidence": [{"item_id": "facts:new:1", "excerpt": "Updated decay alarm"}],
        },
    )
    assert with_ev.status_code == 200, with_ev.text


def test_private_memory_in_case_file_or_pre_read_is_refused(tmp_path: Path) -> None:
    double = _ProviderDouble()
    resp = _post_turn(
        double,
        tmp_path,
        idem_key="priv-pre",
        meeting={
            "meeting_id": "mtg-1",
            "meeting_type": "MT-1",
            "round": 1,
            "own_pre_read": {
                "persona_id": "persona-alpha",
                "entries": [{"item_id": "p1", "relevance_scope": "persona_private"}],
            },
        },
    )
    assert resp.status_code == 502
    assert "private memory cannot enter" in resp.json()["reason"]
    assert not double.invoked_agents
