from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import persona_evaluator_agent as pea  # noqa: E402

NOW = 1790000000.0  # 2026-Q3
QUARTER = pea.quarter_of(pea.datetime.fromtimestamp(NOW, pea.timezone.utc))


def _item(pid, state="paper_owner", score=20.0):
    return {"persona_id": pid, "name": pid, "state": state, "stage": "paper_running", "score": score,
            "tier": "tier-4", "eligible": True, "components": {"risk_score": 30},
            "evidence_refs": [{"refId": f"ev-{pid}"}]}


def _fetch(items, recs, *, snapshot="snap-1", ranking_down=False, agent_down=False, status=201, log=None):
    log = log if log is not None else []

    def fetch(url, data=None, headers=None, timeout=20):
        log.append((url, data))
        if "quarterly-ranking" in url:
            if ranking_down:
                raise OSError("down")
            return {"data": {"ranking_snapshot_id": snapshot, "items": items}}
        if "/structured" in url:
            if agent_down:
                raise OSError("agent down")
            return {"data": {"output": {"structured_data": {"recommendations": recs}}}}
        if "/api/governance/approvals" in url:
            return {"decision_id": data["decision_id"], "_http_status": status}
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
    return [d for u, d in log if "/api/governance/approvals" in u]


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
