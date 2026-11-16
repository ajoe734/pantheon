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
