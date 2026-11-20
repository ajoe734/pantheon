"""Each meeting trigger firing and not firing (MEETING-TRIGGERS-R2-20261116). The consultation route and the
attribution owner are doubled at their function edge; the controller entry point is covered in
test_persona_cycle_controller.py and the quarter-end path in test_persona_evaluator_agent.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.persona import meeting_triggers as mt
from services.persona.meeting_triggers import (
    AttributionPort, MeetingTriggerConfig, MeetingTriggers, convene_quarterly_review, evolution_weekday,
)

PERSONA = {"persona_id": "persona-b", "tenant_id": "t1", "lifecycle_state": "paper_owner"}
OTHER = {"persona_id": "persona-a", "tenant_id": "t1", "lifecycle_state": "paper_owner"}


def at_weekday(weekday, base=datetime(2026, 11, 16, 3, tzinfo=timezone.utc)):  # 2026-11-16 is a Monday
    return base + timedelta(days=weekday)


class Opener:
    def __init__(self, refuse=None):
        self.calls, self.refuse = [], refuse

    def __call__(self, **kw):
        self.calls.append(kw)
        if self.refuse:
            err = RuntimeError("refused")
            err.payload = {"reason": self.refuse}
            raise err
        return {"meeting_id": f"m-{len(self.calls)}", "status": "convened"}


class Attribution(AttributionPort):
    def __init__(self, records=(), run_error=False):
        self.records, self.runs, self.run_error = list(records), [], run_error

    def request_weekly_run(self, window_end):
        self.runs.append(window_end)
        if self.run_error:
            raise RuntimeError("attribution_run_refused:503")
        return {"run_id": "run-1", "status": "queued"}

    def new_records(self, since, limit=50):
        return self.records


def triggers(opener, *, day, attribution=None, **cfg):
    return MeetingTriggers(config=MeetingTriggerConfig(**cfg), opener=opener, attribution=attribution, now=lambda: day)


def run(t, persona=PERSONA, observe=None, packet=None, snapshots=None, enrolled=("persona-a", "persona-b")):
    packet = packet or {"packet_id": "p1", "persona_cycle": {}}
    return t.run(persona, observe or {}, packet, snapshots or {}, list(enrolled))


def types(record):
    return [c["meeting_type"] for c in record["convened"]]


def test_mt1_fires_only_on_the_weekly_evolution_day():
    wd = evolution_weekday(PERSONA)
    op = Opener()
    assert "MT-1" in types(run(triggers(op, day=at_weekday(wd))))
    op2 = Opener()
    rec = run(triggers(op2, day=at_weekday((wd + 1) % 7 if wd < 6 else 0)))
    assert "MT-1" not in types(rec)
    assert {"meeting_type": "MT-1", "reason": "not_weekly_evolution_day"} in rec["not_fired"]


def test_mt1_weekday_is_configurable_per_persona():
    p = {**PERSONA, "metadata": {"meeting_evolution_weekday": 2}}
    assert evolution_weekday(p) == 2
    assert "MT-1" in types(run(triggers(Opener(), day=at_weekday(2)), persona=p))
    assert "MT-1" not in types(run(triggers(Opener(), day=at_weekday(3)), persona=p))


@pytest.mark.parametrize("observe,persona_extra,fires", [
    ({"pending_lesson_candidates": 3}, {}, True),
    ({"pending_lesson_candidates": 2}, {}, False),
    ({}, {"metadata": {"persona_harness": {"patches": {"x": {"status": "previewed"}}}}}, True),
    ({}, {"metadata": {"persona_harness": {"patches": {"x": {"status": "adopted"}}}}}, False),
    ({"counterfactual_window": {"window_id": "w1", "trading_days": 20}}, {}, True),
    ({"counterfactual_window": {"window_id": "w1", "trading_days": 19}}, {}, False),
])
def test_mt2_conditions(observe, persona_extra, fires):
    rec = run(triggers(Opener(), day=at_weekday(6)), persona={**PERSONA, **persona_extra}, observe=observe)
    assert ("MT-2" in types(rec)) is fires
    if not fires:
        assert any(n.get("meeting_type") == "MT-2" for n in rec["not_fired"])


def test_mt2_threshold_comes_from_config():
    rec = run(triggers(Opener(), day=at_weekday(6), lesson_candidate_threshold=2),
              observe={"pending_lesson_candidates": 2})
    assert "MT-2" in types(rec)


def test_mt3_and_weekly_outlier_run_only_for_the_first_enrolled_persona_once_per_week():
    att, op = Attribution(), Opener()
    first = run(triggers(op, day=at_weekday(1), attribution=att), persona=OTHER)
    assert "MT-3" in types(first) and first["weekly_outlier_run"]["status"] == "requested"
    assert att.runs == ["2026-11-15"]  # the Sunday closing the previous week
    second = run(triggers(Opener(), day=at_weekday(1), attribution=att), persona=PERSONA)  # not first
    assert "MT-3" not in types(second) and second["weekly_outlier_run"] is None
    # next daily cycle of the same persona in the same ISO week: nothing is opened or requested again
    packet = {"packet_id": "p0", "tenant_id": "t1", "persona_ids": ["persona-a"], "persona_cycle": {"meetings": first}}
    again = run(triggers(Opener(), day=at_weekday(2), attribution=att), persona=OTHER, snapshots={"p0": packet})
    assert "MT-3" not in types(again) and again["weekly_outlier_run"]["status"] == "already_requested"
    assert att.runs == ["2026-11-15"]
    nextweek = run(triggers(Opener(), day=at_weekday(8), attribution=att), persona=OTHER, snapshots={"p0": packet})
    assert "MT-3" in types(nextweek) and len(att.runs) == 2


def test_weekly_outlier_run_failure_and_missing_owner_are_recorded():
    rec = run(triggers(Opener(), day=at_weekday(1), attribution=Attribution(run_error=True)), persona=OTHER)
    assert rec["weekly_outlier_run"]["status"] == "not_requested" and "503" in rec["weekly_outlier_run"]["reason"]
    rec = run(triggers(Opener(), day=at_weekday(1)), persona=OTHER)
    assert rec["weekly_outlier_run"]["reason"] == "attribution_owner_unconfigured"
    rec = run(triggers(Opener(), day=at_weekday(1), attribution=Attribution(), request_weekly_outlier_run=False), persona=OTHER)
    assert rec["weekly_outlier_run"] is None


RECORDS = [
    {"record_id": "r1", "subject_kind": "persona", "subject_id": "persona-b", "direction": "underperform"},
    {"record_id": "r2", "subject_kind": "pool_member", "subject_id": "persona-b", "direction": "underperform",
     "raised_by": "member_removed_at_drawdown_limit"},
    {"record_id": "r3", "subject_kind": "trader", "subject_id": "trader-9", "direction": "outstanding"},
    {"record_id": "r4", "subject_kind": "persona", "subject_id": "persona-b", "direction": "disagreement",
     "meeting_id": "already"},
]


def test_mt4_opens_one_meeting_per_new_record_for_the_closest_persona():
    op_b = Opener()
    rec_b = run(triggers(op_b, day=at_weekday(6), attribution=Attribution(RECORDS)), persona=PERSONA)
    mt4 = [c for c in op_b.calls if c["meeting_type"] == "MT-4"]
    assert [c["attribution_record_id"] for c in mt4] == ["r1", "r2"]  # r4 already has a meeting; r3 is a trader
    op_a = Opener()
    run(triggers(op_a, day=at_weekday(6), attribution=Attribution(RECORDS)), persona=OTHER)
    assert [c["attribution_record_id"] for c in op_a.calls if c["meeting_type"] == "MT-4"] == ["r3"]
    assert types(rec_b).count("MT-4") == 2


def test_mt4_not_fired_without_records_or_owner():
    rec = run(triggers(Opener(), day=at_weekday(6), attribution=Attribution([])))
    assert {"meeting_type": "MT-4", "reason": "no_new_attribution_record"} in rec["not_fired"]
    rec = run(triggers(Opener(), day=at_weekday(6)))
    assert {"meeting_type": "MT-4", "reason": "attribution_owner_unconfigured"} in rec["not_fired"]


def test_mt4_repeat_cycle_does_not_reconvene_the_same_record():
    first = run(triggers(Opener(), day=at_weekday(6), attribution=Attribution(RECORDS[:1])))
    packet = {"packet_id": "p0", "tenant_id": "t1", "persona_ids": ["persona-b"], "persona_cycle": {"meetings": first}}
    op = Opener()
    run(triggers(op, day=at_weekday(6), attribution=Attribution(RECORDS[:1])), snapshots={"p0": packet})
    assert not [c for c in op.calls if c["meeting_type"] == "MT-4"]


def proposal_packet(params, kind="consult_request"):
    return {"packet_id": "p1", "persona_cycle": {
        "output": {"proposals": [{"kind": kind, "reason": "I want a review", "parameters": params}]},
        "decisions": [{"kind": kind, "status": "requires_owner_gate"}]}}


def test_consult_request_for_a_deliberative_type_opens_it_within_budget():
    op, packet = Opener(), proposal_packet({"meeting_type": "optimisation_review", "topic": "why do exits lag"})
    rec = run(triggers(op, day=at_weekday(6)), packet=packet)
    assert op.calls[0]["meeting_type"] == "MT-2" and op.calls[0]["topic"] == "why do exits lag"
    assert rec["requests"][0]["status"] == "meeting_opened"
    assert packet["persona_cycle"]["decisions"][0]["status"] == "meeting_opened"


def test_consult_request_refusal_reason_is_recorded_in_the_packet():
    packet = proposal_packet({"meeting_type": "MT-5"})
    rec = run(triggers(Opener(refuse="meeting_budget_exhausted"), day=at_weekday(6)), packet=packet)
    assert rec["requests"][0] == {"proposal_index": 0, "meeting_type": "MT-5", "status": "meeting_refused",
                                  "reason": "meeting_budget_exhausted"}
    assert packet["persona_cycle"]["decisions"][0]["reason"] == "meeting_budget_exhausted"


def test_consult_request_over_the_weekly_budget_is_refused_without_calling_the_route():
    prior = {"packet_id": "p0", "tenant_id": "t1", "persona_ids": ["persona-b"], "persona_cycle": {"meetings": {
        "convened": [{"iso_week": "2026-W47", "idempotency_key": f"k{i}"} for i in range(3)]}}}
    op, packet = Opener(), proposal_packet({"meeting_type": "MT-1"})
    rec = run(triggers(op, day=at_weekday(6)), packet=packet, snapshots={"p0": prior})
    assert op.calls == []
    assert rec["requests"][0]["reason"] == "weekly_meeting_budget_spent"


def test_gate_panel_consult_request_and_other_kinds_are_left_alone():
    packet = proposal_packet({"patch_id": "p-1"})
    rec = run(triggers(Opener(), day=at_weekday(6)), packet=packet)
    assert rec["requests"] == [] and packet["persona_cycle"]["decisions"][0]["status"] == "requires_owner_gate"
    assert "unknown_type" not in str(run(triggers(Opener(), day=at_weekday(6)),
                                         packet=proposal_packet({"meeting_type": "unknown_type"}))["requests"])


def test_cycle_and_day_budgets_cap_meetings_and_record_why():
    wd = evolution_weekday(PERSONA)
    rec = run(triggers(Opener(), day=at_weekday(wd), max_meetings_per_cycle=1,
                       attribution=Attribution(RECORDS[:1])), observe={"pending_lesson_candidates": 5})
    assert len(rec["convened"]) == 1
    assert {r["reason"] for r in rec["refused"]} == {"cycle_meeting_budget_spent"}
    rec = run(triggers(Opener(), day=at_weekday(wd), max_meetings_per_day=0))
    assert rec["convened"] == [] and rec["refused"] and {r["reason"] for r in rec["refused"]} == {"daily_meeting_budget_spent"}


def test_disabled_triggers_open_nothing_and_never_raise():
    op = Opener()
    rec = run(triggers(op, day=at_weekday(6), enabled=False))
    assert op.calls == [] and rec["not_fired"] == [{"reason": "meeting_triggers_disabled"}]
    assert "error" in run(MeetingTriggers(now=lambda: 1), packet={"packet_id": "x"})  # broken clock: recorded, not raised


def test_config_from_env_overrides_design_defaults():
    cfg = MeetingTriggerConfig.from_env({"PANTHEON_MEETING_LESSON_CANDIDATE_THRESHOLD": "5",
                                         "PANTHEON_MEETING_ENABLED": "false", "PANTHEON_MEETING_QUARTER_END_DAYS": "x"})
    assert cfg.lesson_candidate_threshold == 5 and cfg.enabled is False and cfg.quarter_end_days == 3
    assert MeetingTriggerConfig().counterfactual_window_days == 20


def test_mt6_quarterly_review_states():
    def reader(state, synthesis=None):
        return lambda mid: {"status": state, "synthesis": synthesis}
    kw = dict(tenant="t1", quarter="2026-Q3", chair_persona_id="persona-a")
    ready = convene_quarterly_review(opener=Opener(), reader=reader("closed", {"outcome": "ok"}), **kw)
    assert ready["status"] == "ready" and ready["evidence_ref"] == "consultation://meetings/m-1"
    assert convene_quarterly_review(opener=Opener(), reader=reader("convened"), **kw)["status"] == "pending"
    refused = convene_quarterly_review(opener=Opener(refuse="meeting_budget_exhausted"), reader=reader("x"), **kw)
    assert refused == {"status": "refused", "quarter": "2026-Q3", "reason": "meeting_budget_exhausted"}
    op = Opener()
    convene_quarterly_review(opener=op, reader=reader("convened"), **kw)
    assert op.calls[0]["meeting_type"] == "MT-6" and op.calls[0]["idempotency_key"]


def test_quarter_review_due_window():
    cfg = MeetingTriggerConfig()
    from datetime import date
    assert mt.quarter_review_due(date(2026, 9, 29), cfg) and mt.quarter_review_due(date(2026, 10, 2), cfg)
    assert not mt.quarter_review_due(date(2026, 11, 16), cfg)


def test_integration_against_the_merged_consultation_meeting_routes(tmp_path, monkeypatch):
    """Real provider (services.consultation.main app + real client class); only the HTTP wire is a TestClient."""
    from fastapi.testclient import TestClient
    from services.consultation import main as cm
    from services.consultation.client import ConsultationClientError, ConsultationServiceClient
    from services.consultation.store import ConsultationStore
    from services.consultation.workflow_state import WorkflowStateStore
    for k, v in {"CONSULTATION_AUTH_REQUIRED": "true", "CONSULTATION_SERVICE_TOKEN": "tok", "PANTHEON_TENANT_ID": "t1",
                 "PANTHEON_MEMORY_STORE": str(tmp_path / "i.json"), "PANTHEON_PERSONA_MEMORY_STORE": str(tmp_path / "p.json"),
                 "PANTHEON_MEMORY_DATA_DIR": str(tmp_path / "m")}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(cm, "store", ConsultationStore(str(tmp_path / "d")))
    monkeypatch.setattr(cm, "workflow_state", WorkflowStateStore(tmp_path / "wf.sqlite3"))
    monkeypatch.setattr(cm, "meeting_provider", None)
    monkeypatch.setattr(cm, "auto_run_meetings", False)
    web = TestClient(cm.app)

    def wire(self, method, path, payload=None, *, tenant_id=None, service_actor=None, extra_headers=None, **_):
        headers = {"Authorization": "Bearer tok", "X-Pantheon-Tenant-Id": tenant_id or "t1",
                   "X-Pantheon-Service-Actor": service_actor or "x", **(extra_headers or {})}
        r = web.request(method, path, json=payload, headers=headers)
        if r.status_code >= 400:
            raise ConsultationClientError(r.json().get("detail", "err"), status_code=r.status_code, payload=r.json())
        return r.json()
    monkeypatch.setattr(ConsultationServiceClient, "_request_json", wire)

    wd = evolution_weekday(PERSONA)
    rec = run(MeetingTriggers(opener=mt.default_opener(PERSONA), now=lambda: at_weekday(wd)))
    assert rec["convened"], rec  # MT-1 convened through the real route
    first = rec["convened"][0]
    assert first["meeting_type"] == "MT-1" and str(first["meeting_id"]).startswith("mtg-")
    # a replayed cycle (no local snapshot) sends the same key and payload: the provider answers with the same meeting
    replay = run(MeetingTriggers(opener=mt.default_opener(PERSONA), now=lambda: at_weekday(wd)))
    assert replay["convened"][0]["meeting_id"] == first["meeting_id"]


def _regime_obs(
    session: str,
    *,
    macro: str = "expansion",
    vol_state: str = "low",
    trend_state: str = "up",
    state: str = "active",
    model_status: str = "approved",
):
    vol_cls = "low_volatility" if vol_state == "low" else "high_volatility"
    trend_cls = "trending_up" if trend_state == "up" else "trending_down"
    ms = {
        "market_state_id": f"TW:{session}:set-1:r1",
        "market": "TW",
        "session": session,
        "model_set_version": "set-1",
        "model_status": model_status,
        "revision": 1,
        "state": "proposed" if state == "proposed" else "current",
        "axes": {
            "volatility": {"short": {"available": True, "state": vol_state, "horizon": "short"}},
            "trend": {"medium": {"available": True, "state": trend_state, "horizon": "medium"}},
            "macro": {"available": True, "state": macro, "horizon": "monthly", "as_of_session": session[:7]},
        },
    }
    records = {
        "momentum": {
            "regime_id": f"regime:TW:momentum:{session}:set1:r1",
            "strategy_id": "momentum",
            "state": state,
            "model_status": model_status,
            "regime_class": vol_cls,
            "confidence": 0.85,
            "feature_snapshot": {
                "profile": {"primary_axis": "volatility", "horizon": "short"},
                "axes": {
                    "volatility": {"available": True, "state": vol_state, "horizon": "short"},
                    "macro": {"available": True, "state": macro, "as_of_session": session[:7]},
                },
                "macro_environment": macro,
            },
        },
        "carry": {
            "regime_id": f"regime:TW:carry:{session}:set1:r1",
            "strategy_id": "carry",
            "state": state,
            "model_status": model_status,
            "regime_class": trend_cls,
            "confidence": 0.82,
            "feature_snapshot": {
                "profile": {"primary_axis": "trend", "horizon": "medium"},
                "axes": {
                    "trend": {"available": True, "state": trend_state, "horizon": "medium"},
                    "macro": {"available": True, "state": macro, "as_of_session": session[:7]},
                },
                "macro_environment": macro,
            },
        },
    }
    return {
        "regime": {
            "status": "observed",
            "reason": None,
            "session": session,
            "regime_state_ref": f"regime-state://{records['momentum']['regime_id']}",
            "regime_tag": vol_cls,
            "records": records,
            "macro": {"state": macro, "session": session, "as_of": session[:7]},
            "market_state": ms,
        },
        "pool_exposures": {
            "persona-a": {
                "persona_id": "persona-a",
                "pool_version": 2,
                "strategy_families": ["momentum"],
                "underlyings": ["2330"],
                "members": [{"artifact_ref": "art-mom-1", "stage": "active", "risk_weight": 0.6}],
                "gross_exposure": 0.6,
                "net_exposure": 0.6,
            },
            "persona-b": {
                "persona_id": "persona-b",
                "pool_version": 3,
                "strategy_families": ["carry"],
                "underlyings": ["2882"],
                "members": [{"artifact_ref": "art-carry-1", "stage": "active", "risk_weight": 0.45}],
                "gross_exposure": 0.45,
                "net_exposure": 0.45,
            },
        },
    }


def _snapshot(packet_id: str, persona_id: str, obs: dict, meetings: dict | None = None):
    return {
        "packet_id": packet_id,
        "tenant_id": "t1",
        "persona_ids": [persona_id],
        "persona_cycle": {
            "regime": obs.get("regime"),
            "observe_bundle": obs,
            "meetings": meetings or {"convened": [], "refused": [], "not_fired": [], "requests": []},
        },
    }


def test_mt5_convenes_once_on_macro_or_enrolled_pool_primary_axis_transition_and_repeats_do_not_reconvene():
    op = Opener()
    trig = triggers(op, day=at_weekday(6))
    obs_s1 = _regime_obs("2026-11-12", macro="expansion", vol_state="low", trend_state="up")
    # First session: no prior session -> no transition
    rec_s1 = run(trig, observe=obs_s1)
    assert "MT-5" not in types(rec_s1)
    assert {"meeting_type": "MT-5", "reason": "no_regime_transition"} in rec_s1["not_fired"]

    # Second session: macro transitions expansion -> slowdown
    obs_s2 = _regime_obs("2026-11-13", macro="slowdown", vol_state="low", trend_state="up")
    snaps = {"p1": _snapshot("p1", "persona-b", obs_s1, rec_s1)}
    rec_s2 = run(trig, observe=obs_s2, snapshots=snaps)
    assert "MT-5" in types(rec_s2)
    mt5_call_1 = next(c for c in op.calls if c["meeting_type"] == "MT-5")
    assert "macro:expansion->slowdown" in mt5_call_1["topic"] or mt5_call_1["metadata"]["regime_transitions"][0]["axis"] == "macro"
    assert mt5_call_1["metadata"]["chair_persona_id"] == "persona-a"

    # Repeated read of the same transition on the same trigger instance -> already_convened
    rec_repeat_instance = run(trig, observe=obs_s2, snapshots=snaps)
    assert "MT-5" not in types(rec_repeat_instance)
    assert any(n.get("meeting_type") == "MT-5" and n.get("reason") == "already_convened" for n in rec_repeat_instance["not_fired"])
    assert len([c for c in op.calls if c["meeting_type"] == "MT-5"]) == 1

    # Repeated read by the second enrolled persona in the same session with snapshot recorded -> already_convened
    snaps["p2"] = _snapshot("p2", "persona-b", obs_s2, rec_s2)
    op_other = Opener()
    rec_other = run(triggers(op_other, day=at_weekday(6)), persona=OTHER, observe=obs_s2, snapshots=snaps)
    assert "MT-5" not in types(rec_other)
    assert any(n.get("meeting_type") == "MT-5" and n.get("reason") == "already_convened" for n in rec_other["not_fired"])
    assert not [c for c in op_other.calls if c["meeting_type"] == "MT-5"]

    # Third session with unchanged macro=slowdown -> no_regime_transition
    obs_s3 = _regime_obs("2026-11-16", macro="slowdown", vol_state="low", trend_state="up")
    rec_s3 = run(triggers(op_other, day=at_weekday(6)), observe=obs_s3, snapshots=snaps)
    assert "MT-5" not in types(rec_s3)
    assert {"meeting_type": "MT-5", "reason": "no_regime_transition"} in rec_s3["not_fired"]
    snaps["p3"] = _snapshot("p3", "persona-b", obs_s3, rec_s3)

    # Fourth session: enrolled pool primary axis transitions (carry trend: up -> down)
    obs_s4 = _regime_obs("2026-11-17", macro="slowdown", vol_state="low", trend_state="down")
    op_s4 = Opener()
    rec_s4 = run(triggers(op_s4, day=at_weekday(6)), observe=obs_s4, snapshots=snaps)
    assert "MT-5" in types(rec_s4)
    mt5_call_2 = next(c for c in op_s4.calls if c["meeting_type"] == "MT-5")
    trans = mt5_call_2["metadata"]["regime_transitions"]
    assert len(trans) == 1 and trans[0]["axis"] == "trend" and trans[0]["strategy_id"] == "carry"
    assert trans[0]["from_state"] == "up" and trans[0]["to_state"] == "down"
    # Chair rotates to the next enrolled trading persona on the second MT-5
    assert mt5_call_2["metadata"]["chair_persona_id"] == "persona-b"


def test_mt5_never_convenes_from_missing_or_proposed_regime_record():
    op = Opener()
    # 1. Missing regime observation
    rec_missing = run(triggers(op, day=at_weekday(6)), observe={})
    assert "MT-5" not in types(rec_missing)
    assert {"meeting_type": "MT-5", "reason": "no_governed_regime_record"} in rec_missing["not_fired"]

    # 2. Unavailable regime (no active record) even when macro state changed from an earlier governed session
    obs_s1 = _regime_obs("2026-11-12", macro="expansion", vol_state="low")
    snaps = {"p1": _snapshot("p1", "persona-b", obs_s1)}
    obs_no_rec = {
        "regime": {
            "status": "unavailable",
            "reason": "no_active_record_for_pool_strategies",
            "session": "2026-11-13",
            "records": {},
            "macro": {"state": "slowdown", "session": "2026-11-13"},
        }
    }
    rec_no_rec = run(triggers(op, day=at_weekday(6)), observe=obs_no_rec, snapshots=snaps)
    assert "MT-5" not in types(rec_no_rec)
    assert {"meeting_type": "MT-5", "reason": "no_governed_regime_record"} in rec_no_rec["not_fired"]

    # 3. Proposed record only (candidate model) even when primary axis and macro changed
    obs_proposed = _regime_obs("2026-11-13", macro="slowdown", vol_state="high", state="proposed", model_status="candidate")
    rec_prop = run(triggers(op, day=at_weekday(6)), observe=obs_proposed, snapshots=snaps)
    assert "MT-5" not in types(rec_prop)
    assert {"meeting_type": "MT-5", "reason": "proposed_regime_record_only"} in rec_prop["not_fired"]

    # 4. Prior record was proposed and current record is active with a different state -> never transitions from proposed
    snaps_prop = {"p0": _snapshot("p0", "persona-b", obs_proposed)}
    obs_active = _regime_obs("2026-11-14", macro="expansion", vol_state="low", state="active")
    rec_from_prop = run(triggers(op, day=at_weekday(6)), observe=obs_active, snapshots=snaps_prop)
    assert "MT-5" not in types(rec_from_prop)
    assert {"meeting_type": "MT-5", "reason": "no_regime_transition"} in rec_from_prop["not_fired"]
    assert not [c for c in op.calls if c["meeting_type"] == "MT-5"]


def test_mt5_case_file_and_consultation_execution_writes_participant_interpretations_and_preserves_minority_view(
    tmp_path, monkeypatch
):
    """End-to-end MT-5: case file carries regime records, market-state measurement, and each pool's exposure;
    each participant's interpretation is written to its own memory and the minority view is kept in the minutes."""
    import json
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from services.consultation import main as cm
    from services.consultation.client import ConsultationClientError, ConsultationServiceClient
    from services.consultation.store import ConsultationStore
    from services.consultation.workflow_state import WorkflowStateStore

    inst_path = tmp_path / "inst_memory.json"
    persona_path = tmp_path / "persona_memory.json"
    for k, v in {
        "CONSULTATION_AUTH_REQUIRED": "true",
        "CONSULTATION_SERVICE_TOKEN": "tok",
        "PANTHEON_TENANT_ID": "t1",
        "PANTHEON_MEMORY_STORE": str(inst_path),
        "PANTHEON_PERSONA_MEMORY_STORE": str(persona_path),
        "PANTHEON_MEMORY_DATA_DIR": str(tmp_path / "m"),
    }.items():
        monkeypatch.setenv(k, v)

    c_store = ConsultationStore(str(tmp_path / "d"))
    monkeypatch.setattr(cm, "store", c_store)
    monkeypatch.setattr(cm, "workflow_state", WorkflowStateStore(tmp_path / "wf.sqlite3"))

    class _RegimeMeetingProvider:
        def ensure_agents(self, *, tenant_id, persona_ids):
            return None

        def obtain(self, *, tenant_id, request, participant_scope):
            cf_items = [i["item_id"] for i in request["case_file"]["groups"]["facts"]["items"]]
            pid = request["participant"]["persona_id"]
            round_id = request["meeting"]["round"]
            if round_id == "synthesis":
                return SimpleNamespace(
                    summary="Chair synthesis: adopt defensive volatility posture while preserving carry minority view.",
                    recommendation="approve_with_conditions",
                    confidence=0.81,
                    evidence=[{"id": cf_items[0], "evidence_type": "regime_record"}],
                    event_content={
                        "synthesis": {
                            "summary": "Adopt defensive volatility posture; carry pool dissent recorded.",
                            "outcome": "completed_no_proposal",
                            "proposals": [],
                        }
                    },
                )
            stances = {
                "persona-a": "INTERP-PERSONA-A: volatility transition to high requires trimming momentum beta.",
                "persona-b": "INTERP-PERSONA-B: carry spread remains resilient; spike is short-lived.",
                "persona-risk-officer": "INTERP-RISK-OFFICER: macro slowdown plus high volatility warrants tight gross limit.",
            }
            disputed = (
                ["MINORITY-VIEW-PERSONA-B: objects to broad de-risking because carry drawdown is contained."]
                if round_id == 2 and pid == "persona-b"
                else []
            )
            return SimpleNamespace(
                summary=stances[pid],
                recommendation="approve_with_conditions",
                confidence=0.85 if pid != "persona-b" else 0.65,
                evidence=[{"id": cid, "evidence_type": "meeting_fact"} for cid in cf_items],
                event_content={
                    "stance": stances[pid],
                    "confidence": 0.85 if pid != "persona-b" else 0.65,
                    "citations": cf_items,
                    "accepted": ["baseline_case_facts"],
                    "disputed": disputed,
                    "changed": False,
                    "proposals": [],
                },
            )

    monkeypatch.setattr(cm, "meeting_provider", _RegimeMeetingProvider())
    monkeypatch.setattr(cm, "auto_run_meetings", True)
    web = TestClient(cm.app)

    def wire(self, method, path, payload=None, *, tenant_id=None, service_actor=None, extra_headers=None, **_):
        headers = {
            "Authorization": "Bearer tok",
            "X-Pantheon-Tenant-Id": tenant_id or "t1",
            "X-Pantheon-Service-Actor": service_actor or "x",
            **(extra_headers or {}),
        }
        r = web.request(method, path, json=payload, headers=headers)
        if r.status_code >= 400:
            raise ConsultationClientError(r.json().get("detail", "err"), status_code=r.status_code, payload=r.json())
        return r.json()

    monkeypatch.setattr(ConsultationServiceClient, "_request_json", wire)

    obs_s1 = _regime_obs("2026-11-12", macro="expansion", vol_state="low", trend_state="up")
    obs_s2 = _regime_obs("2026-11-13", macro="slowdown", vol_state="high", trend_state="up")
    snaps = {"p1": _snapshot("p1", "persona-b", obs_s1)}

    rec = run(
        MeetingTriggers(opener=mt.default_opener(PERSONA), now=lambda: at_weekday(6)),
        persona=PERSONA,
        observe=obs_s2,
        snapshots=snaps,
        enrolled=("persona-a", "persona-b"),
    )
    mt5_entries = [c for c in rec["convened"] if c["meeting_type"] == "MT-5"]
    assert len(mt5_entries) == 1
    meeting_id = mt5_entries[0]["meeting_id"]
    assert mt5_entries[0]["status"] == "closed"

    detail = c_store.get_meeting(meeting_id)
    assert detail is not None and detail["status"] == "closed"
    fact_items = detail["case_file"]["groups"]["facts"]["items"]
    by_kind: dict[str, list[dict]] = {}
    for item in fact_items:
        by_kind.setdefault(item["kind"], []).append(item)

    # Case file carries the governed regime records, the market-state measurement, and each pool's exposure
    assert {i["subject_fields"]["strategy_id"] for i in by_kind["regime_record"]} == {"momentum", "carry"}
    assert len(by_kind["market_state"]) == 1
    assert by_kind["market_state"][0]["market_state"]["session"] == "2026-11-13"
    assert by_kind["market_state"][0]["market_state"]["axes"]["macro"]["state"] == "slowdown"
    assert {i["subject_fields"]["persona_id"] for i in by_kind["pool_exposure"]} == {"persona-a", "persona-b"}
    exp_by_persona = {i["subject_fields"]["persona_id"]: i["pool_exposure"] for i in by_kind["pool_exposure"]}
    assert exp_by_persona["persona-a"]["gross_exposure"] == 0.6
    assert exp_by_persona["persona-b"]["gross_exposure"] == 0.45

    # Each participant's interpretation goes to its own private memory
    persona_memories = json.loads(persona_path.read_text(encoding="utf-8"))
    by_persona_mem = {m["persona_id"]: m for m in persona_memories}
    assert set(by_persona_mem) == {"persona-a", "persona-b", "persona-risk-officer"}
    assert "INTERP-PERSONA-A" in by_persona_mem["persona-a"]["content"]["summary"]
    assert "INTERP-PERSONA-B" not in by_persona_mem["persona-a"]["content"]["summary"]
    assert "INTERP-PERSONA-B" in by_persona_mem["persona-b"]["content"]["summary"]
    assert "INTERP-PERSONA-A" not in by_persona_mem["persona-b"]["content"]["summary"]
    assert "INTERP-RISK-OFFICER" in by_persona_mem["persona-risk-officer"]["content"]["summary"]
    for m in persona_memories:
        assert m["memory_type"] == "consultation_outcome"

    # Minority view is kept in the minutes (dissent memo + institutional minutes record)
    dissent_id = detail["minutes"]["dissent_memo_id"]
    assert dissent_id
    dissent_memo = c_store.get_memo(dissent_id)
    assert dissent_memo is not None and dissent_memo.memo_type.value == "dissent"
    assert "MINORITY-VIEW-PERSONA-B" in dissent_memo.summary
    inst_memories = json.loads(inst_path.read_text(encoding="utf-8"))
    assert len(inst_memories) == 1
    assert dissent_id in inst_memories[0]["content"]["body"]



def test_mt2_three_pending_fire_once_per_week_key_and_two_do_not():
    op = Opener()
    t = triggers(op, day=at_weekday(6))
    first = run(t, observe={"pending_lesson_candidates": 3})
    second = run(t, observe={"pending_lesson_candidates": 3})
    assert "MT-2" in types(first) and "MT-2" not in types(second)
    assert any(n.get("meeting_type") == "MT-2" and n["reason"] == "already_convened" for n in second["not_fired"])
    assert len([c for c in op.calls if c.get("meeting_type") == "MT-2"]) == 1
    rec = run(triggers(Opener(), day=at_weekday(6)), observe={"pending_lesson_candidates": 2})
    assert {"meeting_type": "MT-2", "reason": "no_optimisation_condition", "pending_lesson_candidates": 2} in rec["not_fired"]


def test_mt2_names_unavailable_pending_count():
    rec = run(triggers(Opener(), day=at_weekday(6)), observe={"pending_lesson_candidates": None})
    assert {"meeting_type": "MT-2", "reason": "pending_lesson_count_unavailable"} in rec["not_fired"]


# --- CONSULT-DEAD-LETTER-RECOVERY-AFTER-FIX-20261120: a meeting that was never held does not block the next trigger
def _mt2_snapshot(first):
    return {"p0": {"packet_id": "p0", "tenant_id": "t1", "persona_ids": ["persona-b"],
                   "persona_cycle": {"meetings": first}}}


def _reader(states):
    seen = []
    def read(meeting_id):
        seen.append(meeting_id)
        return {"meeting_id": meeting_id, "execution_state": states.get(meeting_id)}
    read.seen = seen
    return read


def _mt2_run(opener, reader, snapshots):
    t = MeetingTriggers(config=MeetingTriggerConfig(), opener=opener, reader=reader, now=lambda: at_weekday(6))
    return run(t, observe={"pending_lesson_candidates": 4}, snapshots=snapshots)


def test_meeting_dead_lettered_with_exhausted_budget_is_not_already_convened():
    first = _mt2_run(Opener(), _reader({}), {})
    entry = [c for c in first["convened"] if c["meeting_type"] == "MT-2"][0]
    snaps = _mt2_snapshot(first)
    exhausted = {entry["meeting_id"]: {"status": "dead_letter", "recovery_exhausted": True}}
    op = Opener()
    record = _mt2_run(op, _reader(exhausted), snaps)
    mt2 = [c for c in record["convened"] if c["meeting_type"] == "MT-2"]
    assert len(mt2) == 1 and mt2[0]["idempotency_key"] == entry["idempotency_key"] + ":retry-1"
    assert op.calls[0]["idempotency_key"] == mt2[0]["idempotency_key"]
    assert record["not_held"] == [{"meeting_type": "MT-2", "trigger": entry["trigger"],
                                   "idempotency_key": entry["idempotency_key"], "meeting_id": entry["meeting_id"],
                                   "reason": "execution_failed_recovery_exhausted"}]


def test_meeting_still_running_or_dead_lettered_for_other_reasons_stays_convened():
    first = _mt2_run(Opener(), _reader({}), {})
    snaps = _mt2_snapshot(first)
    mid = [c for c in first["convened"] if c["meeting_type"] == "MT-2"][0]["meeting_id"]
    for state in (None, {"status": "pending"}, {"status": "dead_letter", "recovery_exhausted": False},
                  {"status": "completed"}):
        op = Opener()
        record = _mt2_run(op, _reader({mid: state}), snaps)
        assert not [c for c in record["convened"] if c["meeting_type"] == "MT-2"]
        assert {"meeting_type": "MT-2", "reason": "already_convened"}.items() <= [
            n for n in record["not_fired"] if n.get("meeting_type") == "MT-2"][0].items()
        assert "not_held" not in record

    def boom(_):
        raise RuntimeError("consultation down")
    assert not [c for c in _mt2_run(Opener(), boom, snaps)["convened"] if c["meeting_type"] == "MT-2"]


def test_replacement_meetings_are_bounded():
    snaps = {}
    states = {}
    for n in range(mt.MAX_MEETING_REPLACEMENTS + 1):
        record = _mt2_run(Opener(), _reader(states), snaps)
        entry = [c for c in record["convened"] if c["meeting_type"] == "MT-2"][0]
        states[entry["meeting_id"] + ""] = {"status": "dead_letter", "recovery_exhausted": True}
        snaps[f"p{n}"] = {"packet_id": f"p{n}", "tenant_id": "t1", "persona_ids": ["persona-b"],
                          "persona_cycle": {"meetings": record}}
    # the Opener reuses m-1 across instances, so key the states by id above; the chain ends at the cap
    last = _mt2_run(Opener(), _reader({"m-1": {"status": "dead_letter", "recovery_exhausted": True}}), snaps)
    assert not [c for c in last["convened"] if c["meeting_type"] == "MT-2"]
    assert [n for n in last["not_fired"] if n.get("meeting_type") == "MT-2"][0]["reason"] == "replacement_budget_spent"


def test_not_held_meeting_does_not_spend_the_weekly_budget():
    first = _mt2_run(Opener(), _reader({}), {})
    entry = [c for c in first["convened"] if c["meeting_type"] == "MT-2"][0]
    snaps = _mt2_snapshot(first)
    state = {entry["meeting_id"]: {"status": "dead_letter", "recovery_exhausted": True}}
    t = MeetingTriggers(config=MeetingTriggerConfig(max_meetings_per_persona_per_week=len(first["convened"])),
                        opener=Opener(), reader=_reader(state), now=lambda: at_weekday(6))
    record = run(t, observe={"pending_lesson_candidates": 4}, snapshots=snaps)
    assert [c for c in record["convened"] if c["meeting_type"] == "MT-2"]


def test_default_reader_uses_one_dead_letter_listing_and_the_recovery_rule(monkeypatch):
    from services.consultation import client as cc
    from services.consultation.workflow_state import AUTO_REDRIVE_MAX_PER_ITEM
    calls = []

    def fake(self, method, path, payload=None, *, tenant_id=None):
        calls.append(path)
        return {"items": [
            {"request_id": "m-spent", "status": "dead_letter", "replay_count": AUTO_REDRIVE_MAX_PER_ITEM,
             "last_error": "consultation agent ensure transport failed for p"},
            {"request_id": "m-other", "status": "dead_letter", "replay_count": 9, "last_error": "schema invalid"}]}
    monkeypatch.setattr(cc.ConsultationServiceClient, "_request_json", fake)
    read = mt.default_meeting_reader(PERSONA)
    assert read("m-spent")["execution_state"]["recovery_exhausted"] is True
    assert read("m-other")["execution_state"]["recovery_exhausted"] is True  # non-recoverable is terminal
    assert read("m-live")["execution_state"] is None and len(calls) == 1
