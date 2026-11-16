"""End-to-end integration tests for deliberative persona_meeting (MT-1..MT-6) and gate panels.

Runs through the real consultation service, real ConsultationStore, real
workflow_executor, real memory service (Learn-feedback writeback), and the
real OpenClaw adapter consultation_provider HTTP app with the model provider
doubled at the adapter edge.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import socket
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any, Iterator

from fastapi import FastAPI
import pytest
import uvicorn

_ADAPTER_DIR = Path(__file__).resolve().parents[2] / "openclaw-gateway-adapter"
if str(_ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(_ADAPTER_DIR))

from consultation_provider import consult_agent_id, create_app as create_adapter_app  # noqa: E402
from services.consultation import main as consultation_main  # noqa: E402
from services.consultation import meetings as meetings_mod  # noqa: E402
from services.consultation.meeting_outputs import output_routing_context  # noqa: E402
from services.consultation.client import ConsultationServiceClient, open_persona_meeting  # noqa: E402
from services.consultation.provider import HttpContributionProvider  # noqa: E402
from services.consultation.store import ConsultationStore  # noqa: E402
from services.consultation.workflow_executor import ExecutorConfig, run_tick  # noqa: E402
from services.consultation.workflow_state import WorkflowStateStore  # noqa: E402
from services.memory.test_main import JWT_SECRET, _make_jwt  # noqa: E402

TENANT = "tenant-deliberative"
SERVICE_TOKEN = "delib-consult-service-token"
PROVIDER_TOKEN = "delib-provider-token"
ACTOR = "consultation-workflow-executor"

DENY_ALL = {
    "tools": {"allow": [], "deny": ["*"], "exec": {"security": "deny", "ask": "always"}},
    "skills": [],
    "memorySearch": {"enabled": False, "sources": []},
    "contextInjection": "never",
}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _serve(app: Any) -> Iterator[str]:
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() >= deadline:
            raise RuntimeError("test server did not start")
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


class _AdapterModelDouble:
    """Model provider double injected into the real adapter consultation_provider HTTP app."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.synthesis_mode: str = "normal"  # "normal" | "unreadable" | "mt4_no_hypothesis"
        self.custom_citations_by_persona: dict[str, list[str]] = {}
        self.custom_proposals: list[dict[str, Any]] | None = None

    def _gateway_call(self, method: str, **kwargs: Any) -> Any:
        if method == "config.get":
            personas = (
                "persona-alpha",
                "persona-beta",
                "persona-gamma",
                "persona-risk-officer",
                "persona-red-team",
                "agora-advisor-macro",
                "agora-servant-u1",
            )
            return {
                "valid": True,
                "config": {
                    "agents": {
                        "list": [
                            {"id": consult_agent_id(TENANT, pid), **DENY_ALL}
                            for pid in personas
                        ]
                    }
                },
            }
        return {"ok": True}

    def gateway_agents_list(self, **kwargs: Any) -> list[dict[str, Any]]:
        personas = (
            "persona-alpha",
            "persona-beta",
            "persona-gamma",
            "persona-risk-officer",
            "persona-red-team",
            "agora-advisor-macro",
            "agora-servant-u1",
        )
        return [{"id": consult_agent_id(TENANT, pid)} for pid in personas]

    def invoke_structured(self, prompt: str, **kwargs: Any) -> Any:
        raise AssertionError("persona turns must use persona's own consultation agent")

    def invoke(self, prompt: str, **kwargs: Any) -> Any:
        agent_id = str(kwargs.get("agent_id") or "")
        # Extract consultation envelope JSON from prompt
        marker = "Consultation envelope JSON: "
        envelope = json.loads(prompt.split(marker, 1)[1].splitlines()[0])
        req = envelope["request"]
        req_id = envelope["request_id"]
        participant = req["participant"]
        persona_id = str(participant["persona_id"])
        role = str(participant.get("role") or "participant")
        meeting = req.get("meeting") or (req.get("metadata") or {}).get("meeting") or {}
        round_val = meeting.get("round", 1)
        meeting_code = str(meeting.get("meeting_code") or meeting.get("meeting_type") or "MT-1")

        self.calls.append(
            {
                "request_id": req_id,
                "persona_id": persona_id,
                "role": role,
                "round": round_val,
                "meeting": meeting,
                "prompt": prompt,
            }
        )

        case_items = (
            ((req.get("case_file") or {}).get("groups") or {})
            .get("facts", {})
            .get("items")
            or []
        )
        first_case_id = str(case_items[0]["item_id"]) if case_items else f"facts:meeting_topic:{req_id}"
        own_preread_entries = (meeting.get("own_pre_read") or {}).get("entries") or []
        own_preread_id = str(own_preread_entries[0]["item_id"]) if own_preread_entries else first_case_id

        citations = list(
            self.custom_citations_by_persona.get(
                persona_id, [first_case_id, own_preread_id]
            )
        )

        if round_val == "synthesis":
            if self.synthesis_mode == "unreadable":
                return SimpleNamespace(
                    status="completed",
                    output={
                        "agent_id": agent_id,
                        "json_events": [{"item": {"text": "NOT-VALID-JSON"}}],
                    },
                )
            if self.custom_proposals is not None:
                proposals = self.custom_proposals
            elif self.synthesis_mode == "mt4_no_hypothesis":
                proposals = [
                    {
                        "proposal_id": f"prop-{req_id}-bad-lesson",
                        "kind": "lesson_candidate",
                        "sponsor_persona_id": persona_id,
                        "citations": [first_case_id],
                        "payload": {"summary": "Untestable generic lesson"},
                    }
                ]
            elif meeting_code == "MT-4":
                proposals = [
                    {
                        "proposal_id": f"prop-{req_id}-1",
                        "kind": "research_demand",
                        "sponsor_persona_id": persona_id,
                        "citations": [first_case_id],
                        "payload": {
                            "component": "exit",
                            "test": "Out-of-sample bootstrap over 60 high-volatility sessions (n>=30)",
                            "hypothesis": "Early stop-loss on high-vol days reduces tail loss by 31 bps",
                            "summary": "Validate high-vol stop-loss exit timing hypothesis",
                        },
                    }
                ]
            else:
                kind_by_code = {
                    "MT-1": "evolution_proposal",
                    "MT-2": "harness_patch",
                    "MT-3": "research_demand",
                    "MT-5": "pool_decision",
                    "MT-6": "governance_approval",
                }
                proposals = [
                    {
                        "proposal_id": f"prop-{req_id}-1",
                        "kind": kind_by_code.get(meeting_code, "research_demand"),
                        "sponsor_persona_id": persona_id,
                        "citations": [first_case_id],
                        "payload": {"summary": f"{meeting_code} proposal sponsored by {persona_id}"},
                    }
                ]
            event_content = {
                "claims": [{"claim": f"Chair synthesis for {req_id}", "evidence_refs": [first_case_id]}],
                "summary_evidence_refs": [first_case_id],
                "synthesis": {
                    "summary": f"Chair {persona_id} synthesis for {meeting_code} ({req_id})",
                    "proposals": proposals,
                },
            }
        else:
            is_panel_turn = isinstance((req.get("metadata") or {}).get("panel"), dict)
            prior = meeting.get("prior_round_positions") or []
            accepted = [p["label"] for p in prior[:1] if isinstance(p, dict) and p.get("label")]
            disputed = (
                ["argue luck, small sample, or confounder for outlier"]
                if role == "red_team" and meeting_code == "MT-4"
                else (["challenge leading assumption"] if role == "red_team" else [])
            )
            stance = "reject" if role == "red_team" else "approve_with_conditions"
            if is_panel_turn:
                event_content = {
                    "claims": [{"claim": f"{persona_id} panel claim", "evidence_refs": [first_case_id]}],
                    "summary_evidence_refs": [first_case_id],
                }
            else:
                event_content = {
                    "stance": stance,
                    "confidence": 0.65 if role == "red_team" else 0.85,
                    "citations": citations,
                    "claims": [{"claim": f"{persona_id} round {round_val} claim", "evidence_refs": citations}],
                    "summary_evidence_refs": citations,
                    "accepted": accepted,
                    "disputed": disputed,
                    "changed": bool(round_val == 2 and role != "red_team"),
                    "assigned_case": meeting.get("assigned_case"),
                }

        body = {
            "contribution_id": f"contrib-{req_id}-{persona_id}-{round_val}-{len(self.calls)}",
            "tenant_id": TENANT,
            "request_id": req_id,
            "participant_type": "persona",
            "participant_ref": persona_id,
            "author_type": "persona",
            "summary": f"{persona_id} ({role}) round={round_val}",
            "recommendation": "approve_with_conditions",
            "confidence": 0.8,
            "event_type": "persona_contribution",
            "event_content": event_content,
            "evidence": [
                {
                    "id": cid,
                    "evidence_type": "meeting_evidence",
                    "link": f"artifact://{req_id}/{cid}",
                }
                for cid in citations
            ],
            "findings": [],
        }
        return SimpleNamespace(
            status="completed",
            output={
                "agent_id": agent_id,
                "json_events": [{"item": {"text": json.dumps(body)}}],
            },
        )


def _owner_stub(method: str, url: str, headers: Any, body: Any) -> tuple[int, dict[str, Any]]:
    # No research owner listens in CI; stand in for its persona-demand intake and read-back.
    if method.upper() == "GET":
        assert "/api/research/persona-demands" in url, url
        return 200, {"demands": [{"demand_id": "demand-stub-1", "status": "submitted"}], "count": 1}
    assert method.upper() == "POST" and url.endswith("/api/research/persona-demands"), url
    return 201, {"demand_id": "demand-stub-1", "id": "demand-stub-1", "status": "submitted"}


@pytest.fixture
def stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    monkeypatch.setenv("CONSULTATION_AUTH_REQUIRED", "true")
    monkeypatch.setenv("CONSULTATION_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("PANTHEON_TENANT_ID", TENANT)
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    monkeypatch.delenv("PANTHEON_MEMORY_AUTHZ_MODE", raising=False)
    monkeypatch.setenv(
        "PANTHEON_MEMORY_AUTH_TOKEN",
        _make_jwt(roles=["memory-writer", "operator"], tenant_id=TENANT),
    )
    monkeypatch.setenv("PANTHEON_MEMORY_ACTOR_ID", "consultation-svc")
    monkeypatch.setenv("PANTHEON_MEMORY_ACTOR_ROLES", "operator")
    monkeypatch.setenv("PANTHEON_MEMORY_STORE", str(tmp_path / "institutional.json"))
    monkeypatch.setenv("PANTHEON_PERSONA_MEMORY_STORE", str(tmp_path / "persona.json"))
    monkeypatch.setenv("PANTHEON_MEMORY_DATA_DIR", str(tmp_path / "memory"))

    consultation_main.store = ConsultationStore(str(tmp_path / "domain"))
    consultation_main.workflow_state = WorkflowStateStore(tmp_path / "wf.sqlite3")

    from services.memory import main as memory_main

    double = _AdapterModelDouble()
    adapter_app = FastAPI()
    create_adapter_app(
        application=adapter_app,
        provider=double,
        provider_token=PROVIDER_TOKEN,
        expected_service_actor=ACTOR,
        replay_path=tmp_path / "adapter_replay.sqlite3",
        committee_memory_fetcher=lambda tenant_id, persona_id, session_id, query: {
            "hits": [
                {
                    "type": "persona",
                    "relevance_score": 0.95,
                    "entry": {
                        "memory_id": f"preread:{persona_id}:1",
                        "tenant_id": tenant_id,
                        "persona_id": persona_id,
                        "relevance_scope": "persona_and_committee",
                        "memory_type": "consultation_outcome",
                        "content": {"summary": f"{persona_id} committee-visible memory"},
                    },
                }
            ]
        },
    )

    @adapter_app.post("/api/openclaw-adapter/consultation/agents/ensure")
    def _ensure_override(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "ready",
            "tenant_id": payload["tenant_id"],
            "persona_id": payload["persona_id"],
            "agent_id": consult_agent_id(payload["tenant_id"], payload["persona_id"]),
        }

    # Override the ensure route with the fast test double while keeping
    # CONSULTATION_CONTRIBUTION_PATH running through the real consultation_provider!
    adapter_app.router.routes = [
        r for r in adapter_app.router.routes if getattr(r, "path", "") != "/api/openclaw-adapter/consultation/agents/ensure"
    ]

    @adapter_app.post("/api/openclaw-adapter/consultation/agents/ensure")
    def _ensure_fast(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "ready",
            "tenant_id": payload["tenant_id"],
            "persona_id": payload["persona_id"],
            "agent_id": consult_agent_id(payload["tenant_id"], payload["persona_id"]),
        }

    @adapter_app.post("/api/governance/consultation-handoffs")
    def _handoff_sink(payload: dict[str, Any]) -> dict[str, Any]:
        handoff = payload.get("handoff") or {}
        return {
            "acknowledged": True,
            "handoff_id": handoff.get("handoff_id"),
            "consumer_ref": "governance-handoff-sink",
        }

    @adapter_app.get("/api/registry/entries/{ref}")
    def _registry_entry(ref: str) -> dict[str, Any]:
        return {
            "registry_id": ref,
            "tenant_id": TENANT,
            "strategy_id": "momentum",
            "artifact_type": "strategy_spec",
            "version": "1",
            "artifact_state": "candidate",
            "metadata": {"underlyings": ["2330"], "instrument_class": "equity"},
            "evaluation_summary": {"sharpe": 1.3},
        }

    @adapter_app.get("/api/registry/strategies/{family}/entries")
    def _registry_history(family: str) -> list[Any]:
        return []

    @adapter_app.get("/api/personas/{pid}/strategy-pool")
    def _persona_pool(pid: str) -> dict[str, Any]:
        return {"pool_version": 1, "members": [{"artifact_ref": "reg-member-1", "stage": "active"}]}

    @adapter_app.get("/api/personas/{pid}/strategy-pool/versions")
    def _persona_pool_versions(pid: str) -> dict[str, Any]:
        return {"versions": [], "refusals": []}

    with _serve(consultation_main.app) as api_url, _serve(memory_main.app) as memory_url, _serve(
        adapter_app
    ) as adapter_url:
        monkeypatch.setenv("PANTHEON_MEMORY_API_URL", memory_url)
        monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", adapter_url)
        monkeypatch.setenv("PANTHEON_PERSONA_API_URL", adapter_url)
        cfg = ExecutorConfig(
            api_url=api_url,
            tenant_id=TENANT,
            api_token=SERVICE_TOKEN,
            provider_url=f"{adapter_url}/api/openclaw-adapter/consultation/contributions",
            provider_token=PROVIDER_TOKEN,
            provider_service_actor=ACTOR,
            handoff_sink_url=f"{adapter_url}/api/governance/consultation-handoffs",
            handoff_token=PROVIDER_TOKEN,
            worker_id="meeting-worker",
            state_path=str(tmp_path / "wf.sqlite3"),
            memory_url=memory_url,
            retry_after_seconds=0,
            timeout_seconds=15.0,
            batch_size=10,
        )
        client = ConsultationServiceClient(
            base_url=api_url,
            token=SERVICE_TOKEN,
            tenant_id=TENANT,
            service_actor="meeting-test-client",
        )
        with output_routing_context(transport=_owner_stub, store_path=tmp_path / "meeting_outputs.json"):
            yield {
                "api_url": api_url,
                "memory_url": memory_url,
                "adapter_url": adapter_url,
                "cfg": cfg,
                "client": client,
                "double": double,
                "memory_main": memory_main,
            }


def _tick(stack: dict[str, Any]) -> dict[str, Any]:
    cfg: ExecutorConfig = stack["cfg"]
    provider = HttpContributionProvider(
        endpoint=cfg.provider_url,
        bearer_token=cfg.provider_token,
        service_actor=cfg.provider_service_actor,
        timeout_seconds=cfg.timeout_seconds,
    )
    return run_tick(
        config=cfg,
        state=WorkflowStateStore(cfg.state_path),
        provider=provider,
    )


def test_six_meeting_types_and_mt4_attribution_review_end_to_end(
    stack: dict[str, Any],
) -> None:
    client: ConsultationServiceClient = stack["client"]
    double: _AdapterModelDouble = stack["double"]

    specs = [
        ("MT-1", "Weekly pool evolution review", ["persona-alpha", "persona-beta", "persona-risk-officer"], None, None),
        ("MT-2", "Optimisation review on pending lessons", ["persona-alpha", "persona-beta", "persona-risk-officer"], None, None),
        ("MT-3", "Cross-persona strategy ideation", ["persona-alpha", "persona-beta", "persona-red-team"], None, None),
        (
            "MT-4",
            "Outlier attribution review for trader-u1",
            [
                {"persona_id": "persona-alpha", "role": "chair", "style": "momentum"},
                {
                    "persona_id": "agora-servant-u1",
                    "origin": "private_servant",
                    "agora_user_id": "trader-u1",
                    "role": "subject",
                    "style": "trader_habit_shadow",
                },
                {"persona_id": "persona-risk-officer", "role": "risk_reviewer"},
                {"persona_id": "persona-red-team", "role": "red_team"},
            ],
            "trader:trader-u1",
            "attr-u1-outlier-1",
        ),
        ("MT-5", "Macro regime transition interpretation", ["persona-alpha", "persona-beta", "persona-risk-officer"], None, None),
        ("MT-6", "Q4 quarterly review before allocation", ["persona-alpha", "persona-beta", "persona-risk-officer", "persona-red-team"], None, None),
    ]

    meeting_ids: dict[str, str] = {}
    for idx, (mtype, topic, roster, subj, attr_id) in enumerate(specs):
        created = open_persona_meeting(
            base_url=stack["api_url"],
            token=SERVICE_TOKEN,
            tenant_id=TENANT,
            meeting_type=mtype,
            topic=topic,
            convened_by={"kind": "persona", "id": "persona-alpha"},
            subject_ref=subj,
            participants=roster,
            evidence_refs=[f"facts:{mtype}:1"],
            attribution_record_id=attr_id,
            idempotency_key=f"e2e-{mtype}",
            iso_week=f"2026-W{40 + idx}",
        )
        assert created["status"] == "convened"
        meeting_ids[mtype] = created["meeting_id"]

    tick_res = _tick(stack)
    assert tick_res["errors"] == [], tick_res
    assert tick_res["completed"] == 6, tick_res

    # Verify list route
    listed = client.list_meetings(status="closed")
    assert len(listed["items"]) == 6

    # Verify MT-4 detail, attribution record, red-team assignment, testable hypothesis, and memory writebacks
    mt4_id = meeting_ids["MT-4"]
    mt4 = client.get_meeting(mt4_id)
    assert mt4 is not None
    assert mt4["status"] == "closed"
    assert mt4["round"] == 2
    assert mt4["attribution_record"]["attribution_record_id"] == "attr-u1-001" or (
        mt4["attribution_record"]["attribution_record_id"] == "attr-u1-outlier-1"
    )
    assert any(i["kind"] == "attribution_record" for i in mt4["case_file_items"])

    # Pre-read has committee-visible entries for each participant and no persona_private
    assert len(mt4["pre_read"]) == 4
    assert "persona_private" not in json.dumps(mt4)

    # Round 1 & Round 2 positions and anonymisation check
    assert len(mt4["rounds"]) == 2
    r1_calls = [c for c in double.calls if c["request_id"] == mt4_id and c["round"] == 1]
    r2_calls = [c for c in double.calls if c["request_id"] == mt4_id and c["round"] == 2]
    assert len(r1_calls) == 4
    assert len(r2_calls) == 4
    for call in r1_calls:
        assert not call["meeting"].get("prior_round_positions")
    for call in r2_calls:
        prior = call["meeting"]["prior_round_positions"]
        assert len(prior) == 4
        for p in prior:
            assert "persona_id" not in p
            assert p["role"] and p["style"]
    red_r1 = next(c for c in r1_calls if c["persona_id"] == "persona-red-team")
    assert red_r1["meeting"]["assigned_case"] == "luck_or_small_sample_or_confounder"

    # Synthesis proposals, tally, dissent memo, and C2 output dispatch
    synth = mt4["synthesis"]
    assert synth["voting"] is False
    assert synth["tally"]["voting"] is False
    assert synth["dissent_memo_id"] is not None
    assert len(synth["proposals"]) == 1
    prop = synth["proposals"][0]
    assert prop["kind"] == "research_demand"
    assert prop["sponsor_persona_id"] == "persona-alpha"
    assert prop["payload"]["component"] == "exit"
    assert "bootstrap" in prop["payload"]["test"].lower()
    assert prop["downstream"]["status"] == "submitted"

    # Memory writebacks: 1 institutional fact + 1 persona memory per participant (4 participants)
    assert len(mt4["writebacks"]) == 4
    mm = stack["memory_main"]
    inst_entries = mm._store().find_by_source_event(
        source_event_type="consultation_closed",
        source_event_id=f"consultation:meeting:{mt4_id}",
        write_authority="consultation-svc",
        active_only=False,
    )
    assert len(inst_entries) == 1
    persona_entries = mm._persona_store().find_by_source_event(
        source_event_type="consultation_closed",
        source_event_id=f"consultation:meeting:{mt4_id}",
        write_authority="consultation-svc",
        active_only=False,
    )
    assert {e.persona_id for e in persona_entries} == {
        "persona-alpha",
        "agora-servant-u1",
        "persona-risk-officer",
        "persona-red-team",
    }

    # Proposals read route and Catalogue schedule route
    proposals_view = client.list_meeting_proposals(meeting_id=mt4_id)
    assert len(proposals_view["items"]) == 1
    assert proposals_view["items"][0]["proposal_id"] == prop["proposal_id"]

    catalogue_view = client.get_meeting_catalogue()
    assert len(catalogue_view["schedule"]) >= 6
    assert all(s["last_run_at"] and s["next_due_at"] for s in catalogue_view["schedule"])


def test_citation_rejection_and_round_3_with_chair_evidence_and_review_outcomes(
    stack: dict[str, Any],
) -> None:
    client: ConsultationServiceClient = stack["client"]
    double: _AdapterModelDouble = stack["double"]

    # 1. Participant citing outside case file and own pre-read is rejected;
    #    chair records new_evidence so Round 3 runs!
    double.custom_citations_by_persona = {
        "persona-beta": ["preread:persona-alpha:1"],  # Another persona's pre-read -> must be rejected!
    }
    created = client.open_persona_meeting(
        meeting_type="MT-1",
        topic="Pool evolution with new chair evidence",
        convened_by={"kind": "persona", "id": "persona-alpha"},
        participants=[
            {"persona_id": "persona-alpha", "role": "chair"},
            {"persona_id": "persona-beta", "role": "trading_peer"},
            {"persona_id": "persona-risk-officer", "role": "risk_reviewer"},
        ],
        evidence_refs=["facts:mt1:base"],
        new_evidence=[{"item_id": "facts:mt1:chair-new-1", "excerpt": "Intraday decay spike"}],
        iso_week="2026-W48",
    )
    mtg_id = created["meeting_id"]
    _tick(stack)
    detail = client.get_meeting(mtg_id)
    assert detail is not None
    assert detail["round"] == 3
    assert len(detail["rounds"]) == 3
    # persona-beta's position citing persona-alpha's pre-read was rejected in every round
    for r in detail["rounds"]:
        pids = [p["persona_id"] for p in r["positions"]]
        assert "persona-beta" not in pids
        assert "persona-alpha" in pids
    assert any(rp["persona_id"] == "persona-beta" for rp in detail["rejected_positions"])
    double.custom_citations_by_persona.clear()

    # 2. MT-4 synthesis with no testable causal hypothesis records no_testable_hypothesis
    double.synthesis_mode = "mt4_no_hypothesis"
    mt4_no_hyp = client.open_persona_meeting(
        meeting_type="MT-4",
        topic="MT-4 where outlier is pure luck",
        convened_by={"kind": "persona", "id": "persona-alpha"},
        attribution_record_id="attr-luck-only",
        participants=["persona-alpha", "persona-risk-officer", "persona-red-team"],
        iso_week="2026-W49",
    )
    _tick(stack)
    mt4_detail = client.get_meeting(mt4_no_hyp["meeting_id"])
    assert mt4_detail is not None
    assert mt4_detail["outcome"] == "no_testable_hypothesis"
    assert mt4_detail["synthesis"]["outcome"] == "no_testable_hypothesis"
    assert mt4_detail["synthesis"]["proposals"] == []

    # 3. Unreadable synthesis is recorded as REVIEW with no proposal; advisor/servant sponsor rejected
    double.synthesis_mode = "unreadable"
    rev_mtg = client.open_persona_meeting(
        meeting_type="MT-2",
        topic="Meeting with unreadable synthesis",
        convened_by={"kind": "persona", "id": "persona-alpha"},
        participants=["persona-alpha", "persona-risk-officer"],
        iso_week="2026-W50",
    )
    _tick(stack)
    rev_detail = client.get_meeting(rev_mtg["meeting_id"])
    assert rev_detail is not None
    assert rev_detail["outcome"] == "REVIEW"
    assert rev_detail["synthesis"]["outcome"] == "REVIEW"
    assert rev_detail["synthesis"]["proposals"] == []
    double.synthesis_mode = "normal"

    # 4. Advisor and servant cannot sponsor proposals
    double.custom_proposals = [
        {
            "proposal_id": "prop-bad-advisor-sponsor",
            "kind": "research_demand",
            "sponsor_persona_id": "agora-advisor-macro",
            "citations": ["facts:mt1:sponsor-test"],
            "payload": {"summary": "Advisor tried to sponsor"},
        },
        {
            "proposal_id": "prop-bad-servant-sponsor",
            "kind": "research_demand",
            "sponsor_persona_id": "agora-servant-u1",
            "citations": ["facts:mt1:sponsor-test"],
            "payload": {"summary": "Servant tried to sponsor"},
        },
        {
            "proposal_id": "prop-valid-agora-unsponsored",
            "kind": "agora_candidate_decision",
            "sponsor_persona_id": None,
            "citations": ["facts:mt1:sponsor-test"],
            "payload": {"summary": "Agora candidate decision without trading sponsor"},
        },
    ]
    sp_mtg = client.open_persona_meeting(
        meeting_type="MT-1",
        topic="Sponsor validation test",
        convened_by={"kind": "agora_user", "id": "trader-u1"},
        participants=[
            {"persona_id": "persona-alpha", "role": "chair"},
            {"persona_id": "agora-advisor-macro", "origin": "agora_advisor", "lifecycle_state": "consultable"},
            {"persona_id": "agora-servant-u1", "origin": "private_servant", "agora_user_id": "trader-u1"},
            {"persona_id": "persona-risk-officer", "role": "risk_reviewer"},
        ],
        evidence_refs=["facts:mt1:sponsor-test"],
        iso_week="2026-W51",
    )
    _tick(stack)
    sp_detail = client.get_meeting(sp_mtg["meeting_id"])
    assert sp_detail is not None
    kept_ids = [p["proposal_id"] for p in sp_detail["synthesis"]["proposals"]]
    assert kept_ids == ["prop-valid-agora-unsponsored"]
    assert len(sp_detail["synthesis"]["rejected_proposals"]) == 2
    double.custom_proposals = None


def test_both_consultation_classes_gate_panel_and_deliberative_with_c2_module(
    stack: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.persona import consult_triggers as triggers

    client: ConsultationServiceClient = stack["client"]
    dispatched_calls: list[dict[str, Any]] = []

    fake_c2 = SimpleNamespace(
        dispatch_proposals=lambda meeting: [
            dispatched_calls.append(meeting)
            or {
                "proposal_id": p["proposal_id"],
                "downstream_target": "research_persona_demands",
                "downstream_id": f"demand-{p['proposal_id']}",
                "status": "queued_for_validation",
                "error": None,
            }
            for p in meeting["synthesis"]["proposals"]
        ],
        read_downstream_status=lambda proposal_id: {
            "status": "validated_in_research",
            "downstream_id": f"demand-{proposal_id}",
            "updated_at": "2026-11-16T14:00:00Z",
        },
    )
    monkeypatch.setitem(sys.modules, "services.consultation.meeting_outputs", fake_c2)

    # Class 1: Gate panel (persona_panel)
    req = triggers.required_consultation(
        triggers.DECISION_TRIAL_ADMISSION,
        "reg-member-1",
        initiator_persona_id="persona-alpha",
    )
    assert req is not None
    panel_created = client.open_persona_panel(
        req,
        persona_id="persona-alpha",
        trace_id="trace-panel-class-1",
    )

    # Class 2: Deliberative meeting (persona_meeting, MT-4)
    mt4_created = client.open_persona_meeting(
        meeting_type="MT-4",
        topic="Class 2 deliberative MT-4 alongside gate panel",
        convened_by={"kind": "persona", "id": "persona-alpha"},
        attribution_record_id="attr-class-2",
        participants=["persona-alpha", "persona-risk-officer", "persona-red-team"],
        iso_week="2026-W52",
    )

    res = _tick(stack)
    assert res["errors"] == [], res
    assert res["completed"] == 2, res

    # Gate panel published its memo and kept its protocol
    panel_memos = client.list_memos(request_id=panel_created["request_id"])
    assert len(panel_memos) == 1 and panel_memos[0]["status"] == "published"

    # Deliberative meeting called C2 dispatch_proposals and reflects live read_downstream_status
    assert len(dispatched_calls) == 1
    mt4_detail = client.get_meeting(mt4_created["meeting_id"])
    assert mt4_detail is not None
    prop = mt4_detail["synthesis"]["proposals"][0]
    assert prop["downstream"]["id"] == f"demand-{prop['proposal_id']}"
    assert prop["downstream"]["status"] == "validated_in_research"
    props_list = client.list_meeting_proposals(meeting_id=mt4_created["meeting_id"])
    assert props_list["items"][0]["downstream"]["status"] == "validated_in_research"

