"""Tests for C1 consultation meeting routes, roster/servant rules, budgets, and client."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

from fastapi.testclient import TestClient
import pytest

from services.consultation import main as consultation_main
from services.consultation.store import ConsultationStore
from services.consultation.workflow_state import WorkflowStateStore

TENANT = "tenant-routes"
SERVICE_TOKEN = "routes-service-token"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CONSULTATION_AUTH_REQUIRED", "true")
    monkeypatch.setenv("CONSULTATION_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("PANTHEON_TENANT_ID", TENANT)
    monkeypatch.setenv("PANTHEON_MEMORY_STORE", str(tmp_path / "inst_mem.json"))
    monkeypatch.setenv("PANTHEON_PERSONA_MEMORY_STORE", str(tmp_path / "persona_mem.json"))
    monkeypatch.setenv("PANTHEON_MEMORY_DATA_DIR", str(tmp_path / "mem_dir"))
    consultation_main.store = ConsultationStore(str(tmp_path / "domain"))
    consultation_main.workflow_state = WorkflowStateStore(tmp_path / "wf.sqlite3")
    consultation_main.meeting_provider = None
    consultation_main.auto_run_meetings = False
    yield TestClient(consultation_main.app)


def _headers(idem_key: str | None = None, tenant: str = TENANT) -> dict[str, str]:
    h = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Pantheon-Tenant-Id": tenant,
        "X-Pantheon-Service-Actor": "test-caller",
    }
    if idem_key:
        h["Idempotency-Key"] = idem_key
    return h


def test_catalogue_lists_six_amended_types_with_mt4_attribution_review(
    client: TestClient,
) -> None:
    resp = client.get("/api/consult/meetings/catalogue", headers=_headers())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) >= {"types", "schedule", "refusals"}
    types = body["types"]
    assert len(types) == 6
    by_code = {t["meeting_type"]: t for t in types}
    assert set(by_code) == {"MT-1", "MT-2", "MT-3", "MT-4", "MT-5", "MT-6"}
    assert by_code["MT-4"]["slug"] == "performance_attribution_review"
    for item in types:
        assert item["enabled"] is True
        assert item["cadence"]
        assert isinstance(item["roster_rule"], dict)
        assert isinstance(item["budget"], dict)


def test_post_meetings_accepts_six_types_refuses_others_and_is_idempotent(
    client: TestClient,
) -> None:
    payload = {
        "meeting_type": "MT-1",
        "topic": "Weekly pool evolution direction",
        "convened_by": {"kind": "persona", "id": "persona-alpha"},
        "participants": ["persona-alpha", "persona-beta", "persona-risk-officer"],
        "evidence_refs": ["facts:pool:alpha-v1"],
    }
    r1 = client.post("/api/consult/meetings", json=payload, headers=_headers("idem-mt1"))
    assert r1.status_code == 202, r1.text
    d1 = r1.json()
    assert d1["status"] == "convened"
    assert d1["meeting_id"].startswith("mtg-")

    # Idempotent replay with same Idempotency-Key and payload
    r2 = client.post("/api/consult/meetings", json=payload, headers=_headers("idem-mt1"))
    assert r2.status_code == 202
    assert r2.json() == d1

    # Conflicting payload with same Idempotency-Key -> 409
    conflict = {**payload, "topic": "Different topic"}
    r_conflict = client.post("/api/consult/meetings", json=conflict, headers=_headers("idem-mt1"))
    assert r_conflict.status_code == 409

    # Retired post_mortem and unknown types are refused with 422
    for bad_type in ("post_mortem", "MT-4_post_mortem", "unknown_meeting_type"):
        bad_resp = client.post(
            "/api/consult/meetings",
            json={**payload, "meeting_type": bad_type},
            headers=_headers(f"idem-{bad_type}"),
        )
        assert bad_resp.status_code == 422, bad_resp.text
        assert bad_resp.json()["reason"] == "unsupported_meeting_type"

    # Also refused on /api/consult/requests when consultation_type=persona_meeting
    req_resp = client.post(
        "/api/consult/requests",
        json={
            "request_type": "persona_policy",
            "requested_by": {"actor_type": "persona", "actor_id": "persona-alpha"},
            "from_persona_id": "persona-alpha",
            "target_type": "post_mortem",
            "target_id": "t-1",
            "consultation_type": "persona_meeting",
            "trace_id": "trace-bad-req",
        },
        headers=_headers(),
    )
    assert req_resp.status_code == 422


def test_private_servant_and_agora_advisor_roster_rules(client: TestClient) -> None:
    # 1. Private servant attends a meeting convened by its owning user
    own_user_resp = client.post(
        "/api/consult/meetings",
        json={
            "meeting_type": "MT-1",
            "topic": "Trader workshop strategy review",
            "convened_by": {"kind": "agora_user", "id": "trader-u1"},
            "participants": [
                {"persona_id": "persona-alpha", "role": "chair", "origin": "institutional"},
                {
                    "persona_id": "agora-servant-u1",
                    "origin": "private_servant",
                    "agora_user_id": "trader-u1",
                },
                {
                    "persona_id": "agora-advisor-macro",
                    "origin": "agora_advisor",
                    "lifecycle_state": "consultable",
                    "environment_ceiling": "paper",
                },
                {"persona_id": "persona-risk-officer", "role": "risk_reviewer"},
            ],
        },
        headers=_headers("idem-servant-own"),
    )
    assert own_user_resp.status_code == 202, own_user_resp.text
    mtg_id = own_user_resp.json()["meeting_id"]
    detail = client.get(f"/api/consult/meetings/{mtg_id}", headers=_headers()).json()
    origins = {p["persona_id"]: p for p in detail["participants"]}
    assert origins["agora-servant-u1"]["origin"] == "private_servant"
    assert origins["agora-servant-u1"]["agora_user_id"] == "trader-u1"
    assert origins["agora-advisor-macro"]["origin"] == "agora_advisor"

    # 2. Private servant attends an MT-4 review whose subject is its own trader (even when convened by persona)
    mt4_own_trader = client.post(
        "/api/consult/meetings",
        json={
            "meeting_type": "MT-4",
            "topic": "Outlier attribution for trader-u1",
            "convened_by": {"kind": "persona", "id": "persona-alpha"},
            "subject_ref": "trader:trader-u1",
            "attribution_record_id": "attr-u1-001",
            "participants": [
                {"persona_id": "persona-alpha", "role": "chair"},
                {
                    "persona_id": "agora-servant-u1",
                    "origin": "private_servant",
                    "agora_user_id": "trader-u1",
                },
                {"persona_id": "persona-risk-officer", "role": "risk_reviewer"},
                {"persona_id": "persona-red-team", "role": "red_team"},
            ],
        },
        headers=_headers("idem-mt4-servant"),
    )
    assert mt4_own_trader.status_code == 202, mt4_own_trader.text

    # 3. Private servant is REFUSED in a meeting convened by another user
    other_user = client.post(
        "/api/consult/meetings",
        json={
            "meeting_type": "MT-1",
            "topic": "Other user workshop",
            "convened_by": {"kind": "agora_user", "id": "trader-u2"},
            "participants": [
                {"persona_id": "persona-alpha", "role": "chair"},
                {
                    "persona_id": "agora-servant-u1",
                    "origin": "private_servant",
                    "agora_user_id": "trader-u1",
                },
            ],
        },
        headers=_headers("idem-servant-other-user"),
    )
    assert other_user.status_code == 422
    assert other_user.json()["reason"] == "private_servant_not_allowed"

    # 4. Private servant is REFUSED in an institutional non-MT-4 meeting
    inst_non_mt4 = client.post(
        "/api/consult/meetings",
        json={
            "meeting_type": "MT-2",
            "topic": "Institutional optimisation",
            "convened_by": {"kind": "persona", "id": "persona-alpha"},
            "participants": [
                {"persona_id": "persona-alpha", "role": "chair"},
                {
                    "persona_id": "agora-servant-u1",
                    "origin": "private_servant",
                    "agora_user_id": "trader-u1",
                },
            ],
        },
        headers=_headers("idem-servant-inst-mt2"),
    )
    assert inst_non_mt4.status_code == 422
    assert inst_non_mt4.json()["reason"] == "private_servant_not_allowed"

    # 5. Private servant is REFUSED in an MT-4 review whose subject is a DIFFERENT trader
    mt4_other_trader = client.post(
        "/api/consult/meetings",
        json={
            "meeting_type": "MT-4",
            "topic": "Outlier attribution for trader-u2",
            "convened_by": {"kind": "persona", "id": "persona-alpha"},
            "subject_ref": "trader:trader-u2",
            "attribution_record_id": "attr-u2-001",
            "participants": [
                {"persona_id": "persona-alpha", "role": "chair"},
                {
                    "persona_id": "agora-servant-u1",
                    "origin": "private_servant",
                    "agora_user_id": "trader-u1",
                },
                {"persona_id": "persona-risk-officer", "role": "risk_reviewer"},
                {"persona_id": "persona-red-team", "role": "red_team"},
            ],
        },
        headers=_headers("idem-mt4-wrong-servant"),
    )
    assert mt4_other_trader.status_code == 422
    assert mt4_other_trader.json()["reason"] == "private_servant_not_allowed"

    # 6. User-private or non-operational Agora advisor is REFUSED
    bad_advisor = client.post(
        "/api/consult/meetings",
        json={
            "meeting_type": "MT-1",
            "topic": "Advisor eligibility check",
            "convened_by": {"kind": "persona", "id": "persona-alpha"},
            "participants": [
                {"persona_id": "persona-alpha", "role": "chair"},
                {
                    "persona_id": "agora-advisor-draft",
                    "origin": "agora_advisor",
                    "lifecycle_state": "draft",
                },
            ],
        },
        headers=_headers("idem-bad-advisor"),
    )
    assert bad_advisor.status_code == 422
    assert bad_advisor.json()["reason"] == "agora_advisor_ineligible"


def test_budgets_and_insufficient_participants_refusals(client: TestClient) -> None:
    # 1. Cross-persona meeting (MT-3) with < 2 trading personas -> insufficient_participants
    insuff = client.post(
        "/api/consult/meetings",
        json={
            "meeting_type": "MT-3",
            "topic": "Strategy ideation with only 1 trading persona",
            "convened_by": {"kind": "persona", "id": "persona-alpha"},
            "participants": [
                {"persona_id": "persona-alpha", "role": "chair"},
                {"persona_id": "persona-red-team", "role": "red_team"},
            ],
        },
        headers=_headers("idem-insuff"),
    )
    assert insuff.status_code == 422
    assert insuff.json()["reason"] == "insufficient_participants"

    # 2. > 5 participants -> participant_budget_exceeded
    too_many = client.post(
        "/api/consult/meetings",
        json={
            "meeting_type": "MT-3",
            "topic": "Too many participants",
            "convened_by": {"kind": "persona", "id": "persona-1"},
            "participants": [f"persona-{i}" for i in range(1, 7)],
        },
        headers=_headers("idem-too-many"),
    )
    assert too_many.status_code == 422
    assert too_many.json()["reason"] == "participant_budget_exceeded"

    # 3. Rounds > 2 without chair new_evidence -> round_budget_exceeded
    bad_rounds = client.post(
        "/api/consult/meetings",
        json={
            "meeting_type": "MT-1",
            "topic": "Too many rounds",
            "convened_by": {"kind": "persona", "id": "persona-alpha"},
            "participants": ["persona-alpha", "persona-beta", "persona-risk-officer"],
            "rounds_requested": 3,
        },
        headers=_headers("idem-bad-rounds"),
    )
    assert bad_rounds.status_code == 422
    assert bad_rounds.json()["reason"] == "round_budget_exceeded"

    # 4. Calls > max_calls_per_meeting -> call_budget_exceeded
    bad_calls = client.post(
        "/api/consult/meetings",
        json={
            "meeting_type": "MT-1",
            "topic": "Too many calls",
            "convened_by": {"kind": "persona", "id": "persona-alpha"},
            "participants": ["persona-alpha", "persona-beta", "persona-risk-officer"],
            "calls_requested": 25,
        },
        headers=_headers("idem-bad-calls"),
    )
    assert bad_calls.status_code == 422
    assert bad_calls.json()["reason"] == "call_budget_exceeded"

    # 5. Weekly persona limit (max 3 meetings per persona per ISO week)
    for idx in range(3):
        ok = client.post(
            "/api/consult/meetings",
            json={
                "meeting_type": "MT-1",
                "topic": f"Weekly meeting #{idx + 1}",
                "convened_by": {"kind": "persona", "id": "persona-weekly-cap"},
                "participants": ["persona-weekly-cap", "persona-risk-officer"],
                "iso_week": "2026-W46",
            },
            headers=_headers(f"idem-wk-{idx}"),
        )
        assert ok.status_code == 202, ok.text

    fourth = client.post(
        "/api/consult/meetings",
        json={
            "meeting_type": "MT-2",
            "topic": "Fourth meeting in same ISO week",
            "convened_by": {"kind": "persona", "id": "persona-weekly-cap"},
            "participants": ["persona-weekly-cap", "persona-risk-officer"],
            "iso_week": "2026-W46",
        },
        headers=_headers("idem-wk-4"),
    )
    assert fourth.status_code == 422
    assert fourth.json()["reason"] == "weekly_persona_budget_exceeded"

    # Verify all refusals are recorded in GET /api/consult/meetings/catalogue
    cat = client.get("/api/consult/meetings/catalogue", headers=_headers()).json()
    reasons = {r["reason"] for r in cat["refusals"]}
    assert {
        "insufficient_participants",
        "participant_budget_exceeded",
        "round_budget_exceeded",
        "call_budget_exceeded",
        "weekly_persona_budget_exceeded",
    } <= reasons

    # Verify unknown meeting_id returns 404
    assert client.get("/api/consult/meetings/mtg-does-not-exist", headers=_headers()).status_code == 404
