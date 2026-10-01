"""PERSONA-EVALUATOR-AGENT-002: the BFF only projects the evaluator's saved result."""
from __future__ import annotations

from services.control_plane.bff import test_bff_promotion_review_governance as gov_test
from services.control_plane.bff.pm12 import evaluator_results
from services.control_plane.bff.personas import service as personas_service

HEADERS = gov_test.OPERATOR_HEADERS
URL = "/bff/management/quarterly-ranking/recommendations"


def _items(client):
    response = client.get(URL, headers=HEADERS, params={"quarter": "2026-Q1", "page_size": 50})
    assert response.status_code == 200, response.text
    return response.json()["data"]["items"]


def test_score_alone_creates_no_recommendation_when_nothing_is_saved(monkeypatch):
    monkeypatch.setattr(evaluator_results, "saved_evaluator_result", lambda *_a, **_k: None)
    with gov_test._isolated_client() as (client, _store, _commands):
        ranking = client.get("/bff/management/quarterly-ranking", headers=HEADERS, params={"quarter": "2026-Q1"})
        assert any(float(i["score"]) >= 85.0 for i in ranking.json()["data"]["items"])  # high scores exist
        assert _items(client) == []
        reviews = client.get("/bff/management/promotion-reviews", headers=HEADERS, params={"quarter": "2026-Q1"})
        assert reviews.json()["data"]["items"] == []


def test_ranking_and_promotion_review_return_the_same_saved_provider_recommendation(monkeypatch):
    with gov_test._isolated_client() as (client, _store, _commands):
        persona = _items(client)[0]["persona_id"]  # first read admits the snapshot the evaluator judged
        saved = {
            "run_id": "persona-eval-1", "evaluated_at": "2026-01-01T00:00:00+00:00",
            "items": [{
                "persona_id": persona, "action_id": "promote_to_canary_candidate",
                "rationale": "Provider: sustained risk-adjusted outperformance.", "evidence_ref_ids": ["ev-1"],
                "recommendation_id": f"pm12-2026-q1-{persona}-promote_to_canary_candidate",
                "ranking_snapshot_id": _items(client)[0]["ranking_snapshot_id"], "governance_request": None,
            }],
        }
        calls = []
        monkeypatch.setattr(evaluator_results, "saved_evaluator_result", lambda *a, **k: calls.append(a) or saved)
        first, again = _items(client), _items(client)  # refresh reads the same saved result
        assert first == again and len(first) == 1
        assert first[0]["rationale"] == "Provider: sustained risk-adjusted outperformance."
        assert first[0]["recommendation_source"] == "persona_evaluator_agent"
        assert first[0]["evidence_ref_ids"] == ["ev-1"] and first[0]["evaluator_run_id"] == "persona-eval-1"
        reviews = client.get("/bff/management/promotion-reviews", headers=HEADERS, params={"quarter": "2026-Q1"})
        review = reviews.json()["data"]["items"][0]
        assert review["recommendation_id"] == first[0]["recommendation_id"]
        assert review["rationale"] == first[0]["rationale"]


def test_bff_has_no_score_to_action_rule_or_static_rationale():
    assert not hasattr(personas_service, "_pm12_recommendation_action_ids")
    assert not hasattr(personas_service, "_pm12_add_recommendation_action")
    assert all("rationale" not in a for a in personas_service._PM12_QUARTERLY_RECOMMENDATION_ACTIONS.values())


def test_refresh_after_ranking_advances_keeps_saved_snapshot_projection(monkeypatch):
    with gov_test._isolated_client() as (client, _store, _commands):
        first = _items(client)[0]
        persona, snap = first["persona_id"], first["ranking_snapshot_id"]
        saved = {
            "run_id": "persona-eval-1", "evaluated_at": "2026-01-01T00:00:00+00:00",
            "items": [{
                "persona_id": persona, "action_id": "promote_to_canary_candidate", "rationale": "Provider.",
                "evidence_ref_ids": ["ev-old"], "governance_request": None, "ranking_snapshot_id": snap,
                "recommendation_id": f"pm12-2026-q1-{persona}-promote_to_canary_candidate",
            }],
        }
        monkeypatch.setattr(evaluator_results, "saved_evaluator_result", lambda *a, **k: saved)
        before = _items(client)[0]
        real_attach = personas_service._pm12_attach_ranking_snapshot

        def advanced(items, **kwargs):  # live ranking moves: new scores/evidence
            moved = [{**i, "score": 95.0, "state": "frozen", "evidence_refs": [{"id": "ev-new"}]} for i in items]
            return real_attach(moved, **kwargs)

        monkeypatch.setattr(personas_service, "_pm12_attach_ranking_snapshot", advanced)
        after = _items(client)[0]
        for key in ("ranking_snapshot_id", "score", "state", "evidence_refs", "evidence_ref_ids"):
            assert after[key] == before[key]
        assert after["evidence_ref_ids"] == ["ev-old"] and after["evidence_refs"] == []


def test_unresolvable_saved_snapshot_fails_closed(monkeypatch):
    with gov_test._isolated_client() as (client, _store, _commands):
        persona = _items(client)[0]["persona_id"]
        saved = {"run_id": "r", "items": [{
            "persona_id": persona, "action_id": "promote_to_canary_candidate", "rationale": "x",
            "evidence_ref_ids": [], "ranking_snapshot_id": "snap-missing",
            "recommendation_id": f"pm12-2026-q1-{persona}-promote_to_canary_candidate",
        }]}
        monkeypatch.setattr(evaluator_results, "saved_evaluator_result", lambda *a, **k: saved)
        assert _items(client) == []


def test_saved_recommendations_are_limited_to_personas_visible_to_the_caller(monkeypatch):
    with gov_test._isolated_client() as (client, _store, _commands):
        items = _items(client)
        personas = sorted({i["persona_id"] for i in items})
        assert len(personas) >= 2
        visible, hidden = personas[0], personas[1]
        snap = items[0]["ranking_snapshot_id"]
        saved = {"run_id": "r", "evaluated_at": "2026-01-01T00:00:00+00:00", "items": [{
            "persona_id": p, "action_id": "promote_to_canary_candidate", "rationale": f"Provider {p}.",
            "evidence_ref_ids": [], "ranking_snapshot_id": snap, "governance_request": None,
            "recommendation_id": f"pm12-2026-q1-{p}-promote_to_canary_candidate",
        } for p in (visible, hidden)]}
        monkeypatch.setattr(evaluator_results, "saved_evaluator_result", lambda *a, **k: saved)
        assert {i["persona_id"] for i in _items(client)} == {visible, hidden}  # same caller scope: stable
        # Caller scope no longer includes `hidden`: saved evaluator output must not leak it.
        real_filter = personas_service._pm12_filter_persona_items

        def scoped(rows, **kwargs):
            return [r for r in real_filter(rows, **kwargs) if r.get("persona_id") != hidden]

        monkeypatch.setattr(personas_service, "_pm12_filter_persona_items", scoped)
        assert {i["persona_id"] for i in _items(client)} == {visible}
        reviews = client.get("/bff/management/promotion-reviews", headers=HEADERS, params={"quarter": "2026-Q1"})
        assert {r["persona_id"] for r in reviews.json()["data"]["items"]} <= {visible}
