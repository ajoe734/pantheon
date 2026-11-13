from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import persona_evaluator_agent as pea  # noqa: E402

NOW = 1790000000.0  # 2026-Q3
QUARTER = pea.quarter_of(pea.datetime.fromtimestamp(NOW, pea.timezone.utc))


def _item(pid, state="paper_owner", score=40.0):
    return {"persona_id": pid, "name": pid, "owner_lifecycle_state": state, "stage": "paper_running", "score": score,
            "tier": "tier-4", "eligible": True, "components": {"risk_score": 60, "execution_score": 70, "activity_score": 70},
            "evidence_refs": [{"refId": f"ev-{pid}"}]}


def _fetch(items, recs, *, snapshot="snap-1", ranking_down=False, agent_down=False, status=201, log=None,
           surfaces=None, gov_down=False, conflict_detail=None, readback_404=False, readback_data=None):
    log = log if log is not None else []
    items = [{**item, "formula_version": snapshot} for item in items]
    snapshot = pea.snapshot_record(items, surface="quarterly", period=QUARTER).ranking_snapshot_id

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



def _sid(label="snap-1"):
    return pea.snapshot_record([{**_item("p1"), "formula_version": label}], surface="quarterly", period=QUARTER).ranking_snapshot_id


@pytest.fixture(autouse=True)
def ranking_owner(monkeypatch, request):
    monkeypatch.setenv("PANTHEON_CONSULTATION_API_URL", "http://consult")
    monkeypatch.setenv("CONSULTATION_SERVICE_TOKEN", "test-panel-token")
    real_dsn = os.getenv("PANTHEON_PM12_TEST_POSTGRES_DSN")
    if real_dsn:
        import uuid
        monkeypatch.setenv("RANKING_STORE_DSN", real_dsn)
        monkeypatch.setenv("RANKING_STORE_TABLE", "rankings.pm12_" + uuid.uuid4().hex[:12])
        return
    from services.rankings.test_store import _FakeConnection, _fake_psycopg
    from services.rankings.store import RankingWriteStore
    monkeypatch.setattr(_FakeConnection, "rows", {})
    monkeypatch.setattr(_FakeConnection, "statements", [])
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg())
    monkeypatch.setattr(pea, "build_rankings_store", lambda: RankingWriteStore("postgresql://isolated/evaluator"))


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
    saved = pea.Store(tmp_path / "state.json").load()["results"][f"{QUARTER}|{_sid()}"]
    assert saved["ranking_snapshot_id"] == _sid() and saved["items"][0]["rationale"] == "weak risk posture"
    assert saved["items"][0]["governance_request"]["to_state"] == "frozen"


def test_rationale_writer_cannot_omit_fixed_low_score_actions(tmp_path):
    fetch, log = _fetch([_item("p1", score=1.0)], [])
    assert _run(tmp_path, fetch)["created"] == 2
    assert {p["subject"]["to_state"] for p in _proposals(log)} == {"frozen"}


def test_advisory_and_unsupported_transitions_never_create_requests(tmp_path):
    items = [_item("p1"), _item("p2", state="retired")]
    fetch, log = _fetch(items, [_rec("p1", "reduce_capital_access"), _rec("p1", "suspend_persona"), _rec("p2")])
    out = _run(tmp_path, fetch)
    assert out["recommendations"] == 6 and out["created"] == 1
    assert len(_proposals(log)) == 1  # the agent cannot remove the fixed freeze for p1


def test_unsourced_or_unknown_agent_output_is_dropped(tmp_path):
    bad = [{**_rec("p1"), "evidence_ref_ids": ["forged"]}, {**_rec("p1"), "rationale": " "}, _rec("ghost")]
    fetch, log = _fetch([_item("p1")], bad)
    assert _run(tmp_path, fetch)["recommendations"] == 3  # fixed actions survive bad rationale output


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


def test_ranking_outage_is_recorded_but_rationale_outage_cannot_veto_mapping(tmp_path):
    fetch, log = _fetch([_item("p1")], [_rec("p1")], ranking_down=True)
    assert _run(tmp_path, fetch)["status"] == "degraded"
    assert _proposals(log) == []
    fetch, log = _fetch([_item("p1")], [_rec("p1")], agent_down=True)
    assert _run(tmp_path, fetch)["created"] == 1
    state = pea.Store(tmp_path / "state.json").load()
    assert next(iter(state["results"].values()))["provider"] == "fixed_rule"


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
    assert saved["results"][f"{QUARTER}|{_sid()}"]["items"][0]["governance_request"] is None
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
    req = saved["results"][f"{QUARTER}|{_sid()}"]["items"][0]["governance_request"]
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
    assert saved["results"][f"{QUARTER}|{_sid()}"]["items"][0]["governance_request"] is None
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
    rec = saved["results"][f"{QUARTER}|{_sid('snap-2')}"]["items"][0]
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
    assert state["results"][f"{QUARTER}|{_sid()}"]["items"][0]["governance_request"] is None


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
    assert state["results"][f"{QUARTER}|{_sid()}"]["items"][0]["governance_request"] is not None


def test_token_files_are_read_every_run_and_env_is_only_the_fallback(tmp_path, monkeypatch):
    path = tmp_path / "TOKEN"
    monkeypatch.setenv("T", "env-token")
    assert pea.read_token("T") == "env-token"
    monkeypatch.setenv("T_FILE", str(path))
    path.write_text("first\n")
    assert pea.read_token("T") == "first"
    path.write_text("rotated\n")
    assert pea.read_token("T") == "rotated"
    path.unlink()
    with pytest.raises(pea.Degraded):
        pea.read_token("T")
    path.write_text("")
    with pytest.raises(pea.Degraded):
        pea.read_token("T")


def test_main_uses_rotated_token_and_records_missing_file_as_degraded(tmp_path, monkeypatch):
    bff, gov = tmp_path / "bff", tmp_path / "gov"
    bff.write_text("bff-1"), gov.write_text("gov-1")
    seen = []
    monkeypatch.setattr(pea, "serve", lambda *a: None)
    monkeypatch.setattr(pea, "run_once", lambda **kw: seen.append((kw["bff_headers"], kw["governance_token"])) or {"status": "ok"})
    for key, value in {"PERSONA_EVALUATOR_BFF_TOKEN_FILE": bff, "PERSONA_EVALUATOR_GOVERNANCE_TOKEN_FILE": gov,
                       "PERSONA_EVALUATOR_STATE_PATH": tmp_path / "state.json", "PERSONA_EVALUATOR_ONCE": "1"}.items():
        monkeypatch.setenv(key, str(value))
    pea.main()
    bff.write_text("bff-2"), gov.write_text("gov-2")
    pea.main()
    gov.unlink()
    pea.main()
    assert seen == [({"Authorization": "Bearer bff-1"}, "gov-1"), ({"Authorization": "Bearer bff-2"}, "gov-2")]
    assert pea.Store(tmp_path / "state.json").load()["last_run"]["status"] == "degraded"


def _skipped(pid, **over):
    return {**_item(pid), "eligible": False, "telemetry_resolution": "missing", "source_confidence": "unavailable",
            "evidence_refs": [], **over}


def _prompt_ids(log):
    prompts = [d["prompt"] for u, d in log if "/structured" in u]
    return [json.loads(p.split("PERSONAS=", 1)[1]) for p in prompts]


def test_explicit_ineligible_missing_evidence_rows_are_skipped_and_snapshot_is_full(tmp_path):
    items = [_skipped(f"x{i}") for i in range(72)] + [_item("p1"), _item("p2")]
    fetch, log = _fetch(items, [_rec("p1")])
    got, snap = pea.collect_evidence("http://bff", QUARTER, {}, fetch)
    assert [i["persona_id"] for i in got] == ["p1", "p2"]
    full = pea.snapshot_record([{**i, "formula_version": "snap-1"} for i in items], surface="quarterly", period=QUARTER)
    assert snap.ranking_snapshot_id == full.ranking_snapshot_id
    out = _run(tmp_path, fetch)
    assert out["status"] != "degraded" and out["created"] == 2
    assert [p["persona_id"] for p in _prompt_ids(log)[0]] == ["p1", "p2"]


def test_ineligible_with_valid_evidence_is_still_model_input():
    ine = {**_item("p0"), "eligible": False}
    fetch, _ = _fetch([ine, _item("p1")], [])
    got, _ = pea.collect_evidence("http://bff", QUARTER, {}, fetch)
    assert [i["persona_id"] for i in got] == ["p0", "p1"]


@pytest.mark.parametrize("eligible", [True, "false", 0, None, "absent"])
def test_non_strict_false_eligibility_with_missing_evidence_degrades(tmp_path, eligible):
    bad = _skipped("p0")
    if eligible == "absent":
        del bad["eligible"]
    else:
        bad["eligible"] = eligible
    fetch, log = _fetch([bad, _item("p1")], [_rec("p1")])
    out = _run(tmp_path, fetch)
    assert out["status"] == "degraded" and out["created"] == 0 and _proposals(log) == []
    assert not [u for u, _ in log if "/structured" in u]


def test_all_excluded_is_degraded_without_provider(tmp_path):
    fetch, log = _fetch([_skipped("a"), _skipped("b")], [_rec("a")])
    out = _run(tmp_path, fetch)
    assert out["status"] == "degraded" and out["created"] == 0 and _proposals(log) == []
    assert not [u for u, _ in log if "/structured" in u]


def test_global_unavailable_surface_still_degrades_with_skippable_rows(tmp_path):
    fetch, log = _fetch([_skipped("a"), _item("p1")], [_rec("p1")], surfaces={"persona_health": {"status": "unavailable"}})
    out = _run(tmp_path, fetch)
    assert out["status"] == "degraded" and not [u for u, _ in log if "/structured" in u]


def test_max_personas_bound_applies_to_admitted_rows_in_order():
    items = [_skipped("x0")] + [_item(f"p{i}") for i in range(pea.MAX_PERSONAS + 5)]
    fetch, _ = _fetch(items, [])
    got, _ = pea.collect_evidence("http://bff", QUARTER, {}, fetch)
    assert [i["persona_id"] for i in got] == [f"p{i}" for i in range(pea.MAX_PERSONAS)]


# --- governed quarterly paper allocation cycle trigger -------------------------------------------------------------

NEXT_QUARTER_NOW = NOW + 120 * 86400  # 2026-Q4 is after 2026-Q3 (NOW)


def _seed_closed_quarter(tmp_path, items=None):
    fetch, _ = _fetch(items or [_item("p1"), _item("p2")], [])
    assert _run(tmp_path, fetch)["status"] == "ok"
    return pea.Store(tmp_path / "state.json")


def _cycle_fetch(log, *, applied=True, fail=False, pools=None):
    def fetch(url, data=None, headers=None, timeout=20):
        log.append((url, data, headers))
        if "/strategy-pool" in url:
            pid = url.split("/personas/")[1].split("/")[0]
            if pools and pid in pools:
                return pools[pid]
            raise OSError("no pool")
        if fail:
            raise RuntimeError("owner refused")
        return {"data": {"applied": applied, "replayed": False, "rebalance": {"rebalance_id": "rb-1"}}}
    return fetch


def _paper_cycle(store, fetch, pool="pool-paper", now=NEXT_QUARTER_NOW):
    return pea.paper_cycle_once(store=store, bff_url="http://bff", bff_headers={"Authorization": "Bearer x"},
                                persona_url="http://persona", persona_token="p", tenant="t1", paper_pool_id=pool,
                                fetch=fetch, now=lambda: now)


def test_saved_snapshot_records_the_paper_personas_for_the_quarter_close_cycle(tmp_path):
    store = _seed_closed_quarter(tmp_path, [_item("p1"), _item("p2", state="frozen") | {"stage": "retired"}])
    assert next(iter(store.load()["results"].values()))["paper_persona_ids"] == ["p1"]


def test_cycle_waits_for_quarter_close_and_needs_a_configured_pool(tmp_path):
    store = _seed_closed_quarter(tmp_path)
    log = []
    assert _paper_cycle(store, _cycle_fetch(log), now=NOW)["status"] == "waiting_for_quarter_close"  # same quarter
    assert _paper_cycle(store, _cycle_fetch(log), pool="")["status"] == "not_configured"
    assert not [e for e in log if "allocation-policy/evaluate" in e[0]]


def test_quarter_close_triggers_exactly_one_cycle_with_the_persisted_snapshot_and_is_remembered(tmp_path):
    store = _seed_closed_quarter(tmp_path)
    snapshot_id = store.load()["latest"][QUARTER]
    log = []
    record = _paper_cycle(store, _cycle_fetch(log))
    assert record["status"] == "applied" and record["quarter"] == QUARTER and record["rebalance_id"] == "rb-1"
    calls = [e for e in log if "allocation-policy/evaluate" in e[0]]
    assert len(calls) == 1
    _, body, headers = calls[0]
    assert body["ranking_snapshot_id"] == snapshot_id and body["quarter"] == QUARTER and body["capital_pool_id"] == "pool-paper"
    assert headers["Idempotency-Key"] == f"paper-cycle:t1:{QUARTER}:pool-paper"
    assert _paper_cycle(store, _cycle_fetch(log))["reused"] is True
    assert len([e for e in log if "allocation-policy/evaluate" in e[0]]) == 1


def test_failed_cycle_is_recorded_and_retried_with_the_same_idempotency_key(tmp_path):
    store = _seed_closed_quarter(tmp_path)
    log = []
    failed = _paper_cycle(store, _cycle_fetch(log, fail=True))
    assert failed["status"] == "degraded" and "owner refused" in failed["reason"]
    assert _paper_cycle(store, _cycle_fetch(log))["status"] == "applied"
    keys = {e[2]["Idempotency-Key"] for e in log if "allocation-policy/evaluate" in e[0]}
    assert len(keys) == 1


def test_pool_health_factors_come_from_recorded_health_and_stay_one_with_a_reason_otherwise():
    health = {"gross_utilisation": {"status": "known", "value": 0.4}, "idle_day_share": {"status": "known", "value": 0.25}}
    factors = pea.pool_health_factors(health, {"budget_full": 0.8})
    assert factors["risk_budget_factor"] == 0.5 and factors["capacity_factor"] == 1.0 and factors["status"] == "partial"
    # regression: utilisation and idle share known, capacity facts absent -> capacity stays 1.0, reason recorded, never "known"
    assert factors["reasons"] == {"capacity_factor": "capacity_observation_unavailable"}
    full = pea.pool_health_factors({"gross_utilisation": {"status": "known", "value": 0.95},
                                    "idle_day_share": {"status": "known", "value": 0.0}}, {"budget_full": 0.8})
    assert full["risk_budget_factor"] == 1.0 and full["capacity_factor"] == 1.0
    unknown = pea.pool_health_factors({"gross_utilisation": {"status": "unknown", "value": None}}, {"budget_full": 0.8})
    assert unknown["risk_budget_factor"] == 1.0 and unknown["capacity_factor"] == 1.0
    assert unknown["status"] == "unavailable" and set(unknown["reasons"]) == {"risk_budget_factor", "capacity_factor"}
    assert pea.pool_health_factors(health, None)["risk_budget_factor"] == 1.0  # no policy: unknown, never invented


def test_collect_pool_health_records_the_reason_when_the_pool_is_missing(tmp_path):
    pool = {"decision": {"policy": {"budget_full": 0.5}, "health": {"gross_utilisation": {"status": "known", "value": 0.25}}}}
    got = pea.collect_pool_health(["p1", "p2"], persona_url="http://persona", token="t", tenant="t1",
                                  fetch=_cycle_fetch([], pools={"p1": pool}))
    assert got["p1"]["risk_budget_factor"] == 0.5
    assert got["p2"]["risk_budget_factor"] == 1.0 and "pool health unavailable" in got["p2"]["reason"]
    none = pea.collect_pool_health(["p1"], persona_url="http://persona", token="", tenant="t1", fetch=_cycle_fetch([]))
    assert none["p1"]["risk_budget_factor"] == 1.0 and none["p1"]["status"] == "unavailable"


def _promotion_item(pid="p1", *, days=30, orders=20, score=90, rank=1):
    from datetime import timedelta
    start = pea.datetime(2026, 7, 1, tzinfo=pea.timezone.utc)
    refs = [{"ref_id": f"event-{n}", "source_type": "quarter_equity",
             "observed_at": (start + timedelta(days=n)).isoformat()} for n in range(days)]
    return {**_item(pid, score=score), "components": {"risk_score": 80, "execution_score": 80, "activity_score": 80}, "rank": rank, "exclusion_codes": [],
            "formula_version": "pm12-return-on-capital-v2", "evidence_ref_ids": ["ev-" + pid],
            "metrics": {"pnl_return_status": "known", "pnl_return": 0.9,
                        "drawdown_status": "known", "drawdown": 0.01,
                        "pnl_period": {"quarter": QUARTER}, "fill_rate": 0.99,
                        "telemetry_evidence_refs": refs,
                        "fill_rate_details": {"status": "known", "window": {"quarter": QUARTER}, "sources": [
                            {"runtime_id": "r1", "binding_id": "b1", "order_id": f"o{n}",
                             "submitted_at": start.isoformat(), "submitted_quantity": 10, "filled_quantity": 9,
                             "event_ids": [f"submit-{n}", f"fill-{n}"]} for n in range(orders)]}}}


def _seed_promotion(tmp_path, items=None):
    items = items if items is not None else [_promotion_item()]
    snapshot = pea.snapshot_record(items, surface="quarterly", period=QUARTER)
    pea.admit_snapshot(pea.build_rankings_store(), snapshot)
    store = pea.Store(tmp_path / "state.json")
    store.update(lambda s: s.update(latest={QUARTER: snapshot.ranking_snapshot_id}, results={
        f"{QUARTER}|{snapshot.ranking_snapshot_id}": {"items": [], "run_id": "r", "evaluated_at": "t"}}))
    return store, snapshot


def _promotion_fetch(log, *, agent_down=False, gov_down=False, probe=False, tenant="t1", panel_pending=False):
    panel = {}
    def fetch(url, data=None, headers=None, timeout=20):
        log.append((url, data))
        if "/api/consult/requests" in url:
            if url.endswith("/requests"):
                panel.update(data)
                return {**data, "status": "submitted"}
            if url.endswith("/transcript"):
                from services.persona.consult_triggers import RISK_PERSONA_ID, RED_TEAM_PERSONA_ID
                return {"request_id": panel["request_id"], "events": [] if panel_pending else [
                    {"actor": {"actor_type": "persona", "actor_id": pid},
                     "content": {"recommendation": "approve", "claim": "Recorded panel conclusion"},
                     "evidence_refs": ["panel-evidence-" + pid]} for pid in (RISK_PERSONA_ID, RED_TEAM_PERSONA_ID)]}
        if "/api/consult/memos" in url:
            return [{"request_id": panel["request_id"], "memo_id": "memo-panel-1", "status": "published",
                     "memo_type": "committee_summary", "author_ref": "consult-panel:" + panel["request_id"],
                     "target_type": "trial_admission", "target_id": "p1", "summary": "Risk and red team reviewed",
                     "recommendation": "approve"}]
        if "/api/personas/" in url:
            return {"persona_id": "p1", "tenant_id": tenant, "metadata": {"acceptance_probe": probe}}
        if "/structured" in url:
            if agent_down:
                raise OSError("agent down")
            return {"data": {"output": {"structured_data": {"rationale": "Fixed evidence supports nomination."}}}}
        if gov_down:
            raise TimeoutError("outcome unknown")
        return {**data, "metadata": {"subject": data["subject"]}, "decision_state": "proposed"}
    return fetch


def _promote(store, fetch, **kwargs):
    return pea.promotion_once(store=store, tenant="t1", actor="evaluator", governance_url="http://gov",
                              governance_token="g", persona_url="http://persona", persona_token="p",
                              adapter_url="http://ad", adapter_token="a", fetch=fetch,
                              now=lambda: NEXT_QUARTER_NOW, **kwargs)


def test_fixed_promotion_survives_agent_outage_and_rerun_and_catchup(tmp_path):
    store, snapshot = _seed_promotion(tmp_path)
    log = []
    first = _promote(store, _promotion_fetch(log, agent_down=True))
    assert first["status"] == "proposed", first
    assert first["recommendation"]["provider"] == "fixed_rule"
    assert first["request"]["body"]["target_type"] == pea.PROMOTION_TARGET
    assert first["request"]["body"]["target_version"] == snapshot.ranking_snapshot_id
    assert _promote(pea.Store(store.path), _promotion_fetch(log))["reused"] is True
    # A different later snapshot for this closed quarter must never nominate a second persona.
    store.update(lambda s: s["latest"].update({QUARTER: "later-snapshot"}))
    assert _promote(store, _promotion_fetch(log))["decision_id"] == first["decision_id"]
    assert len(_proposals(log)) == 1
    assert "observed_trading_days" in first["recommendation"]["rationale"]


@pytest.mark.parametrize("items,reason", [
    ([_promotion_item(), _promotion_item("p2", rank=1)], "rank_one_tie"),
    ([_promotion_item(), _promotion_item("p2", rank=2)], "rank_one_tie_or_inconsistent_order"),
    ([_promotion_item(days=29)], "insufficient_quarter_sample"),
    ([_promotion_item(orders=19)], "insufficient_quarter_sample"),
    ([_promotion_item() | {"eligible": False}], "rank_one_excluded_or_eligibility_unknown"),
    ([_promotion_item() | {"exclusion_codes": ["release_probe"]}], "rank_one_excluded_or_eligibility_unknown"),
    ([_promotion_item() | {"score": None}], "unknown_ranking_metric"),
    ([_promotion_item() | {"metrics": {}}], "unknown_ranking_metric"),
    ([_promotion_item() | {"formula_version": "unknown"}], "unknown_ranking_formula"),
])
def test_promotion_fail_closed_records_readable_reason(tmp_path, items, reason):
    store, snapshot = _seed_promotion(tmp_path, items)
    log = []
    record = _promote(store, _promotion_fetch(log))
    assert record["reason"] == reason
    assert _proposals(log) == []
    assert store.load()["last_promotion"]["reason"] == reason
    if record["status"] == "no_nomination":
        assert pea.saved_result(store.load(), QUARTER, snapshot.ranking_snapshot_id)["promotion_nomination"][0]["reason"] == reason


@pytest.mark.parametrize("probe,tenant,reason", [(True, "t1", "release_or_acceptance_probe"),
                                                (False, "foreign", "persona_identity_or_tenant_mismatch")])
def test_probe_or_foreign_persona_is_never_nominated(tmp_path, probe, tenant, reason):
    store, _ = _seed_promotion(tmp_path)
    log = []
    assert _promote(store, _promotion_fetch(log, probe=probe, tenant=tenant))["reason"] == reason
    assert _proposals(log) == []


def test_unknown_governance_outcome_replays_frozen_request(tmp_path):
    store, _ = _seed_promotion(tmp_path)
    log = []
    assert _promote(store, _promotion_fetch(log, gov_down=True))["status"] == "degraded"
    assert _promote(pea.Store(store.path), _promotion_fetch(log))["status"] == "proposed"
    proposals = _proposals(log)
    assert len(proposals) == 2 and proposals[0] == proposals[1]


def test_promotion_config_and_sample_identity_deduplication(tmp_path):
    item = _promotion_item(days=2, orders=1)
    item["metrics"]["fill_rate_details"]["sources"] *= 50
    item["metrics"]["telemetry_evidence_refs"] *= 40
    candidate, summary = pea.nomination_rule([item], QUARTER, min_days=3, min_orders=2)
    assert candidate is None and summary["observed_trading_days"] == 2 and summary["filled_orders"] == 1
    store, _ = _seed_promotion(tmp_path, [item])
    assert _promote(store, _promotion_fetch([]), min_days=2, min_orders=1)["status"] == "proposed"


def test_agent_cannot_choose_promotion():
    assert "promote_to_canary_candidate" not in pea.ACTIONS
    fetch, _ = _fetch([_item("p1")], [_rec("p1", "promote_to_canary_candidate")])
    assert all(r["action_id"] != "promote_to_canary_candidate" for r in pea.ask_agent(
        [{**_item("p1"), "evidence_ref_ids": ["ev-p1"]}], "http://ad", "t", fetch))


@pytest.mark.parametrize('metadata', [{'data_scope': 'acceptance'}, {'is_acceptance': 'true'}, {'release_probe': True}])
def test_canonical_test_scope_always_excludes_nomination(metadata):
    assert pea._probe_persona({'persona_id': 'ordinary-name', 'metadata': metadata})
    assert not pea._probe_persona({'persona_id': 'ordinary-name', 'metadata': {'acceptance_probe': False}})


def test_promotion_threshold_environment_is_configurable_and_invalid_values_fail_closed(tmp_path, monkeypatch):
    store, _ = _seed_promotion(tmp_path, [_promotion_item(days=2, orders=1)])
    monkeypatch.setenv('PERSONA_EVALUATOR_PROMOTION_MIN_TRADING_DAYS', '2')
    monkeypatch.setenv('PERSONA_EVALUATOR_PROMOTION_MIN_FILLED_ORDERS', '1')
    assert _promote(store, _promotion_fetch([]))['status'] == 'proposed'
    store, _ = _seed_promotion(tmp_path / 'invalid')
    monkeypatch.setenv('PERSONA_EVALUATOR_PROMOTION_MIN_FILLED_ORDERS', '0')
    log = []
    assert _promote(store, _promotion_fetch(log))['reason'] == 'invalid_sample_thresholds'
    assert _proposals(log) == []


@pytest.mark.parametrize("score,risk,execution,activity,expected", [
    (90, 80, 80, 80, {"increase_research_budget", "grant_tool_access"}),
    (70, 60, 55, 45, {"increase_research_budget", "grant_tool_access"}),
    (69, 60, 55, 45, set()),
    (60, 54, 55, 45, {"reduce_capital_access"}),
    (60, 55, 54, 45, {"require_retraining"}),
    (60, 55, 55, 44, {"require_retraining"}),
    (55, 55, 55, 45, set()),
    (54, 55, 55, 45, {"require_retraining", "reduce_capital_access"}),
    (45, 55, 55, 45, {"require_retraining", "reduce_capital_access"}),
    (44, 55, 55, 45, {"require_retraining", "reduce_capital_access", "freeze_persona"}),
    (34, 55, 55, 45, {"require_retraining", "reduce_capital_access", "freeze_persona", "suspend_persona"}),
    (24, 55, 55, 45, {"require_retraining", "reduce_capital_access", "freeze_persona", "suspend_persona", "retire_persona"}),
])
def test_fixed_governance_mapping_boundaries(score, risk, execution, activity, expected):
    item = {**_item("p1", score=score), "components": {
        "risk_score": risk, "execution_score": execution, "activity_score": activity}}
    assert set(pea.fixed_actions(item)) == expected


@pytest.mark.parametrize("changes", [
    {"score": None}, {"score": True}, {"score": float("nan")}, {"score": 101},
    {"components": {}}, {"components": None}, {"components": "unknown"},
])
def test_missing_metrics_record_reason_and_make_no_recommendation(tmp_path, changes):
    fetch, log = _fetch([{**_item("p1"), **changes}], [_rec("p1")])
    result = _run(tmp_path, fetch)
    assert result["status"] == "degraded" and result["reason"]
    assert _proposals(log) == [] and not _prompt_ids(log)
    state = pea.Store(tmp_path / "state.json").load()
    assert all(not entry["items"] for entry in state["results"].values())
    assert state["last_run"]["reason"] == result["reason"]


def test_candidacy_waits_for_real_panel_and_fixed_thresholds(tmp_path):
    store, _ = _seed_promotion(tmp_path)
    log = []
    result = _promote(store, _promotion_fetch(log, panel_pending=True))
    assert result["reason"] == "canary_panel_conclusions_pending" and not _proposals(log)
    assert _promote(store, _promotion_fetch(log))["status"] == "proposed"
    store, _ = _seed_promotion(tmp_path / "low", [_promotion_item(score=84)])
    assert _promote(store, _promotion_fetch([]))["reason"] == "fixed_mapping_canary_threshold_not_met"


def test_candidacy_panel_policy_includes_risk_and_red_team():
    from services.persona.consult_triggers import required_consultation, RISK_PERSONA_ID, RED_TEAM_PERSONA_ID
    requirement = required_consultation("trial_admission", "p1", initiator_persona_id="p1",
                                       touches=[pea.PROMOTION_TARGET], target_version="snapshot")
    assert {p.persona_id for p in requirement.participants} == {"p1", RISK_PERSONA_ID, RED_TEAM_PERSONA_ID}
    assert requirement.to_panel_metadata()["gated_decision"]["target_version"] == "snapshot"


@pytest.mark.skipif(not os.getenv("PANTHEON_PM12_TEST_POSTGRES_DSN"), reason="requires isolated real Rankings PostgreSQL")
def test_real_postgres_ranking_to_each_owner_over_http(tmp_path, monkeypatch):
    """Real evaluator entry points and Rankings persistence; only owner HTTP is faked."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse
    from services.persona.harness import PersonaPatch
    from services.evolution.models import ProposeRequest

    items = [_promotion_item(),
             _item("research", score=75),
             {**_item("retrain", score=60), "components": {"risk_score": 60, "execution_score": 40, "activity_score": 70}},
             {**_item("reduce", score=60), "components": {"risk_score": 50, "execution_score": 70, "activity_score": 70}},
             _item("freeze", score=40), _item("suspend", score=30), _item("retire", state="frozen", score=20)]
    for n, item in enumerate(items, 1):
        item.update(rank=n, session_id="session-" + item["persona_id"], formula_version="pm12-return-on-capital-v2",
                    evidence_ref_ids=["ev-" + item["persona_id"]])
    snapshot = pea.snapshot_record(items, surface="quarterly", period=QUARTER)
    calls, panels, errors = [], {}, []

    def dispatch(path, data):
        url = urlparse(path)
        calls.append((url.path, data))
        if url.path == "/bff/management/quarterly-ranking":
            return {"data": {"ranking_snapshot_id": snapshot.ranking_snapshot_id, "items": items}}, 200
        if url.path.endswith("/structured"):
            return {"data": {"output": {"structured_data": {"recommendations": [
                {**_rec("research", "retire_persona"), "rationale": "model tries to override"}],
                "rationale": "Evidence supports the already fixed candidate"}}}}, 200
        if url.path.endswith("/harness/patches"):
            assert PersonaPatch.model_validate(data).model_dump(mode="json") == data
            return {"proposal": data, "status": "draft", "checks": []}, 201
        if url.path.endswith("/harness"):
            return {"active_version": 1, "versions": [{"version": 1, "surface": {
                "tool_guidance": {"research.read": "Read existing governed research"}}}]}, 200
        if url.path.endswith("/strategy-pool"):
            pid = url.path.split("/")[3]
            return {"persona_id": pid, "tenant_id": "t1", "pool_version": 1, "members": [
                {"artifact_ref": "strong", "checksum": "sum-strong", "joined_at": "2026-07-01", "stage": "active"},
                {"artifact_ref": "weak", "checksum": "sum-weak", "joined_at": "2026-07-01", "stage": "active"}]}, 200
        if url.path == "/api/bindings":
            return [{"persona_id": i["persona_id"], "tenant_id": "t1", "status": "active",
                     "binding_id": "binding-" + i["persona_id"]} for i in items], 200
        if url.path == "/api/telemetry/runtime-summaries":
            return [{"persona_capital_binding_id": "binding-" + i["persona_id"], "tenant_id": "t1",
                     "deployment_stage": "paper", "member_virtual_books": [
                         {"member_id": ref, "checksum": "sum-" + ref, "source_ref": "telemetry:" + ref,
                          "dates": ["2026-07-02"], "net_of_costs": {"sharpe_ratio": sharpe}}
                         for ref, sharpe in (("strong", 1.5), ("weak", -0.4))]} for i in items], 200
        if url.path.startswith("/api/registry/strategy-artifacts/"):
            ref = url.path.rsplit("/", 1)[1]
            return {"entry": {"registry_id": ref, "owner_tenant": "t1", "checksum": "sum-" + ref,
                              "version": "1", "artifact_state": "approved"}}, 200
        if url.path == "/api/evolution/proposals":
            ProposeRequest.model_validate(data)
            assert data["target_id"] == "weak" and data["metadata"]["pool_member"]["checksum"] == "sum-weak"
            assert data["metadata"]["member_book_source_ref"] == "telemetry:weak"
            return {**data, "decision_state": "proposed"}, 201
        if url.path == "/api/governance/approvals":
            return {**data, "metadata": {"subject": data["subject"]}, "decision_state": "proposed"}, 201
        if url.path == pea.PAPER_CYCLE_PATH:
            inputs = data["pool_health"]["reduce"]["recommendation_inputs"]
            assert inputs[0]["action_id"] == "reduce_capital_access"
            assert inputs[0]["ranking_snapshot_id"] == snapshot.ranking_snapshot_id
            return {"data": {"applied": True, "rebalance": {"rebalance_id": "paper-rebalance-1"}}}, 200
        if url.path == "/api/consult/requests":
            panels[data["request_id"]] = data
            return {**data, "status": "submitted"}, 201
        if url.path.endswith("/transcript"):
            rid = url.path.split("/")[-2]
            assert panels[rid]["metadata"]["panel"]["gated_decision"]["target_version"] == snapshot.ranking_snapshot_id
            return {"request_id": rid, "events": [
                {"actor": {"actor_type": "persona", "actor_id": pid},
                 "content": {"recommendation": "approve", "claim": pid + " conclusion"},
                 "evidence_refs": ["evidence:" + pid]} for pid in ("persona-risk-officer", "persona-red-team")]}, 200
        if url.path == "/api/consult/memos":
            rid = parse_qs(url.query)["request_id"][0]
            return [{"memo_id": "memo-real-panel", "request_id": rid, "status": "published",
                     "memo_type": "committee_summary", "author_ref": "consult-panel:" + rid,
                     "target_type": "trial_admission", "target_id": "p1", "summary": "Both personas reviewed",
                     "recommendation": "approve"}], 200
        if url.path.startswith("/api/personas/"):
            return {"persona_id": url.path.split("/")[3], "tenant_id": "t1"}, 200
        raise AssertionError(path)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond(None)

        def do_POST(self):
            self.respond(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))

        def respond(self, data):
            try:
                body, status = dispatch(self.path, data)
            except Exception as exc:
                errors.append(str(exc))
                body, status = {"error": str(exc)}, 500
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    for owner in ("TELEMETRY", "CAPITAL", "REGISTRY", "EVOLUTION"):
        monkeypatch.setenv("AUTO_CONSUMER_" + owner + "_URL", base)
        monkeypatch.setenv("AUTO_CONSUMER_" + owner + "_TOKEN", "owner-test-token")
    monkeypatch.setenv("PANTHEON_CONSULTATION_API_URL", base)
    store = pea.Store(tmp_path / "state.json")
    try:
        for n in range(6):
            result = pea.run_once(store=store, bff_url=base, bff_headers={}, adapter_url=base,
                                  adapter_token="a", governance_url=base, governance_token="g", tenant="t1",
                                  actor="evaluator", persona_url=base, persona_token="p", now=lambda: NOW + n)
            assert result["ranking_snapshot_id"] == snapshot.ranking_snapshot_id
        saved = next(iter(store.load()["results"].values()))
        assert {r["action_id"] for r in saved["items"]} == set(pea.ACTIONS)
        assert not any(r["persona_id"] == "research" and r["action_id"] == "retire_persona" for r in saved["items"])
        for rec in saved["items"]:
            if rec["action_id"] in {"increase_research_budget", "grant_tool_access", "require_retraining"}:
                assert rec["routing"]["status"] == "proposed", rec
        assert {d["subject"]["to_state"] for p, d in calls if p == "/api/governance/approvals"} == {"frozen", "retired"}
        cycle = pea.paper_cycle_once(store=store, bff_url=base, bff_headers={}, persona_url=base, persona_token="p",
                                     tenant="t1", paper_pool_id="paper-pool", now=lambda: NEXT_QUARTER_NOW)
        assert cycle["status"] == "applied"
        nomination = pea.promotion_once(store=store, tenant="t1", actor="evaluator", governance_url=base,
                                        governance_token="g", persona_url=base, persona_token="p", adapter_url=base,
                                        adapter_token="a", now=lambda: NEXT_QUARTER_NOW)
        assert nomination["status"] == "proposed", nomination
        rec = nomination["recommendation"]
        assert rec["panel_memo"]["memo_id"] == "memo-real-panel"
        assert rec["panel_memo"]["evidence_ref"] in rec["evidence_ref_ids"]
        assert json.loads(nomination["request"]["body"]["subject"]["panel_memo"]) == rec["panel_memo"]
        assert pea.build_rankings_store().get_ranking_snapshot(snapshot.ranking_snapshot_id) is not None
        assert not errors, errors
        assert "owner-test-token" not in store.path.read_text()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
