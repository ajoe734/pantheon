"""End-to-end integration tests for deliberative persona_meeting (MT-1..MT-6) and gate panels.

Runs through the real consultation service, real ConsultationStore, real
workflow_executor, real memory service (Learn-feedback writeback), and the
real OpenClaw adapter consultation_provider HTTP app with the model provider
doubled at the adapter edge.
"""

from __future__ import annotations

from contextlib import contextmanager
import copy
import json
from pathlib import Path
import socket
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any, Iterator, Mapping
import urllib.error
import urllib.parse

from fastapi import FastAPI
import pytest
import uvicorn

_ADAPTER_DIR = Path(__file__).resolve().parents[2] / "openclaw-gateway-adapter"
if str(_ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(_ADAPTER_DIR))

from consultation_provider import consult_agent_id, create_app as create_adapter_app  # noqa: E402
from services.consultation import main as consultation_main  # noqa: E402
from services.consultation import meetings as meetings_mod  # noqa: E402
from services.consultation.client import ConsultationServiceClient, open_persona_meeting  # noqa: E402
from services.consultation.meeting_outputs import (  # noqa: E402
    configure_output_routing,
    output_routing_context,
)
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
        self.custom_actions: list[dict[str, Any]] | None = None

    def _gateway_call(self, method: str, **kwargs: Any) -> Any:
        if method == "config.get":
            personas = (
                "persona-alpha",
                "persona-beta",
                "persona-gamma",
                "persona-2448c7053e33ea48da47",
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
            "persona-2448c7053e33ea48da47",
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
            if self.custom_actions is not None:
                event_content["synthesis"].update(
                    outcome="actions_adopted", actions=[
                        {**a, "source_refs": [first_case_id]} for a in self.custom_actions])
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


class _SlowGatewayDouble(_AdapterModelDouble):
    """Stateful gateway whose every call costs ``cost`` seconds (hosted CLI spawn).

    Hosted measures 5-7 s per call against a 30 s executor timeout; the tests
    scale both by 10 (0.6 s vs 3 s) so the ratio, not the wall clock, is what
    decides the outcome.
    """

    cost = 0.6

    def __init__(self, tmp_path: Path) -> None:
        super().__init__()
        self.state_lock = threading.Lock()
        self.entries: list[dict[str, Any]] = [{"id": "main", "default": True}]
        self.revision = 0
        self.log: list[tuple[str, float]] = []

    def _gateway_call(self, method: str, params: Any = None, **kwargs: Any) -> Any:
        self.log.append((method, time.monotonic()))
        time.sleep(self.cost)
        with self.state_lock:
            if method == "config.get":
                return {
                    "valid": True,
                    "hash": str(self.revision),
                    "config": {"agents": {"list": copy.deepcopy(self.entries)}},
                }
            if method == "config.patch":
                assert params["baseHash"] == str(self.revision)
                for desired in json.loads(params["raw"])["agents"]["list"]:
                    current = next((a for a in self.entries if a["id"] == desired["id"]), None)
                    assert current is None or current != {**current, **desired}, (
                        "config.patch sent for an agent that already holds the spec"
                    )
                    if current is None:
                        self.entries.append(desired)
                    else:
                        current.update(desired)
                self.revision += 1
            return {"ok": True}

    def gateway_agents_list(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.log.append(("agents.list", time.monotonic()))
        time.sleep(self.cost)
        with self.state_lock:
            return [{"id": a["id"]} for a in self.entries]

    def spawns(self) -> int:
        return len(self.log)


@pytest.fixture
def stack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[dict[str, Any]]:
    real_ensure = getattr(request, "param", None) == "real_ensure"
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

    double = _SlowGatewayDouble(tmp_path) if real_ensure else _AdapterModelDouble()
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

    @adapter_app.get("/api/personas")
    def _persona_directory_double():
        # Persona owner stand-in: the directory is readable (and empty for these flows).
        return []

    if not real_ensure:
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

    output_calls: list[dict[str, Any]] = []
    recorded_demands: list[dict[str, Any]] = []
    recorded_patches: dict[str, dict[str, Any]] = {}
    output_ledger_path = tmp_path / "meeting_output_dispatches.json"

    def _recording_output_transport(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: dict[str, Any] | None,
    ) -> tuple[int, dict[str, Any]]:
        parsed = urllib.parse.urlsplit(url)
        path_and_query = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        output_calls.append(
            {
                "method": method.upper(),
                "url": url,
                "path": path_and_query,
                "headers": dict(headers),
                "body": copy.deepcopy(body),
            }
        )
        idem_key = str(headers.get("Idempotency-Key") or "")
        proposal_id = (
            idem_key.rsplit(":", 1)[-1]
            if ":" in idem_key
            else f"call-{len(output_calls)}"
        )
        now_ts = "2026-11-16T12:00:00Z"

        if parsed.path.startswith("/api/research/persona-demands"):
            if method.upper() == "POST":
                demand = {
                    "demand_id": f"demand-{proposal_id}",
                    "persona_id": (body or {}).get("persona_id"),
                    "status": "submitted",
                    "created_at": now_ts,
                    "updated_at": now_ts,
                }
                recorded_demands.append(demand)
                return 201, dict(demand)
            return 200, {"demands": list(recorded_demands)}

        if parsed.path.startswith("/api/evolution/proposals"):
            if method.upper() == "POST":
                dec_id = str((body or {}).get("decision_id") or f"evo-{proposal_id}")
                return 201, {
                    "decision_id": dec_id,
                    "decision_state": "submitted",
                    "updated_at": now_ts,
                }
            dec_id = parsed.path.rsplit("/", 1)[-1]
            return 200, {
                "decision_id": dec_id,
                "decision_state": "submitted",
                "updated_at": now_ts,
            }

        if parsed.path.endswith("/harness/patches") or parsed.path.endswith("/harness"):
            if method.upper() == "POST":
                patch_id = str((body or {}).get("patch_id") or f"patch-{proposal_id}")
                recorded_patches[patch_id] = {
                    "patch_id": patch_id,
                    "status": "submitted",
                }
                return 201, {
                    "patch_id": patch_id,
                    "proposal": {"patch_id": patch_id},
                    "status": "submitted",
                    "updated_at": now_ts,
                }
            return 200, {"patches": dict(recorded_patches), "events": []}

        if "/strategy-pool/" in parsed.path:
            return (201 if method.upper() == "POST" else 200), {
                "pool_version": 2,
                "decision": {"status": "recorded", "execution_status": "submitted"},
                "updated_at": now_ts,
            }

        if parsed.path.startswith("/api/governance/approvals"):
            dec_id = (
                str((body or {}).get("decision_id") or f"apv-{proposal_id}")
                if method.upper() == "POST"
                else parsed.path.rsplit("/", 1)[-1]
            )
            return (201 if method.upper() == "POST" else 200), {
                "decision_id": dec_id,
                "decision_state": "submitted",
                "recorded_at": now_ts,
            }

        if parsed.path.startswith("/bff/agora/"):
            cand_id = (
                f"cand-{proposal_id}"
                if method.upper() == "POST"
                else parsed.path.split("/")[4]
            )
            return (201 if method.upper() == "POST" else 200), {
                "data": {
                    "candidate": {
                        "proposal_id": cand_id,
                        "state": "submitted",
                        "updated_at": now_ts,
                    }
                }
            }

        if parsed.path.startswith("/api/memory/trade-lessons"):
            lc_id = (
                str((body or {}).get("lesson_candidate_id") or f"lc-{proposal_id}")
                if method.upper() == "POST"
                else parsed.path.rsplit("/", 1)[-1]
            )
            return (201 if method.upper() == "POST" else 200), {
                "lesson_candidate_id": lc_id,
                "review_state": "submitted",
                "updated_at": now_ts,
            }

        raise AssertionError(f"Unrouted output transport path: {parsed.path}")

    configure_output_routing(transport=None, store_path=None, clear_live_refs=True)
    with _serve(consultation_main.app) as api_url, _serve(memory_main.app) as memory_url, _serve(
        adapter_app
    ) as adapter_url, output_routing_context(
        transport=_recording_output_transport,
        store_path=output_ledger_path,
    ):
        monkeypatch.setenv("PANTHEON_MEMORY_API_URL", memory_url)
        monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", adapter_url)
        monkeypatch.setenv("PANTHEON_PERSONA_API_URL", adapter_url)
        if real_ensure:
            import consult_agent_provisioning as provisioning

            monkeypatch.setenv("PANTHEON_OPENCLAW_AGENT_REGISTRY_LOCK", str(tmp_path / "registry.lock"))
            monkeypatch.setenv("CONSULT_AGENT_ENSURE_WAIT_SECONDS", "2.0")
            monkeypatch.setattr(
                provisioning, "_persona_record",
                lambda tenant, persona: {"tenant_id": tenant, "persona_id": persona},
            )
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
            timeout_seconds=3.0 if real_ensure else 15.0,
            batch_size=10,
        )
        client = ConsultationServiceClient(
            base_url=api_url,
            token=SERVICE_TOKEN,
            tenant_id=TENANT,
            service_actor="meeting-test-client",
        )
        try:
            yield {
                "api_url": api_url,
                "memory_url": memory_url,
                "adapter_url": adapter_url,
                "cfg": cfg,
                "client": client,
                "double": double,
                "memory_main": memory_main,
                "output_calls": output_calls,
                "output_ledger_path": output_ledger_path,
            }
        finally:
            configure_output_routing(transport=None, store_path=None, clear_live_refs=True)


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
    assert prop["downstream"]["id"] == f"demand-{prop['proposal_id']}"
    assert prop["downstream"]["target"] == "/api/research/persona-demands"

    mt4_dispatch_calls = [
        c
        for c in stack["output_calls"]
        if c["method"] == "POST"
        and c["headers"].get("Idempotency-Key")
        == f"meeting-output:{mt4_id}:{prop['proposal_id']}"
    ]
    assert len(mt4_dispatch_calls) == 1
    assert mt4_dispatch_calls[0]["method"] == "POST"
    assert mt4_dispatch_calls[0]["path"] == "/api/research/persona-demands"
    assert mt4_dispatch_calls[0]["body"] == {
        "persona_id": "persona-alpha",
        "mandate": "meeting_research_demand",
        "missing_datasets": [],
        "tried_families": ["momentum"],
        "short_measure": "effective_bets",
        "pool_version": 1,
        "evidence_ref": "facts:attribution_record:attr-u1-outlier-1",
    }

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
    from services.consultation import meeting_outputs as actual_c2

    fake_c2 = SimpleNamespace(
        # Only the proposal seam is doubled here. Retain the actual action
        # contract so this older cross-class test exercises the same module API.
        read_meeting_actions=actual_c2.read_meeting_actions,
        dispatch_meeting_actions=actual_c2.dispatch_meeting_actions,
        review_meeting_actions=actual_c2.review_meeting_actions,
        MeetingOutputRoutingError=actual_c2.MeetingOutputRoutingError,
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


def test_mt4_output_transport_connection_error_records_failed_and_meeting_still_closes(
    stack: dict[str, Any],
) -> None:
    client: ConsultationServiceClient = stack["client"]

    def _refused_transport(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: dict[str, Any] | None,
    ) -> tuple[int, dict[str, Any]]:
        raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

    with output_routing_context(
        transport=_refused_transport,
        store_path=stack["output_ledger_path"],
    ):
        created = client.open_persona_meeting(
            meeting_type="MT-4",
            topic="MT-4 with downstream research connection refused",
            convened_by={"kind": "persona", "id": "persona-alpha"},
            attribution_record_id="attr-conn-refused-1",
            participants=["persona-alpha", "persona-risk-officer", "persona-red-team"],
            iso_week="2026-W53",
        )
        res = _tick(stack)
        assert res["errors"] == [], res
        assert res["completed"] == 1, res

        mt4_detail = client.get_meeting(created["meeting_id"])
        assert mt4_detail is not None
        assert mt4_detail["status"] == "closed"
        prop = mt4_detail["synthesis"]["proposals"][0]
        assert prop["downstream"]["status"] == "failed"
        assert prop["downstream"]["id"] is None
        assert prop["downstream"]["target"] == "/api/research/persona-demands"
        assert "Connection refused" in str(prop["downstream"].get("error") or "")
        props_list = client.list_meeting_proposals(meeting_id=created["meeting_id"])
        assert props_list["items"][0]["downstream"]["status"] == "failed"
        assert props_list["items"][0]["downstream"]["id"] is None




@pytest.mark.parametrize("stack", ["real_ensure"], indirect=True)
def test_mt2_meeting_held_with_slow_gateway_and_concurrent_persona_ensure(
    stack: dict[str, Any],
) -> None:
    """Real executor + real adapter ensure; 0.6 s gateway calls vs a 3 s executor timeout.

    A concurrent persona agent ensure holds the shared agent registry lock for
    two gateway calls. Before the fix the first ensure alone needs four calls
    (2.4 s) plus the lock wait, exceeds the executor timeout, and the work item
    dead-letters after three attempts.
    """
    from integrations.openclaw.persona_agent_sync import agent_registry_lock

    double: _SlowGatewayDouble = stack["double"]
    created = open_persona_meeting(
        base_url=stack["api_url"], token=SERVICE_TOKEN, tenant_id=TENANT,
        meeting_type="MT-2", topic="Optimisation review on pending lessons",
        convened_by={"kind": "persona", "id": "persona-alpha"},
        participants=["persona-alpha", "persona-beta", "persona-risk-officer"],
        evidence_refs=["facts:MT-2:1"], idempotency_key="slow-gateway-mt2", iso_week="2026-W47",
    )
    meeting_id = created["meeting_id"]
    holding = threading.Event()

    def _persona_ensure() -> None:
        with agent_registry_lock():
            holding.set()
            double._gateway_call("config.get")
            double._gateway_call("config.get")

    writer = threading.Thread(target=_persona_ensure, daemon=True)
    writer.start()
    assert holding.wait(5)

    state = WorkflowStateStore(stack["cfg"].state_path)
    statuses: list[str] = []
    for _ in range(40):
        _tick(stack)
        item = state.get(tenant_id=TENANT, request_id=meeting_id)
        statuses.append(item["status"])
        assert item["status"] != "dead_letter", (statuses, item)
        if item["status"] == "completed":
            break
    writer.join(10)
    assert statuses[-1] == "completed", statuses
    item = state.get(tenant_id=TENANT, request_id=meeting_id)
    # In-progress holds use the uncapped provisioning-hold rule: never dead_letter.
    meeting = stack["client"].get_meeting(meeting_id)
    assert meeting["status"] == "closed" and meeting["rounds"]
    assert [a["id"] for a in double.entries if a["id"] != "main"]  # provisioned, then reused
    patched = [m for m, _ in double.log if m == "config.patch"]
    assert len(patched) == 3  # one write per persona, none repeated


# --- RC6 WP6.1: execution_state / dead_letter truth (hosted shapes 2026-11-20) ---

class _FakeMeetingStore:
    def __init__(self, rows):
        self._rows = rows

    def list_meetings(self):
        return list(self._rows)

    def get_meeting(self, meeting_id):
        return next((r for r in self._rows if r["meeting_id"] == meeting_id), None)


class _FakeWorkflowState:
    def __init__(self, items):
        self._items = items
        self.list_calls = 0

    def list_items(self, *, tenant_id, status=None):
        self.list_calls += 1
        return [i for i in self._items if i["tenant_id"] == tenant_id]

    def get(self, *, tenant_id, request_id):
        return next(
            (i for i in self._items if i["tenant_id"] == tenant_id and i["request_id"] == request_id),
            None,
        )


def _hosted_meeting(mid, mtype, topic, status="convened", tenant="t1"):
    return {
        "meeting_id": mid, "meeting_type": mtype, "topic": topic, "status": status,
        "round": 0, "tenant_id": tenant, "created_at": f"2026-11-19T00:00:0{mid[-1]}Z",
        "participants": [],
    }


def _hosted_fixture():
    meetings = [
        _hosted_meeting("m-1", "MT-1", "Pool evolution review for persona-alpha (2026-W47)"),
        _hosted_meeting("m-2", "MT-2", "Optimisation review for persona-beta: lessons:4"),
        _hosted_meeting("m-3", "MT-1", "Pool evolution review for persona-gamma (2026-W47)"),
        _hosted_meeting("m-4", "MT-2", "Optimisation review for persona-delta: lessons:2", status="closed"),
    ]
    wf = [
        {"tenant_id": "t1", "request_id": rid, "status": "dead_letter", "attempt_count": 3,
         "blocked_count": 3, "last_error": "provider timeout", "updated_at": 1763510400.0}
        for rid in ("m-1", "m-2")
    ] + [
        {"tenant_id": "other", "request_id": "m-3", "status": "dead_letter", "attempt_count": 3,
         "blocked_count": 3, "last_error": "x", "updated_at": 1763510400.0},
    ]
    return _FakeMeetingStore(meetings), _FakeWorkflowState(wf)


def test_list_hides_dead_letter_from_upcoming_with_count_single_read():
    st, wf = _hosted_fixture()
    out = meetings_mod.list_meetings_view(st, tenant_id="t1", status="upcoming", workflow_state=wf)
    assert [i["meeting_id"] for i in out["items"]] == ["m-3"]
    assert out["summary"]["dead_letter_count"] == 2
    assert wf.list_calls == 1  # no N+1; other-tenant row for m-3 must not leak


def test_list_include_failed_shows_execution_failed_state():
    st, wf = _hosted_fixture()
    out = meetings_mod.list_meetings_view(
        st, tenant_id="t1", status="upcoming", workflow_state=wf, include_failed=True
    )
    by_id = {i["meeting_id"]: i for i in out["items"]}
    assert set(by_id) == {"m-1", "m-2", "m-3"}
    assert by_id["m-1"]["effective_status"] == "execution_failed"
    assert by_id["m-1"]["status"] == "convened"
    es = by_id["m-1"]["execution_state"]
    assert es["status"] == "dead_letter" and es["attempt_count"] == 3
    assert es["max_attempts"] == 3 and es["last_error"] == "provider timeout"
    assert es["updated_at"].startswith("2025-11-1") or es["updated_at"]
    assert by_id["m-3"]["execution_state"] is None
    assert by_id["m-3"]["effective_status"] == "convened"
    assert out["summary"]["dead_letter_count"] == 0


def test_detail_carries_execution_state():
    st, wf = _hosted_fixture()
    d = meetings_mod.get_meeting_detail_view(st, tenant_id="t1", meeting_id="m-2", workflow_state=wf)
    assert d["effective_status"] == "execution_failed"
    assert d["execution_state"]["status"] == "dead_letter"
    d3 = meetings_mod.get_meeting_detail_view(st, tenant_id="t1", meeting_id="m-3", workflow_state=wf)
    assert d3["execution_state"] is None


def test_retry_after_blocked_writeback_reuses_recorded_memos_and_closes_mt1(
    stack: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hosted sequence: memo 201 + publish 200, writeback 403, retry (memo text differs) -> closed."""
    client: ConsultationServiceClient = stack["client"]
    double: _AdapterModelDouble = stack["double"]
    real_write = meetings_mod._write_meeting_memory
    calls = {"n": 0}

    def _write_once_refused(**kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            double.synthesis_mode = "normal"  # the retry synthesizes different memo text
            raise urllib.error.HTTPError(
                kwargs.get("memory_url") or "http://memory", 403, "Forbidden", {}, None
            )
        return real_write(**kwargs)

    monkeypatch.setattr(meetings_mod, "_write_meeting_memory", _write_once_refused)
    created = client.open_persona_meeting(
        meeting_type="MT-1",
        topic="Retry after blocked memory writeback",
        convened_by={"kind": "persona", "id": "persona-alpha"},
        participants=["persona-alpha", "persona-risk-officer"],
        iso_week="2026-W52",
    )
    mid = created["meeting_id"]
    double.synthesis_mode = "unreadable"  # first attempt's memo text differs from the retry's
    second = _tick(stack)
    assert second["blocked"] == 1 and second["completed"] == 1, second
    assert all("http_error=403" in e for e in second["errors"]), second
    detail = client.get_meeting(mid)
    assert detail["status"] == "closed" and detail["closed_at"]
    assert detail["round"] == 2
    assert detail["writebacks"], detail
    assert calls["n"] == 2
    memos = [
        m for m in _api_memos(stack, mid)
        if m["memo_type"] in {"committee_summary", "dissent"}
    ]
    # Never a second memo of a kind for the same request: the 409 reused the recorded record.
    kinds = [m["memo_type"] for m in memos]
    assert sorted(kinds) == ["committee_summary", "dissent"], kinds
    assert all(m["status"] == "published" for m in memos)


def _api_memos(stack: dict[str, Any], request_id: str) -> list[dict[str, Any]]:
    from services.consultation.workflow_executor import _api_get

    return _api_get(stack["cfg"], f"/api/consult/memos?request_id={request_id}")


def test_chair_declared_review_keeps_its_summary_and_empty_synthesis_keeps_the_unreadable_text() -> None:
    def build(raw: Any) -> dict[str, Any]:
        return meetings_mod.validate_and_build_synthesis(
            meeting_id="mtg-x", meeting_code="MT-1", spec={}, chair_persona_id="persona-alpha",
            convened_by={"kind": "persona", "id": "persona-alpha"},
            participants=[{"persona_id": "persona-alpha", "role": "chair"}],
            rounds=[{"positions": [
                {"persona_id": "persona-alpha", "stance": "request_more_research", "confidence": 0.5},
                {"persona_id": "persona-risk-officer", "stance": "request_more_research", "confidence": 0.5},
            ]}],
            raw_synthesis=raw, allowed_case_ids=set(), chair_preread_ids=set(),
        )

    declared = build({"outcome": "REVIEW", "summary": "Both stances request more research."})
    assert declared["outcome"] == "REVIEW" and declared["summary"] == "Both stances request more research."
    assert declared["proposals"] == [] and declared["tally"]["by_stance"] == {"request_more_research": 2}
    for raw in (None, {}, {"unreadable": True}, {"outcome": "REVIEW"}):
        assert "empty or unreadable" in build(raw)["summary"]


# --- MEETING-OPERATOR-ROSTER-SUBJECT-REF-20261120 ---------------------------

_ROSTER_DIR = {
    "persona-2448c7053e33ea48da47": {"metadata": {"trading": True}},
    "persona-alpha": {"metadata": {"trading": True}},
}


def _roster(subject_ref, *, convened_by=None, participants=None):
    spec = meetings_mod.resolve_meeting_type_spec("MT-1")
    return meetings_mod.resolve_and_validate_roster(
        spec=spec,
        convened_by=convened_by or {"kind": "operator", "id": "op-1"},
        raw_participants=participants,
        subject_ref=subject_ref,
        attribution_record=None,
        persona_directory=_ROSTER_DIR,
    )


@pytest.mark.parametrize(
    "ref",
    ["persona:persona-2448c7053e33ea48da47", "persona-2448c7053e33ea48da47"],
)
def test_operator_default_roster_chair_is_bare_persona_id(ref):
    roster, chair = _roster(ref)
    assert chair == "persona-2448c7053e33ea48da47"
    assert roster[0]["persona_id"] == "persona-2448c7053e33ea48da47"
    assert all(":" not in p["persona_id"] for p in roster)


@pytest.mark.parametrize(
    "ref,reason",
    [
        ("persona:persona-unknown", "subject_persona_not_found"),
        ("persona:bad id:x", "invalid_subject_persona"),
        ("persona:", "invalid_subject_persona"),
    ],
)
def test_operator_default_roster_refuses_unknown_subject_persona(ref, reason):
    with pytest.raises(meetings_mod.MeetingRefusedError) as exc:
        _roster(ref)
    assert exc.value.reason == reason
    assert 400 <= exc.value.status_code < 500


def test_persona_convened_and_explicit_participants_unchanged():
    roster, chair = _roster(
        "persona:persona-unknown",
        convened_by={"kind": "persona", "id": "persona-alpha"},
    )
    assert chair == "persona-alpha"
    roster, chair = _roster(
        "persona:persona-unknown",
        participants=[{"persona_id": "persona-alpha", "role": "chair"}],
    )
    assert chair == "persona-alpha"


@pytest.mark.parametrize("ref", ["trader:t1", "agora_user:u1", "user:u1"])
def test_other_subject_ref_kinds_unchanged(ref):
    roster, chair = _roster(ref)
    # directory-derived default roster (sorted), not the subject_ref path
    assert chair == "persona-2448c7053e33ea48da47"


def test_preread_memory_refusal_is_named_and_does_not_block(monkeypatch):
    import urllib.error
    import integrations.openclaw.persona_memory_bridge as bridge
    from services.consultation.meetings import build_participant_pre_read

    calls = []

    def refuse(url, **kw):
        calls.append(kw)
        raise urllib.error.HTTPError(url, 403, "forbidden", {}, None)

    monkeypatch.setattr(bridge, "fetch_canonical_memory", refuse)
    monkeypatch.delenv("CONSULTATION_MEMORY_READ_TOKEN_FILE", raising=False)
    monkeypatch.setenv("PANTHEON_MEMORY_ACTOR_ID", "")
    monkeypatch.setenv("PANTHEON_MEMORY_ACTOR_ROLES", "")
    refusals: dict = {}
    out = build_participant_pre_read(
        refusals=refusals, meeting_id="m1", tenant_id=TENANT, topic="t", memory_url="http://memory.invalid",
        participants=[{"persona_id": "persona-a", "role": "r", "style": "s", "origin": "o"}])
    assert refusals == {"persona-a": "memory_read_refused:HTTPError:http_403"}
    assert out[0]["entries"] and "memory_read_refusal" not in out[0]  # round proceeds; request content stable
    assert calls[0]["actor_id"] and calls[0]["actor_roles"]


# --- CONSULTATION-WRITEBACK-PROBE-AND-PERSONA-DIRECTORY-20261120 ------------

_DIR_SECRET = "directory-read-test-secret"


def _persona_owner_over_urlopen(monkeypatch, tmp_path, *, token_file_roles):
    """Real persona owner (strict auth) behind urllib.request.urlopen."""
    import io
    import urllib.error
    from fastapi.testclient import TestClient
    from services.persona.write_owner import (
        CreatePersonaRequest, PersistentPersonaOwner, create_app)
    from services.runtime_auth_inbound import encode_jwt_hs256

    monkeypatch.setenv("PERSONA_AUTH_MODE", "strict")
    monkeypatch.setenv("PERSONA_JWT_SECRET", _DIR_SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", _DIR_SECRET)
    store = PersistentPersonaOwner.from_json_path(tmp_path / "personas.json")
    store.create(CreatePersonaRequest(
        actor_id="operator-bff", persona_id="persona-2448c7053e33ea48da47", name="Trader",
        mandate="m", strategy_family="f", tenant_id="tenant-dev", metadata={"trading": True}))
    client = TestClient(create_app(store), raise_server_exceptions=False)
    token = encode_jwt_hs256(
        {"sub": "pantheon-dev-consultation-persona-directory-reader", "roles": token_file_roles,
         "tenant_id": "tenant-dev", "allowed_tenants": ["tenant-dev"], "exp": 4102444800},
        secret=_DIR_SECRET)
    token_file = tmp_path / "directory.token"
    token_file.write_text(token + "\n")
    monkeypatch.setenv("PANTHEON_PERSONA_API_URL", "http://persona:8002")
    monkeypatch.setenv("CONSULTATION_PERSONA_DIRECTORY_READ_TOKEN_FILE", str(token_file))
    for name in ("PANTHEON_PERSONA_SERVICE_TOKEN", "PERSONA_SERVICE_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    requests = []

    def urlopen(req, timeout=None):
        path = req.full_url.split("persona:8002", 1)[1]
        requests.append(path)
        res = client.get(path, headers={k: v for k, v in req.header_items()})
        if res.status_code >= 400:
            raise urllib.error.HTTPError(req.full_url, res.status_code, "err", {}, io.BytesIO(b""))

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return res.content
        return _Resp()

    monkeypatch.setattr(meetings_mod.urllib.request, "urlopen", urlopen)
    return token, requests


@pytest.mark.parametrize("ref", ["persona-2448c7053e33ea48da47", "persona:persona-2448c7053e33ea48da47"])
def test_operator_subject_ref_resolves_roster_with_issued_directory_credential(
        monkeypatch, tmp_path, ref):
    _, requests = _persona_owner_over_urlopen(monkeypatch, tmp_path, token_file_roles=["viewer"])
    spec = meetings_mod.resolve_meeting_type_spec("MT-1")
    roster, chair = meetings_mod.resolve_and_validate_roster(
        spec=spec, convened_by={"kind": "operator", "id": "op-1"}, raw_participants=None,
        subject_ref=ref, attribution_record=None, tenant_id="tenant-dev")
    assert chair == "persona-2448c7053e33ea48da47" and requests == ["/api/personas"]
    assert roster[0]["persona_id"] == chair


@pytest.mark.parametrize("roles", [["persona_pool_reader"], ["consultation-svc"]])
def test_unreadable_directory_is_refused_by_name_not_empty_or_not_found(monkeypatch, tmp_path, roles):
    token, _ = _persona_owner_over_urlopen(monkeypatch, tmp_path, token_file_roles=roles)
    spec = meetings_mod.resolve_meeting_type_spec("MT-1")
    with pytest.raises(meetings_mod.MeetingRefusedError) as exc:
        meetings_mod.resolve_and_validate_roster(
            spec=spec, convened_by={"kind": "operator", "id": "op-1"}, raw_participants=None,
            subject_ref="persona:persona-2448c7053e33ea48da47", attribution_record=None,
            tenant_id="tenant-dev")
    assert exc.value.reason == "persona_directory_unavailable"
    assert exc.value.reason != "subject_persona_not_found"
    assert token not in exc.value.detail and token not in str(exc.value)


def test_directory_unconfigured_or_unreachable_is_refused_by_name(monkeypatch):
    monkeypatch.delenv("PANTHEON_PERSONA_API_URL", raising=False)
    monkeypatch.delenv("PERSONA_API_URL", raising=False)
    spec = meetings_mod.resolve_meeting_type_spec("MT-1")
    with pytest.raises(meetings_mod.MeetingRefusedError) as exc:
        meetings_mod.resolve_and_validate_roster(
            spec=spec, convened_by={"kind": "operator", "id": "op-1"}, raw_participants=None,
            subject_ref="persona:persona-2448c7053e33ea48da47", attribution_record=None,
            tenant_id="tenant-dev")
    assert exc.value.reason == "persona_directory_unavailable" and exc.value.status_code == 503
    monkeypatch.setenv("PANTHEON_PERSONA_API_URL", "http://persona:8002")
    monkeypatch.setattr(meetings_mod.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    with pytest.raises(meetings_mod.MeetingRefusedError) as exc:
        meetings_mod._fetch_persona_directory("tenant-dev")
    assert exc.value.reason == "persona_directory_unavailable"


# --- MEETING-CASE-FILE-RESOLVES-MEMBER-FACTS-20261120 ------------------------

def test_unresolvable_ref_stays_in_meeting_case_file_with_named_unavailable_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.knowledge import case_file as cf

    ref = "artifact-missing-404"

    def _not_found(endpoint: Any, path: str, **kwargs: Any) -> Any:
        if path.startswith("/api/registry/entries/"):
            raise cf.OwnerUnavailable("http_404:not_found")
        if path.endswith("/strategy-pool"):
            return {"pool_version": 4, "members": []}
        return {}

    monkeypatch.setattr(cf, "http_json", _not_found)
    owners = cf.Owners(
        registry=cf.OwnerEndpoint("http://registry"),
        pool=cf.OwnerEndpoint("http://persona"),
    )
    case_file, items, _ = meetings_mod.compose_meeting_case_file(
        meeting_id="mtg-unresolvable-1",
        meeting_code="MT-1",
        topic="Pool evolution review",
        subject_ref="persona:persona-2448c7053e33ea48da47",
        evidence_refs=[ref],
        attribution_record_id=None,
        attribution_record=None,
        supplied_case_file=None,
        tenant_id=TENANT,
        owners=owners,
    )
    reg_items = [i for i in items if i["source_id"] == ref]
    assert len(reg_items) == 1
    item = reg_items[0]
    assert item["status"] == "unavailable"
    assert item["unavailable_reason"] == "http_404:not_found"
    assert item["excerpt"] != ref
    assert f"Evidence {ref}" not in item["excerpt"]
    parsed_excerpt = json.loads(item["excerpt"])
    assert parsed_excerpt["ref"] == ref
    assert parsed_excerpt["status"] == "unavailable"
    assert "http_404:not_found" in parsed_excerpt["unavailable_reason"]
    assert {
        "owner": f"registry:{ref}",
        "reason": "http_404:not_found",
    } in case_file["groups"]["facts"]["unavailable"]


def test_private_persona_memory_never_enters_meeting_case_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.knowledge import case_file as cf

    pid = "persona-2448c7053e33ea48da47"

    def _memory_with_private(endpoint: Any, path: str, **kwargs: Any) -> Any:
        if path.startswith("/api/memory/retrieve"):
            return {
                "hits": [
                    {
                        "type": "persona",
                        "relevance_score": 2.0,
                        "entry": {
                            "memory_id": "pmem-secret-1",
                            "persona_id": pid,
                            "relevance_scope": "persona_private",
                            "content": {"summary": "private secret sizing note"},
                        },
                    },
                    {
                        "type": "persona",
                        "relevance_score": 1.0,
                        "entry": {
                            "memory_id": "pmem-committee-1",
                            "persona_id": pid,
                            "relevance_scope": "persona_and_committee",
                            "content": {"summary": "shared committee lesson"},
                        },
                    },
                ]
            }
        if path.endswith("/strategy-pool"):
            return {"pool_version": 4, "members": []}
        return {}

    monkeypatch.setattr(cf, "http_json", _memory_with_private)
    owners = cf.Owners(
        pool=cf.OwnerEndpoint("http://persona"),
        memory=cf.OwnerEndpoint("http://memory", "tok"),
    )
    case_file, items, _ = meetings_mod.compose_meeting_case_file(
        meeting_id="mtg-priv-check",
        meeting_code="MT-1",
        topic="Pool evolution review",
        subject_ref=f"persona:{pid}",
        evidence_refs=[],
        attribution_record_id=None,
        attribution_record=None,
        supplied_case_file=None,
        tenant_id=TENANT,
        owners=owners,
    )
    lesson_ids = {i["source_id"] for i in case_file["groups"]["lessons"]["items"]}
    assert "pmem-committee-1" in lesson_ids
    assert "pmem-secret-1" not in lesson_ids
    assert "private secret sizing note" not in json.dumps(case_file)
    assert all(i.get("relevance_scope") != "persona_private" for i in items)

    for bad_kw in (
        {"evidence_refs": [{"item_id": "p1", "relevance_scope": "persona_private", "excerpt": "secret"}]},
        {"supplied_case_file": {"items": [{"item_id": "p2", "memory_layer": "persona_private", "excerpt": "secret"}]}},
    ):
        with pytest.raises(meetings_mod.MeetingRefusedError) as exc:
            meetings_mod.compose_meeting_case_file(
                meeting_id="mtg-priv-refused",
                meeting_code="MT-1",
                topic="Pool evolution review",
                subject_ref=f"persona:{pid}",
                evidence_refs=bad_kw.get("evidence_refs", []),
                attribution_record_id=None,
                attribution_record=None,
                supplied_case_file=bad_kw.get("supplied_case_file"),
                tenant_id=TENANT,
                owners=owners,
            )
        assert exc.value.reason == "private_memory_forbidden"


def test_hosted_shape_mt1_resolves_member_facts_pool_v4_and_zero_observation_book_into_participant_prompts(
    stack: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.knowledge import case_file as cf

    client: ConsultationServiceClient = stack["client"]
    double: _AdapterModelDouble = stack["double"]
    pid = "persona-2448c7053e33ea48da47"
    ref = "artifact-persona-paper-039ad01f8bcd2b4aa466"
    binding_id = "pcb-persona-paper-039ad01f8bcd2b4aa466"

    hosted_entry = {
        "registry_id": ref,
        "artifact_type": "execution_bundle",
        "strategy_id": "strategy-persona-039ad01f8bcd2b4aa466",
        "version": "1.0.0",
        "artifact_state": "approved",
        "owner_tenant": TENANT,
        "metadata": {
            "market": "TW",
            "tenant_id": TENANT,
            "persona_id": pid,
            "execution_context": "paper",
            "persona_capital_binding_id": binding_id,
            "strategy_artifact": {
                "artifact_id": ref,
                "strategy_id": "strategy-persona-039ad01f8bcd2b4aa466",
                "version": "1.0.0",
                "strategy_logic": {
                    "kind": "close_to_close_momentum",
                    "positive_action": "BUY",
                    "non_positive_action": "SELL",
                    "lookback_parameter": "lookback_bars",
                    "threshold_parameter": "momentum_threshold",
                },
                "parameters": {
                    "market": "TW",
                    "symbols": ["2330.TWSE"],
                    "data_source": "source-ingest:tw_price_daily",
                    "bar_frequency": "1d",
                    "lookback_bars": 2,
                    "momentum_threshold": 0.0,
                    "order_quantity": 1,
                    "quantity_type": "SHARES",
                    "zero_momentum_action": "SELL",
                    "strength_scale_k": 1.0,
                    "strength_floor_momentum": 0.01,
                },
            },
        },
    }
    hosted_pool = {
        "schema_version": "persona_strategy_pool.v1",
        "tenant_id": TENANT,
        "persona_id": pid,
        "pool_version": 4,
        "change_reason": "paper pool reweight: refused; virtual_book_source_missing",
        "members": [
            {
                "artifact_ref": ref,
                "stage": "active",
                "risk_weight": 1.0,
                "baseline_ref": "apv-persona-paper-artifact-039ad01f8bcd2b4aa466",
                "checksum": "sha256:" + "a" * 64,
            }
        ],
        "decision": {
            "action": "reweight",
            "status": "refused",
            "reasons": ["virtual_book_source_missing"],
            "health": {
                "window_trading_days": 20,
                "demand_reasons": ["health_inputs_unknown"],
                "effective_bets": {
                    "status": "unknown",
                    "value": None,
                    "reason": "health_window_missing_or_invalid",
                },
            },
        },
    }
    hosted_telemetry = {
        "summaries": [
            {
                "runtime_id": "rt-persona-paper-039ad01f8bcd2b4aa466",
                "tenant_id": TENANT,
                "deployment_stage": "paper",
                "persona_capital_binding_id": binding_id,
                "member_virtual_books": [
                    {
                        "member_id": ref,
                        "artifact_id": ref,
                        "version": "1.0.0",
                        "observation_count": 0,
                        "total_return": 0.0,
                        "sharpe_ratio": None,
                        "sharpe_undefined_reason": "insufficient_or_constant_returns",
                        "max_drawdown": 0.0,
                        "marks_complete": False,
                        "as_of": None,
                    }
                ],
            }
        ]
    }

    real_http_json = cf.http_json

    def _owner_double(endpoint: Any, path: str, **kwargs: Any) -> Any:
        if path == f"/api/registry/entries/{ref}":
            return hosted_entry
        if path == f"/api/personas/{pid}/strategy-pool":
            return hosted_pool
        if path == f"/api/personas/{pid}/strategy-pool/versions":
            return {"versions": [hosted_pool], "refusals": []}
        if path == "/api/telemetry/runtime-summaries":
            return hosted_telemetry
        return real_http_json(endpoint, path, **kwargs)

    monkeypatch.setenv("PANTHEON_TELEMETRY_API_URL", stack["adapter_url"])
    monkeypatch.setattr(cf, "http_json", _owner_double)

    double.calls.clear()
    created = client.open_persona_meeting(
        meeting_type="MT-1",
        topic=f"Pool evolution review for {pid} (2026-W47)",
        convened_by={"kind": "persona", "id": pid},
        subject_ref=f"persona:{pid}",
        participants=[pid, "persona-risk-officer"],
        evidence_refs=[ref],
        iso_week="2026-W47",
    )
    mtg_id = created["meeting_id"]
    tick_res = _tick(stack)
    assert tick_res["errors"] == [], tick_res
    assert tick_res["completed"] == 1, tick_res

    detail = client.get_meeting(mtg_id)
    assert detail is not None and detail["status"] == "closed"
    fact_items = {i["kind"]: i for i in detail["case_file"]["groups"]["facts"]["items"]}
    assert {"registry_entry", "strategy_pool", "member_virtual_book"} <= set(fact_items)

    mtg_calls = [c for c in double.calls if c["request_id"] == mtg_id]
    assert len(mtg_calls) == 5  # 2 participants x 2 rounds + 1 chair synthesis
    for call in mtg_calls:
        prompt = call["prompt"]
        # Registry entry resolved facts: strategy family, approved scope, rule parameters
        assert "close_to_close_momentum" in prompt
        assert '"approved_scope":"paper"' in prompt
        assert '"lookback_bars":2' in prompt
        assert '"2330.TWSE"' in prompt
        # Pool version 4 membership and weights + pool health unavailable reason
        assert '"pool_version":4' in prompt
        assert '"risk_weight":1.0' in prompt
        assert "virtual_book_source_missing" in prompt
        assert "health_window_missing_or_invalid" in prompt
        # Virtual book with zero observations + unavailable reason
        assert '"observation_count":0' in prompt
        assert "zero_observations:insufficient_or_constant_returns" in prompt


def test_chair_synthesis_records_why_empty_or_unreadable_cause_on_meeting(
    tmp_path: Path,
) -> None:
    store = ConsultationStore(str(tmp_path / "synth_causes"))
    wf_state = WorkflowStateStore(tmp_path / "synth_causes.sqlite3")

    def _run_with_synth_behavior(idx: int, synth_fn: Any) -> dict[str, Any]:
        created, _ = meetings_mod.convene_persona_meeting(
            store=store,
            workflow_state=wf_state,
            tenant_id=TENANT,
            payload={
                "meeting_type": "MT-1",
                "topic": f"Synthesis cause test {idx}",
                "convened_by": {"kind": "persona", "id": "persona-alpha"},
                "participants": ["persona-alpha", "persona-risk-officer"],
                "iso_week": f"2026-W{10 + idx}",
            },
        )
        mid = created["meeting_id"]

        class _Prov:
            def obtain(self, *, tenant_id: str, request: Mapping[str, Any], participant_scope: str = "") -> Any:
                if participant_scope.endswith(":synthesis"):
                    return synth_fn(mid)
                cid = request["case_file"]["groups"]["facts"]["items"][0]["item_id"]
                return SimpleNamespace(
                    summary="round position",
                    recommendation="request_more_research",
                    confidence=0.7,
                    event_content={
                        "stance": "request_more_research",
                        "confidence": 0.7,
                        "citations": [cid],
                        "claims": [{"claim": "need facts", "evidence_refs": [cid]}],
                    },
                    evidence=[{"id": cid, "evidence_type": "meeting_evidence", "link": f"artifact://{mid}/{cid}"}],
                )

        return meetings_mod.execute_persona_meeting(
            store=store,
            meeting_id=mid,
            tenant_id=TENANT,
            provider=_Prov(),
        )

    # 1. Model output missing
    m_missing = _run_with_synth_behavior(
        1,
        lambda _mid: (_ for _ in ()).throw(RuntimeError("OpenClaw persona agent did not complete")),
    )
    assert m_missing["outcome"] == "REVIEW"
    assert m_missing["synthesis"]["unreadable_reason"] == "model_output_missing"
    assert m_missing["synthesis_unreadable_reason"] == "model_output_missing"
    assert "model_output_missing" in m_missing["synthesis"]["summary"]

    # 2. Parse failure with raw length
    m_parse = _run_with_synth_behavior(
        2,
        lambda _mid: (_ for _ in ()).throw(json.JSONDecodeError("Expecting value", "NOT-VALID-JSON", 0)),
    )
    assert m_parse["outcome"] == "REVIEW"
    assert m_parse["synthesis"]["unreadable_reason"] == "parse_failure (raw_length=14)"
    assert m_parse["synthesis_unreadable_reason"] == "parse_failure (raw_length=14)"
    assert "parse_failure (raw_length=14)" in m_parse["synthesis"]["summary"]

    # 3. Timeout
    m_timeout = _run_with_synth_behavior(
        3,
        lambda _mid: (_ for _ in ()).throw(TimeoutError("provider transport timed out after 30s")),
    )
    assert m_timeout["outcome"] == "REVIEW"
    assert m_timeout["synthesis"]["unreadable_reason"] == "timeout"
    assert m_timeout["synthesis_unreadable_reason"] == "timeout"
    assert "timeout" in m_timeout["synthesis"]["summary"]

    # 4. Real HttpContributionProvider with 1700-char invalid JSON preserves JSONDecodeError cause & raw_length=1700 (not 36)
    import io

    def _run_with_http_provider(idx: int, urlopen_fn: Any, monkeypatch_obj: Any) -> dict[str, Any]:
        created, _ = meetings_mod.convene_persona_meeting(
            store=store,
            workflow_state=wf_state,
            tenant_id=TENANT,
            payload={
                "meeting_type": "MT-1",
                "topic": f"HTTP provider synthesis cause test {idx}",
                "convened_by": {"kind": "persona", "id": "persona-alpha"},
                "participants": ["persona-alpha", "persona-risk-officer"],
                "iso_week": f"2026-W{20 + idx}",
            },
        )
        mid = created["meeting_id"]
        cid = created["case_file"]["groups"]["facts"]["items"][0]["item_id"]

        def _fake_urlopen(req: urllib.request.Request, timeout: float = 10.0) -> Any:
            if req.full_url.endswith("/agents/ensure"):
                body = json.loads((req.data or b"{}").decode("utf-8"))
                raw_bytes = json.dumps(
                    {
                        "status": "ready",
                        "tenant_id": body.get("tenant_id"),
                        "persona_id": body.get("persona_id"),
                    }
                ).encode("utf-8")
            else:
                envelope = json.loads((req.data or b"{}").decode("utf-8"))
                inner_req = envelope.get("request") or {}
                part = inner_req.get("participant") or {}
                if part.get("round") == "synthesis":
                    raw_bytes = urlopen_fn(mid, cid)
                else:
                    raw_bytes = json.dumps(
                        {
                            "contribution_id": f"c-{mid}-{part.get('persona_id')}-{part.get('round')}",
                            "tenant_id": TENANT,
                            "request_id": mid,
                            "participant_type": "persona",
                            "participant_ref": str(part.get("persona_id") or "persona-alpha"),
                            "summary": "round position",
                            "recommendation": "request_more_research",
                            "confidence": 0.7,
                            "event_type": "persona_contribution",
                            "event_content": {
                                "stance": "request_more_research",
                                "confidence": 0.7,
                                "citations": [cid],
                                "claims": [{"claim": "need facts", "evidence_refs": [cid]}],
                            },
                            "evidence": [{"id": cid, "evidence_type": "meeting_evidence", "link": f"artifact://{mid}/{cid}"}],
                            "findings": [],
                        }
                    ).encode("utf-8")

            class _Resp:
                def __enter__(self) -> "_Resp":
                    return self

                def __exit__(self, *args: Any) -> bool:
                    return False

                def read(self) -> bytes:
                    return raw_bytes

            return _Resp()

        import services.consultation.provider as prov_mod

        monkeypatch_obj.setattr(prov_mod.urllib.request, "urlopen", _fake_urlopen)
        http_prov = HttpContributionProvider(
            endpoint="http://provider.invalid/api/openclaw-adapter/consultation/contributions",
            bearer_token="tok",
            service_actor="consultation-workflow-executor",
            timeout_seconds=5.0,
        )
        return meetings_mod.execute_persona_meeting(
            store=store,
            meeting_id=mid,
            tenant_id=TENANT,
            provider=http_prov,
        )

    mp = pytest.MonkeyPatch()
    try:
        m_http_invalid_json = _run_with_http_provider(
            1,
            lambda _mid, _cid: ("x" * 1700).encode("utf-8"),
            mp,
        )
        assert m_http_invalid_json["outcome"] == "REVIEW"
        assert m_http_invalid_json["synthesis"]["unreadable_reason"] == "parse_failure (raw_length=1700)"
        assert m_http_invalid_json["synthesis_unreadable_reason"] == "parse_failure (raw_length=1700)"
        assert "raw_length=36" not in m_http_invalid_json["synthesis"]["summary"]

        # 5. Real HttpContributionProvider with missing summary is classified as model_output_missing (not parse_failure)
        m_http_missing_summary = _run_with_http_provider(
            2,
            lambda mid, cid: json.dumps(
                {
                    "contribution_id": f"c-{mid}-synth",
                    "tenant_id": TENANT,
                    "request_id": mid,
                    "participant_type": "persona",
                    "participant_ref": "persona-alpha",
                    "summary": "",
                    "recommendation": "request_more_research",
                    "confidence": 0.7,
                    "event_type": "persona_contribution",
                    "event_content": {"synthesis": {"outcome": "completed_no_proposal", "proposals": []}},
                    "evidence": [{"id": cid, "evidence_type": "meeting_evidence", "link": f"artifact://{mid}/{cid}"}],
                    "findings": [],
                }
            ).encode("utf-8"),
            mp,
        )
        assert m_http_missing_summary["outcome"] == "REVIEW"
        assert m_http_missing_summary["synthesis"]["unreadable_reason"] == "model_output_missing"
        assert m_http_missing_summary["synthesis_unreadable_reason"] == "model_output_missing"

        # 6. Synthesis event_content missing summary is also classified as model_output_missing
        m_http_missing_synth_summary = _run_with_http_provider(
            3,
            lambda mid, cid: json.dumps(
                {
                    "contribution_id": f"c-{mid}-synth",
                    "tenant_id": TENANT,
                    "request_id": mid,
                    "participant_type": "persona",
                    "participant_ref": "persona-alpha",
                    "summary": "top-level turn summary",
                    "recommendation": "request_more_research",
                    "confidence": 0.7,
                    "event_type": "persona_contribution",
                    "event_content": {"synthesis": {"outcome": "completed_no_proposal"}},
                    "evidence": [{"id": cid, "evidence_type": "meeting_evidence", "link": f"artifact://{mid}/{cid}"}],
                    "findings": [],
                }
            ).encode("utf-8"),
            mp,
        )
        assert m_http_missing_synth_summary["outcome"] == "REVIEW"
        assert m_http_missing_synth_summary["synthesis"]["unreadable_reason"] == "model_output_missing"
        assert m_http_missing_synth_summary["synthesis_unreadable_reason"] == "model_output_missing"

        # 7. Real HttpContributionProvider with wrapped URLError(TimeoutError) is classified as timeout
        m_http_timeout = _run_with_http_provider(
            4,
            lambda _mid, _cid: (_ for _ in ()).throw(urllib.error.URLError(TimeoutError("read timed out"))),
            mp,
        )
        assert m_http_timeout["outcome"] == "REVIEW"
        assert m_http_timeout["synthesis"]["unreadable_reason"] == "timeout"
        assert m_http_timeout["synthesis_unreadable_reason"] == "timeout"
    finally:
        mp.undo()


def test_execute_preserves_explicit_evidence_and_supplied_facts_and_migrates_only_legacy_placeholders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.knowledge import case_file as cf

    def _owner_404(endpoint: Any, path: str, **kwargs: Any) -> Any:
        if path == "/api/registry/entries/legacy-member-1":
            return {
                "registry_id": "legacy-member-1",
                "strategy_id": "momentum",
                "artifact_type": "execution_bundle",
                "artifact_state": "approved",
                "approved_scope": "paper",
            }
        if path.startswith("/api/registry/entries/"):
            raise cf.OwnerUnavailable("http_404")
        if path.endswith("/strategy-pool"):
            return {"pool_version": 4, "members": []}
        return {}

    monkeypatch.setattr(cf, "http_json", _owner_404)
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.invalid")

    store = ConsultationStore(str(tmp_path / "preserve_case"))
    wf_state = WorkflowStateStore(tmp_path / "preserve_case.sqlite3")

    # 1. Convene with explicit evidence_ref with real excerpt + supplied case_file fact, then execute
    created, _ = meetings_mod.convene_persona_meeting(
        store=store,
        workflow_state=wf_state,
        tenant_id=TENANT,
        persona_directory={},
        payload={
            "meeting_type": "MT-1",
            "topic": "Explicit and supplied preservation test",
            "convened_by": {"kind": "persona", "id": "persona-alpha"},
            "participants": ["persona-alpha", "persona-risk-officer"],
            "evidence_refs": [
                {"item_id": "measurement-1", "excerpt": "Observed drawdown is 0.12"},
            ],
            "case_file": {
                "items": [
                    {"item_id": "supplied-fact", "group": "facts", "excerpt": "Approved weight is 0.4"},
                ]
            },
            "iso_week": "2026-W31",
        },
    )
    mid = created["meeting_id"]

    class _SimpleProv:
        def obtain(self, *, tenant_id: str, request: Mapping[str, Any], participant_scope: str = "") -> Any:
            cid = request["case_file"]["groups"]["facts"]["items"][0]["item_id"]
            return SimpleNamespace(
                summary="round position",
                recommendation="approve_with_conditions",
                confidence=0.8,
                event_content={
                    "stance": "approve_with_conditions",
                    "confidence": 0.8,
                    "citations": [cid],
                    "claims": [{"claim": "reviewed facts", "evidence_refs": [cid]}],
                    "synthesis": {"summary": "Chair synthesis complete", "proposals": []},
                },
                evidence=[{"id": cid, "evidence_type": "meeting_evidence", "link": f"artifact://{mid}/{cid}"}],
            )

    completed = meetings_mod.execute_persona_meeting(
        store=store,
        meeting_id=mid,
        tenant_id=TENANT,
        provider=_SimpleProv(),
    )
    by_id = {i["item_id"]: i for i in completed["case_file"]["groups"]["facts"]["items"]}
    assert "measurement-1" in by_id
    assert by_id["measurement-1"]["excerpt"] == "Observed drawdown is 0.12"
    assert by_id["measurement-1"].get("status") != "unavailable"
    assert "supplied-fact" in by_id
    assert by_id["supplied-fact"]["excerpt"] == "Approved weight is 0.4"
    assert "facts:registry_entry:measurement-1" not in by_id

    # 2. Legacy meeting record with a genuinely unresolved placeholder alongside explicit & supplied items
    created_legacy, _ = meetings_mod.convene_persona_meeting(
        store=store,
        workflow_state=wf_state,
        tenant_id=TENANT,
        persona_directory={},
        payload={
            "meeting_type": "MT-1",
            "topic": "Legacy migration topic",
            "convened_by": {"kind": "persona", "id": "persona-alpha"},
            "participants": ["persona-alpha", "persona-risk-officer"],
            "iso_week": "2026-W32",
        },
    )
    legacy_mid = created_legacy["meeting_id"]
    legacy_Items = [
        {
            "item_id": "legacy-member-1",
            "group": "facts",
            "kind": "evidence_ref",
            "source_owner": "consultation",
            "source_id": "legacy-member-1",
            "excerpt": "Evidence legacy-member-1 for Legacy migration topic",
            "subject_fields": {},
        },
        {
            "item_id": "measurement-1",
            "group": "facts",
            "kind": "evidence_ref",
            "source_owner": "consultation",
            "source_id": "measurement-1",
            "excerpt": "Observed drawdown is 0.12",
            "subject_fields": {},
        },
        {
            "item_id": "supplied-fact",
            "group": "facts",
            "kind": "supplied",
            "source_owner": "persona",
            "source_id": "supplied-fact",
            "excerpt": "Approved weight is 0.4",
            "subject_fields": {},
        },
    ]
    created_legacy["case_file_items"] = legacy_Items
    created_legacy["case_file"] = {"items": list(legacy_Items)}
    store.put_meeting(created_legacy)

    migrated = meetings_mod.execute_persona_meeting(
        store=store,
        meeting_id=legacy_mid,
        tenant_id=TENANT,
        provider=_SimpleProv(),
    )
    mig_by_id = {i["item_id"]: i for i in migrated["case_file"]["groups"]["facts"]["items"]}
    assert "facts:registry_entry:legacy-member-1" in mig_by_id
    assert "momentum" in mig_by_id["facts:registry_entry:legacy-member-1"]["excerpt"]
    assert mig_by_id["measurement-1"]["excerpt"] == "Observed drawdown is 0.12"
    assert mig_by_id["supplied-fact"]["excerpt"] == "Approved weight is 0.4"


def test_meeting_case_file_enforces_bounded_owner_reads_and_item_excerpt_and_total_budgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.knowledge import case_file as cf

    reg_calls: list[str] = []

    def _missing_owners(endpoint: Any, path: str, **kwargs: Any) -> Any:
        if endpoint is None or not getattr(endpoint, "base_url", ""):
            raise cf.OwnerUnavailable("owner_unconfigured")
        if path.startswith("/api/registry/entries/"):
            reg_calls.append(path)
            raise cf.OwnerUnavailable("http_404")
        if path.endswith("/strategy-pool"):
            return {"pool_version": 4, "members": []}
        return {}

    monkeypatch.setattr(cf, "http_json", _missing_owners)
    owners = cf.Owners(
        registry=cf.OwnerEndpoint("http://registry"),
        pool=cf.OwnerEndpoint("http://persona"),
    )

    # 1. 20 missing refs -> capped at 8 registry entry reads and <=8 fact items
    case_20, items_20, _ = meetings_mod.compose_meeting_case_file(
        meeting_id="mtg-budget-20",
        meeting_code="MT-1",
        topic="Budget cap test 20 missing refs",
        subject_ref="persona:persona-2448c7053e33ea48da47",
        evidence_refs=[f"missing-ref-{i}" for i in range(20)],
        attribution_record_id=None,
        attribution_record=None,
        supplied_case_file=None,
        tenant_id=TENANT,
        owners=owners,
    )
    assert len(reg_calls) == 8
    assert len(case_20["groups"]["facts"]["items"]) <= 8
    assert len(items_20) <= 8
    assert case_20["groups"]["facts"]["truncated"] is True

    # 2. Explicit excerpt x*20000 and oversized subject_fields -> clipped to <=400 chars and <=12000 total
    case_huge, items_huge, _ = meetings_mod.compose_meeting_case_file(
        meeting_id="mtg-budget-huge",
        meeting_code="MT-1",
        topic="Budget cap test huge excerpt",
        subject_ref="persona:persona-2448c7053e33ea48da47",
        evidence_refs=[
            {
                "item_id": "huge-explicit-1",
                "excerpt": "x" * 20000,
                "subject_fields": {"huge_field": "y" * 20000},
            }
        ],
        attribution_record_id=None,
        attribution_record=None,
        supplied_case_file={
            "groups": {
                g: {
                    "items": [
                        {
                            "item_id": f"{g}-supplied-{i}",
                            "group": g,
                            "excerpt": f"{g}-{i}-" + ("z" * 1000),
                            "subject_fields": {"note": "w" * 1000},
                        }
                        for i in range(10)
                    ]
                }
                for g in cf.GROUPS
            }
        },
        tenant_id=TENANT,
        owners=owners,
    )
    for g_name, grp in case_huge["groups"].items():
        assert len(grp["items"]) <= 8, g_name
        for it in grp["items"]:
            assert len(it["excerpt"]) <= 400
            assert len(json.dumps(it["subject_fields"])) <= 400
    independent_huge_chars = sum(
        len(it["excerpt"]) + len(json.dumps(it["subject_fields"], sort_keys=True))
        for grp in case_huge["groups"].values()
        for it in grp["items"]
    )
    assert independent_huge_chars <= 12000
    assert len(items_huge) <= 15

    # 3. Reproduction: Owners(), four supplied groups of eight items, excerpt=x*400 and subject_fields={note:y*370}
    case_repro, items_repro, _ = meetings_mod.compose_meeting_case_file(
        meeting_id="mtg-budget-repro",
        meeting_code="MT-1",
        topic="Additive excerpt plus subject_fields budget reproduction",
        subject_ref="persona:persona-2448c7053e33ea48da47",
        evidence_refs=[],
        attribution_record_id=None,
        attribution_record=None,
        supplied_case_file={
            "groups": {
                g: {
                    "items": [
                        {
                            "item_id": f"{g}-item-{i}",
                            "group": g,
                            "excerpt": "x" * 400,
                            "subject_fields": {"note": "y" * 370},
                        }
                        for i in range(8)
                    ]
                }
                for g in cf.GROUPS
            }
        },
        tenant_id=TENANT,
        owners=cf.Owners(),
    )
    independent_repro_chars = sum(
        len(it["excerpt"]) + len(json.dumps(it["subject_fields"], sort_keys=True))
        for grp in case_repro["groups"].values()
        for it in grp["items"]
    )
    assert independent_repro_chars <= 12000
    assert len(items_repro) <= 15
    assert all(case_repro["groups"][g]["truncated"] is True for g in cf.GROUPS)

    # 4. Unicode (20000 Chinese chars) and escaped strings (20000 double quotes / backslashes) fit <=400 serialized chars
    for label, huge_note in (("zh", "中" * 20000), ("quotes", '"' * 20000), ("backslashes", "\\" * 20000)):
        case_ue, items_ue, _ = meetings_mod.compose_meeting_case_file(
            meeting_id=f"mtg-budget-{label}",
            meeting_code="MT-1",
            topic=f"Unicode and escaped subject_fields cap test {label}",
            subject_ref="persona:persona-2448c7053e33ea48da47",
            evidence_refs=[
                {
                    "item_id": f"explicit-{label}",
                    "excerpt": "explicit item with oversized unicode/escaped subject_fields",
                    "subject_fields": {"note": huge_note},
                }
            ],
            attribution_record_id=None,
            attribution_record=None,
            supplied_case_file=None,
            tenant_id=TENANT,
            owners=cf.Owners(),
        )
        by_id_ue = {it["item_id"]: it for it in items_ue}
        explicit_it = by_id_ue[f"explicit-{label}"]
        serialized_sf = json.dumps(explicit_it["subject_fields"], sort_keys=True)
        assert len(serialized_sf) <= 400, (label, len(serialized_sf))
        assert explicit_it["subject_fields"]["note"].startswith(huge_note[0])
        assert explicit_it["subject_fields"]["note"].endswith("…")
        for grp in case_ue["groups"].values():
            for it in grp["items"]:
                assert len(it["excerpt"]) <= 400
                assert len(json.dumps(it["subject_fields"], sort_keys=True)) <= 400


def test_operator_review_disposition_exposes_specific_cause_when_stored_and_unknown_on_legacy(
    tmp_path: Path,
) -> None:
    store = ConsultationStore(str(tmp_path / "review_cause_store"))
    wf_state = WorkflowStateStore(tmp_path / "review_cause_wf.sqlite3")

    # 1. Fresh REVIEW meeting with stored specific cause ('timeout')
    created, _ = meetings_mod.convene_persona_meeting(
        store=store,
        workflow_state=wf_state,
        tenant_id=TENANT,
        payload={
            "meeting_type": "MT-1",
            "topic": "Specific cause review disposition test",
            "convened_by": {"kind": "persona", "id": "persona-alpha"},
            "participants": ["persona-alpha", "persona-risk-officer"],
            "iso_week": "2026-W48",
        },
    )
    fresh_id = created["meeting_id"]

    class _TimeoutSynthProv:
        def obtain(self, *, tenant_id: str, request: Mapping[str, Any], participant_scope: str = "") -> Any:
            if participant_scope.endswith(":synthesis"):
                raise TimeoutError("synthesis provider timed out after 30s")
            cid = request["case_file"]["groups"]["facts"]["items"][0]["item_id"]
            return SimpleNamespace(
                summary="round position",
                recommendation="request_more_research",
                confidence=0.7,
                event_content={
                    "stance": "request_more_research",
                    "confidence": 0.7,
                    "citations": [cid],
                    "claims": [{"claim": "need more research", "evidence_refs": [cid]}],
                },
                evidence=[{"id": cid, "evidence_type": "meeting_evidence", "link": f"artifact://{fresh_id}/{cid}"}],
            )

    meetings_mod.execute_persona_meeting(
        store=store,
        meeting_id=fresh_id,
        tenant_id=TENANT,
        provider=_TimeoutSynthProv(),
    )

    fresh_detail = meetings_mod.get_meeting_detail_view(store, tenant_id=TENANT, meeting_id=fresh_id)
    assert fresh_detail is not None
    assert fresh_detail["outcome"] == "REVIEW"
    assert fresh_detail["synthesis_unreadable_reason"] == "timeout"
    assert fresh_detail["synthesis"]["unreadable_reason"] == "timeout"
    assert fresh_detail["operator_review"] is None
    assert fresh_detail["evidence_refs"]

    updated_fresh, replayed_fresh = meetings_mod.record_meeting_operator_review(
        store=store,
        tenant_id=TENANT,
        meeting_id=fresh_id,
        reviewed_by="op-reviewer",
        note="Confirmed synthesis timeout; closed review with no proposal.",
    )
    assert replayed_fresh is False
    assert updated_fresh["operator_review"]["status"] == "reviewed"
    assert updated_fresh["operator_review"]["cause"] == "timeout"
    assert updated_fresh["operator_review"]["unreadable_reason"] == "timeout"

    # 2. Legacy REVIEW meeting with null unreadable_reason records and exposes cause='unknown'
    legacy_id = "mtg-legacy-null-cause"
    store.put_meeting(
        {
            "meeting_id": legacy_id,
            "tenant_id": TENANT,
            "meeting_type": "MT-1",
            "topic": "Legacy REVIEW meeting without stored unreadable_reason",
            "status": "closed",
            "outcome": "REVIEW",
            "chair_persona_id": "persona-alpha",
            "convened_by": {"kind": "persona", "id": "persona-alpha"},
            "participants": [{"persona_id": "persona-alpha", "role": "chair"}],
            "round": 2,
            "case_file_items": [{"item_id": "facts:legacy:1", "excerpt": "Legacy fact"}],
            "rounds": [],
            "synthesis": {
                "proposals": [],
                "outcome": "REVIEW",
                "unreadable_reason": None,
                "summary": "Synthesis empty or unreadable; recorded as REVIEW with no proposal.",
            },
        }
    )
    legacy_detail = meetings_mod.get_meeting_detail_view(store, tenant_id=TENANT, meeting_id=legacy_id)
    assert legacy_detail is not None
    assert legacy_detail["synthesis_unreadable_reason"] == "unknown"
    assert legacy_detail["synthesis"]["unreadable_reason"] == "unknown"
    assert legacy_detail["evidence_refs"] == ["facts:legacy:1"]

    updated_legacy, replayed_legacy = meetings_mod.record_meeting_operator_review(
        store=store,
        tenant_id=TENANT,
        meeting_id=legacy_id,
        reviewed_by="op-reviewer",
        note="Reviewed legacy REVIEW meeting; cause not stored on legacy record.",
    )
    assert replayed_legacy is False
    assert updated_legacy["operator_review"]["status"] == "reviewed"
    assert updated_legacy["operator_review"]["cause"] == "unknown"
    assert updated_legacy["operator_review"]["unreadable_reason"] == "unknown"


def test_evidence_refs_withholds_all_private_and_unauthorized_trader_refs_across_source_list_detail_inbox(
    tmp_path: Path,
) -> None:
    from services.control_plane.bff.meetings.client import (
        project_human_inbox_meeting_items,
        shape_meeting_detail,
        shape_meeting_list,
    )

    store = ConsultationStore(str(tmp_path / "privacy_evidence_store"))

    # 1. Exact reviewer isolated repro: only private case items & private round positions
    isolated_id = "mtg-isolated-private-repro"
    store.put_meeting(
        {
            "meeting_id": isolated_id,
            "tenant_id": TENANT,
            "meeting_type": "MT-1",
            "topic": "Isolated private evidence repro",
            "status": "closed",
            "outcome": "REVIEW",
            "chair_persona_id": "persona-alpha",
            "convened_by": {"kind": "persona", "id": "persona-alpha"},
            "participants": [{"persona_id": "persona-alpha", "role": "chair"}],
            "round": 1,
            "case_file_items": [
                {"item_id": "secret-private-item", "scope": "private", "excerpt": "private scope item"},
                {"item_id": "secret-private-flag", "is_private": True, "excerpt": "is_private flag item"},
                {"item_id": "secret-private-kind", "kind": "private_memory", "excerpt": "private kind item"},
                {"item_id": "secret-private-vis", "visibility_scope": "user_private", "excerpt": "user_private item"},
                {"item_id": "secret-private-memscope", "memory_scope": "private_user", "excerpt": "private_user item"},
            ],
            "rounds": [
                {
                    "round": 1,
                    "positions": [
                        {
                            "persona_id": "persona-alpha",
                            "role": "chair",
                            "is_private": True,
                            "citations": ["secret-private-position-ref"],
                        },
                        {
                            "persona_id": "persona-beta",
                            "role": "participant",
                            "scope": "private",
                            "citations": ["secret-private-scope-pos-ref"],
                        },
                    ],
                }
            ],
            "synthesis": {"proposals": [], "outcome": "REVIEW", "unreadable_reason": None},
        }
    )

    source_detail = meetings_mod.get_meeting_detail_view(store, tenant_id=TENANT, meeting_id=isolated_id)
    assert source_detail is not None
    assert source_detail["evidence_refs"] == []

    shaped_detail = shape_meeting_detail(
        source_detail,
        is_operator=True,
        caller_user_id="op-alice",
        snapshot_at="2026-11-21T12:00:00Z",
    )
    assert shaped_detail["case_file_items"] == []
    assert shaped_detail["rounds"][0]["positions"] == []
    assert shaped_detail["evidence_refs"] == []

    source_list = meetings_mod.list_meetings_view(store, tenant_id=TENANT)
    assert source_list["items"][0]["evidence_refs"] == []
    shaped_list = shape_meeting_list(
        source_list,
        is_operator=True,
        caller_user_id="op-alice",
        snapshot_at="2026-11-21T12:00:00Z",
    )
    assert shaped_list["items"][0]["evidence_refs"] == []

    inbox_items = project_human_inbox_meeting_items(
        snapshot_at="2026-11-21T12:00:00Z",
        meetings=[source_detail],
    )
    assert inbox_items[0]["evidence_refs"] == []

    # 2. Mixed shared, private, and trader-scoped items across operator, owning trader, and other trader
    mixed_id = "mtg-mixed-visibility"
    store.put_meeting(
        {
            "meeting_id": mixed_id,
            "tenant_id": TENANT,
            "meeting_type": "MT-4",
            "topic": "Trader attribution visibility test",
            "status": "closed",
            "outcome": "REVIEW",
            "chair_persona_id": "persona-alpha",
            "convened_by": {"kind": "agora_user", "id": "trader-alice", "agora_user_id": "trader-alice"},
            "subject_ref": {"kind": "trader", "id": "trader-alice"},
            "participants": [{"persona_id": "persona-alpha", "role": "chair"}],
            "round": 1,
            "case_file_items": [
                {"item_id": "cf-shared-public", "relevance_scope": "committee"},
                {"item_id": "cf-private-hidden", "scope": "private"},
                {
                    "item_id": "cf-trader-alice-only",
                    "source_owner": "agora",
                    "trader_id": "trader-alice",
                    "agora_user_id": "trader-alice",
                },
            ],
            "rounds": [
                {
                    "round": 1,
                    "positions": [
                        {
                            "persona_id": "persona-alpha",
                            "role": "chair",
                            "citations": ["cf-shared-public", "cf-trader-alice-only", "cf-private-hidden"],
                        },
                        {
                            "persona_id": "servant-alice",
                            "origin": "private_servant",
                            "trader_id": "trader-alice",
                            "citations": ["cit-trader-alice-servant"],
                        },
                        {
                            "persona_id": "persona-secret",
                            "is_private": True,
                            "citations": ["cit-private-only"],
                        },
                    ],
                }
            ],
            "synthesis": {"proposals": [], "outcome": "REVIEW", "dissent_memo_id": "memo-dissent-mixed"},
        }
    )

    mixed_source = meetings_mod.get_meeting_detail_view(store, tenant_id=TENANT, meeting_id=mixed_id)
    assert mixed_source is not None
    for forbidden_private in ("cf-private-hidden", "cit-private-only"):
        assert forbidden_private not in mixed_source["evidence_refs"]

    op_shaped = shape_meeting_detail(
        mixed_source,
        is_operator=True,
        caller_user_id="op-alice",
        snapshot_at="2026-11-21T12:00:00Z",
    )
    assert op_shaped["evidence_refs"] == [
        "cf-shared-public",
        "cf-trader-alice-only",
        "cit-trader-alice-servant",
        "memo-dissent-mixed",
    ]

    alice_shaped = shape_meeting_detail(
        mixed_source,
        is_operator=False,
        caller_user_id="trader-alice",
        snapshot_at="2026-11-21T12:00:00Z",
    )
    assert alice_shaped["evidence_refs"] == [
        "cf-shared-public",
        "cf-trader-alice-only",
        "cit-trader-alice-servant",
        "memo-dissent-mixed",
    ]

    bob_shaped = shape_meeting_detail(
        mixed_source,
        is_operator=False,
        caller_user_id="trader-bob",
        snapshot_at="2026-11-21T12:00:00Z",
    )
    assert bob_shaped["evidence_refs"] == ["cf-shared-public", "memo-dissent-mixed"]
    assert "cf-trader-alice-only" not in bob_shaped["rounds"][0]["positions"][0]["citations"]
    assert "cf-private-hidden" not in bob_shaped["rounds"][0]["positions"][0]["citations"]


def test_meeting_memory_writeback_honors_consultation_memory_write_token_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient
    from scripts.issue_dev_paper_principals import issue_environment
    from scripts.test_issue_dev_paper_principals import KEY, configured
    from services.memory import main as memory_main

    values = issue_environment(configured())
    token_file = tmp_path / "CONSULTATION_MEMORY_WRITE_TOKEN"
    token_file.write_text(values["CONSULTATION_MEMORY_WRITE_TOKEN"], encoding="utf-8")
    token_file.chmod(0o600)

    monkeypatch.delenv("PANTHEON_MEMORY_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("PANTHEON_MEMORY_AUTH_TOKEN_FILE", raising=False)
    monkeypatch.setenv("CONSULTATION_MEMORY_WRITE_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PANTHEON_MEMORY_STORE", str(tmp_path / "inst.json"))
    monkeypatch.setenv("PANTHEON_PERSONA_MEMORY_STORE", str(tmp_path / "pers.json"))
    monkeypatch.setenv("PANTHEON_MEMORY_AUTHZ_MODE", "local")
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", KEY)
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_ISSUER", "isolated-issuer")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_AUDIENCE", "isolated-audience")
    memory_main._STORE = None
    memory_main._PERSONA_STORE = None

    client = TestClient(memory_main.app)

    class _Resp:
        def __init__(self, raw: bytes) -> None:
            self._raw = raw

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self) -> bytes:
            return self._raw

    def fake_urlopen(req, timeout=15.0):
        resp = client.post(
            "/api/memory/writebacks/learn-feedback",
            content=req.data,
            headers=dict(req.header_items()),
        )
        assert resp.status_code in (200, 201), resp.text
        return _Resp(resp.content)

    monkeypatch.setattr(meetings_mod.urllib.request, "urlopen", fake_urlopen)
    meeting = {
        "meeting_id": "mtg-wb-file-1",
        "meeting_type": "MT-1",
        "topic": "Weekly review",
        "chair_persona_id": "persona-alpha",
        "participants": [
            {"persona_id": "persona-alpha", "role": "chair"},
            {"persona_id": "persona-beta", "role": "trading_peer"},
        ],
        "case_file_items": [{"item_id": "facts:item:1"}],
        "rounds": [
            {
                "round": 2,
                "positions": [
                    {"persona_id": "persona-alpha", "stance": "approve", "changed": False},
                    {"persona_id": "persona-beta", "stance": "approve_with_conditions", "changed": True},
                ],
            }
        ],
        "synthesis": {"outcome": "proposals_issued", "proposals": [{"proposal_id": "prop-1", "sponsor_persona_id": "persona-alpha"}]},
    }
    writebacks, inst_id = meetings_mod._write_meeting_memory(
        tenant_id="tenant-dev",
        meeting=meeting,
        summary_memo_id="mem-summary-mtg-wb-file-1",
        memory_url="http://memory",
    )
    assert len(writebacks) == 2
    assert inst_id


@pytest.mark.parametrize("configured, expected", [(None, 15.0), ("22.5", 22.5)])
def test_preread_memory_timeout_default_and_override(monkeypatch, configured, expected):
    import integrations.openclaw.persona_memory_bridge as bridge
    from services.consultation.meetings import build_participant_pre_read

    if configured is None:
        monkeypatch.delenv("CONSULTATION_MEMORY_READ_TIMEOUT_SECONDS", raising=False)
    else:
        monkeypatch.setenv("CONSULTATION_MEMORY_READ_TIMEOUT_SECONDS", configured)
    calls = []

    def fetch(url, **kw):
        calls.append(kw)
        return {"hits": []}

    monkeypatch.setattr(bridge, "fetch_canonical_memory", fetch)
    build_participant_pre_read(
        meeting_id="mtg-budget", tenant_id=TENANT, topic="memory latency",
        memory_url="http://memory.invalid", participants=[{
            "persona_id": "persona-a", "role": "chair", "style": "momentum", "origin": "institutional"}])
    assert len(calls) == 1 and calls[0]["timeout_seconds"] == expected
    assert calls[0]["scope"] == "both" and calls[0]["limit"] == 12

# MEETING-ACTION-ADOPTION-AND-REVIEW-20261123 component contract regressions.
class _ActionResearchBoundary:
    """Scripted HTTP owner boundary, not a substitute for the chain's real-owner proof."""
    def __init__(self):
        self.tickets = {}
        self.requests = []
        self.malformed_once = False
        self.notes = {}
        self.more = None

    def __call__(self, method, url, headers, body):
        parsed = urllib.parse.urlsplit(url)
        self.requests.append((method, parsed, copy.deepcopy(body), dict(headers)))
        tenant = headers['X-Tenant-Id']
        if method == 'POST':
            assert 'ticket_id' not in body
            source = (tenant, body['meeting_action']['meeting_id'], body['meeting_action']['source_action_id'])
            row = next((r for r in self.tickets.values() if r['_source'] == source), None)
            if row is None:
                tid = f'owner-issued-ticket-{len(self.tickets) + 1}'
                row = {**copy.deepcopy(body), 'ticket_id': tid, 'tenant_id': tenant,
                       'action_state': 'open', 'revision': 0, 'evidence_refs': [], '_source': source}
                self.tickets[tid] = row
            if self.malformed_once:
                self.malformed_once = False
                return 200, {}
            return 201, copy.deepcopy(row)
        if '/notes/' in parsed.path:
            return 200, copy.deepcopy(self.notes[parsed.path.rsplit('/', 1)[-1]])
        if parsed.path == '/api/research/tickets':
            q = urllib.parse.parse_qs(parsed.query)
            assert q['meeting_linked'] == ['true'] and q['page_size'] == ['1']
            rows = [copy.deepcopy(r) for r in self.tickets.values()
                    if r['tenant_id'] == tenant
                    and r['meeting_action']['meeting_type'] == q['meeting_type'][0]
                    and r['meeting_action']['subject_ref'] == q['subject_ref'][0]
                    and r['action_state'] in q['action_state'][0].split(',')]
            return 200, {'data': rows[:1], 'page_info': {'next_page_token': self.more}}
        tid = parsed.path.rsplit('/', 1)[-1]
        row = self.tickets.get(tid)
        if row is None or row['tenant_id'] != tenant:
            return 404, {}
        if method == 'PATCH':
            patch = body['patch']
            assert patch['expected_revision'] == row['revision']
            assert patch['action'] in {'accept', 'return'}
            row.update(action_state='accepted' if patch['action'] == 'accept' else 'returned',
                       revision=row['revision'] + 1,
                       review_decision={'decision': patch['action'], 'review_source': patch['review_source'],
                                        'reason': patch['reason']})
        return 200, copy.deepcopy(row)


def _adopted_action(cid='facts:source', source='chair-1'):
    return {'source_action_id': source, 'assignee': 'persona-alpha', 'assignee_kind': 'persona',
            'deliverable': 'Saved comparison of the current pool alternatives',
            'acceptance_criterion': 'Compare measured drawdown and costs with cited source records',
            'due_at': '2026-11-20T00:00:00Z', 'source_refs': [cid]}


@pytest.mark.parametrize('bad_receipt', [{}, {'ticket_id': 'invented'},
    {'ticket_id': 'invented', 'tenant_id': TENANT, 'action_state': 'open', 'revision': 0, 'meeting_action': {}},
    {'ticket_id': 23, 'tenant_id': TENANT, 'action_state': 'open', 'revision': 0, 'meeting_action': {}}])
def test_action_dispatch_malformed_success_does_not_suppress_owner_retry(tmp_path, monkeypatch, bad_receipt):
    from services.consultation.meeting_outputs import dispatch_meeting_actions, list_dispatched_proposals
    monkeypatch.setenv('PANTHEON_RESEARCH_JWT_SECRET', 'test-action-secret-with-enough-length')
    meeting = {'tenant_id': TENANT, 'meeting_id': 'receipt-test',
               'meeting_series': {'meeting_type': 'MT-1', 'subject_ref': 'pool:persona-alpha'},
               'synthesis': {'actions': [_adopted_action()]}}
    owner = _ActionResearchBoundary()
    attempts = []
    def transport(method, url, headers, body):
        attempts.append(copy.deepcopy(body))
        _, valid = owner(method, url, headers, body)
        return (200, bad_receipt) if len(attempts) == 1 else (200, valid)
    ledger = tmp_path / 'dispatch.json'
    first = dispatch_meeting_actions(meeting, transport=transport, store_path=ledger)[0]
    assert first['state'] == 'dispatch_pending' and first.get('ticket_id') is None
    assert first['dispatch_error']
    second = dispatch_meeting_actions(meeting, transport=transport, store_path=ledger)[0]
    assert second['ticket_id'] == 'owner-issued-ticket-1' and second['dispatch_error'] is None
    dispatch_meeting_actions(meeting, transport=transport, store_path=ledger)
    assert len(attempts) == 2 and attempts[0] == attempts[1] and len(owner.tickets) == 1
    assert list_dispatched_proposals(store_path=ledger) == []


def test_action_http_fault_then_genuine_receipt_and_tenant_isolation(tmp_path, monkeypatch):
    from services.consultation.meeting_outputs import dispatch_meeting_actions
    monkeypatch.setenv('PANTHEON_RESEARCH_JWT_SECRET', 'test-action-secret-with-enough-length')
    owner = _ActionResearchBoundary()
    calls = 0
    def transport(*args):
        nonlocal calls
        calls += 1
        return (503, {}) if calls == 1 else owner(*args)
    m = {'tenant_id': TENANT, 'meeting_id': 'same-source',
         'meeting_series': {'meeting_type': 'MT-1', 'subject_ref': 'pool:persona-alpha'},
         'synthesis': {'actions': [_adopted_action()]}}
    ledger = tmp_path / 'dispatch.json'
    assert dispatch_meeting_actions(m, transport=transport, store_path=ledger)[0]['state'] == 'dispatch_pending'
    assert dispatch_meeting_actions(m, transport=transport, store_path=ledger)[0]['ticket_id'] == 'owner-issued-ticket-1'
    foreign = copy.deepcopy(m)
    foreign['tenant_id'] = 'foreign-tenant'
    assert dispatch_meeting_actions(foreign, transport=transport, store_path=ledger)[0]['ticket_id'] == 'owner-issued-ticket-2'
    assert calls == 3


@pytest.mark.parametrize('decision', ['accept', 'return'])
def test_two_meetings_adoption_actual_round_inputs_review_same_ticket(tmp_path, monkeypatch, decision):
    import jsonschema
    from consultation_provider import _meeting_event_content_schema
    from services.consultation.meeting_outputs import read_meeting_actions
    monkeypatch.setenv('PANTHEON_RESEARCH_JWT_SECRET', 'test-action-secret-with-enough-length')
    monkeypatch.setattr(meetings_mod, '_participant_action_capability', lambda tenant, pid:
        {'persona_id': pid, 'status': 'allowed' if pid == 'persona-alpha' else 'advisory_only',
         'can_receive_persona_action': pid == 'persona-alpha', 'allowed_deliverables': ['research_note'],
         'consultation_can_mutate_trading_state': False})
    monkeypatch.setattr(meetings_mod, '_write_meeting_memory', lambda **kwargs: ([], None))
    monkeypatch.setattr(meetings_mod, 'build_participant_pre_read', lambda **kwargs: [])  # zero semantic hits
    store = ConsultationStore(str(tmp_path / 'meetings'))
    owner = _ActionResearchBoundary()
    calls = []
    class Provider:
        def obtain(self, *, tenant_id, request, participant_scope):
            ctx = request['meeting']
            calls.append(copy.deepcopy(request))
            cid = request['case_file']['groups']['facts']['items'][0]['item_id']
            if ctx['round'] == 'synthesis':
                if not ctx['prior_actions']:
                    synth = {'summary': 'Adopt two research deliverables', 'outcome': 'completed_no_proposal',
                             'proposals': [], 'actions': [_adopted_action(cid), _adopted_action(cid, 'chair-2')]}
                else:
                    submitted = next(a for a in ctx['prior_actions'] if a['state'] == 'submitted')
                    synth = {'summary': 'Review the delivered comparison and retain pending work',
                             'outcome': 'completed_no_proposal', 'proposals': [], 'actions': [],
                             'no_action_reason': 'Continue the original outstanding assignment',
                             'action_reviews': [{'action_id': submitted['action_id'], 'decision': decision,
                                 'reason': 'Compared the stated criterion to the measured result: ' + decision,
                                 'acceptance_criterion': submitted['acceptance_criterion'],
                                 'evidence_refs': submitted['evidence_refs']}]}
                event = {'claims': [{'claim': 'Review the available evidence', 'evidence_refs': [cid]}], 'summary_evidence_refs': [cid], 'synthesis': synth}
                jsonschema.validate(event, _meeting_event_content_schema(ctx))
                return SimpleNamespace(event_content=event, summary=synth['summary'], evidence=[])
            event = {'stance': 'approve', 'confidence': .8, 'citations': [cid], 'claims': [{'claim': 'Discuss the case', 'evidence_refs': [cid]}],
                     'summary_evidence_refs': [cid], 'accepted': [], 'disputed': [], 'changed': False,
                     'proposals': [{'kind': 'research_demand', 'sponsor_persona_id': 'persona-alpha',
                                    'citations': [cid], 'payload': {'summary': 'Advisory only'}}]}
            jsonschema.validate(event, _meeting_event_content_schema(ctx))
            return SimpleNamespace(event_content=event, summary='Discuss alternatives', evidence=[], confidence=.8, recommendation='approve')
    def convene(week):
        return meetings_mod.convene_persona_meeting(store=store, workflow_state=None, tenant_id=TENANT,
            payload={'meeting_type': 'MT-1', 'topic': 'Review pool alternatives', 'iso_week': week,
                     'convened_by': {'kind': 'persona', 'id': 'persona-alpha'},
                     'participants': ['persona-alpha', 'persona-risk-officer'],
                     'subject_ref': 'persona:persona-alpha'}, idempotency_key=week)[0]
    with output_routing_context(transport=owner, store_path=tmp_path / 'dispatch.json'):
        first = convene('2026-W47')
        owner.malformed_once = True
        monkeypatch.setattr(meetings_mod, 'utc_now', lambda: '2026-11-23T00:00:00Z')
        from services.consultation.meeting_outputs import MeetingOutputRoutingError
        with pytest.raises(MeetingOutputRoutingError, match='frozen decision retained'):
            meetings_mod.execute_persona_meeting(store=store, meeting_id=first['meeting_id'],
                                                tenant_id=TENANT, provider=Provider())
        frozen = store.get_meeting(first['meeting_id'])
        assert frozen['action_decision_frozen'] and frozen['status'] != 'closed'
        assert store.get_request(first['meeting_id']).status.value != 'published'
        before_retry_calls = len(calls)
        monkeypatch.setattr(meetings_mod, 'utc_now', lambda: '2026-11-23T00:00:05Z')
        m1 = meetings_mod.execute_persona_meeting(store=store, meeting_id=first['meeting_id'],
                                               tenant_id=TENANT, provider=Provider())
        assert len(calls) == before_retry_calls  # no second synthesis after a lost owner receipt
        assert store.get_memo(m1['synthesis']['dissent_memo_id']).published_at == '2026-11-23T00:00:00Z'
        assert m1['closed_at'] == '2026-11-23T00:00:05Z'
        assert m1['status'] == 'closed' and m1['synthesis']['proposals'] == []
        assert m1['outcome'] == 'actions_adopted' and len(m1['actions']) == 2
        t1, t2 = owner.tickets.values()
        t1.update(action_state='submitted', revision=2,
                  evidence_refs=[{'kind': 'research_note', 'artifact_id': 'note-1', 'checksum': 'digest-1', 'tenant_id': TENANT}])
        owner.notes['note-1'] = {'_meeting_binding': {'tenant_id': TENANT, 'ticket_id': t1['ticket_id']},
                                '_meeting_digest': 'digest-1', 'body': 'Measured drawdown 4%; costs 12 bps.',
                                'title': 'Actual result', 'source_refs': ['registered-input']}
        owner.more = 'remaining-keyset-token'
        second = convene('2026-W48')
        m2 = meetings_mod.execute_persona_meeting(store=store, meeting_id=second['meeting_id'],
                                               tenant_id=TENANT, provider=Provider())
        assert m2['meeting_id'] != m1['meeting_id']
        assert m2['agenda'][0].startswith('Review previous work')
        assert len(m2['prior_actions']) == 2 and m2['actions'] == []
        assert all(p['has_more'] for p in m2['prior_actions_source']['pages'].values())
        for request in [c for c in calls if c['request_id'] == m2['meeting_id']]:
            ctx = request['meeting']
            assert len(ctx['prior_actions']) == 2
            assert ctx['prior_action_evidence'][0]['content']['body'].startswith('Measured drawdown')
            assert ctx['prior_actions_source']['pages']['submitted']['next_page_token']
            assert request['case_file']['groups']['meeting_actions']['items']
            if ctx['round'] != 'synthesis':
                assert 'participant_action_capabilities' not in ctx
        assert t1['action_state'] == ('accepted' if decision == 'accept' else 'returned')
        assert t2['action_state'] == 'open'
        assert m2['action_review_dispatches'][0]['status'] == 'recorded'
        before = len(calls)
        meetings_mod.execute_persona_meeting(store=store, meeting_id=m2['meeting_id'], tenant_id=TENANT, provider=Provider())
        assert len(calls) == before and len([r for r in owner.requests if r[0] == 'PATCH']) == 1
        detail = meetings_mod.get_meeting_detail_view(store, tenant_id=TENANT, meeting_id=m1['meeting_id'])
        assert detail['actions'][0]['state'] == 'open'  # frozen receipt
        assert detail['actions'][0]['live_owner_status']['ticket']['state'] == t1['action_state']
        assert store.get_meeting(m1['meeting_id'])['actions'][0]['state'] == 'open'
        import runpy
        probe = runpy.run_path(str(Path(__file__).resolve().parents[3] /
            'docs/deployment/evidence/MEETING-ACTION-ADOPTION-AND-REVIEW-20261123/probe.py'))['inspect_records']
        assert probe(detail, m2)['passed']
        assert not probe(detail, {**m2, 'meeting_id': m1['meeting_id']})['passed']
        assert not probe(detail, {**m2, 'tenant_id': 'foreign-tenant'})['passed']
        assert not probe(detail, {**m2, 'action_review_dispatches': []})['passed']
        unrelated = copy.deepcopy(m2)
        unrelated['meeting_series']['subject_ref'] = 'other-committee'
        assert read_meeting_actions(unrelated)['items'] == []
        unrelated['tenant_id'] = 'other-tenant'
        unrelated['meeting_series']['subject_ref'] = 'pool:persona-alpha'
        assert read_meeting_actions(unrelated)['items'] == []
    # Measured component envelope, captured for the task runtime budget.
    measurement = {'meetings': [len(json.dumps(m).encode()) for m in (m1, m2)],
                   'max_provider_request_bytes': max(len(json.dumps(c).encode()) for c in calls),
                   'ledger_bytes': (tmp_path / 'dispatch.json').stat().st_size,
                   'lifecycle_bytes': store.lifecycle_log_path.stat().st_size,
                   'lifecycle_events': len(store.lifecycle_log_path.read_text().splitlines()),
                   'owner_requests': len(owner.requests)}
    print('ACTION_ENVELOPE_MEASUREMENT ' + json.dumps(measurement, sort_keys=True))


@pytest.mark.parametrize('ref', [None, 'meeting:mtg-1', 'week:2026-W47', 'iso-week:2026-W47'])
def test_unrelated_committees_do_not_join_by_type_default(ref):
    a = {'meeting_code': 'MT-3', 'subject_ref': ref, 'chair_persona_id': 'persona-alpha',
         'convened_by': {'kind': 'operator', 'id': 'op-1'}}
    assert meetings_mod._meeting_series(a) is None
    a.update(meeting_code='MT-1', convened_by={'kind': 'persona', 'id': 'persona-alpha'})
    if ref is None or str(ref).startswith(('week:', 'meeting:')):
        assert meetings_mod._meeting_series(a)['subject_ref'] == 'pool:persona-alpha'


def test_adoption_declines_forged_capability_review_and_keeps_legacy_minutes(tmp_path):
    action = _adopted_action()
    meeting = {'meeting_series': {'meeting_type': 'MT-1', 'subject_ref': 'pool:persona-alpha'},
               'participant_action_capabilities': [{'persona_id': 'persona-alpha', 'status': 'advisory_only',
                                                    'can_receive_persona_action': False}], 'prior_actions': []}
    accepted, reviews, rejected = meetings_mod._validate_adopted_actions({'actions': [action]}, meeting, {'facts:source'})
    assert accepted == [] and rejected[0]['reason'] == 'persona_owner_did_not_allow_assignment'
    accepted, reviews, _ = meetings_mod._validate_adopted_actions({'outcome': 'REVIEW', 'actions': [action]}, meeting, {'facts:source'})
    assert accepted == reviews == []
    store = ConsultationStore(str(tmp_path / 'legacy'))
    legacy = {'meeting_id': 'mtg-W47', 'tenant_id': TENANT, 'status': 'closed', 'outcome': 'REVIEW',
              'participants': [], 'rounds': [{'round': 1}, {'round': 2}], 'source_refs': ['original-ref'],
              'synthesis': {'outcome': 'REVIEW', 'proposals': [], 'summary': 'Original no-action minutes'}}
    store.put_meeting(legacy)
    before = copy.deepcopy(store.get_meeting('mtg-W47'))
    detail = meetings_mod.get_meeting_detail_view(store, tenant_id=TENANT, meeting_id='mtg-W47')
    assert detail['actions'] == [] and detail['prior_actions'] == []
    assert store.get_meeting('mtg-W47') == before
    assert detail['synthesis']['summary'] == 'Original no-action minutes'


def test_capability_http_owner_result_is_safe_and_does_not_trust_role_or_inline_fields(monkeypatch):
    from io import BytesIO
    from contextlib import closing
    seen = []
    monkeypatch.setenv('PANTHEON_PERSONA_API_URL', 'http://persona-owner')
    monkeypatch.delenv('CONSULTATION_PERSONA_DIRECTORY_READ_TOKEN_FILE', raising=False)
    monkeypatch.setenv('CONSULTATION_PERSONA_DIRECTORY_READ_TOKEN', 'issued-read-token')
    def urlopen(req, timeout):
        seen.append(req)
        return closing(BytesIO(json.dumps({'persona_id': 'persona-alpha', 'status': 'advisory_only',
            'can_receive_persona_action': False, 'role': 'chair', 'private_memory': 'do-not-share',
            'token': 'do-not-share', 'reason': 'no authorized workflow'}).encode()))
    monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
    result = meetings_mod._participant_action_capability(TENANT, 'persona-alpha')
    assert result['status'] == 'advisory_only' and result['can_receive_persona_action'] is False
    assert 'do-not-share' not in json.dumps(result) and 'role' not in result
    assert seen[0].get_header('Authorization') == 'Bearer issued-read-token'
    assert seen[0].get_header('X-tenant-id') == TENANT
    assert 'run_purpose=daily_cycle' in seen[0].full_url
    def denied(*args, **kwargs):
        raise urllib.error.HTTPError('http://persona-owner', 403, 'private owner details', {}, None)
    monkeypatch.setattr(urllib.request, 'urlopen', denied)
    result = meetings_mod._participant_action_capability(TENANT, 'persona-alpha')
    assert result['status'] == 'denied' and not result['can_receive_persona_action']
    assert 'private owner details' not in json.dumps(result)


def test_action_review_cannot_accept_criterion_mismatch_or_unread_evidence():
    submitted = {'action_id': 'owner-id', 'state': 'submitted', 'revision': 2,
                 'acceptance_criterion': 'Compare drawdown', 'evidence_available': True,
                 'evidence_refs': [{'kind': 'research_note', 'artifact_id': 'result', 'checksum': 'digest'}]}
    review = {'action_id': 'owner-id', 'decision': 'accept', 'reason': 'Measured result meets criterion',
              'acceptance_criterion': 'Compare drawdown', 'evidence_refs': submitted['evidence_refs']}
    meeting = {'prior_actions': [submitted]}
    assert len(meetings_mod._validate_adopted_actions({'action_reviews': [review]}, meeting, set())[1]) == 1
    for change in ({'acceptance_criterion': 'Unrelated test'}, {'evidence_refs': []}, {'action_id': ['owner-id']}):
        _, reviews, rejected = meetings_mod._validate_adopted_actions({'action_reviews': [{**review, **change}]}, meeting, set())
        assert not reviews and rejected
    submitted['evidence_available'] = False
    assert not meetings_mod._validate_adopted_actions({'action_reviews': [review]}, meeting, set())[1]


def test_new_review_dispositions_use_schedule_watermark_without_archival_scan(tmp_path, monkeypatch):
    from services.consultation.meeting_outputs import read_meeting_actions
    monkeypatch.setenv('PANTHEON_RESEARCH_JWT_SECRET', 'test-action-secret-with-enough-length')
    store = ConsultationStore(str(tmp_path / 'schedule'))
    schedule = {'tenant_id': TENANT, 'persona_id': 'persona-alpha', 'meeting_type': 'MT-1',
                'action_review_watermark': '2026-11-22T00:00:00Z',
                'meeting_series': {'meeting_type': 'MT-1', 'subject_ref': 'pool:persona-alpha'}}
    store.put_meeting_schedule(schedule)
    assert store.get_meeting_schedule(TENANT, 'persona-alpha', 'MT-1') == schedule
    assert store.get_meeting_schedule('foreign', 'persona-alpha', 'MT-1') is None
    owner = _ActionResearchBoundary()
    m = {'tenant_id': TENANT, 'meeting_id': 'm2', 'meeting_series': schedule['meeting_series'],
         'previous_action_review_at': schedule['action_review_watermark'],
         'metadata': {'prior_actions_page_tokens': {'reviewed': 'same-scope-keyset'}}}
    result = read_meeting_actions(m, transport=owner)
    assert result['status'] == 'ok'
    queries = [urllib.parse.parse_qs(r[1].query) for r in owner.requests]
    assert all(q.get('updated_after') is None for q in queries[:2])
    assert queries[2]['updated_after'] == ['2026-11-22T00:00:00Z']
    # A legacy cursor without a scan start must restart safely.
    assert 'page_token' not in queries[2]
    assert result['scan']['started_at'] and not result['scan']['page_tokens']
    # A first-page failure can persist an empty cursor map with a scan start.
    # Legacy metadata must not override that authoritative restart position.
    m['action_review_scan'] = result['scan']
    read_meeting_actions(m, transport=owner)
    assert 'page_token' not in urllib.parse.parse_qs(owner.requests[-1][1].query)


def test_oversize_shared_case_and_provider_envelope_are_refused_without_truncating_work():
    with pytest.raises(meetings_mod.MeetingRefusedError, match='256 KiB'):
        meetings_mod._attach_meeting_action_case({'groups': {}, 'unexpected_owner_payload': 'x' * 262145}, [])
    with pytest.raises(meetings_mod.MeetingRefusedError, match='2 MiB'):
        meetings_mod._check_meeting_prompt_budget({'meeting': {'prior_actions': [{'deliverable': 'x' * 2097153}]}})


@pytest.fixture(params=['scripted', 'real_owner'])
def action_owner_contract(request, monkeypatch):
    """Run stable component cases and optionally the unmerged real owner contract.

    Supply MEETING_ACTION_RESEARCH_OWNER_SOURCE to compose a frozen Research
    work package before integration; after integration the normal import works.
    Only persistence is isolated, not create/list normalization or cursor logic.
    """
    import importlib.util
    import os
    import sqlite3
    owner = _ActionResearchBoundary()
    if request.param == 'scripted':
        original = owner.__call__
        def send(method, url, headers, body):
            if method != 'GET' or urllib.parse.urlsplit(url).path != '/api/research/tickets':
                return original(method, url, headers, body)
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            rows = sorted((r for r in owner.tickets.values()
                if r['tenant_id'] == headers['X-Tenant-Id']
                and r['meeting_action']['meeting_type'] == q['meeting_type'][0]
                and r['meeting_action']['subject_ref'] == q['subject_ref'][0]
                and r['action_state'] in q['action_state'][0].split(',')
                and r['updated_at'] >= q.get('updated_after', [''])[0]
                and r['ticket_id'] > q.get('page_token', [''])[0]), key=lambda r: r['ticket_id'])
            size = int(q['page_size'][0])
            return 200, {'data': copy.deepcopy(rows[:size]), 'page_info': {
                'next_page_token': rows[size - 1]['ticket_id'] if len(rows) > size else None}}
        owner.send = send
        return owner
    source = os.getenv('MEETING_ACTION_RESEARCH_OWNER_SOURCE')
    if source:
        spec = importlib.util.spec_from_file_location('meeting_contract_research_owner', source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        from services.research import write_owner as module
    if not hasattr(module.ResearchWriteOwner, '_create_meeting_ticket'):
        pytest.skip('Research owner work package not integrated; supply its frozen source explicitly')
    real = module.ResearchWriteOwner.__new__(module.ResearchWriteOwner)
    ctx = SimpleNamespace(tenant_id=TENANT, actor_id='consultation-svc', roles={'service', 'viewer'})
    class SqlBoundary:
        table = 'tickets'
        _decode_payload = staticmethod(json.loads)
        def get(self, tid):
            return copy.deepcopy(owner.tickets.get(tid))
        @contextmanager
        def _connect(self):
            with sqlite3.connect(':memory:') as conn:
                conn.execute('CREATE TABLE tickets (record_id TEXT PRIMARY KEY, payload TEXT)')
                conn.executemany('INSERT INTO tickets VALUES (?, ?)',
                    [(tid, json.dumps(row)) for tid, row in owner.tickets.items()])
                self.conn = conn
                yield self
        def execute(self, sql, params):
            # Preserve the owner's actual SQL filtering/order/limit. Translate
            # only PostgreSQL placeholders, array syntax and timestamp casts.
            values = []
            for value in params:
                if isinstance(value, list):
                    sql = sql.replace('= ANY(%s)', 'IN (' + ','.join('?' for _ in value) + ')', 1)
                    values.extend(value)
                else:
                    values.append(value)
            return self.conn.execute(sql.replace('::timestamptz', '').replace('%s', '?'), values)
    real._tickets_store = SqlBoundary()
    owner.list_research_tickets = real.list_research_tickets
    owner.get_research_ticket = real.get_research_ticket
    def insert(store, tid, row, **kwargs):
        if tid in owner.tickets:
            return False, owner.tickets[tid]
        owner.tickets[tid] = copy.deepcopy(row)
        return True, owner.tickets[tid]
    real._bounded_action_insert = insert
    def send(method, url, headers, body):
        if '/notes/' in urllib.parse.urlsplit(url).path:
            return owner(method, url, headers, body)
        if method == 'POST':
            return 201, real._create_meeting_ticket(**body, actor_id=ctx.actor_id, context=ctx, ticket_id=None)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}
        return 200, real.list_research_tickets(context=ctx, meeting_linked=True, statuses=None, owner=None,
            meeting_type=q['meeting_type'], subject_ref=q['subject_ref'], action_states=q['action_state'].split(','),
            due_before=None, submitted_after=None, updated_after=q.get('updated_after'),
            page_size=int(q['page_size']), page_token=q.get('page_token'))
    owner.send = send
    return owner


@pytest.mark.parametrize('use_due', [False, True])
def test_adoption_normalizes_before_freeze_and_real_owner_receipt(tmp_path, monkeypatch, action_owner_contract, use_due):
    from services.consultation.meeting_outputs import dispatch_meeting_actions
    monkeypatch.setenv('PANTHEON_RESEARCH_JWT_SECRET', 'test-action-secret-with-enough-length')
    raw = {k: (' ' + v + ' ' if isinstance(v, str) else v) for k, v in _adopted_action().items()}
    raw['source_refs'] = [' facts:source ']
    if use_due:
        raw['due_at'] = '2026-11-20T00:00:00+00:00'
    else:
        raw.pop('due_at')
        raw['next_review'] = ' next weekly review '
    meeting = {'tenant_id': TENANT, 'meeting_id': 'normalization',
        'meeting_series': {'meeting_type': 'MT-1', 'subject_ref': 'pool:persona-alpha'},
        'participant_action_capabilities': [{'persona_id': 'persona-alpha', 'status': 'allowed',
            'can_receive_persona_action': True, 'allowed_deliverables': ['research_note']}]}
    actions, _, rejected = meetings_mod._validate_adopted_actions({'actions': [raw]}, meeting, {'facts:source'})
    assert not rejected and len(actions) == 1
    assert all(v == v.strip() for v in actions[0].values() if isinstance(v, str))
    assert actions[0]['source_refs'] == ['facts:source']
    _, _, rejected = meetings_mod._validate_adopted_actions({'actions': [raw, {**raw, 'source_action_id': 'chair-1'}]}, meeting, {'facts:source'})
    assert rejected == [{'index': 1, 'reason': 'duplicate_source_action_id'}]
    meeting['synthesis'] = {'actions': actions}
    calls = []
    def transport(*args):
        calls.append(copy.deepcopy(args[3]))
        code, reply = action_owner_contract.send(*args)
        return (code, {}) if len(calls) == 1 else (code, reply)  # durable write, lost receipt
    ledger = tmp_path / 'dispatch.json'
    assert dispatch_meeting_actions(meeting, transport=transport, store_path=ledger)[0]['state'] == 'dispatch_pending'
    result = dispatch_meeting_actions(meeting, transport=transport, store_path=ledger)[0]
    assert result['state'] == 'open' and result['dispatch_error'] is None
    dispatch_meeting_actions(meeting, transport=transport, store_path=ledger)
    assert len(calls) == 2 and calls[0] == calls[1] and len(action_owner_contract.tickets) == 1


def test_review_traversal_revisits_concurrent_earlier_keys_for_chair(tmp_path, monkeypatch, action_owner_contract):
    from services.consultation import meeting_outputs as outputs
    from datetime import datetime, timezone
    monkeypatch.setenv('PANTHEON_RESEARCH_JWT_SECRET', 'test-action-secret-with-enough-length')
    monkeypatch.setattr(meetings_mod, '_write_meeting_memory', lambda **kw: ([], None))
    monkeypatch.setattr(outputs, '_utc_now', lambda: datetime(2026, 11, 21, tzinfo=timezone.utc))
    series = {'meeting_type': 'MT-1', 'subject_ref': 'pool:persona-alpha'}
    for i in range(1, 10):
        tid = 'rt-action-' + f'{i:032x}'
        action_owner_contract.tickets[tid] = {'ticket_id': tid, 'tenant_id': TENANT,
            'owner': 'persona-alpha', 'meeting_action': {**series, 'meeting_id': 'origin',
                'deliverable': 'Compare costs', 'acceptance_criterion': 'Measured costs', 'assignee_kind': 'persona'},
            'action_state': 'accepted', 'revision': 1, 'updated_at': '2026-11-20T12:00:00Z', 'evidence_refs': []}
    participants = [{'persona_id': f'participant-{i}', 'trading': True, 'role': 'chair' if i == 0 else 'participant'} for i in range(5)]
    store = ConsultationStore(str(tmp_path / 'scan'))
    def close_page(number, schedule):
        meeting = {'tenant_id': TENANT, 'meeting_id': f'review-{number}', 'meeting_series': series,
            'created_at': '2026-11-23T00:00:00Z', 'meeting_code': 'MT-1', 'meeting_type': 'MT-1',
            'topic': 'Review results', 'chair_persona_id': 'participant-0', 'trace_id': 'scan',
            'participants': participants, 'rounds': [],
            'previous_action_review_at': schedule.get('action_review_watermark'),
            'action_review_scan': schedule.get('action_review_scan') or {},
            'synthesis': {'summary': 'Read this page', 'outcome': 'completed_no_proposal', 'proposals': []}}
        read = outputs.read_meeting_actions(meeting, transport=action_owner_contract.send)
        assert read['status'] == 'ok' and len(read['items']) <= 3
        assert all(p['returned'] <= 1 for p in read['pages'].values())
        meeting.update(prior_actions=read['items'], prior_action_evidence=read['evidence'],
            prior_actions_source={k: v for k, v in read.items() if k not in {'items', 'evidence'}})
        meetings_mod._close_persona_meeting(store=store, meeting=meeting, spec={'cadence': 'weekly'}, memory_url='unused')
        saved = store.get_meeting_schedule(TENANT, 'participant-0', 'MT-1')
        for p in participants[1:]:
            peer = store.get_meeting_schedule(TENANT, p['persona_id'], 'MT-1')
            assert peer['last_run_at'] == saved['last_run_at'] and peer['next_due_at'] == saved['next_due_at']
            assert not {'meeting_series', 'action_review_watermark', 'action_review_scan'} & peer.keys()
        return read, saved
    first, schedule = close_page(1, {'action_review_watermark': '2026-11-20T00:00:00Z'})
    assert len(first['items']) == 1 and first['pages']['reviewed']['has_more']
    # Both a new accepted item and a new disposition on a previously seen item
    # sort below the cursor, after page 1 and before the final meeting begins.
    new = copy.deepcopy(next(iter(action_owner_contract.tickets.values())))
    new.update(ticket_id='rt-action-' + '0' * 32, updated_at='2026-11-22T00:00:00Z')
    action_owner_contract.tickets[new['ticket_id']] = new
    changed = action_owner_contract.tickets['rt-action-' + f'{1:032x}']
    changed.update(updated_at='2026-11-22T00:00:00Z', revision=2,
                   review_decision={'decision': 'accept', 'reason': 'New disposition'})
    monkeypatch.setattr(outputs, '_utc_now', lambda: datetime(2026, 11, 23, tzinfo=timezone.utc))
    # A failed page leaves its cursor and original scan boundary intact.
    send = action_owner_contract.send
    def failed_page(method, url, headers, body):
        if urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get('action_state') == ['accepted']:
            return 503, {}
        return send(method, url, headers, body)
    action_owner_contract.send = failed_page
    failed = outputs.read_meeting_actions({'tenant_id': TENANT, 'meeting_id': 'retry', 'meeting_series': series,
        'action_review_scan': schedule['action_review_scan'],
        'previous_action_review_at': schedule['action_review_watermark']}, transport=failed_page)
    assert failed['status'] == 'unavailable' and failed['scan'] == schedule['action_review_scan']
    action_owner_contract.send = send
    for number in range(2, 9):
        _, schedule = close_page(number, schedule)
        assert schedule['action_review_watermark'] == '2026-11-20T00:00:00Z'
    final, schedule = close_page(9, schedule)
    assert len(final['items']) == 1 and not final['pages']['reviewed']['has_more']
    assert schedule['action_review_watermark'] == '2026-11-21T00:00:00Z'
    assert not schedule['action_review_scan']
    next_scan, schedule = close_page(10, schedule)
    assert [a['action_id'] for a in next_scan['items']] == [new['ticket_id']]
    next_scan, _ = close_page(11, schedule)
    assert [a['action_id'] for a in next_scan['items']] == [changed['ticket_id']]
    assert next_scan['items'][0]['revision'] == 2


@pytest.mark.parametrize('token', ['x' * 129, 17, ''])
def test_owner_continuation_is_bounded_before_schedule_persistence(monkeypatch, token):
    from services.consultation.meeting_outputs import read_meeting_actions
    monkeypatch.setenv('PANTHEON_RESEARCH_JWT_SECRET', 'test-action-secret-with-enough-length')
    owner = _ActionResearchBoundary()
    owner.more = token
    read = read_meeting_actions({'tenant_id': TENANT, 'meeting_id': 'invalid-page',
        'meeting_series': {'meeting_type': 'MT-1', 'subject_ref': 'pool:persona-alpha'}}, transport=owner)
    assert read['status'] == 'unavailable' and not read['items']
    assert all(p['reason'] == 'research_owner_invalid_continuation_token' for p in read['pages'].values())


@pytest.mark.parametrize('ref_count', [2, 8])
@pytest.mark.parametrize('failure', ['none', 'transient', 'persistent'])
def test_evidence_and_lane_progress_across_distinct_meetings(
        tmp_path, monkeypatch, action_owner_contract, ref_count, failure):
    from datetime import datetime, timezone
    from services.consultation import meeting_outputs as outputs
    monkeypatch.setenv('PANTHEON_RESEARCH_JWT_SECRET', 'test-action-secret-with-enough-length')
    monkeypatch.setattr(outputs, '_utc_now', lambda: datetime(2026, 11, 21, tzinfo=timezone.utc))
    monkeypatch.setattr(meetings_mod, '_write_meeting_memory', lambda **kw: ([], None))
    owner = action_owner_contract
    series = {'meeting_type': 'MT-1', 'subject_ref': 'pool:persona-alpha'}
    # Unequal lane lengths exercise completed-lane persistence as well as >8
    # tickets with multiple refs. Neither acceptance nor removal drains a lane.
    for lane, state in enumerate(('returned', 'submitted', 'accepted')):
        for i in range(9 + lane):
            tid = 'rt-action-' + f'{lane * 100 + i:032x}'
            refs = []
            for r in range(ref_count):
                aid = f'note-{lane}-{i}-{r}'
                refs.append({'kind': 'research_note', 'artifact_id': aid, 'checksum': aid})
                owner.notes[aid] = {'title': 'Measured deliverable', 'body': 'Actual result',
                    'source_refs': ['facts:source'], '_meeting_digest': aid,
                    '_meeting_binding': {'tenant_id': TENANT, 'ticket_id': tid}}
            owner.tickets[tid] = {'ticket_id': tid, 'tenant_id': TENANT, 'owner': 'persona-alpha',
                'meeting_action': {**series, 'meeting_id': 'origin', 'assignee_kind': 'persona',
                    'deliverable': 'Measure costs', 'acceptance_criterion': 'Compare measured costs'},
                'action_state': state, 'revision': 1, 'updated_at': '2026-11-20T12:00:00Z',
                'evidence_refs': refs}
    send = owner.send
    requests, failed = [], []
    def transport(method, url, headers, body):
        requests.append(urllib.parse.urlsplit(url))
        if url.endswith('/notes/note-1-0-0') and failure != 'none':
            if failure == 'persistent' or not failed:
                failed.append(url)
                return 503, {}
        return send(method, url, headers, body)
    store = ConsultationStore(str(tmp_path / 'fair-scan'))
    watermark = '2026-11-20T00:00:00Z'
    schedule = {'action_review_watermark': watermark}
    seen = [set(), set()]
    for number in range(22):
        meeting = {'tenant_id': TENANT, 'meeting_id': f'evidence-review-{number}',
            'meeting_series': series, 'meeting_code': 'MT-1', 'meeting_type': 'MT-1',
            'topic': 'Review deliverables',
            'chair_persona_id': 'persona-alpha', 'trace_id': 'fair-review', 'rounds': [],
            'participants': [{'persona_id': 'persona-alpha', 'trading': True, 'role': 'chair'}],
            'previous_action_review_at': schedule.get('action_review_watermark'),
            'action_review_scan': schedule.get('action_review_scan') or {},
            'synthesis': {'summary': 'Read actual results; retain pending work',
                          'outcome': 'completed_no_proposal', 'proposals': []}}
        requests.clear()
        read = outputs.read_meeting_actions(meeting, transport=transport)
        assert read['status'] == 'ok' and len(read['items']) <= 3
        assert len([p for p in requests if '/notes/' in p.path]) <= 24
        assert len([p for p in requests if p.path == '/api/research/tickets']) <= 3
        assert len(json.dumps(read).encode()) <= 524288
        assert len(read['evidence']) == ref_count * len(read['items'])
        assert all(e.get('reason') != 'evidence_read_budget_exhausted' for e in read['evidence'])
        seen[number // 11].update(a['action_id'] for a in read['items'])
        for item in read['items']:
            records = [e for e in read['evidence'] if e['action_id'] == item['action_id']]
            available = all(e['status'] == 'ok' for e in records)
            assert item['evidence_available'] == available
            assert all(e.get('content', {}).get('body') == 'Actual result' for e in records if e['status'] == 'ok')
            if not available and item['state'] == 'submitted':
                review = {'action_id': item['action_id'], 'decision': 'accept', 'reason': 'Meets costs',
                    'acceptance_criterion': item['acceptance_criterion'], 'evidence_refs': item['evidence_refs']}
                assert not meetings_mod._validate_adopted_actions(
                    {'action_reviews': [review]}, {'prior_actions': read['items']}, set())[1]
        meeting.update(prior_actions=read['items'], prior_action_evidence=read['evidence'],
            prior_actions_source={k: v for k, v in read.items() if k not in {'items', 'evidence'}})
        meetings_mod._close_persona_meeting(store=store, meeting=meeting,
            spec={'cadence': 'weekly'}, memory_url='unused')
        schedule = store.get_meeting_schedule(TENANT, 'persona-alpha', 'MT-1')
        if number == 8:
            assert schedule['action_review_scan']['completed_lanes'] == ['outstanding']
        if number == 9:
            assert schedule['action_review_scan']['completed_lanes'] == ['outstanding', 'submitted']
        if number == 10:
            assert not schedule['action_review_scan']
            assert schedule['action_review_watermark'] == (watermark if failure != 'none' else '2026-11-21T00:00:00Z')
            # Keep the second clean scan populated as new dispositions; failures
            # instead retry all original obligations with the unchanged watermark.
            for ticket in owner.tickets.values():
                ticket['updated_at'] = '2026-11-22T00:00:00Z'
            monkeypatch.setattr(outputs, '_utc_now', lambda: datetime(2026, 11, 23, tzinfo=timezone.utc))
    assert seen == [set(owner.tickets), set(owner.tickets)]
    assert not schedule['action_review_scan']
    assert schedule['action_review_watermark'] == (watermark if failure == 'persistent' else '2026-11-23T00:00:00Z')
    assert len(failed) == (0 if failure == 'none' else 1 if failure == 'transient' else 2)


@pytest.mark.parametrize('backend', ['jsonl', 'postgres'])
def test_series_checkpoints_replay_existing_schedule_events_without_eviction(tmp_path, monkeypatch, backend):
    """Legacy schedule events already contain every subject's last checkpoint."""
    from services.consultation.store import PostgresConsultationStore
    if backend == 'postgres':
        import sys
        from services.consultation.test_postgres_store import _make_fake_psycopg, _reset_fake_connection
        _reset_fake_connection()
        fake, connection = _make_fake_psycopg()
        monkeypatch.setitem(sys.modules, 'psycopg', fake)
        make_store = lambda: PostgresConsultationStore(dsn='postgresql://component/db', bootstrap=False)
        events = lambda: copy.deepcopy(connection.lifecycle_rows)
    else:
        make_store = lambda: ConsultationStore(str(tmp_path / 'legacy-series'))
        events = lambda: (tmp_path / 'legacy-series' / 'consult_lifecycle_events.jsonl').read_bytes()
    store = make_store()
    expected = []
    for number in range(24):
        checkpoint = {'meeting_series': {'meeting_type': 'MT-2', 'subject_ref': f'strategy:cost-{number}'},
            'action_review_watermark': '2026-11-20T00:00:00Z',
            'action_review_scan': {'started_at': '2026-11-21T00:00:00Z',
                'page_tokens': {'outstanding': f'ticket-{number}'},
                'completed_lanes': ['submitted'], 'evidence_failed': bool(number % 2)}}
        expected.append(checkpoint)
        store.put_meeting_schedule({'tenant_id': TENANT, 'persona_id': 'persona-alpha',
            'meeting_type': 'MT-2', **checkpoint})
    # Attendance writes only cadence; neither it nor many newer subjects may
    # erase an unfinished scan. There is still only one cadence slot.
    store.put_meeting_schedule({'tenant_id': TENANT, 'persona_id': 'persona-alpha',
        'meeting_type': 'MT-2', 'next_due_at': '2026-11-24T00:00:00Z'})
    before = events()
    store = make_store()
    assert len(store.list_meeting_schedules()) == 1
    for checkpoint in expected:
        series = checkpoint['meeting_series']
        assert store.get_meeting_schedule(TENANT, 'persona-alpha', 'MT-2', meeting_series=series) == checkpoint
        for tenant, chair, code in [('foreign', 'persona-alpha', 'MT-2'),
                                    (TENANT, 'persona-beta', 'MT-2'), (TENANT, 'persona-alpha', 'MT-1')]:
            assert store.get_meeting_schedule(tenant, chair, code, meeting_series=series) is None
    assert store.get_meeting_schedule(TENANT, 'persona-alpha', 'MT-2',
        meeting_series={'meeting_type': 'MT-2', 'subject_ref': 'strategy:unknown'}) is None
    assert events() == before  # Replay and exact-key reads perform no migration/write.


@pytest.mark.parametrize('restart', [False, True])
@pytest.mark.parametrize('http_execution', [False, True])
@pytest.mark.parametrize('same_chair', [False, True])
def test_interleaved_series_keep_owned_review_state(
        tmp_path, monkeypatch, action_owner_contract, restart, http_execution, same_chair):
    from datetime import datetime, timezone
    from services.consultation import meeting_outputs as outputs
    monkeypatch.setenv('PANTHEON_RESEARCH_JWT_SECRET', 'test-action-secret-with-enough-length')
    monkeypatch.setattr(meetings_mod, '_write_meeting_memory', lambda **kw: ([], None))
    monkeypatch.setattr(meetings_mod, 'build_participant_pre_read', lambda **kw: [])
    monkeypatch.setattr(meetings_mod, '_participant_action_capability', lambda tenant, pid:
        {'persona_id': pid, 'status': 'advisory_only', 'can_receive_persona_action': False})
    chairs = ('persona-alpha', 'persona-alpha' if same_chair else 'persona-beta')
    subjects = ('strategy:cost-a', 'strategy:cost-b') if same_chair else tuple(f'pool:{p}' for p in chairs)
    code = 'MT-2' if same_chair else 'MT-1'
    series = {ref: {'meeting_type': code, 'subject_ref': ref} for ref in subjects}
    owner = action_owner_contract
    expected = {ref: set() for ref in subjects}
    for lane, (chair, ref) in enumerate(zip(chairs, subjects)):
        for state in ('open', 'accepted'):
            for i in range(3):
                tid = 'rt-action-' + f'{lane * 100 + (10 if state == "accepted" else 0) + i:032x}'
                owner.tickets[tid] = {'ticket_id': tid, 'tenant_id': TENANT, 'owner': chair,
                    'meeting_action': {**series[ref],
                        'meeting_id': 'origin', 'assignee_kind': 'persona',
                        'deliverable': 'Compare costs', 'acceptance_criterion': 'Measured costs'},
                    'action_state': state, 'revision': 0, 'evidence_refs': [],
                    'updated_at': '2026-11-20T12:00:00Z'}
                expected[ref].add(tid)
    store_dir = str(tmp_path / 'interleaved')
    store = ConsultationStore(store_dir)
    for lane, (chair, ref) in enumerate(zip(chairs, subjects)):
        store.put_meeting_schedule({'tenant_id': TENANT, 'persona_id': chair, 'meeting_type': code,
            'meeting_series': series[ref],
            'action_review_watermark': f'2026-11-{19 + lane}T00:00:00Z', 'action_review_scan': {}})
    seen = {ref: [] for ref in subjects}
    if http_execution:
        from fastapi.testclient import TestClient
        from services.consultation import workflow_executor as executor
        monkeypatch.setenv('CONSULTATION_AUTH_REQUIRED', 'true')
        monkeypatch.setenv('CONSULTATION_SERVICE_TOKEN', SERVICE_TOKEN)
        monkeypatch.setenv('PANTHEON_TENANT_ID', TENANT)
        monkeypatch.setattr(consultation_main, 'workflow_state', None)
        client = TestClient(consultation_main.app)
        headers = {'Authorization': f'Bearer {SERVICE_TOKEN}', 'X-Pantheon-Tenant-Id': TENANT,
                   'X-Pantheon-Service-Actor': ACTOR}
        def api_get(config, path):
            reply = client.get(path, headers=headers)
            assert reply.status_code == 200, reply.text
            return reply.json()
        def api_post(config, path, payload):
            reply = client.post(path, headers=headers, json=payload)
            assert reply.status_code in {200, 201, 202}, reply.text
            return reply.json()
        monkeypatch.setattr(executor, '_api_get', api_get)
        monkeypatch.setattr(executor, '_api_post', api_post)
    class Provider:
        def obtain(self, *, tenant_id, request, participant_scope):
            ctx = request['meeting']
            assert ctx['prior_actions'] and all(a['action_id'] in expected[current_ref] for a in ctx['prior_actions'])
            cid = request['case_file']['groups']['facts']['items'][0]['item_id']
            event = {'claims': [{'claim': 'Review original obligations', 'evidence_refs': [cid]}],
                     'summary_evidence_refs': [cid]}
            if ctx['round'] == 'synthesis':
                event['synthesis'] = {'summary': 'Retain the original work', 'outcome': 'completed_no_proposal',
                    'proposals': [], 'actions': [], 'no_action_reason': 'Original deliverables remain assigned'}
            else:
                event.update(stance='approve', confidence=.8, citations=[cid], accepted=[], disputed=[],
                             changed=False, proposals=[])
            return SimpleNamespace(event_content=event, summary='Review original obligations', evidence=[],
                                   confidence=.8, recommendation='approve')
    with outputs.output_routing_context(transport=owner.send, store_path=tmp_path / 'dispatch.json'):
        for number, (current_chair, current_ref) in enumerate(tuple(zip(chairs, subjects)) * 3):
            if restart:
                store = ConsultationStore(store_dir)
            peer = 'persona-risk-officer' if same_chair else next(p for p in chairs if p != current_chair)
            other_ref = next(ref for ref in subjects if ref != current_ref)
            other_chair = chairs[subjects.index(other_ref)]
            peer_before = store.get_meeting_schedule(TENANT, other_chair, code, meeting_series=series[other_ref])
            monkeypatch.setattr(outputs, '_utc_now', lambda: datetime(2026, 11, 21, number, tzinfo=timezone.utc))
            meeting, _ = meetings_mod.convene_persona_meeting(store=store, workflow_state=None, tenant_id=TENANT,
                payload={'meeting_type': code, 'topic': 'Review pool alternatives',
                         'iso_week': f'2026-W{47 + number}',
                         'convened_by': {'kind': 'persona', 'id': current_chair},
                         'participants': [current_chair, peer], 'subject_ref': current_ref},
                idempotency_key=f'interleaved-{number}')
            execution_store = store
            if http_execution:
                monkeypatch.setattr(consultation_main, 'store', store)
                execution_store = executor._HttpMeetingStoreProxy(ExecutorConfig.from_env(), meeting['meeting_id'], 'review')
            closed = meetings_mod.execute_persona_meeting(store=execution_store, meeting_id=meeting['meeting_id'],
                                                          tenant_id=TENANT, provider=Provider())
            assert closed['status'] == 'closed' and len(closed['rounds']) == 2
            assert not closed['rejected_positions']
            seen[current_ref].extend(a['action_id'] for a in closed['prior_actions'])
            peer_after = store.get_meeting_schedule(TENANT, other_chair, code, meeting_series=series[other_ref])
            assert peer_after == peer_before
            assert store.get_meeting_schedule(TENANT, current_chair, code)['last_run_at'] == closed['closed_at']
            if not same_chair:
                assert store.get_meeting_schedule(TENANT, peer, code)['last_run_at'] == closed['closed_at']
    store = ConsultationStore(store_dir)
    for lane, (chair, ref) in enumerate(zip(chairs, subjects)):
        assert set(seen[ref]) == expected[ref] and len(seen[ref]) == 6
        saved = store.get_meeting_schedule(TENANT, chair, code, meeting_series=series[ref])
        assert saved['meeting_series'] == series[ref]
        assert not saved['action_review_scan']
        assert saved['action_review_watermark'] == f'2026-11-21T0{lane}:00:00Z'


@pytest.mark.parametrize('convener', [
    {'kind': 'persona', 'id': 'persona-alpha'},
    {'kind': 'operator', 'id': 'op-1'},
])
@pytest.mark.parametrize('assignee', ['operator', 'invented-human-user'])
def test_human_action_normal_adapter_two_round_close_targets_existing_inbox(
        stack, tmp_path, monkeypatch, convener, assignee):
    """No approval/capital prerequisite: a human must obtain a real document."""
    monkeypatch.setenv('PANTHEON_RESEARCH_JWT_SECRET', 'test-action-secret-with-enough-length')
    monkeypatch.setattr(meetings_mod, '_write_meeting_memory', lambda **kwargs: ([], None))
    monkeypatch.setattr(meetings_mod, 'build_participant_pre_read', lambda **kwargs: [])
    requirement = 'Only a human can obtain the broker-signed historical fee statement.'
    raw = {**_adopted_action(), 'assignee_kind': 'human', 'assignee': assignee,
           'deliverable': 'Provide the broker-signed historical fee statement',
           'acceptance_criterion': 'Statement covers the full prior quarter and lists every fee',
           'human_requirement': requirement}
    stack['double'].custom_proposals = []
    stack['double'].custom_actions = [raw]
    created = open_persona_meeting(
        base_url=stack['api_url'], token=SERVICE_TOKEN, tenant_id=TENANT,
        meeting_type='MT-1', topic='Resolve missing historical fee evidence',
        convened_by=convener, subject_ref='persona:persona-alpha',
        participants=['persona-alpha', 'persona-risk-officer'],
        idempotency_key='human-work', iso_week='2026-W47')
    cfg = stack['cfg']
    provider = HttpContributionProvider(endpoint=cfg.provider_url, bearer_token=cfg.provider_token,
        service_actor=cfg.provider_service_actor, timeout_seconds=cfg.timeout_seconds)
    owner = _ActionResearchBoundary()
    with output_routing_context(transport=owner, store_path=tmp_path / 'human-dispatch.json'):
        meeting = meetings_mod.execute_persona_meeting(store=consultation_main.store,
            meeting_id=created['meeting_id'], tenant_id=TENANT, provider=provider)
        calls_before_retry = len(stack['double'].calls)
        meetings_mod.execute_persona_meeting(store=consultation_main.store,
            meeting_id=created['meeting_id'], tenant_id=TENANT, provider=provider)
    assert meeting['status'] == 'closed' and len(meeting['rounds']) == 2
    assert meeting['synthesis']['proposals'] == []
    assert len(stack['double'].calls) == calls_before_retry == 5
    prompt = stack['double'].calls[-1]['prompt']
    assert 'existing tenant operator inbox' in prompt and 'Do not invent usernames' in prompt
    if assignee != 'operator':
        assert not owner.tickets and meeting['actions'] == []
        assert meeting['synthesis']['rejected_actions'] == [
            {'index': 0, 'reason': 'human_assignee_unresolvable_use_operator_inbox'}]
        return
    ticket, = owner.tickets.values()
    action, = meeting['actions']
    assert action['assignee'] == ticket['owner'] == 'operator'
    assert action['human_requirement'] == ticket['description'] == requirement
    assert ticket['meeting_action']['deliverable'] == raw['deliverable']
    assert ticket['meeting_action']['acceptance_criterion'] == raw['acceptance_criterion']
    assert ticket['meeting_action']['due_at'] == raw['due_at']
    assert ticket['meeting_action']['assignee_kind'] == 'human'
    assert len([r for r in owner.requests if r[0] == 'POST']) == 1


@pytest.mark.parametrize('requirement', [None, '', '   ', ['invented text'], 23, 'x' * 2001])
def test_human_action_requires_bounded_specific_reason_and_never_dispatches(
        tmp_path, requirement):
    from services.consultation.meeting_outputs import dispatch_meeting_actions
    raw = {**_adopted_action(), 'assignee_kind': 'human', 'assignee': 'operator',
           'human_requirement': requirement}
    meeting = {'tenant_id': TENANT, 'meeting_id': 'invalid-human',
               'meeting_series': {'meeting_type': 'MT-1', 'subject_ref': 'pool:persona-alpha'}}
    actions, _, rejected = meetings_mod._validate_adopted_actions(
        {'actions': [raw]}, meeting, {'facts:source'})
    assert actions == [] and rejected == [
        {'index': 0, 'reason': 'human_requirement_required_or_unbounded'}]
    # Older frozen producers also fail visibly without issuing an owner request.
    meeting['synthesis'] = {'actions': [raw]}
    def no_owner_call(*args):
        pytest.fail('Invalid human assignment must not reach the owner')
    pending, = dispatch_meeting_actions(meeting, transport=no_owner_call,
                                       store_path=tmp_path / 'ledger.json')
    assert pending['state'] == 'dispatch_pending' and 'ticket_id' not in pending
    assert pending['dispatch_error'] == 'human_requirement_required_or_unbounded'


def test_frozen_invented_human_target_is_unresolved_without_dispatch(tmp_path):
    from services.consultation.meeting_outputs import dispatch_meeting_actions
    meeting = {'tenant_id': TENANT, 'meeting_id': 'old-producer',
        'synthesis': {'actions': [{**_adopted_action(), 'assignee_kind': 'human',
            'assignee': 'fabricated-user', 'human_requirement': 'Obtain a signed source document'}]}}
    def no_owner_call(*args):
        pytest.fail('Invented human target must not receive a ticket')
    pending, = dispatch_meeting_actions(meeting, transport=no_owner_call,
                                       store_path=tmp_path / 'ledger.json')
    assert pending['state'] == 'dispatch_pending' and 'ticket_id' not in pending
    assert pending['dispatch_error'] == 'human_assignee_unresolvable_use_operator_inbox'


def test_human_reason_frozen_across_lost_receipt_real_owner_create(
        tmp_path, monkeypatch, action_owner_contract):
    from services.consultation.meeting_outputs import dispatch_meeting_actions
    monkeypatch.setenv('PANTHEON_RESEARCH_JWT_SECRET', 'test-action-secret-with-enough-length')
    requirement = 'Only a human can obtain the broker-signed historical fee statement.'
    raw = {**_adopted_action(), 'assignee_kind': 'human', 'assignee': ' operator ',
           'human_requirement': ' ' + requirement + ' '}
    meeting = {'tenant_id': TENANT, 'meeting_id': 'human-receipt-retry',
        'meeting_series': {'meeting_type': 'MT-1', 'subject_ref': 'pool:persona-alpha'}}
    actions, _, rejected = meetings_mod._validate_adopted_actions(
        {'actions': [raw]}, meeting, {'facts:source'})
    assert not rejected
    meeting['synthesis'] = {'actions': actions}
    calls = []
    def transport(*args):
        calls.append(copy.deepcopy(args[3]))
        code, reply = action_owner_contract.send(*args)
        return (200, {}) if len(calls) == 1 else (code, reply)
    path = tmp_path / 'ledger.json'
    pending, = dispatch_meeting_actions(meeting, transport=transport, store_path=path)
    assert pending['state'] == 'dispatch_pending' and 'ticket_id' not in pending
    # A process restart reloads both frozen meeting state and its ledger.
    restored = json.loads(json.dumps(meeting))
    delivered, = dispatch_meeting_actions(restored, transport=transport, store_path=path)
    assert delivered['ticket_id'] and delivered['state'] == 'open'
    dispatch_meeting_actions(restored, transport=transport, store_path=path)
    assert len(calls) == 2 and calls[0] == calls[1]
    ticket, = action_owner_contract.tickets.values()
    assert ticket['owner'] == 'operator' and ticket['description'] == requirement
    assert ticket['meeting_action']['deliverable'] == raw['deliverable']
    assert ticket['meeting_action']['acceptance_criterion'] == raw['acceptance_criterion']
    assert 'human_requirement' not in ticket['meeting_action']  # existing RW-01 schema


def test_human_action_owner_receipt_must_preserve_human_requirement(tmp_path, monkeypatch):
    from services.consultation.meeting_outputs import dispatch_meeting_actions
    monkeypatch.setenv('PANTHEON_RESEARCH_JWT_SECRET', 'test-action-secret-with-enough-length')
    requirement = 'Only a human can obtain the broker-signed historical fee statement.'
    meeting = {'tenant_id': TENANT, 'meeting_id': 'missing-human-reason',
        'meeting_series': {'meeting_type': 'MT-1', 'subject_ref': 'pool:persona-alpha'},
        'synthesis': {'actions': [{**_adopted_action(), 'assignee': 'operator',
            'assignee_kind': 'human', 'human_requirement': requirement}]}}
    owner = _ActionResearchBoundary()
    def transport(*args):
        status, receipt = owner(*args)
        receipt['description'] = 'Generic task acknowledgement'
        return status, receipt
    pending, = dispatch_meeting_actions(meeting, transport=transport, store_path=tmp_path / 'ledger.json')
    assert pending['state'] == 'dispatch_pending' and 'ticket_id' not in pending
    assert pending['dispatch_error'] == 'research_owner_human_requirement_mismatch'
    delivered, = dispatch_meeting_actions(meeting, transport=owner, store_path=tmp_path / 'ledger.json')
    assert delivered['ticket_id'] and delivered['dispatch_error'] is None
    assert len(owner.tickets) == 1


@pytest.mark.parametrize('action_owner_contract', ['real_owner'], indirect=True)
def test_human_assignment_reaches_authenticated_tenant_operator_at_real_owner(
        tmp_path, monkeypatch, action_owner_contract):
    """The inbox target resolves through existing RW-01 roles, not a directory."""
    from services.consultation.meeting_outputs import dispatch_meeting_actions, action_snapshot
    monkeypatch.setenv('PANTHEON_RESEARCH_JWT_SECRET', 'test-action-secret-with-enough-length')
    requirement = 'Only a human can obtain the signed external statement for the prior quarter.'
    meeting = {'tenant_id': TENANT, 'meeting_id': 'human-role-delivery',
        'meeting_series': {'meeting_type': 'MT-1', 'subject_ref': 'pool:persona-alpha'},
        'synthesis': {'actions': [{**_adopted_action(), 'assignee': 'operator',
            'assignee_kind': 'human', 'human_requirement': requirement}]}}
    delivered, = dispatch_meeting_actions(meeting, transport=action_owner_contract.send,
                                         store_path=tmp_path / 'dispatch.json')
    assert delivered['state'] == 'open'
    owner = action_owner_contract
    # This actual person differs from both the model target and the convener.
    operator = SimpleNamespace(tenant_id=TENANT, actor_id='signed-in-alice', roles={'operator'})
    page = owner.list_research_tickets(context=operator, meeting_linked=True, action_states=['open'])
    row, = page['data']
    assert row['ticket_id'] == delivered['ticket_id'] and row['required_actor'] == 'operator'
    ticket = owner.get_research_ticket(row['ticket_id'], context=operator)
    assert ticket['description'] == requirement
    assert row['meeting_action']['deliverable'] == ticket['meeting_action']['deliverable']
    assert row['meeting_action']['acceptance_criterion'] == ticket['meeting_action']['acceptance_criterion']
    for ctx in (
        SimpleNamespace(tenant_id=TENANT, actor_id='unrelated-viewer', roles={'viewer'}),
        SimpleNamespace(tenant_id=TENANT, actor_id='invented-human-user', roles={'human'}),
        SimpleNamespace(tenant_id='foreign-tenant', actor_id='signed-in-alice', roles={'operator'}),
    ):
        assert owner.list_research_tickets(context=ctx, meeting_linked=True)['data'] == []
        assert owner.get_research_ticket(row['ticket_id'], context=ctx) is None
    # Simulate the owner's later disposition; exercise its real projection.
    stored = owner.tickets[row['ticket_id']]
    stored.update(action_state='returned', revision=2, review_decision={
        'decision': 'return', 'reason': 'Signed statement omits March fees', 'review_source': 'next-meeting'})
    returned, = owner.list_research_tickets(context=operator, meeting_linked=True,
                                          action_states=['returned'])['data']
    assert returned['review_decision']['reason'] == 'Signed statement omits March fees'
    assert action_snapshot(returned)['review_decision'] == returned['review_decision']
    assert owner.get_research_ticket(row['ticket_id'], context=operator)['description'] == requirement


@pytest.fixture
def persona_action_policy():
    """Compose the real Persona policy from its frozen source until it merges."""
    import importlib.util
    import os
    source = os.getenv('MEETING_ACTION_PERSONA_POLICY_SOURCE')
    if source:
        spec = importlib.util.spec_from_file_location('meeting_contract_persona_policy', source)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    else:
        from services.control_plane.persona import persona_policy_resolver as module
    if not hasattr(module, 'evaluate_participant_action_capability'):
        pytest.skip('Persona policy not integrated; supply its frozen source explicitly')
    return module.evaluate_participant_action_capability


@pytest.mark.parametrize('feed_access', [False, True])
def test_data_aware_chair_three_personas_normal_engine(
        tmp_path, monkeypatch, action_owner_contract, persona_action_policy, feed_access):
    """Real policy/create/list, isolated owner persistence and HTTP transport; no hosted claim."""
    from contextlib import closing
    from io import BytesIO
    import jsonschema
    from consultation_provider import _meeting_event_content_schema
    from services.consultation.meeting_outputs import action_snapshot
    from services.persona.harness import HarnessSurface, HarnessVersion, digest
    pids = ('persona-2448c7053e33ea48da47', 'persona-6507750312870d03a241', 'persona-ee7112a829d38b9135ac')
    surface = HarnessSurface.model_validate({
        'identity_inputs': ['Cite supplied evidence'], 'guardrails': {'allowed_tools': ['read'],
        'forbidden_solo_actions': ['admit', 'harness_patch']}, 'consult_policy': [],
        'tool_guidance': {'read': 'Read governed evidence'}, 'skills': [],
        'workflow': {'cadence_seconds': 86400, 'steps': ['observe', 'orient', 'decide', 'act', 'learn']},
        'cycle_thresholds': {'minimum_evidence_count': 1, 'minimum_confidence': .8},
        'memory_retrieval_policy': {'scopes': ['own_private', 'institutional'], 'max_items': 8, 'minimum_confidence': .8}})
    personas = {}
    for pid in pids:
        version = HarnessVersion(persona_id=pid, version=1, parent_version=None, patch_id=None,
            created_at='2026-11-23T00:00:00Z', created_by='operator', surface=surface,
            content_digest=digest(surface.model_dump(mode='json')))
        personas[pid] = {'persona_id': pid, 'tenant_id': TENANT, 'status': 'active', 'lifecycle_state': 'paper_owner',
            'required_data_sources': [
                {'dataset': 'tw_price_daily', 'market': 'TW', 'cadence': 'daily', 'source_class': 'live_pull',
                 'connector_candidates': ['tw-twse-tpex-official-market'],
                 'policy_gates': ['require_connector_approved', 'require_schedule_active', 'require_source_health_ok', 'no_live_capital']},
                {'dataset': 'tw_broker_top', 'market': 'TW', 'cadence': 'daily', 'source_class': 'live_pull',
                 'connector_candidates': ['tw-finmind-broker-daily-report'],
                 'policy_gates': ['require_connector_approved', 'require_schedule_active', 'require_source_health_ok', 'no_live_capital']}],
            'metadata': {'persona_harness': {'active_version': 1, 'versions': [version.model_dump(mode='json')]}}}
    seen = []
    original_urlopen = urllib.request.urlopen
    def capability_http(req, timeout):
        parsed = urllib.parse.urlsplit(req.full_url)
        if parsed.netloc != 'persona-owner':
            return original_urlopen(req, timeout=timeout)
        assert req.get_header('X-tenant-id') == TENANT
        if parsed.path.endswith('/strategy-pool'):
            return closing(BytesIO(json.dumps({'pool_version': 1, 'members': []}).encode()))
        if not parsed.path.endswith('/action-capability'):
            raise urllib.error.HTTPError(req.full_url, 404, 'not supplied by this owner fixture', {}, None)
        pid = parsed.path.split('/')[3]
        q = urllib.parse.parse_qs(parsed.query)
        seen.append((pid, q.get('required_data', [])))
        snapshot = {'persona_id': pid, 'capabilities': ['persona_opinion'],
            'allowed_capabilities': ['persona_opinion'], 'effective_tools': [], 'effective_workflows': [],
            'metadata': {'canonical_write_authority': 'persona_service', 'scope': 'paper_advisory',
                         'accessible_data': ['tw_price_daily', 'tw_broker_top'] if feed_access else []}}
        receipt = persona_action_policy(personas[pid], snapshot, run_purpose=q['run_purpose'][0],
                                       requirements={'required_data': q.get('required_data', [])})
        return closing(BytesIO(json.dumps(receipt).encode()))
    monkeypatch.setenv('PANTHEON_PERSONA_API_URL', 'http://persona-owner')
    monkeypatch.setenv('PANTHEON_RESEARCH_JWT_SECRET', 'test-action-secret-with-enough-length')
    monkeypatch.setattr(urllib.request, 'urlopen', capability_http)
    monkeypatch.setattr(meetings_mod, '_write_meeting_memory', lambda **kwargs: ([], None))
    monkeypatch.setattr(meetings_mod, 'build_participant_pre_read', lambda **kwargs: [])
    calls = []
    class Provider:
        def obtain(self, *, tenant_id, request, participant_scope):
            ctx = request['meeting']
            calls.append(copy.deepcopy(request))
            cid = request['case_file']['groups']['facts']['items'][0]['item_id']
            event = {'claims': [{'claim': 'Use supplied evidence', 'evidence_refs': [cid]}], 'summary_evidence_refs': [cid]}
            if ctx['round'] == 'synthesis':
                actions = []
                for index, pid in enumerate(pids):
                    note = {**_adopted_action(cid, f'note-{index}'), 'assignee': pid}
                    if index == 1:
                        note['required_data'] = []  # distinct from omission; retained in the owner origin
                    actions.extend([note, {**_adopted_action(cid, f'feed-{index}'), 'assignee': pid,
                        'required_data': [' tw_price_daily ', 'tw_broker_top'], 'deliverable': 'Compare licensed market feeds'}])
                # Only an explicitly human-only external document goes to the existing operator inbox.
                actions.append({**_adopted_action(cid, 'signed-statement'), 'assignee': 'operator',
                    'assignee_kind': 'human', 'human_requirement': 'Only the operator can obtain the broker-signed statement',
                    'required_data': ['broker_signed_statement']})
                # Raw model whitespace is normalized by adoption; schema accepts valid bounded IDs.
                event['synthesis'] = {'summary': 'Adopt specific feasible work', 'outcome': 'completed_no_proposal',
                                      'proposals': [], 'actions': actions}
            else:
                event.update(stance='approve', confidence=.8, citations=[cid], accepted=[], disputed=[], changed=False, proposals=[])
            jsonschema.validate(event, _meeting_event_content_schema(ctx))
            return SimpleNamespace(event_content=event, summary='Use supplied evidence', evidence=[], confidence=.8, recommendation='approve')
    store = ConsultationStore(str(tmp_path / 'data-meetings'))
    meeting = meetings_mod.convene_persona_meeting(store=store, workflow_state=None, tenant_id=TENANT,
        persona_directory=personas, payload={'meeting_type': 'MT-1', 'topic': 'Evaluate actual deliverable data needs',
            'convened_by': {'kind': 'persona', 'id': pids[0]}, 'participants': list(pids),
            'subject_ref': 'persona:' + pids[0]}, idempotency_key='data-aware')[0]
    owner = action_owner_contract
    bodies, lost_bodies = [], []
    def lost_receipt_once(method, url, headers, body):
        status, reply = owner.send(method, url, headers, body)
        if method == 'POST':
            bodies.append(copy.deepcopy(body))
            if body['meeting_action'].get('required_data') and not lost_bodies:
                lost_bodies.append(copy.deepcopy(body))
                return 503, {}  # data-aware owner origin committed before the reply was lost
        return status, reply
    with output_routing_context(transport=lost_receipt_once, store_path=tmp_path / 'data-dispatch.json'):
        from services.consultation.meeting_outputs import MeetingOutputRoutingError
        with pytest.raises(MeetingOutputRoutingError, match='frozen decision retained'):
            meetings_mod.execute_persona_meeting(store=store, meeting_id=meeting['meeting_id'], tenant_id=TENANT, provider=Provider())
        requests_at_freeze = (len(calls), len(seen))
        result = meetings_mod.execute_persona_meeting(store=store, meeting_id=meeting['meeting_id'], tenant_id=TENANT, provider=Provider())
        assert (len(calls), len(seen)) == requests_at_freeze
    assert result['status'] == 'closed' and len(result['rounds']) == 2
    assert result['synthesis']['proposals'] == []
    assert lost_bodies and bodies[-1] == lost_bodies[0]  # exact data-aware request on ambiguous retry
    assert len(owner.tickets) == (7 if feed_access else 4)
    for row in owner.tickets.values():
        origin = row['meeting_action']
        if origin['source_action_id'].startswith('feed-'):
            assert origin['required_data'] == ['tw_price_daily', 'tw_broker_top']
        elif origin['source_action_id'] == 'note-1':
            assert origin['required_data'] == []
        elif origin['source_action_id'].startswith('note-'):
            assert 'required_data' not in origin
        else:
            assert row['owner'] == 'operator' and row['description'].startswith('Only the operator')
        projection = action_snapshot(row)
        assert projection.get('required_data') == origin.get('required_data')
    for pid in pids:
        assert seen.count((pid, ['tw_price_daily', 'tw_broker_top'])) == 1
        assert seen.count((pid, [])) == 2  # pre-read and independent evidence-only adoption
    rejected = result['synthesis']['rejected_actions']
    assert len(rejected) == (0 if feed_access else 3)
    for row in rejected:
        assert row['required_data'] == ['tw_price_daily', 'tw_broker_top']
        assert row['capability_status'] == 'restricted_data'
        assert row['capability_reason'] == 'deliverable_data_not_accessible'


@pytest.mark.parametrize('datasets', ['tw_price_daily', None, [''], ['x'] * 9, ['x', ' x '], ['x' * 257], ['研' * 256, '究' * 256, '員' * 256]])
def test_adoption_invalid_data_never_calls_capability_owner(monkeypatch, datasets):
    def unexpected(*args):
        pytest.fail('invalid requirement must not reach capability owner')
    monkeypatch.setattr(meetings_mod, '_participant_action_capability', unexpected)
    meeting = {'tenant_id': TENANT, 'meeting_series': {'meeting_type': 'MT-1', 'subject_ref': 'pool:persona-alpha'}}
    actions, _, rejected = meetings_mod._validate_adopted_actions(
        {'actions': [{**_adopted_action(), 'required_data': datasets}]}, meeting, {'facts:source'})
    assert not actions and rejected == [{'index': 0, 'reason': 'invalid_required_data'}]


@pytest.mark.parametrize('bad_requirement', [None, ['other-feed']])
def test_data_aware_dispatch_does_not_freeze_changed_or_missing_requirement(tmp_path, monkeypatch, bad_requirement):
    from services.consultation.meeting_outputs import dispatch_meeting_actions
    monkeypatch.setenv('PANTHEON_RESEARCH_JWT_SECRET', 'test-action-secret-with-enough-length')
    meeting = {'tenant_id': TENANT, 'meeting_id': 'data-receipt',
        'meeting_series': {'meeting_type': 'MT-1', 'subject_ref': 'pool:persona-alpha'},
        'synthesis': {'actions': [{**_adopted_action(), 'required_data': ['tw_price_daily']}]}}
    owner = _ActionResearchBoundary()
    requests = []
    def transport(method, url, headers, body):
        requests.append(copy.deepcopy(body))
        status, reply = owner(method, url, headers, body)
        if len(requests) == 1:
            if bad_requirement is None:
                reply['meeting_action'].pop('required_data')
            else:
                reply['meeting_action']['required_data'] = bad_requirement
        return status, reply
    ledger = tmp_path / 'data-receipt.json'
    first = dispatch_meeting_actions(meeting, transport=transport, store_path=ledger)[0]
    assert first['state'] == 'dispatch_pending' and not first.get('ticket_id')
    assert first['dispatch_error'] == 'research_owner_missing_or_invalid_ticket_receipt'
    second = dispatch_meeting_actions(meeting, transport=transport, store_path=ledger)[0]
    assert second['ticket_id'] == 'owner-issued-ticket-1' and second['dispatch_error'] is None
    dispatch_meeting_actions(meeting, transport=transport, store_path=ledger)
    assert requests[0] == requests[1] and len(requests) == 2 and len(owner.tickets) == 1
