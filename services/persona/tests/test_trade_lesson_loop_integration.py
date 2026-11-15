"""Trade lesson loop, end to end over real HTTP between the real owners.

Real: persona owner app (pattern review route, reflection pipeline, lesson registrar, lesson-rule
route, harness owner and converter), Memory app (candidate store, risk review, paper endorsement,
persona memory store, retrieve), the case-file composer and its HTTP client, and the daily-cycle
``LessonCycle`` + ``PersonaCycleController`` hook. Scripted: the persona models (the reflection
provider and the risk persona's answer) and the trade journal owner (telemetry) as a small HTTP
fixture serving the telemetry episode routes. Not hosted-performance evidence.
"""
from __future__ import annotations

import json
import socket
import threading
import time
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.testclient import TestClient

from services.knowledge import case_file as case_files
from services.persona.consult_triggers import RISK_PERSONA_ID
from services.persona.lesson_cycle import LessonCycle
from services.persona.lesson_governance import MemoryLessonRegistrar
from services.persona.test_harness import BASE, SECRET, headers
from services.persona.trade_pattern_review import telemetry_episode_lister
from services.persona.write_owner import CreatePersonaRequest, PersistentPersonaOwner, create_app
from services.runtime_auth_inbound import encode_jwt_hs256

TENANT, PERSONA = "tenant-a", "p-alpha"
SERVICE_TOKEN, TELEMETRY_TOKEN = "persona-owner-service-token", "telemetry-service-token"
EP1, EP2 = str(uuid.uuid4()), str(uuid.uuid4())
PERSONA_SESSION = ("researcher", "persona_session")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _serve(app: Any) -> Iterator[str]:
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
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


def _journal_app(rows: dict[str, dict[str, Any]]) -> FastAPI:
    """The trade journal owner's episode routes (telemetry shape); service-token authenticated."""
    app = FastAPI()

    def check(authorization: str | None, tenant: str | None) -> None:
        if authorization != f"Bearer {TELEMETRY_TOKEN}" or not tenant:
            raise HTTPException(401, "service credential required")

    @app.get("/api/telemetry/trade-episodes")
    def listing(persona_id: str = Query(...), limit: int = 100, cursor: str | None = None,
                authorization: str | None = Header(None), tenant: str | None = Header(None, alias="X-Tenant-Id")):
        check(authorization, tenant)
        return {"projections": [r for r in rows.values() if r["persona_id"] == persona_id and r["tenant_id"] == tenant],
                "next_cursor": None}

    @app.get("/api/telemetry/trade-episodes/{episode_id}")
    def one(episode_id: str, authorization: str | None = Header(None), tenant: str | None = Header(None, alias="X-Tenant-Id")):
        check(authorization, tenant)
        row = rows.get(episode_id)
        if row is None or row["tenant_id"] != tenant:
            raise HTTPException(404, "not found")
        return row

    return app


class _Provider:
    """The persona's reflection model: one execution lesson per pattern review."""
    name, model = "scripted", "fixture"

    def reflect(self, *, facts: dict, trigger: str) -> dict:
        return {"expected_vs_actual": {"thesis": "supported", "entry_quality": "good"}, "attribution": "process",
                "counterfactuals": [{"alternative_action": "wait", "estimated_impact": "higher", "assumptions": "same"}],
                "lesson_candidates": [{"scope": "execution", "proposed_change": "enter on pullbacks to the opening range",
                                       "confidence": 0.8}],
                "mistakes": [], "what_worked": [], "unknowns": [], "followups": []}


class _RiskPersona:
    """The risk persona's own agent turn; records what it was asked and answers as scripted."""

    def __init__(self, verdict: str = "approve") -> None:
        self.verdict, self.asked = verdict, []

    def __call__(self, persona_id: str, prompt: str):
        self.asked.append((persona_id, prompt))
        return SimpleNamespace(status="completed", persona_id=persona_id,
                               decision=json.dumps({"verdict": self.verdict, "reason": "no control is weakened"}))


def _episode(episode_id: str, **over: Any) -> dict[str, Any]:
    return {"trade_episode_id": episode_id, "persona_id": PERSONA, "tenant_id": TENANT, "status": "closed",
            "strategy_id": "trend", "instrument_id": "2330.TW", "opened_at": "2026-11-10T01:00:00Z",
            "realized_pnl": 12.5, **over}


@pytest.fixture
def stack(tmp_path, monkeypatch):
    from services.memory import main as memory_main

    journal = {EP1: _episode(EP1), EP2: _episode(EP2, opened_at="2026-11-11T01:00:00Z")}
    for name, value in {
        "PANTHEON_MEMORY_AUTH_MODE": "strict", "PANTHEON_MEMORY_JWT_SECRET": SECRET,
        "PANTHEON_MEMORY_AUTHZ_MODE": "local", "PERSONA_AUTH_MODE": "strict", "PERSONA_JWT_SECRET": SECRET,
        "PANTHEON_PERSONA_SERVICE_TOKEN": SERVICE_TOKEN, "PANTHEON_TELEMETRY_SERVICE_TOKEN": TELEMETRY_TOKEN,
        "PANTHEON_TRADE_LESSON_CANDIDATE_STORE": str(tmp_path / "lessons.json"),
        "PANTHEON_PERSONA_MEMORY_STORE": str(tmp_path / "persona_memory.json"),
        "PANTHEON_MEMORY_STORE": str(tmp_path / "institutional.json"),
        "PANTHEON_MEMORY_DATA_DIR": str(tmp_path / "memory"),
        "PERSONA_LESSON_RULE_STORE_BACKEND": "json", "PERSONA_LESSON_RULE_STORE_PATH": str(tmp_path / "rules.json"),
    }.items():
        monkeypatch.setenv(name, value)
    for fn in (memory_main._candidate_store, memory_main._persona_store, memory_main._store):
        if hasattr(fn, "cache_clear"):
            fn.cache_clear()
    with _serve(_journal_app(journal)) as telemetry_url:
        monkeypatch.setenv("PANTHEON_TELEMETRY_API_URL", telemetry_url)
        with _serve(memory_main.app) as memory_url:
            monkeypatch.setenv("PANTHEON_MEMORY_API_URL", memory_url)
            owner = PersistentPersonaOwner.from_json_path(tmp_path / "personas.json")
            owner.create(CreatePersonaRequest(actor_id="owner-actor", persona_id=PERSONA, name="Alpha",
                                             mandate="Paper analysis", tenant_id=TENANT))
            raw = owner._records.get(PERSONA)
            assert owner._records.compare_and_set(PERSONA, raw, {**raw, "lifecycle_state": "paper_owner"})[0]
            app = create_app(owner=owner, reflection_provider=_Provider(),
                             pattern_episode_lister=telemetry_episode_lister(telemetry_url, TELEMETRY_TOKEN),
                             lesson_registrar=MemoryLessonRegistrar(memory_url))
            with _serve(app) as persona_url:
                monkeypatch.setenv("PERSONA_API_URL", persona_url)
                local = TestClient(app)
                assert local.post(f"/api/personas/{PERSONA}/harness", headers=headers(), json=BASE).status_code == 201
                yield SimpleNamespace(journal=journal, memory=memory_main, memory_url=memory_url, persona_url=persona_url,
                                      owner=owner, persona=owner.list_json()[0], app=local)


def _memory_token(*roles: str, sub: str = "persona_cycle_controller") -> str:
    return encode_jwt_hs256({"sub": sub, "roles": list(roles), "tenant_id": TENANT, "exp": 4102444800}, secret=SECRET)


def _review_pattern(stack) -> dict:
    response = stack.app.post(f"/api/personas/{PERSONA}/trade-reflections:pattern-review", json={"tenant_id": TENANT},
                              headers={"Authorization": f"Bearer {SERVICE_TOKEN}"})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _case_file(stack) -> dict:
    subject = case_files.Subject(decision_type="weight_change", persona_id=PERSONA, tenant_id=TENANT,
                                 strategy_family="trend", underlyings=("2330",), member_artifact_refs=("reg-member",))
    caller = case_files.Caller(actor_id="persona_cycle_controller", actor_roles=PERSONA_SESSION,
                               session_id="cycle-2", persona_id=PERSONA)
    owners = case_files.Owners(memory=case_files.OwnerEndpoint(stack.memory_url, _memory_token(*PERSONA_SESSION)))
    return case_files.compose_case_file(subject, caller, owners)


def test_two_closed_episodes_become_persona_memory_the_next_case_file_cites_and_a_rule_proposal(stack):
    # 1. real pattern review over the journal owner -> real reflection -> candidate registered in Memory
    data = _review_pattern(stack)
    assert data["status"] == "reviewed" and data["lesson_registration"]["status"] == "registered"
    candidates = stack.memory._candidate_store().list(persona_id=PERSONA)
    assert len(candidates) == 1 and candidates[0]["review_state"] == "proposed"
    assert sorted(candidates[0]["trade_episode_ids"]) == sorted([EP1, EP2])
    cid = candidates[0]["lesson_candidate_id"]
    assert stack.memory._persona_store().get(f"pmem-lesson-{cid}") is None  # nothing is memory yet

    # 2. the daily cycle's governance step: risk persona review -> paper endorse -> merge, no operator
    risk = _RiskPersona()
    cycle = LessonCycle(turn_runner=risk)
    result = cycle.govern(stack.persona)
    assert result["status"] == "completed" and result["results"][0]["status"] == "merged", result
    assert [who for who, _ in risk.asked] == [RISK_PERSONA_ID]
    merged = stack.memory._candidate_store().get(cid)
    assert merged["review_state"] == "merged" and merged["receipt"]["operator_id"] == "persona-service"
    entry = stack.memory._persona_store().get(f"pmem-lesson-{cid}")
    assert entry.persona_id == PERSONA and "family:trend" in entry.content["tags"]
    assert cycle.govern(stack.persona)["results"] == []  # nothing left to review: no second risk turn
    assert len(risk.asked) == 1

    # 3. the next cycle's case file retrieves the merged lesson in its lessons group
    case = _case_file(stack)
    lessons = case["groups"]["lessons"]["items"]
    assert [i["source_id"] for i in lessons] == [f"pmem-lesson-{cid}"], case["groups"]["lessons"]

    # 4. a harness proposal citing it is converted through the owner route (service credential, tenant bound)
    from services.persona.persona_cycle_controller import CaseFileProposal, CaseFileTurnOutput
    proposal = CaseFileProposal(
        kind="harness_patch", reason="raise the evidence bar after the pullback lesson", evidence_refs=[],
        case_file_item_ids=[lessons[0]["item_id"]],
        parameters={"lesson_rule": {"kind": "harness_patch", "expected_effect": "fewer low-evidence entries",
                                    "spec": {"session_id": "session-1", "base_version": 1, "patch_type": "threshold",
                                             "proposed_change": {"cycle_thresholds": {"minimum_evidence_count": 4,
                                                                                      "minimum_confidence": 0.85}},
                                             "rollback_target": 1}}})
    output = CaseFileTurnOutput(orient="orient", proposals=[proposal])
    rules = cycle.convert(stack.persona, output, {}, case)
    assert rules[0]["status"] == "harness_proposed" and rules[0]["lesson_ids"] == [cid], rules
    stored = stack.app.get(f"/api/personas/{PERSONA}/lesson-rule-proposals",
                           headers=headers()).json()["proposals"]
    assert [p["lesson_ids"] for p in stored] == [[cid]] and stored[0]["case_file_id"] == case["case_file_id"]


def test_risk_rejection_is_terminal_and_nothing_reaches_memory(stack):
    _review_pattern(stack)
    cid = stack.memory._candidate_store().list(persona_id=PERSONA)[0]["lesson_candidate_id"]
    risk = _RiskPersona("reject")
    out = LessonCycle(turn_runner=risk).govern(stack.persona)["results"][0]
    assert out["status"] == "rejected"
    assert stack.memory._candidate_store().get(cid)["review_state"] == "rejected"
    assert stack.memory._persona_store().get(f"pmem-lesson-{cid}") is None


def test_journal_outage_fails_closed_with_a_recorded_reason(stack, monkeypatch):
    _review_pattern(stack)
    cid = stack.memory._candidate_store().list(persona_id=PERSONA)[0]["lesson_candidate_id"]
    monkeypatch.setenv("PANTHEON_TELEMETRY_SERVICE_TOKEN", "")
    out = LessonCycle(turn_runner=_RiskPersona()).govern(stack.persona)["results"][0]
    assert out["status"] == "endorse_refused" and "supporting_episodes_unavailable" in out["reason"]
    assert stack.memory._candidate_store().get(cid)["review_state"] == "proposed"
    assert stack.memory._persona_store().get(f"pmem-lesson-{cid}") is None
