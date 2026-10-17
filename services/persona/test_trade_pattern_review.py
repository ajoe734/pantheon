from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.persona.test_trade_reflection_owner import MockReflectionProvider, _auth_header
from services.persona.trade_pattern_review import pattern_identity, qualifying_episodes
from services.persona.write_owner import CreatePersonaRequest, PersistentPersonaOwner, create_app

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "persona-evaluator-agent"))
import persona_evaluator_agent as agent  # noqa: E402

SERVICE = {"Authorization": "Bearer persona-svc-token"}
URL = "/api/personas/p-alpha/trade-reflections:pattern-review"


def _episode(episode_id: str, **fields: Any) -> dict[str, Any]:
    return {"trade_episode_id": episode_id, "persona_id": "p-alpha", "tenant_id": "tenant-1", "status": "closed", **fields}


@pytest.fixture
def stack(monkeypatch, tmp_path):
    monkeypatch.setenv("PERSONA_AUTH_MODE", "strict")
    monkeypatch.setenv("PERSONA_JWT_SECRET", "persona-reflection-test-secret")
    monkeypatch.setenv("PANTHEON_PERSONA_SERVICE_TOKEN", "persona-svc-token")
    owner = PersistentPersonaOwner.from_json_path(tmp_path / "personas.json")
    owner.create(CreatePersonaRequest(actor_id="fixture", persona_id="p-alpha", name="A", mandate="M", tenant_id="tenant-1"))
    state: dict[str, Any] = {"rows": [_episode("ep-1"), _episode("ep-2")], "listed": 0, "provider": MockReflectionProvider()}

    def lister(persona_id: str, tenant_id: str) -> list[dict[str, Any]]:
        state["listed"] += 1
        if isinstance(state["rows"], Exception):
            raise state["rows"]
        return state["rows"]

    def telemetry_fetcher(episode_id, tenant_id, authorization):
        return _episode(episode_id)

    state["client"] = TestClient(create_app(owner, reflection_provider=state["provider"], pattern_episode_lister=lister, telemetry_fetcher=telemetry_fetcher))
    state["owner"] = owner
    return state


def _stored(stack) -> list[dict[str, Any]]:
    return stack["client"].get("/api/personas/p-alpha/trade-reflections", headers=_auth_header("tenant-1")).json()["data"]


def test_qualification_is_distinct_closed_and_scoped():
    rows = [_episode("a"), _episode("a"), _episode("b", status="open"), _episode("c", persona_id="other"), _episode("d", tenant_id="other")]
    assert [r["trade_episode_id"] for r in qualifying_episodes(rows, "p-alpha", "tenant-1")] == ["a"]
    assert pattern_identity(["b", "a"]) == pattern_identity(["a", "b", "a"]) != pattern_identity(["a", "c"])


def test_reviews_once_and_rerun_makes_no_second_provider_call(stack):
    first = stack["client"].post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE)
    assert first.status_code == 200 and first.json()["data"]["status"] == "reviewed"
    again = stack["client"].post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE)
    assert again.json()["data"]["status"] == "unchanged"
    assert stack["provider"].calls == 1
    rows = _stored(stack)
    assert [r["trigger"] for r in rows] == ["scheduled_pattern"]
    assert rows[0]["trade_episode_id"] == pattern_identity(["ep-1", "ep-2"])
    assert rows[0]["review_state"] == "proposed"


def test_status_or_fact_change_on_covered_episodes_makes_no_provider_call(stack):
    stack["client"].post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE)
    stack["rows"] = [_episode("ep-1", status="reflected", updated_at="later", realized_pnl=9.0), _episode("ep-2")]
    again = stack["client"].post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE).json()["data"]
    assert again["status"] == "unchanged" and stack["provider"].calls == 1


def test_third_episode_does_not_rereview_the_pair_or_overlap(stack):
    stack["client"].post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE)
    stack["rows"].append(_episode("ep-3"))
    assert stack["client"].post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE).json()["data"]["status"] == "unchanged"
    stack["rows"].append(_episode("ep-4"))
    assert stack["client"].post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE).json()["data"]["status"] == "reviewed"
    rows = _stored(stack)
    covered = [set(r["covered_episode_ids"]) for r in rows]
    assert covered == [{"ep-1", "ep-2"}, {"ep-3", "ep-4"}] and stack["provider"].calls == 2


def test_write_rejects_overlap_when_request_read_stale_existing_rows(stack, monkeypatch):
    stack["client"].post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE)
    stack["rows"].append(_episode("ep-3"))
    import services.persona.write_owner as write_owner
    real = write_owner.review_pattern
    monkeypatch.setattr(write_owner, "review_pattern", lambda **kw: real(**{**kw, "existing": []}))
    result = stack["client"].post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE).json()["data"]
    assert result["status"] == "unchanged" and result["reflection_id"] is None
    assert [set(r["covered_episode_ids"]) for r in _stored(stack)] == [{"ep-1", "ep-2"}]


def test_history_larger_than_cap_is_reviewed_in_bounded_batches(stack, monkeypatch):
    monkeypatch.setattr("services.persona.trade_pattern_review.MAX_REVIEW_EPISODES", 3)
    stack["rows"] = [_episode(f"ep-{i}", opened_at=f"2026-01-0{i}") for i in range(1, 8)]
    for _ in range(4):
        stack["client"].post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE)
    sizes = [len(r["covered_episode_ids"]) for r in _stored(stack)]
    assert sizes == [3, 3] and stack["provider"].calls == 2  # ep-7 alone stays pending


def test_fewer_than_two_episodes_is_a_no_op_without_provider_or_write(stack):
    stack["rows"] = [_episode("ep-1"), _episode("ep-2", status="open")]
    result = stack["client"].post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE).json()["data"]
    assert result["status"] == "no_op" and result["reason"] == "insufficient_closed_episodes"
    assert stack["provider"].calls == 0 and _stored(stack) == []


def test_provider_failure_leaves_nothing_and_next_run_retries(stack):
    stack["provider"].fail = True
    assert stack["client"].post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE).status_code == 503
    assert _stored(stack) == []
    stack["provider"].fail = False
    assert stack["client"].post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE).json()["data"]["status"] == "reviewed"


def test_telemetry_failure_is_503_without_write(stack):
    stack["rows"] = RuntimeError("telemetry down")
    assert stack["client"].post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE).status_code == 503
    assert _stored(stack) == [] and stack["provider"].calls == 0


@pytest.mark.parametrize("headers,body,expected", [
    ({}, {"tenant_id": "tenant-1"}, 401),
    ({"Authorization": "Bearer wrong"}, {"tenant_id": "tenant-1"}, 401),
    (SERVICE, {}, 403),
    (SERVICE, {"tenant_id": "tenant-2"}, 403),
    (_auth_header("tenant-2"), {"tenant_id": "tenant-2"}, 403),
])
def test_missing_credential_or_tenant_fails_closed_with_zero_work(stack, headers, body, expected):
    assert stack["client"].post(URL, json=body, headers=headers).status_code == expected
    assert stack["listed"] == 0 and stack["provider"].calls == 0 and _stored(stack) == []


def test_pattern_and_manual_retry_never_clobber_each_other(stack):
    auth = _auth_header("tenant-1")
    stack["client"].post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE)
    retry = stack["client"].post("/api/personas/p-alpha/trade-journal/ep-1/reflection:retry", headers={**auth, "Idempotency-Key": "k1"}, json={"reason": "again"})
    assert retry.status_code == 202
    assert sorted(r["trigger"] for r in _stored(stack)) == ["manual_retry", "scheduled_pattern"]
    stack["client"].post("/api/personas/p-alpha/trade-journal/ep-1/reflection:retry", headers={**auth, "Idempotency-Key": "k2"}, json={"reason": "third"})
    stack["rows"] += [_episode("ep-3"), _episode("ep-4")]
    stack["client"].post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE)
    assert sorted(r["trigger"] for r in _stored(stack)) == ["manual_retry", "scheduled_pattern", "scheduled_pattern"]


def test_agent_host_fails_closed_without_credential_or_tenant():
    def forbidden(*args, **kwargs):
        raise AssertionError("no call may be made")

    for token, tenant in (("", "tenant-1"), ("t", "")):
        result = agent.pattern_review_once(persona_url="http://persona", token=token, tenant=tenant, fetch=forbidden)
        assert result["status"] == "degraded" and result["reviewed"] == 0


def test_agent_host_counts_failures_and_skips_other_tenants():
    posted = []

    def fetch(url, data=None, headers=None, timeout=20):
        if data is None:
            return [{"persona_id": "a", "tenant_id": "t"}, {"persona_id": "b", "tenant_id": "t"}, {"persona_id": "x", "tenant_id": "other"}]
        posted.append(url)
        if "/a/" in url:
            raise RuntimeError("503")
        return {"data": {"status": "no_op"}}

    result = agent.pattern_review_once(persona_url="http://persona", token="t", tenant="t", fetch=fetch)
    assert result == {"status": "degraded", "reviewed": 0, "unchanged": 0, "no_op": 1, "failed": 1}
    assert len(posted) == 2


def test_real_provider_transport_uses_adapter_token_and_dedupes(stack, monkeypatch):
    from services.persona.test_trade_reflection_owner import _patch_transport
    from services.persona.write_owner import OpenClawReflectionProvider, create_app

    monkeypatch.setenv("PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN", "adapter-svc-token")
    sent = _patch_transport(monkeypatch)
    provider = OpenClawReflectionProvider(adapter_url="http://adapter:8104")
    client = TestClient(create_app(stack["owner"], reflection_provider=provider, pattern_episode_lister=lambda p, t: stack["rows"], telemetry_fetcher=lambda e, t, a: _episode(e)))
    first = client.post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE)
    assert first.status_code == 200 and first.json()["data"]["status"] == "reviewed"
    calls = len(sent)
    assert calls >= 1 and {r.get_header("X-pantheon-service-token") for r in sent} == {"adapter-svc-token"}
    assert client.post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE).status_code == 200
    assert len(sent) == calls


def test_real_provider_missing_adapter_token_is_visible_failure(stack, monkeypatch):
    from services.persona.write_owner import OpenClawReflectionProvider, create_app

    monkeypatch.delenv("PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN", raising=False)
    provider = OpenClawReflectionProvider(adapter_url="http://adapter:8104")
    client = TestClient(create_app(stack["owner"], reflection_provider=provider, pattern_episode_lister=lambda p, t: stack["rows"], telemetry_fetcher=lambda e, t, a: _episode(e)))
    resp = client.post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE)
    assert resp.status_code >= 500 or resp.status_code == 503
    assert _stored(stack) == []


def _seed(stack, rows):
    ok, _ = stack["owner"].try_metadata_cas("p-alpha", guard=lambda cur: True, metadata_updates=lambda cur: {"trade_reflections": rows}, actor_id="fixture")
    assert ok


def _row(stack, covered, **changes):
    from services.persona.trade_reflection_pipeline import ReflectionRequest, TradeReflectionPipeline
    art = TradeReflectionPipeline(stack["provider"]).process(ReflectionRequest(
        request_id=f"seed-{covered[0]}", persona_id="p-alpha", trade_episode_ids=tuple(covered),
        trigger="scheduled_pattern", facts={"seed": covered}))
    stack["provider"].calls = 0
    return {**art, "trade_episode_id": pattern_identity(covered), "covered_episode_ids": list(covered), **changes}


def _invalid(stack, covered):
    row = _row(stack, covered)
    row["lesson_candidates"][0]["confidence"] = "medium"
    return row


def test_nested_coverage_missing_refs_make_partial_with_unknowns(stack):
    gap = {"missing_refs": ["opened_event", "attribution"]}
    stack["rows"] = [_episode("ep-1", coverage=gap), _episode("ep-2", coverage=gap)]
    stack["client"].post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE)
    row = _stored(stack)[0]
    assert row["evidence_coverage"] == "partial"
    assert row["unknowns"] == ["missing canonical ref: opened_event", "missing canonical ref: attribution"]


def test_invalid_provider_output_persists_nothing_and_next_run_recovers(stack):
    good = stack["provider"].reflect
    stack["provider"].reflect = lambda **kw: {**good(**kw), "lesson_candidates": [{"lesson": "x", "confidence": "high"}]}
    assert stack["client"].post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE).status_code == 503
    assert _stored(stack) == []
    stack["provider"].reflect = good
    assert stack["client"].post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE).json()["data"]["status"] == "reviewed"


def test_schema_invalid_saved_row_is_quarantined_and_recovered_once(stack):
    old = _invalid(stack, ["ep-1", "ep-2"])
    _seed(stack, [old])
    first = stack["client"].post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE).json()["data"]
    assert first["status"] == "reviewed" and stack["provider"].calls == 1
    rows = _stored(stack)
    assert rows[0] == {**old, "review_state": "quarantined"}
    assert rows[1]["review_state"] == "proposed" and rows[1]["lesson_candidates"][0]["confidence"] == 0.8
    again = stack["client"].post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE).json()["data"]
    assert again["status"] == "unchanged" and stack["provider"].calls == 1 and len(_stored(stack)) == 2


def test_recovery_failure_keeps_old_row_untouched_and_valid_rows_never_recharge(stack):
    valid, old = _row(stack, ["ep-1", "ep-2"]), _invalid(stack, ["ep-3", "ep-4"])
    _seed(stack, [valid, old])
    stack["rows"] = [_episode(f"ep-{i}") for i in range(1, 5)]
    stack["provider"].fail = True
    assert stack["client"].post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE).status_code == 503
    assert _stored(stack) == [valid, old]
    stack["provider"].fail = False
    assert stack["client"].post(URL, json={"tenant_id": "tenant-1"}, headers=SERVICE).json()["data"]["status"] == "reviewed"
    rows = _stored(stack)
    assert rows[0] == valid and rows[1]["review_state"] == "quarantined" and set(rows[2]["covered_episode_ids"]) == {"ep-3", "ep-4"}
    assert stack["provider"].calls == 2  # failed attempt + one recovery; valid pair never recharged
