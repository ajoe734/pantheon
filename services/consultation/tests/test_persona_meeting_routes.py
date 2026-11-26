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


def test_operator_review_route_persists_disposition_survives_restart_and_enforces_guards(
    client: TestClient,
    tmp_path: Path,
) -> None:
    # 1. Seed a legacy closed REVIEW meeting (null unreadable_reason, 2 rounds, 0 proposals)
    legacy_review_mtg = {
        "meeting_id": "mtg-2c016da5651a0dc4",
        "tenant_id": TENANT,
        "meeting_type": "MT-1",
        "meeting_code": "MT-1",
        "meeting_slug": "weekly_pool_evolution_review",
        "topic": "Pool evolution review for persona-2448c7053e33ea48da47 (2026-W47)",
        "status": "closed",
        "outcome": "REVIEW",
        "chair_persona_id": "persona-2448c7053e33ea48da47",
        "convened_by": {"kind": "persona", "id": "persona-2448c7053e33ea48da47"},
        "participants": [
            {"persona_id": "persona-2448c7053e33ea48da47", "role": "chair", "origin": "institutional"},
            {"persona_id": "persona-risk-officer", "role": "risk_reviewer", "origin": "institutional"},
        ],
        "round": 2,
        "created_at": "2026-11-20T17:38:10Z",
        "closed_at": "2026-11-20T17:40:52Z",
        "case_file_items": [
            {
                "item_id": "facts:pool:persona-2448c7053e33ea48da47:v1",
                "group": "facts",
                "kind": "pool_snapshot",
                "source_owner": "persona",
                "excerpt": "Pool v1 active member",
            }
        ],
        "rounds": [
            {
                "round": 1,
                "positions": [
                    {
                        "label": "role:chair|style:momentum#1",
                        "persona_id": "persona-2448c7053e33ea48da47",
                        "role": "chair",
                        "stance": "request_more_research",
                        "confidence": 0.62,
                        "citations": ["facts:pool:persona-2448c7053e33ea48da47:v1"],
                    }
                ],
            },
            {
                "round": 2,
                "positions": [
                    {
                        "label": "role:risk_reviewer|style:risk_governance#2",
                        "persona_id": "persona-risk-officer",
                        "role": "risk_reviewer",
                        "stance": "request_more_research",
                        "confidence": 0.74,
                        "citations": ["facts:pool:persona-2448c7053e33ea48da47:v1"],
                    }
                ],
            },
        ],
        "synthesis": {
            "proposals": [],
            "tally": {"voting": False, "binding": False, "by_stance": {"request_more_research": 2}, "mean_confidence": 0.68},
            "dissent_memo_id": "mem-dissent-mtg-2c016da5651a0dc4",
            "outcome": "REVIEW",
            "unreadable_reason": None,
            "summary": "Synthesis empty or unreadable; recorded as REVIEW with no proposal.",
            "voting": False,
        },
        "trace_id": "trace-mtg-2c016da5651a0dc4",
    }
    consultation_main.store.put_meeting(legacy_review_mtg)

    # Also seed a closed non-REVIEW meeting
    non_review_mtg = {
        **legacy_review_mtg,
        "meeting_id": "mtg-proposals-issued-01",
        "outcome": "proposals_issued",
        "synthesis": {
            "proposals": [{"proposal_id": "prop-1", "kind": "research_demand"}],
            "outcome": "proposals_issued",
            "summary": "Issued 1 research demand",
        },
    }
    consultation_main.store.put_meeting(non_review_mtg)

    # 2. Legacy detail exposes cause as 'unknown', operator_review as None, and actual evidence_refs
    pre_detail = client.get("/api/consult/meetings/mtg-2c016da5651a0dc4", headers=_headers()).json()
    assert pre_detail["operator_review"] is None
    assert pre_detail["synthesis_unreadable_reason"] == "unknown"
    assert pre_detail["synthesis"]["unreadable_reason"] == "unknown"
    assert "facts:pool:persona-2448c7053e33ea48da47:v1" in pre_detail["evidence_refs"]
    assert "mem-dissent-mtg-2c016da5651a0dc4" in pre_detail["evidence_refs"]

    # 3. Reject other tenants (404 when meeting not in caller tenant, 403 on payload tenant mismatch)
    assert (
        client.post(
            "/api/consult/meetings/mtg-2c016da5651a0dc4/review",
            json={"note": "Cross-tenant attempt", "reviewed_by": "op-1"},
            headers=_headers(tenant="other-tenant"),
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/consult/meetings/mtg-2c016da5651a0dc4/review",
            json={"note": "Mismatch payload tenant", "reviewed_by": "op-1", "tenant_id": "other-tenant"},
            headers=_headers(),
        ).status_code
        == 403
    )

    # 4. Reject disallowed roles (viewer, trader, persona) and spoofed bff:<operator_id>
    for bad_actor in ("persona:persona-alpha", "bff:viewer", "bff:trader"):
        bad_hdrs = {**_headers(), "X-Pantheon-Service-Actor": bad_actor}
        assert (
            client.post(
                "/api/consult/meetings/mtg-2c016da5651a0dc4/review",
                json={"note": "Disallowed actor"},
                headers=bad_hdrs,
            ).status_code
            == 403
        )
    spoof_hdrs = {**_headers(), "X-Pantheon-Service-Actor": "bff:op-alice"}
    assert (
        client.post(
            "/api/consult/meetings/mtg-2c016da5651a0dc4/review",
            json={"note": "Spoofed reviewer", "reviewed_by": "op-bob"},
            headers=spoof_hdrs,
        ).status_code
        == 403
    )

    # 5. Reject non-REVIEW meetings (409) and empty or >2000-char notes (422)
    op_hdrs = {**_headers("idem-rev-1"), "X-Pantheon-Service-Actor": "bff:op-alice"}
    non_rev_resp = client.post(
        "/api/consult/meetings/mtg-proposals-issued-01/review",
        json={"note": "Cannot review non-REVIEW meeting", "reviewed_by": "op-alice"},
        headers=op_hdrs,
    )
    assert non_rev_resp.status_code == 409
    assert non_rev_resp.json()["reason"] == "meeting_not_in_review"

    assert (
        client.post(
            "/api/consult/meetings/mtg-2c016da5651a0dc4/review",
            json={"note": "   ", "reviewed_by": "op-alice"},
            headers=op_hdrs,
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/consult/meetings/mtg-2c016da5651a0dc4/review",
            json={"note": "x" * 2001, "reviewed_by": "op-alice"},
            headers=op_hdrs,
        ).status_code
        == 422
    )

    # 6. Valid operator review persists and records cause='unknown' on legacy data
    note = "Both participants requested more research twice; closed review with zero proposals."
    ok_resp = client.post(
        "/api/consult/meetings/mtg-2c016da5651a0dc4/review",
        json={"note": note, "reviewed_by": "op-alice"},
        headers=op_hdrs,
    )
    assert ok_resp.status_code == 200, ok_resp.text
    reviewed = ok_resp.json()
    assert reviewed["replayed"] is False
    assert reviewed["operator_review"]["status"] == "reviewed"
    assert reviewed["operator_review"]["disposition"] == "closed_no_proposal"
    assert reviewed["operator_review"]["note"] == note
    assert reviewed["operator_review"]["reviewed_by"] == "op-alice"
    assert reviewed["operator_review"]["reviewed_at"]
    assert reviewed["operator_review"]["cause"] == "unknown"
    assert reviewed["outcome"] == "REVIEW"
    assert reviewed["synthesis"]["outcome"] == "REVIEW"
    assert reviewed["synthesis"]["proposals"] == []
    assert reviewed["synthesis"]["summary"] == "Synthesis empty or unreadable; recorded as REVIEW with no proposal."
    assert len(reviewed["rounds"]) == 2

    lifecycle_lines_after_first = len(
        consultation_main.store.lifecycle_log_path.read_text(encoding="utf-8").splitlines()
    )
    audit_events_after_first = len(
        consultation_main.store.list_audit_for_request("mtg-2c016da5651a0dc4")
    )
    assert audit_events_after_first == 1

    # 7. Repeated identical submission returns the exact same disposition and writes 0 new events
    replay_resp = client.post(
        "/api/consult/meetings/mtg-2c016da5651a0dc4/review",
        json={"note": note, "reviewed_by": "op-alice"},
        headers={**_headers("idem-rev-2"), "X-Pantheon-Service-Actor": "bff:op-alice"},
    )
    assert replay_resp.status_code == 200
    replayed = replay_resp.json()
    assert replayed["replayed"] is True
    assert replayed["operator_review"] == reviewed["operator_review"]
    assert (
        len(consultation_main.store.lifecycle_log_path.read_text(encoding="utf-8").splitlines())
        == lifecycle_lines_after_first
    )
    assert len(consultation_main.store.list_audit_for_request("mtg-2c016da5651a0dc4")) == 1

    # 8. Concurrent/conflicting submission by another operator or with a different note returns 409 without overwriting
    conflict_other_op = client.post(
        "/api/consult/meetings/mtg-2c016da5651a0dc4/review",
        json={"note": note, "reviewed_by": "op-bob"},
        headers={**_headers("idem-conflict-op"), "X-Pantheon-Service-Actor": "bff:op-bob"},
    )
    assert conflict_other_op.status_code == 409
    assert conflict_other_op.json()["reason"] == "meeting_already_reviewed"

    conflict_diff_note = client.post(
        "/api/consult/meetings/mtg-2c016da5651a0dc4/review",
        json={"note": "Attempt to overwrite rationale", "reviewed_by": "op-alice"},
        headers=op_hdrs,
    )
    assert conflict_diff_note.status_code == 409
    assert conflict_diff_note.json()["reason"] == "meeting_already_reviewed"

    # 9. Survives process restart (fresh ConsultationStore reload from disk)
    consultation_main.store = ConsultationStore(str(tmp_path / "domain"))
    after_restart = client.get("/api/consult/meetings/mtg-2c016da5651a0dc4", headers=_headers()).json()
    assert after_restart["operator_review"] == reviewed["operator_review"]
    assert after_restart["outcome"] == "REVIEW"
    assert after_restart["synthesis"]["proposals"] == []
    assert len(after_restart["rounds"]) == 2


def test_operator_review_partial_write_failures_restart_concurrency_and_unicode_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setenv("CONSULTATION_AUTH_REQUIRED", "true")
    monkeypatch.setenv("CONSULTATION_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("PANTHEON_TENANT_ID", TENANT)
    data_dir = tmp_path / "domain_recovery"
    consultation_main.store = ConsultationStore(str(data_dir))
    consultation_main.workflow_state = WorkflowStateStore(tmp_path / "wf_recovery.sqlite3")
    consultation_main.meeting_provider = None
    consultation_main.auto_run_meetings = False
    http = TestClient(consultation_main.app, raise_server_exceptions=False)

    def _seed_review_meeting(mid: str) -> None:
        consultation_main.store.put_meeting(
            {
                "meeting_id": mid,
                "tenant_id": TENANT,
                "meeting_type": "MT-1",
                "meeting_code": "MT-1",
                "topic": f"Failure recovery test {mid}",
                "status": "closed",
                "outcome": "REVIEW",
                "chair_persona_id": "persona-2448c7053e33ea48da47",
                "convened_by": {"kind": "persona", "id": "persona-2448c7053e33ea48da47"},
                "participants": [{"persona_id": "persona-2448c7053e33ea48da47", "role": "chair"}],
                "round": 2,
                "case_file_items": [{"item_id": "facts:pool:1", "excerpt": "Pool Sharpe 1.2"}],
                "rounds": [{"round": 1, "positions": []}],
                "synthesis": {
                    "proposals": [],
                    "outcome": "REVIEW",
                    "unreadable_reason": None,
                    "summary": "Synthesis empty or unreadable; recorded as REVIEW with no proposal.",
                },
                "trace_id": f"trace-{mid}",
            }
        )

    def _review_lifecycle_events(mid: str) -> list[dict[str, Any]]:
        import json as _json

        events = []
        for raw_line in consultation_main.store.lifecycle_log_path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            ev = _json.loads(raw_line)
            if ev.get("record_type") == "persona_meeting" and ev.get("record_id") == mid:
                op_rev = (ev.get("payload") or {}).get("operator_review")
                if isinstance(op_rev, dict) and op_rev.get("status") == "reviewed":
                    events.append(ev)
        return events

    def _review_outbox_records(mid: str) -> list[Any]:
        out = []
        for rec in consultation_main.store.outbox.load():
            payload = getattr(rec.event, "payload", None) or {}
            if payload.get("record_type") == "persona_meeting" and payload.get("record_id") == mid:
                op_rev = (payload.get("payload") or {}).get("operator_review")
                if isinstance(op_rev, dict) and op_rev.get("status") == "reviewed":
                    out.append(rec)
        return out

    op_alice_hdrs = {**_headers("idem-rec-alice"), "X-Pantheon-Service-Actor": "bff:op-alice"}
    op_bob_hdrs = {**_headers("idem-rec-bob"), "X-Pantheon-Service-Actor": "bff:op-bob"}

    # --- Case 1: Inject one outbox.append OSError ---
    _seed_review_meeting("mtg-fail-outbox-1")
    real_outbox_append = consultation_main.store.outbox.append
    outbox_calls = {"n": 0}

    def _fail_outbox_once(record: Any) -> None:
        outbox_calls["n"] += 1
        if outbox_calls["n"] == 1:
            raise OSError("simulated outbox disk full on first review commit")
        real_outbox_append(record)

    monkeypatch.setattr(consultation_main.store.outbox, "append", _fail_outbox_once)

    r1_outbox = http.post(
        "/api/consult/meetings/mtg-fail-outbox-1/review",
        json={"note": "Outbox recovery rationale", "reviewed_by": "op-alice"},
        headers=op_alice_hdrs,
    )
    assert r1_outbox.status_code == 500
    assert len(_review_lifecycle_events("mtg-fail-outbox-1")) == 1
    first_ts = _review_lifecycle_events("mtg-fail-outbox-1")[0]["payload"]["operator_review"]["reviewed_at"]
    assert len(_review_outbox_records("mtg-fail-outbox-1")) == 0
    assert len(consultation_main.store.list_audit_for_request("mtg-fail-outbox-1")) == 0

    # Conflicting operator during partial state is rejected with 409 and cannot overwrite op-alice
    r_conflict_outbox = http.post(
        "/api/consult/meetings/mtg-fail-outbox-1/review",
        json={"note": "Bob trying to hijack partial review", "reviewed_by": "op-bob"},
        headers=op_bob_hdrs,
    )
    assert r_conflict_outbox.status_code == 409

    # Identical retry recovers outbox + audit without writing a second review lifecycle event
    r2_outbox = http.post(
        "/api/consult/meetings/mtg-fail-outbox-1/review",
        json={"note": "Outbox recovery rationale", "reviewed_by": "op-alice"},
        headers=op_alice_hdrs,
    )
    assert r2_outbox.status_code == 200, r2_outbox.text
    b2_outbox = r2_outbox.json()
    assert b2_outbox["operator_review"]["reviewed_by"] == "op-alice"
    assert b2_outbox["operator_review"]["reviewed_at"] == first_ts
    assert len(_review_lifecycle_events("mtg-fail-outbox-1")) == 1
    assert len(_review_outbox_records("mtg-fail-outbox-1")) == 1
    assert len(consultation_main.store.list_audit_for_request("mtg-fail-outbox-1")) == 1

    monkeypatch.setattr(consultation_main.store.outbox, "append", real_outbox_append)

    # --- Case 2: Inject one append_audit OSError (plus process restart before retry) ---
    _seed_review_meeting("mtg-fail-audit-1")
    real_append_audit = consultation_main.store.append_audit
    audit_calls = {"n": 0}

    def _fail_audit_once(event: Any) -> None:
        audit_calls["n"] += 1
        if audit_calls["n"] == 1:
            raise OSError("simulated audit log I/O failure on first review commit")
        real_append_audit(event)

    monkeypatch.setattr(consultation_main.store, "append_audit", _fail_audit_once)

    r1_audit = http.post(
        "/api/consult/meetings/mtg-fail-audit-1/review",
        json={"note": "Audit recovery rationale", "reviewed_by": "op-alice"},
        headers=op_alice_hdrs,
    )
    assert r1_audit.status_code == 500
    assert len(_review_lifecycle_events("mtg-fail-audit-1")) == 1
    audit_first_ts = _review_lifecycle_events("mtg-fail-audit-1")[0]["payload"]["operator_review"]["reviewed_at"]
    assert len(_review_outbox_records("mtg-fail-audit-1")) == 1
    assert len(consultation_main.store.list_audit_for_request("mtg-fail-audit-1")) == 0

    # Simulate process restart before retry
    consultation_main.store = ConsultationStore(str(data_dir))

    r2_audit = http.post(
        "/api/consult/meetings/mtg-fail-audit-1/review",
        json={"note": "Audit recovery rationale", "reviewed_by": "op-alice"},
        headers=op_alice_hdrs,
    )
    assert r2_audit.status_code == 200, r2_audit.text
    b2_audit = r2_audit.json()
    assert b2_audit["replayed"] is True
    assert b2_audit["operator_review"]["reviewed_by"] == "op-alice"
    assert b2_audit["operator_review"]["reviewed_at"] == audit_first_ts
    assert len(_review_lifecycle_events("mtg-fail-audit-1")) == 1
    assert len(_review_outbox_records("mtg-fail-audit-1")) == 1
    audits = consultation_main.store.list_audit_for_request("mtg-fail-audit-1")
    assert len(audits) == 1
    assert audits[0].action == "meeting_review_recorded"
    assert audits[0].actor_ref.actor_id == "op-alice"

    # --- Case 3: Concurrent identical and conflicting requests ---
    _seed_review_meeting("mtg-concurrent-1")

    def _attempt(idx: int) -> tuple[int, str]:
        is_alice = idx % 2 == 0
        actor = "op-alice" if is_alice else "op-bob"
        note_val = "Concurrent winning review rationale" if is_alice else "Concurrent conflicting rationale"
        resp = http.post(
            "/api/consult/meetings/mtg-concurrent-1/review",
            json={"note": note_val, "reviewed_by": actor},
            headers={**_headers(f"idem-conc-{idx}"), "X-Pantheon-Service-Actor": f"bff:{actor}"},
        )
        return resp.status_code, (resp.json().get("operator_review") or {}).get("reviewed_by", "")

    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(_attempt, range(6)))

    ok_actors = {actor for code, actor in outcomes if code == 200}
    conflict_codes = [code for code, _ in outcomes if code == 409]
    assert len(ok_actors) == 1
    assert len(conflict_codes) == 3
    assert len(_review_lifecycle_events("mtg-concurrent-1")) == 1
    assert len(_review_outbox_records("mtg-concurrent-1")) == 1
    assert len(consultation_main.store.list_audit_for_request("mtg-concurrent-1")) == 1

    # --- Case 4: Unicode 2000-character boundaries (ASCII, Chinese, 4-byte Emoji, and 2001 rejection) ---
    for idx, valid_note in enumerate(
        (
            "a" * 2000,
            "澄" * 2000,
            "📈" * 2000,
        ),
        start=1,
    ):
        mid = f"mtg-unicode-ok-{idx}"
        _seed_review_meeting(mid)
        u_resp = http.post(
            f"/api/consult/meetings/{mid}/review",
            json={"note": valid_note, "reviewed_by": "op-alice"},
            headers=op_alice_hdrs,
        )
        assert u_resp.status_code == 200, (idx, u_resp.text)
        assert u_resp.json()["operator_review"]["note"] == valid_note

    _seed_review_meeting("mtg-unicode-too-long")
    for invalid_note in ("a" * 2001, "澄" * 2001, "📈" * 2001):
        too_long_resp = http.post(
            "/api/consult/meetings/mtg-unicode-too-long/review",
            json={"note": invalid_note, "reviewed_by": "op-alice"},
            headers=op_alice_hdrs,
        )
        assert too_long_resp.status_code == 422
        assert too_long_resp.json()["reason"] == "review_note_too_long"


def test_mt3_with_enrolled_roster_convened_by_persona_is_convened_not_refused(client: TestClient) -> None:
    from services.persona.meeting_triggers import enrolled_roster, RED_TEAM_ENTRY

    ids = ["persona-alpha", "persona-beta", "persona-gamma"]
    resp = client.post(
        "/api/consult/meetings",
        json={
            "meeting_type": "MT-3",
            "topic": "Strategy ideation (enrolled roster)",
            "convened_by": {"kind": "persona", "id": ids[0]},
            "participants": enrolled_roster(ids[0], ids, trading_cap=4, extras=(RED_TEAM_ENTRY,)),
        },
        headers=_headers("idem-mt3-enrolled"),
    )
    assert resp.status_code == 202, resp.text
    assert resp.json().get("status") == "convened", resp.text
