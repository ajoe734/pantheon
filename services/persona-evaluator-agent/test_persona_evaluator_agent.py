from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import persona_evaluator_agent as pea  # noqa: E402

NOW = 1790000000.0  # 2026-Q3
QUARTER = pea.quarter_of(pea.datetime.fromtimestamp(NOW, pea.timezone.utc))


def _item(pid, state="paper_owner", score=20.0):
    return {"persona_id": pid, "name": pid, "owner_lifecycle_state": state, "stage": "paper_running", "score": score,
            "tier": "tier-4", "eligible": True, "components": {"risk_score": 30},
            "evidence_refs": [{"refId": f"ev-{pid}"}]}


def _fetch(items, recs, *, snapshot="snap-1", ranking_down=False, agent_down=False, status=201, log=None,
           surfaces=None, gov_down=False, conflict_detail=None, readback_404=False, readback_data=None):
    log = log if log is not None else []

    def fetch(url, data=None, headers=None, timeout=20):
        log.append((url, data))
        if "quarterly-ranking" in url:
            if ranking_down:
                raise OSError("down")
            return {"data": {"ranking_snapshot_id": snapshot, "items": items}, "meta": {"surfaces": surfaces or {}}}
        if "/structured" in url:
            if agent_down:
                raise OSError("agent down")
            return {"data": {"output": {"structured_data": {"recommendations": recs}}}}
        if "/api/governance/approvals" in url:
            if gov_down:
                raise TimeoutError("timeout")
            if data is None and url.rstrip("/").split("/")[-1] != "approvals":
                if readback_404:
                    return {"detail": "Approval decision not found", "_http_status": 404}
                if readback_data is not None:
                    return readback_data
                dec_id = url.rstrip("/").split("/")[-1]
                rec0 = recs[0] if recs else {}
                from_s = rec0.get("from_state") or (items[0].get("owner_lifecycle_state") if items else "paper_owner")
                dig = pea.proposal_digest({**rec0, "from_state": from_s})
                return {
                    "decision_id": dec_id, "tenant_id": "t1", "target_id": rec0.get("persona_id", "p1"),
                    "target_type": "persona_lifecycle_transition",
                    "target_version": snapshot,
                    "proposal_content_digest": dig,
                    "_http_status": 200,
                }
            if status == 409:
                return {"detail": conflict_detail or "Approval decision already exists", "_http_status": 409}
            return {
                "decision_id": data["decision_id"],
                "tenant_id": data.get("tenant_id", "t1"),
                "target_id": data.get("target_id", "p1"),
                "target_type": data.get("target_type", "persona_lifecycle_transition"),
                "target_version": data.get("target_version", snapshot),
                "proposal_content_digest": data.get("proposal_content_digest"),
                "_http_status": status,
            }
        raise AssertionError(url)

    return fetch, log


def _run(tmp_path, fetch, now=NOW):
    return pea.run_once(
        store=pea.Store(tmp_path / "state.json"), bff_url="http://bff", bff_headers={}, adapter_url="http://ad",
        adapter_token="t", governance_url="http://gov", governance_token="g", tenant="t1", actor="evaluator",
        fetch=fetch, now=lambda: now,
    )


def _rec(pid, action="freeze_persona"):
    return {"persona_id": pid, "action_id": action, "rationale": "weak risk posture", "evidence_ref_ids": [f"ev-{pid}"]}


def _proposals(log):
    return [d for u, d in log if "/api/governance/approvals" in u and d is not None]


def test_lifecycle_recommendation_creates_one_governance_request_and_saves_result(tmp_path):
    fetch, log = _fetch([_item("p1")], [_rec("p1")])
    out = _run(tmp_path, fetch)
    assert out["status"] == "ok" and out["created"] == 1
    body = _proposals(log)[0]
    assert body["target_type"] == "persona_lifecycle_transition"
    assert body["subject"] == {"persona_id": "p1", "from_state": "paper_owner", "to_state": "frozen"}
    saved = pea.Store(tmp_path / "state.json").load()["results"][f"{QUARTER}|snap-1"]
    assert saved["ranking_snapshot_id"] == "snap-1" and saved["items"][0]["rationale"] == "weak risk posture"
    assert saved["items"][0]["governance_request"]["to_state"] == "frozen"


def test_score_alone_creates_nothing(tmp_path):
    fetch, log = _fetch([_item("p1", score=1.0)], [])
    assert _run(tmp_path, fetch)["created"] == 0
    assert _proposals(log) == []


def test_advisory_and_unsupported_transitions_never_create_requests(tmp_path):
    items = [_item("p1"), _item("p2", state="retired")]
    fetch, log = _fetch(items, [_rec("p1", "reduce_capital_access"), _rec("p1", "suspend_persona"), _rec("p2")])
    out = _run(tmp_path, fetch)
    assert out["recommendations"] == 3 and out["created"] == 0 and _proposals(log) == []


def test_unsourced_or_unknown_agent_output_is_dropped(tmp_path):
    bad = [{**_rec("p1"), "evidence_ref_ids": ["forged"]}, {**_rec("p1"), "rationale": " "}, _rec("ghost")]
    fetch, log = _fetch([_item("p1")], bad)
    assert _run(tmp_path, fetch)["recommendations"] == 0


def test_dedupes_per_persona_and_target_state_and_reuses_saved_result(tmp_path):
    fetch, log = _fetch([_item("p1")], [_rec("p1")])
    _run(tmp_path, fetch)
    out = _run(tmp_path, fetch, now=NOW + 60)
    assert out["reused"] is True and out["created"] == 0 and out["deduped"] == 1
    assert len(_proposals(log)) == 1
    assert len([u for u, _ in log if "/structured" in u]) == 1  # same snapshot: provider not asked again
    fetch2, log2 = _fetch([_item("p1")], [_rec("p1")], snapshot="snap-2")
    assert _run(tmp_path, fetch2, now=NOW + 120)["deduped"] == 1 and _proposals(log2) == []


def test_existing_governance_request_merges_instead_of_creating(tmp_path):
    fetch, log = _fetch([_item("p1")], [_rec("p1")], status=409)
    out = _run(tmp_path, fetch)
    assert out["created"] == 0 and out["deduped"] == 1


def test_rate_limit_per_run_and_per_hour(tmp_path):
    items = [_item(f"p{i}") for i in range(8)]
    fetch, log = _fetch(items, [_rec(f"p{i}") for i in range(8)])
    out = _run(tmp_path, fetch)
    assert out["created"] == pea.MAX_PER_RUN and out["skipped"] == 3
    store = pea.Store(tmp_path / "state.json")
    store.update(lambda s: s.update(created=[NOW + 30] * pea.MAX_PER_HOUR))
    assert _run(tmp_path, fetch, now=NOW + 40)["created"] == 0


def test_degraded_runs_create_nothing_and_are_recorded(tmp_path):
    for kwargs in ({"ranking_down": True}, {"agent_down": True}):
        fetch, log = _fetch([_item("p1")], [_rec("p1")], **kwargs)
        out = _run(tmp_path, fetch)
        assert out["status"] == "degraded" and out["created"] == 0 and _proposals(log) == []
    state = pea.Store(tmp_path / "state.json").load()
    assert state["last_run"]["status"] == "degraded" and state["results"] == {}


def test_unavailable_evidence_records_degraded_and_creates_nothing(tmp_path):
    missing = {**_item("p1"), "telemetry_resolution": "missing", "source_confidence": "unavailable"}
    norefs = {**_item("p1"), "evidence_refs": []}
    cases = [
        ([missing], {}),
        ([norefs], {}),
        ([_item("p1")], {"persona_health": {"status": "unavailable"}}),
    ]
    for items, surfaces in cases:
        fetch, log = _fetch(items, [_rec("p1")], surfaces=surfaces)
        out = _run(tmp_path, fetch)
        assert out["status"] == "degraded" and out["created"] == 0 and _proposals(log) == []
        assert not [u for u, _ in log if "/structured" in u]


def test_unknown_outcomes_count_against_per_run_limit(tmp_path):
    items = [_item(f"p{i}") for i in range(8)]
    fetch, log = _fetch(items, [_rec(f"p{i}") for i in range(8)], gov_down=True)
    out = _run(tmp_path, fetch)
    assert len(_proposals(log)) == pea.MAX_PER_RUN and out["created"] == 0 and out["skipped"] == 8


def test_pending_identity_is_replayed_across_snapshot_change(tmp_path):
    down, log1 = _fetch([_item("p1")], [_rec("p1")], gov_down=True)
    _run(tmp_path, down)
    first = _proposals(log1)[0]
    fetch2, log2 = _fetch([_item("p1")], [_rec("p1")], snapshot="snap-2")
    out = _run(tmp_path, fetch2, now=NOW + 60)
    replay = _proposals(log2)
    assert len(replay) == 1 and replay[0] == first and out["created"] == 1
    assert _run(tmp_path, fetch2, now=NOW + 120)["deduped"] == 1 and len(_proposals(log2)) == 1


def test_agent_has_no_tool_and_no_decision_or_write_path_beyond_propose():
    source = Path(pea.__file__).read_text()
    assert "tools" not in json.dumps(pea.RECOMMENDATIONS_SCHEMA)
    for forbidden in ("/decide", "/review", "/revoke", "/api/persona/", "/capital", "runtime-manager"):
        assert forbidden not in source
    assert source.count("/api/governance/approvals") == 1


def test_transitions_mirror_persona_owner():
    from services.persona.write_owner import _LIFECYCLE_TRANSITIONS

    assert {k: set(v) for k, v in _LIFECYCLE_TRANSITIONS.items()} == pea.LIFECYCLE_TRANSITIONS


def test_read_endpoint_serves_saved_result_with_token(tmp_path):
    fetch, _ = _fetch([_item("p1")], [_rec("p1")])
    _run(tmp_path, fetch)
    server = pea.serve(pea.Store(tmp_path / "state.json"), "tok", 0)
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/api/persona-evaluator/recommendations?quarter={QUARTER}"
    try:
        body = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"X-Pantheon-Service-Token": "tok"})).read())
        assert body["data"]["result"]["items"][0]["persona_id"] == "p1"
        try:
            urllib.request.urlopen(url)
            raise AssertionError("expected 401")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
    finally:
        server.shutdown()


def test_http_preserves_409_response_detail(monkeypatch):
    import io
    import urllib.error

    conflict_payload = {"detail": "Idempotency key has different command content"}
    conflict_bytes = json.dumps(conflict_payload).encode()

    def fake_urlopen(req, timeout=20):
        fp = io.BytesIO(conflict_bytes)
        raise urllib.error.HTTPError(req.full_url, 409, "Conflict", {}, fp)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    res = pea._http("http://gov/api/governance/approvals")
    assert res.get("_http_status") == 409
    assert res.get("detail") == "Idempotency key has different command content"


@pytest.mark.parametrize("conflict_detail", [
    "Idempotency key has different command content",
    "Approval base version is stale",
    "Competing approval command changed the base",
    "Approval decision already exists",
])
def test_distinct_conflict_kinds_unresolved_and_preserve_pending(tmp_path, conflict_detail):
    fetch, log = _fetch([_item("p1")], [_rec("p1")], status=409, conflict_detail=conflict_detail, readback_404=True)
    out = _run(tmp_path, fetch)
    assert out["status"] == "degraded"
    assert out["created"] == 0
    assert out["deduped"] == 0
    assert out["skipped"] == 1
    assert out["conflicts"] == 1
    saved = pea.Store(tmp_path / "state.json").load()
    assert saved["results"][f"{QUARTER}|snap-1"]["items"][0]["governance_request"] is None
    entry = saved["requests"]["p1|frozen"]
    assert entry.get("pending") is not None
    assert entry.get("conflict") is not None
    assert entry["conflict"]["detail"] == conflict_detail


def test_verified_same_content_replay(tmp_path):
    fetch, log = _fetch([_item("p1")], [_rec("p1")], status=200)
    out = _run(tmp_path, fetch)
    assert out["status"] == "ok"
    assert out["created"] == 0
    assert out["deduped"] == 1
    saved = pea.Store(tmp_path / "state.json").load()
    req = saved["results"][f"{QUARTER}|snap-1"]["items"][0]["governance_request"]
    assert req is not None and req["to_state"] == "frozen"
    entry = saved["requests"]["p1|frozen"]
    assert "pending" not in entry


def test_mismatched_readback_treated_as_conflict(tmp_path):
    mismatched_readback = {
        "decision_id": "pev-other", "tenant_id": "other_tenant", "target_id": "p1",
        "proposal_content_digest": "different_digest", "_http_status": 200,
    }
    fetch, _ = _fetch([_item("p1")], [_rec("p1")], status=409, readback_data=mismatched_readback)
    out = _run(tmp_path, fetch)
    assert out["status"] == "degraded"
    assert out["conflicts"] == 1
    assert out["deduped"] == 0
    saved = pea.Store(tmp_path / "state.json").load()
    assert saved["results"][f"{QUARTER}|snap-1"]["items"][0]["governance_request"] is None
    assert saved["requests"]["p1|frozen"].get("pending") is not None


def test_changed_content_never_attaches_to_old_state_pair_request(tmp_path):
    fetch1, _ = _fetch([_item("p1")], [_rec("p1", action="freeze_persona")])
    out1 = _run(tmp_path, fetch1)
    assert out1["created"] == 1
    d1 = pea.Store(tmp_path / "state.json").load()["requests"]["p1|frozen"]["decision_id"]

    rec2 = {**_rec("p1", action="freeze_persona"), "rationale": "changed: severe volatility breach"}
    fetch2, log2 = _fetch([_item("p1")], [rec2], snapshot="snap-2")
    out2 = _run(tmp_path, fetch2, now=NOW + 60)
    assert out2["created"] == 1
    assert out2["deduped"] == 0
    d2 = pea.Store(tmp_path / "state.json").load()["requests"]["p1|frozen"]["decision_id"]
    assert d2 != d1


def test_changed_snapshot_after_retention_window_creates_new_proposal(tmp_path):
    fetch1, _ = _fetch([_item("p1")], [_rec("p1")])
    out1 = _run(tmp_path, fetch1, now=NOW)
    assert out1["created"] == 1

    fetch2, _ = _fetch([_item("p1")], [_rec("p1")], snapshot="snap-2")
    out2 = _run(tmp_path, fetch2, now=NOW + pea.DEDUPE_TTL_SECONDS + 10)
    assert out2["created"] == 1
    assert out2["deduped"] == 0


def test_restart_reloads_pending_identity_and_retries(tmp_path):
    down_fetch, _ = _fetch([_item("p1")], [_rec("p1")], gov_down=True)
    out1 = _run(tmp_path, down_fetch, now=NOW)
    assert out1["created"] == 0
    assert pea.Store(tmp_path / "state.json").load()["requests"]["p1|frozen"].get("pending") is not None

    # Restart: instantiate fresh Store from disk
    store2 = pea.Store(tmp_path / "state.json")
    up_fetch, log = _fetch([_item("p1")], [_rec("p1")])
    out2 = pea.run_once(
        store=store2, bff_url="http://bff", bff_headers={}, adapter_url="http://ad",
        adapter_token="t", governance_url="http://gov", governance_token="g", tenant="t1", actor="evaluator",
        fetch=up_fetch, now=lambda: NOW + 30,
    )
    assert out2["created"] == 1
    assert len(_proposals(log)) == 1
    assert "pending" not in store2.load()["requests"]["p1|frozen"]


def test_changed_content_after_unknown_outcome_does_not_attach_old_proposal(tmp_path):
    down, log1 = _fetch([_item("p1")], [_rec("p1")], gov_down=True)
    _run(tmp_path, down)
    original = _proposals(log1)[0]
    changed = {**_rec("p1"), "rationale": "different provider judgment after new evidence"}
    up, log2 = _fetch([_item("p1")], [changed], snapshot="snap-2")
    _run(tmp_path, up, now=NOW + 30)
    assert _proposals(log2)[0] == original  # unknown identity must be retried unchanged
    saved = pea.Store(tmp_path / "state.json").load()
    rec = saved["results"][f"{QUARTER}|snap-2"]["items"][0]
    assert pea.proposal_digest(rec) != original["proposal_content_digest"]
    assert rec["governance_request"] is None, "changed recommendation attached to old content decision"


def test_incomplete_200_is_not_verified_same_content_replay(tmp_path):
    fallback, _ = _fetch([_item("p1")], [_rec("p1")], readback_404=True)

    def fetch(url, data=None, **kwargs):
        if "/api/governance/approvals" in url and data is not None:
            return {"_http_status": 200}
        return fallback(url, data=data, **kwargs)

    result = _run(tmp_path, fetch)
    assert result["deduped"] == 0, "missing owner identity/content was fabricated from local defaults"
    saved = pea.Store(tmp_path / "state.json").load()
    assert saved["requests"]["p1|frozen"].get("pending")


def test_incomplete_create_keeps_hourly_reservation(tmp_path):
    attempted = []
    for n in range(5):
        ids = [f"p{n * 5 + i}" for i in range(5)]
        fallback, _ = _fetch([_item(p) for p in ids], [_rec(p) for p in ids],
                             snapshot=f"snap-{n}", readback_404=True)
        def fetch(url, data=None, **kwargs):
            if "/api/governance/approvals" in url and data is not None:
                attempted.append(data["decision_id"])
                return {"_http_status": 201}
            return fallback(url, data=data, **kwargs)
        _run(tmp_path, fetch, now=NOW + n * 30)
    assert len(set(attempted)) <= pea.MAX_PER_HOUR


def test_owner_response_must_match_target_type_and_version(tmp_path):
    fallback, _ = _fetch([_item("p1")], [_rec("p1")], readback_404=True)
    def fetch(url, data=None, **kwargs):
        if "/api/governance/approvals" in url and data is not None:
            return {**data, "_http_status": 200, "target_type": "capital_allocation",
                    "target_version": "different-snapshot"}
        return fallback(url, data=data, **kwargs)
    out = _run(tmp_path, fetch)
    assert out["deduped"] == 0
    state = pea.Store(tmp_path / "state.json").load()
    assert state["requests"]["p1|frozen"].get("pending")
    assert state["results"][f"{QUARTER}|snap-1"]["items"][0]["governance_request"] is None


def test_readback_resolved_201_is_treated_as_created(tmp_path):
    fallback, log = _fetch([_item("p1")], [_rec("p1")])
    def fetch(url, data=None, **kwargs):
        if "/api/governance/approvals" in url and data is not None:
            return {"_http_status": 201}
        return fallback(url, data=data, **kwargs)
    out = _run(tmp_path, fetch)
    assert out["created"] == 1
    assert out["deduped"] == 0
    state = pea.Store(tmp_path / "state.json").load()
    assert len(state["created"]) == 1
    assert "pending" not in state["requests"]["p1|frozen"]
    assert state["results"][f"{QUARTER}|snap-1"]["items"][0]["governance_request"] is not None
