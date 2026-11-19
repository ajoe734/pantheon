from __future__ import annotations

import json
from datetime import datetime, timezone

from acceptance import persona_day as pd

PID = "persona-a"
NOW = datetime(2026, 11, 17, 11, 30, tzinfo=timezone.utc)  # after the 10:00Z cycle -> cycle day 2026-11-17
RUN_MS = int(datetime(2026, 11, 17, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)


def packet(**cycle):
    base = {"phase": "terminal", "failure_reason": None,
            "tool_use": {"memory_recall_ok": 2, "proposals_recorded": 1},
            "output": {"proposals": [{"kind": "no_action", "case_file_item_ids": ["i1"]}]}}
    base.update(cycle)
    return {"packet_id": "p1", "persona_ids": [PID], "producer": {"cron_run_at_ms": RUN_MS},
            "updated_at": "2026-11-17T10:05:00Z", "persona_cycle": base,
            "pool_autonomy": {"status": "completed"}}


def day(state="paper_owner", jobs=1, packets=()):
    return pd.evaluate_persona_day(PID, lifecycle_state=state, cron_jobs=jobs, packets=list(packets), now=NOW)


def status(result):
    return {c["name"]: c["status"] for c in result["preconditions"]}


def test_not_enrolled_governance_loop_fails_at_lifecycle():
    r = day(state="research_only", jobs=0)
    assert not r["passed"] and r["first_failed_precondition"] == "lifecycle_paper_owner"
    assert status(r)["terminal_packet_today"] == "failed"


def test_enrolled_but_harness_not_initialised():
    r = day(packets=[packet(failure_reason="harness_unavailable:no_active_version",
                            tool_use={}, output=None)])
    assert r["first_failed_precondition"] == "terminal_packet_today"
    assert status(r)["memory_read_via_broker"] == "failed"
    assert status(r)["lifecycle_paper_owner"] == "passed"


def test_cycle_completed():
    r = day(packets=[packet()])
    assert r["passed"] and r["first_failed_precondition"] is None
    assert all(c["status"] == "passed" and c["evidence_ref"] for c in r["preconditions"])


def test_missing_citation_and_pool_failure_are_named():
    bad = packet(output={"proposals": [{"kind": "no_action"}]})
    assert day(packets=[bad])["first_failed_precondition"] == "proposal_recorded_with_citations"
    bad = packet()
    bad["pool_autonomy"] = {"status": "failed", "reason": "x"}
    assert day(packets=[bad])["first_failed_precondition"] == "pool_cycle_ok"


def test_two_cron_jobs_fail_and_yesterdays_packet_does_not_count():
    assert day(jobs=2, packets=[packet()])["first_failed_precondition"] == "daily_cycle_cron_job"
    old = packet()
    old["producer"]["cron_run_at_ms"] -= 86400_000
    assert day(packets=[old])["first_failed_precondition"] == "terminal_packet_today"


def test_consultation_flag_and_secret_free_output():
    r = pd.evaluate_persona_day(PID, lifecycle_state="paper_owner", cron_jobs=1, packets=[packet()], now=NOW,
                                consultations=[{"at": "2026-11-01T00:00:00Z"}])
    assert r["no_consultation_in_7_days"] and r["passed"]
    r = pd.evaluate_persona_day(PID, lifecycle_state="paper_owner", cron_jobs=1, packets=[packet()], now=NOW,
                                consultations=[{"at": "2026-11-15T00:00:00Z"}])
    assert not r["no_consultation_in_7_days"]


def test_run_aggregates_and_is_get_only():
    seen = []

    def fetch(path):
        seen.append(path)
        if path.startswith("/bff/personas?"):
            return {"items": [row(PID, "research_only")]}
        raise AssertionError(path)

    report = pd.run(fetch, NOW)
    assert report["verdict"] == "failed" and seen == ["/bff/personas?page_size=200"]
    assert pd.aggregate([])["verdict"] == "failed"


def test_tool_use_survives_packet_store_compaction():
    from services.persona.cron_ooda_closure import bound_packet_payload
    packet = {"persona_cycle": {"tool_use": {"memory_recall_ok": 1, "proposals_recorded": 2},
                                "observe_bundle": {"quarter_equity_observations": list(range(5000))}}}
    out = bound_packet_payload(packet)
    assert out["persona_cycle"]["tool_use"] == {"memory_recall_ok": 1, "proposals_recorded": 2}
    assert len(json.dumps(out["persona_cycle"]["tool_use"])) < 200


def test_cycle_day_is_utc_and_late_run_is_harmless():
    early = datetime(2026, 11, 17, 6, 30, tzinfo=timezone.utc)
    assert pd.cycle_day(early) == "2026-11-16"            # before the window ends: previous cycle
    assert pd.cycle_day(datetime(2026, 11, 17, 10, 44, tzinfo=timezone.utc)) == "2026-11-16"
    for h in (10, 11, 15, 23):                              # 10:45Z.. end of day: same cycle day
        assert pd.cycle_day(datetime(2026, 11, 17, h, 45, tzinfo=timezone.utc)) == "2026-11-17"
    r = day(packets=[packet()])
    assert r["cycle_day"] == "2026-11-17" and r["cycle_due_at_utc"] == "2026-11-17T10:00:00+00:00"
    late = pd.evaluate_persona_day(PID, lifecycle_state="paper_owner", cron_jobs=1, packets=[packet()],
                                   now=datetime(2026, 11, 17, 22, 0, tzinfo=timezone.utc))
    assert late["passed"] and late["cycle_day"] == "2026-11-17"
    at_0630 = pd.evaluate_persona_day(PID, lifecycle_state="paper_owner", cron_jobs=1, packets=[packet()], now=early)
    assert at_0630["cycle_day"] == "2026-11-16" and not at_0630["passed"]
    assert pd.aggregate([r])["cycle_day"] == "2026-11-17"


def _env(monkeypatch, **kw):
    for k in ("ACCEPTANCE_BFF_URL", "ACCEPTANCE_VIEWER_CLIENT_ID", "ACCEPTANCE_VIEWER_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    for k, v in kw.items():
        monkeypatch.setenv(k, v)


def test_missing_credential_is_failed_not_skipped(monkeypatch, capsys):
    _env(monkeypatch, ACCEPTANCE_BFF_URL="https://bff.example")
    assert pd.main() == 1
    assert json.loads(capsys.readouterr().out) == {"verdict": "failed", "reason": "credential_missing:viewer_client"}


def test_auth_and_read_failures_carry_http_status(monkeypatch, capsys):
    import urllib.error
    _env(monkeypatch, ACCEPTANCE_BFF_URL="https://bff.example", ACCEPTANCE_VIEWER_CLIENT_ID="v",
         ACCEPTANCE_VIEWER_CLIENT_SECRET="SECRET-VALUE")

    def boom(code):
        def opener(request, timeout=0):
            raise urllib.error.HTTPError(request.full_url, code, "x", {}, None)
        return opener
    monkeypatch.setattr(pd.urllib.request, "urlopen", boom(401))
    assert pd.main() == 1
    out = capsys.readouterr().out
    assert json.loads(out)["reason"] == "auth_failed:401" and "SECRET-VALUE" not in out

    class Resp:
        def __init__(self, body): self.body = body
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, *a): return self.body
    calls = []

    def opener(request, timeout=0):
        calls.append((request.get_method(), request.full_url))
        if request.get_method() == "POST":
            return Resp(b'{"access_token": "TOKEN-VALUE"}')
        assert request.headers["Authorization"] == "Bearer TOKEN-VALUE"
        raise urllib.error.HTTPError(request.full_url, 503, "x", {}, None)
    monkeypatch.setattr(pd.urllib.request, "urlopen", opener)
    assert pd.main() == 1
    out = capsys.readouterr().out
    assert json.loads(out)["reason"] == "read_failed:503" and "TOKEN-VALUE" not in out
    assert [m for m, _ in calls] == ["POST", "GET"]


TRADING = ["persona-2448c7053e33ea48da47", "persona-ee7112a829d38b9135ac", "persona-6507750312870d03a241"]
BASELINE = "persona-d64835a203ce276b8fbb"


def row(pid, owner_state, state=None, tenant="tenant-dev", **kw):
    """Reduced BFF /bff/personas row: only the fields the probe reads, no credential."""
    r = {"id": pid, "name": kw.pop("name", pid), "mandate": kw.pop("mandate", ""), "state": state or owner_state,
         "ownerLifecycleState": owner_state, "tenantId": tenant}
    r.update(kw)
    return r


def _hosted_1118():
    """Shape of the 2026-11-18 hosted list. NOT a hosted capture (no credential in the worker): the selection-relevant
    fields of the rows named in the task are reproduced; the remaining rows are representative non-selected states."""
    rows = [row(pid, "paper_owner") for pid in TRADING]
    rows.append(row(BASELINE, "research_only", state="paper_running", name="Pantheon Dev Paper Release 43688794313 Attempt 1",
                    mandate="Paper-only lifecycle verification in dev", strategyFamily="dev_paper_baseline"))
    rows += [row(f"s-ret{i}", "retired") for i in range(5)] + [row(f"s-dr{i}", "draft") for i in range(5)]
    rows += [row(f"s-res{i}", "research_only") for i in range(3)] + [row("agora-a", "consultable")]
    return rows


def test_probe_set_equals_controller_set_for_same_list():
    from services.persona import persona_cycle_controller as ctl
    items = _hosted_1118()
    probe_ids = {x["id"] for x in pd.daily_cycle_personas(items)}
    controller_ids = {x["id"] for x in items if x["tenantId"]
                      and ctl.is_daily_cycle_persona({"lifecycle_state": x["ownerLifecycleState"], "status": "active"})}
    assert probe_ids == controller_ids == set(TRADING)
    assert pd.DAILY_CYCLE_STATES == ctl.DAILY_CYCLE_STATES == ctl.ELIGIBLE_STATES - {"consultable"}


def test_baseline_left_out_by_owner_lifecycle_not_bff_state():
    judged, left = pd.partition_personas(_hosted_1118())
    assert [x["id"] for x in judged] == TRADING
    reasons = {x["persona_id"]: x["reason"] for x in left}
    assert reasons[BASELINE] == "owner_lifecycle_state_not_daily_cycle:research_only"


def test_hosted_replay_evaluates_exactly_the_three_trading_personas():
    seen = []

    def fetch(path):
        seen.append(path)
        if path.startswith("/bff/personas?"):
            return {"items": _hosted_1118()}
        return {"data": {"personaCycle": {"dayStatus": {"persona_id": path.rsplit("/", 1)[1], "passed": True,
                                                         "preconditions": [{}]}}}}

    report = pd.run(fetch, NOW)
    assert [r["persona_id"] for r in report["personas"]] == TRADING
    assert report["verdict"] == "passed"
    assert report["left_out_count"] == len(_hosted_1118()) - 3 == len(report["left_out"])


def test_selected_persona_without_cron_or_packet_still_fails_verdict():
    def fetch(path):
        if path.startswith("/bff/personas?"):
            return {"items": [row(TRADING[0], "paper_owner")]}
        return {"data": {"personaCycle": {"dayStatus": pd.evaluate_persona_day(
            TRADING[0], lifecycle_state="paper_owner", cron_jobs=0, packets=[], now=NOW)}}}
    report = pd.run(fetch, NOW)
    assert report["verdict"] == "failed" and report["personas"][0]["first_failed_precondition"] == "daily_cycle_cron_job"


def test_probe_test_and_acceptance_personas_are_never_judged():
    items = [row("p-release", "paper_owner", name="Release Probe 1"),
             row("p-test", "paper_owner", dataScope="test"),
             row("p-acc", "paper_owner", metadata={"acceptance_run": True}),
             row("p-ok", "paper_owner")]
    judged, left = pd.partition_personas(items)
    assert [x["id"] for x in judged] == ["p-ok"]
    assert {x["persona_id"] for x in left if x["reason"] == "probe_or_test_persona"} == {"p-release", "p-test", "p-acc"}


def test_controller_conditions_status_and_tenant():
    judged, left = pd.partition_personas([row("a", "paper_owner", tenant=""), row("b", "paper_owner", state="archived"),
                                          row("c", "paper_owner", status="suspended")])
    assert not judged and [x["reason"] for x in left] == ["no_tenant_id", "status_not_active:archived",
                                                          "status_not_active:suspended"]


def test_eligible_persona_without_cron_is_failed_not_dropped():
    r = pd.evaluate_persona_day("t-1", lifecycle_state="paper_running", cron_jobs=0, packets=[], now=NOW)
    assert status(r)["daily_cycle_cron_job"] == "failed" and status(r)["lifecycle_paper_owner"] == "passed"


def test_row_without_owner_lifecycle_field_is_not_selected_by_projection():
    # A row carrying only the projected/legacy fields must not be guessed into the set.
    r = {"id": "x", "state": "paper_running", "lifecycleStatus": "paper_owner", "tenantId": "t"}
    judged, left = pd.partition_personas([r])
    assert judged == [] and left[0]["reason"] == "owner_lifecycle_state_not_daily_cycle:None"
