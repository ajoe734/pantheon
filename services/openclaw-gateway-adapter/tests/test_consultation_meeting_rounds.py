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


def _extract_prompt_schema(prompt: str) -> dict[str, Any]:
    marker = "satisfying this JSON schema:\n"
    schema_line = prompt.split(marker, 1)[1].splitlines()[0]
    return json.loads(schema_line)


def test_round_1_round_2_and_chair_synthesis_prompt_schemas_validate_meeting_fields_and_fenced_json(
    tmp_path: Path,
) -> None:
    import jsonschema

    # 1. Round 1 prompt schema explicitly allows stance, citations, proposals, accepted, disputed, changed
    r1_double = _ProviderDouble(
        event_content_override={
            "stance": "approve_with_conditions",
            "confidence": 0.85,
            "citations": ["facts:item:1"],
            "claims": [{"claim": "Independent round 1 position", "evidence_refs": ["facts:item:1"]}],
            "summary_evidence_refs": ["facts:item:1"],
            "proposals": [
                {
                    "proposal_id": "prop-r1-1",
                    "kind": "research_demand",
                    "sponsor_persona_id": "persona-alpha",
                    "citations": ["facts:item:1"],
                    "payload": {"mandate": "momentum"},
                }
            ],
        }
    )
    r1_resp = _post_turn(
        r1_double,
        tmp_path,
        idem_key="schema-r1",
        meeting={"meeting_id": "mtg-1", "meeting_type": "MT-1", "round": 1},
    )
    assert r1_resp.status_code == 200, r1_resp.text
    r1_schema = _extract_prompt_schema(r1_double.prompts[0])
    r1_ec_schema = r1_schema["properties"]["event_content"]
    assert set(r1_ec_schema["required"]) >= {"stance", "citations", "claims", "summary_evidence_refs"}
    for field in ("stance", "citations", "proposals", "accepted", "disputed", "changed"):
        assert field in r1_ec_schema["properties"]
    r1_contrib = dict(r1_resp.json()["contribution"])
    r1_contrib.pop("author_type", None)
    r1_contrib["event_content"] = dict(r1_contrib["event_content"])
    r1_contrib["event_content"].pop("committee_memory_status", None)
    jsonschema.validate(r1_contrib, r1_schema)

    # 2. Round 2 prompt schema requires accepted, disputed, changed alongside stance, citations, claims, summary_evidence_refs
    r2_double = _ProviderDouble()
    r2_resp = _post_turn(
        r2_double,
        tmp_path,
        idem_key="schema-r2",
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
    assert r2_resp.status_code == 200, r2_resp.text
    r2_schema = _extract_prompt_schema(r2_double.prompts[0])
    r2_ec_schema = r2_schema["properties"]["event_content"]
    assert set(r2_ec_schema["required"]) == {
        "stance",
        "citations",
        "claims",
        "summary_evidence_refs",
        "accepted",
        "disputed",
        "changed",
    }
    r2_contrib = dict(r2_resp.json()["contribution"])
    r2_contrib.pop("author_type", None)
    r2_contrib["event_content"] = dict(r2_contrib["event_content"])
    r2_contrib["event_content"].pop("committee_memory_status", None)
    jsonschema.validate(r2_contrib, r2_schema)

    # 3. Chair synthesis turn schema requires synthesis={summary, outcome, proposals} and _persona_response strips markdown code fences
    class _FencedSynthesisDouble(_ProviderDouble):
        def invoke(self, prompt: str, **kwargs: Any) -> Any:
            base = super().invoke(prompt, **kwargs)
            raw_json = base.output["json_events"][0]["item"]["text"]
            fenced = f"```json\n{raw_json}\n```\n"
            return SimpleNamespace(
                status="completed",
                output={
                    "agent_id": kwargs.get("agent_id"),
                    "json_events": [{"item": {"text": fenced}}],
                },
            )

    synth_double = _FencedSynthesisDouble(
        event_content_override={
            "claims": [{"claim": "Chair synthesis conclusion", "evidence_refs": ["facts:item:1"]}],
            "summary_evidence_refs": ["facts:item:1"],
            "synthesis": {
                "summary": "Chair synthesis with one sponsored research demand proposal",
                "outcome": "proposals_issued",
                "proposals": [
                    {
                        "proposal_id": "prop-mtg-1-1",
                        "kind": "research_demand",
                        "sponsor_persona_id": "persona-alpha",
                        "citations": ["facts:item:1"],
                        "payload": {
                            "mandate": "momentum",
                            "missing_datasets": [],
                            "tried_families": ["momentum"],
                            "short_measure": "effective_bets",
                            "pool_version": 1,
                        },
                    }
                ],
            },
        }
    )
    synth_resp = _post_turn(
        synth_double,
        tmp_path,
        idem_key="schema-synth-fenced",
        meeting={
            "meeting_id": "mtg-1",
            "meeting_type": "MT-1",
            "round": "synthesis",
            "phase": "synthesis",
        },
    )
    assert synth_resp.status_code == 200, synth_resp.text
    synth_schema = _extract_prompt_schema(synth_double.prompts[0])
    synth_ec_schema = synth_schema["properties"]["event_content"]
    assert "synthesis" in synth_ec_schema["required"]
    synth_obj_schema = synth_ec_schema["properties"]["synthesis"]
    assert set(synth_obj_schema["required"]) == {"summary", "outcome", "proposals"}
    assert set(synth_obj_schema["properties"]["outcome"]["enum"]) == {
        "proposals_issued",
        "completed_no_proposal",
        "no_testable_hypothesis",
    }
    synth_contrib = dict(synth_resp.json()["contribution"])
    assert synth_contrib["event_content"]["synthesis"]["outcome"] == "proposals_issued"
    assert (
        synth_contrib["event_content"]["synthesis"]["proposals"][0]["sponsor_persona_id"]
        == "persona-alpha"
    )
    synth_contrib.pop("author_type", None)
    synth_contrib["event_content"] = dict(synth_contrib["event_content"])
    synth_contrib["event_content"].pop("committee_memory_status", None)
    jsonschema.validate(synth_contrib, synth_schema)
