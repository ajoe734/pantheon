from __future__ import annotations

from datetime import datetime, timezone

from acceptance import persona_day as pd

PID = "persona-a"
NOW = datetime(2026, 11, 17, 6, 30, tzinfo=timezone.utc)  # Tue 14:30 Taipei -> trading day 2026-11-17
RUN_MS = int(datetime(2026, 11, 17, 2, 0, tzinfo=timezone.utc).timestamp() * 1000)


def packet(**cycle):
    base = {"phase": "terminal", "failure_reason": None,
            "tool_use": {"memory_recall_ok": 2, "proposals_recorded": 1},
            "output": {"proposals": [{"kind": "no_action", "case_file_item_ids": ["i1"]}]}}
    base.update(cycle)
    return {"packet_id": "p1", "persona_ids": [PID], "producer": {"cron_run_at_ms": RUN_MS},
            "updated_at": "2026-11-17T02:05:00Z", "persona_cycle": base,
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
            return {"items": [{"persona_id": PID, "state": "research_only", "status": "active"}]}
        raise AssertionError(path)

    report = pd.run(fetch, NOW)
    assert report["verdict"] == "failed" and seen == ["/bff/personas?page_size=200"]
    assert pd.aggregate([])["verdict"] == "failed"
