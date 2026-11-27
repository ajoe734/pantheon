"""Discovery pipeline acceptance (acceptance/discovery_pipeline_day.py, probe acceptance/probes/discovery-pipeline).

The 2026-11-25 fixture is reconstructed from the coordinator's recorded hosted facts (see its
``provenance``); the passing world and the per-stage failures are synthetic and say so.
"""
from __future__ import annotations

import copy
import json
import os
import stat
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from acceptance import discovery_pipeline_day as dpd

ROOT = Path(__file__).resolve().parent
PROBE = ROOT / "probes" / "discovery-pipeline"
FIXTURE = json.loads((ROOT / "fixtures" / "hosted_discovery_pipeline_20261125.json").read_text())
NOW = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)


def ok(source="x"):
    return {"status": "ok", "source": source}


def good_board():
    """Synthetic world in which every stage works; one gate refusal and no strategy passed."""
    return {
        "data": {
            "discovered": [
                {"kind": "hypothesis_card", "source_id": "s1", "title": "TSMC supply chain", "claim": "c",
                 "claim_status": "ok", "since": "2026-11-28T08:00:00Z"},
                {"kind": "research_demand", "demand_id": "rd-1", "since": "2026-11-20T00:00:00Z"},
            ],
            "scaffolded": [
                {"kind": "template_search", "demand_id": "rd-1", "attempt_count": 2,
                 "latest_at": "2026-11-28T10:03:00Z", "outcome": "completed",
                 "reason_code": "survivorship_limited", "reason": "survivorship_limited: no attestation"},
            ],
            "replicated": [],
            "document_checked": [],
            "counts": {
                "collected_text_records": {"value": 120, "status": "ok"},
                "hypothesis_cards_today": {"value": 3, "status": "ok"},
                "llm_calls_today": {"value": 40, "status": "ok", "cap": 400},
                "templates_registered": {"value": 182, "status": "ok"},
                "templates_evaluated": {"value": 171, "status": "ok"},
                "passes": {"value": 0, "status": "ok"},
            },
            "templates": [{"template_id": "t1", "evaluation": {"evaluation_context": {
                "governed_snapshot_id": "gmd-abc123", "window_end": "2026-11-27"}}}],
            "discovery_summary": {
                "collected_by_source_family": {"news": 120},
                "closed_last_24h": {"by_reason": {}},
                "llm_calls": {
                    "status": "ok",
                    "source": "source_ingestion.l2_extraction_ledger",
                    "scope": "service",
                    "utc_day": "2026-11-28",
                    "calls": 40,
                    "daily_cap": 400,
                    "cap_source": "ledger",
                    "release_switched_at": "2026-11-28T06:00:00Z",
                    "ritual_events_status": "ok",
                    "ritual_events_since_switch": 0,
                    "events_examined_since_switch": 3,
                    "ritual_events_truncated": False,
                    "ritual_events": [],
                    "ritual_events_sample_truncated": False,
                },
            },
        },
        "meta": {"surfaces": {k: ok() for k in (
            "alpha_factory", "alpha_factory_templates", "alpha_factory_demands", "alpha_factory_runs",
            "alpha_factory_hypothesis_cards", "alpha_factory_source_records", "alpha_factory_llm_calls")}},
    }


def good_health():
    return {"data": {"last_success": {"at": "2026-11-28T11:30:00Z"}, "last_failure": None,
                     "downstream_actual_state": {"status": "ready", "authoritative": True,
                                                 "checked_at": "2026-11-28T11:30:00Z",
                                                 "summary": "4204 terminal drafts; 0 dead letters"}}}


def run(board=None, health=None):
    board = good_board() if board is None else board
    health = good_health() if health is None else health
    return dpd.judge(board, health, NOW)


def stage(report, name):
    return next(s for s in report["stages"] if s["stage"] == name)


def count(board, key, **entry):
    board["data"]["counts"][key] = entry


def test_every_stage_is_reported_once_in_order():
    report = run()
    assert [s["stage"] for s in report["stages"]] == list(dpd.STAGES)


def test_working_pipeline_passes_and_says_no_strategy_passed():
    report = run()
    assert report["verdict"] == "passed" and report["failed_stages"] == []
    assert report["strategy_verdict"] == "no strategy passed" and report["strategies_passed"] == 0
    gate = stage(report, "gate_attempts")
    assert gate["status"] == "passed" and gate["values"]["by_reason"] == {"survivorship_limited": 1}
    assert stage(report, "governed_market_dataset")["values"] == {"snapshot_id": "gmd-abc123", "day": "2026-11-27"}
    assert stage(report, "hypothesis_cards")["values"]["cap"] == 400
    assert stage(report, "hypothesis_cards")["values"]["ritual_events_since_switch"] == 0
    assert report["ritual_events_since_switch"] == 0


def test_hosted_2026_11_25_fixture_fails_naming_every_broken_stage():
    now = datetime.fromisoformat(FIXTURE["now"].replace("Z", "+00:00"))
    report = dpd.judge(FIXTURE["board"], FIXTURE["health"], now)
    assert report["verdict"] == "failed"
    assert report["failed_stages"] == list(dpd.STAGES)
    reasons = {s["stage"]: s["reason"] for s in report["stages"]}
    assert reasons["template_search"] == "last_search_unavailable:mandate_universe_missing"
    assert reasons["templates_evaluated"] == "no_template_evaluated"
    assert reasons["governed_market_dataset"] == "no_governed_snapshot_recorded"
    assert reasons["gate_attempts"] == "gate_attempts_without_result"  # the one search attempt was unavailable
    assert reasons["distillation_dead_letters"] == "distillation_state_stale"
    assert reasons["external_records"].startswith("unreadable:collected_text_records")
    assert reasons["hypothesis_cards"].startswith("unreadable:")
    assert reasons["no_fallback_drafts"].startswith("unreadable:alpha_factory_hypothesis_cards")
    assert report["strategy_verdict"] == "no strategy passed"


def test_the_fixture_makes_the_probe_exit_one_naming_the_stages():
    out = dpd.render(dpd.judge(FIXTURE["board"], FIXTURE["health"],
                               datetime.fromisoformat(FIXTURE["now"].replace("Z", "+00:00"))))
    assert "VERDICT failed" in out and "template_search" in out.splitlines()[-1]


# ---- one failing case per stage

def test_no_external_records_fails():
    board = good_board()
    count(board, "collected_text_records", value=0, status="ok")
    assert stage(run(board), "external_records")["reason"] == "no_external_records_last_24h"


def test_cards_over_cap_fails():
    board = good_board()
    board["data"]["counts"]["llm_calls_today"]["value"] = 401
    assert stage(run(board), "hypothesis_cards")["reason"] == "llm_calls_over_cap"


def test_no_verified_card_names_disabled_extraction():
    board = good_board()
    board["data"]["discovered"] = []
    board["data"]["discovery_summary"]["closed_last_24h"] = {"by_reason": {"l2_extraction_disabled": 90}}
    reason = stage(run(board), "hypothesis_cards")["reason"]
    assert reason == "no_verified_hypothesis_card_last_24h:l2_extraction_disabled=90"


def test_old_card_does_not_count_as_new():
    board = good_board()
    board["data"]["discovered"][0]["since"] = "2026-11-20T08:00:00Z"
    assert stage(run(board), "hypothesis_cards")["status"] == "failed"
    assert stage(run(board), "no_fallback_drafts")["status"] == "passed"  # zero NEW drafts


def test_ritual_marked_events_since_release_switch_must_be_zero():
    switched_at = "2026-11-28T06:00:00Z"
    pre_switch_and_genuine = [
        {
            "ts": "2026-11-27T05:15:00Z",
            "outcome": "no_hypothesis",
            "detail": (
                "First-run setup is pending, so I haven't extracted the document yet. "
                "I'd go by Finch, a calm, direct AI assistant 🐦. Does that work for you, and what should I call you?"
            ),
        },
        {
            "ts": "2026-11-28T07:00:00Z",
            "outcome": "no_hypothesis",
            "detail": "The article reports quarterly earnings with no testable market mechanism.",
        },
    ]
    assert dpd.count_ritual_events_since_switch(pre_switch_and_genuine, switched_at) == 0
    passing = dpd.judge(good_board(), good_health(), NOW, ledger_events=pre_switch_and_genuine, switched_at=switched_at)
    assert passing["verdict"] == "passed"
    assert passing["ritual_events_since_switch"] == 0
    assert stage(passing, "hypothesis_cards")["values"]["ritual_events_since_switch"] == 0

    post_switch_ritual = [
        *pre_switch_and_genuine,
        {
            "ts": "2026-11-28T07:30:00Z",
            "outcome": "no_hypothesis",
            "detail": "First-run setup is pending; what should I call you?",
        },
        {
            "ts": "2026-11-28T08:10:00Z",
            "outcome": "no_hypothesis",
            "detail": "請問該怎麼稱呼你？本次工作階段無法安全完成 BOOTSTRAP.md",
        },
    ]
    assert dpd.count_ritual_events_since_switch(post_switch_ritual, switched_at) == 2
    failing = dpd.judge(good_board(), good_health(), NOW, ledger_events=post_switch_ritual, switched_at=switched_at)
    assert failing["verdict"] == "failed" and failing["failed_stages"] == ["hypothesis_cards"]
    assert failing["ritual_events_since_switch"] == 2
    hc = stage(failing, "hypothesis_cards")
    assert hc["status"] == "failed" and hc["reason"] == "ritual_marked_events_since_switch:2"
    assert hc["values"]["ritual_events_since_switch"] == 2

    board_with_summary_events = good_board()
    board_with_summary_events["data"]["discovery_summary"]["switched_at"] = switched_at
    board_with_summary_events["data"]["discovery_summary"]["events"] = post_switch_ritual
    from_board = run(board_with_summary_events)
    assert from_board["verdict"] == "failed"
    assert stage(from_board, "hypothesis_cards")["reason"] == "ritual_marked_events_since_switch:2"


def test_missing_unreadable_or_incomplete_ritual_observation_or_switch_time_fails(monkeypatch):
    monkeypatch.delenv("ACCEPTANCE_RELEASE_SWITCHED_AT", raising=False)

    # Missing observation (neither count nor event list) must fail, never default to 0.
    missing_obs = good_board()
    missing_obs["data"]["discovery_summary"]["llm_calls"] = {
        "status": "ok",
        "release_switched_at": "2026-11-28T06:00:00Z",
    }
    report = run(missing_obs)
    assert report["verdict"] == "failed"
    assert report["ritual_events_since_switch"] is None
    assert stage(report, "hypothesis_cards")["reason"] == "unreadable:ritual_events_since_switch:observation_missing"

    # Missing release switch time must fail.
    missing_switch = good_board()
    del missing_switch["data"]["discovery_summary"]["llm_calls"]["release_switched_at"]
    report = run(missing_switch)
    assert report["verdict"] == "failed"
    assert stage(report, "hypothesis_cards")["reason"] == "unreadable:ritual_events_since_switch:release_switch_time_missing"

    # Invalid release switch time must fail.
    invalid_switch = good_board()
    invalid_switch["data"]["discovery_summary"]["llm_calls"]["release_switched_at"] = "not-a-timestamp"
    report = run(invalid_switch)
    assert report["verdict"] == "failed"
    assert stage(report, "hypothesis_cards")["reason"] == "unreadable:ritual_events_since_switch:release_switch_time_invalid"

    # Truncated ledger event scan must fail.
    truncated_obs = good_board()
    truncated_obs["data"]["discovery_summary"]["llm_calls"]["ritual_events_truncated"] = True
    report = run(truncated_obs)
    assert report["verdict"] == "failed"
    assert stage(report, "hypothesis_cards")["reason"] == "unreadable:ritual_events_since_switch:observation_truncated"

    # Unreadable ritual_events_status must fail.
    unreadable_obs = good_board()
    unreadable_obs["data"]["discovery_summary"]["llm_calls"]["ritual_events_status"] = "unavailable"
    unreadable_obs["data"]["discovery_summary"]["llm_calls"]["ritual_events_reason"] = "extraction_ledger_events_missing"
    report = run(unreadable_obs)
    assert report["verdict"] == "failed"
    assert (
        stage(report, "hypothesis_cards")["reason"]
        == "unreadable:ritual_events_since_switch:extraction_ledger_events_missing"
    )

    # Event list with missing/invalid timestamp must fail.
    bad_event_ts = good_board()
    bad_event_ts["data"]["discovery_summary"]["llm_calls"]["ritual_events"] = [
        {"ts": "bad-ts", "outcome": "no_hypothesis", "detail": "what should I call you?"}
    ]
    report = run(bad_event_ts)
    assert report["verdict"] == "failed"
    assert (
        stage(report, "hypothesis_cards")["reason"]
        == "unreadable:ritual_events_since_switch:ledger_event_timestamp_invalid"
    )


def test_e2e_check_and_main_with_real_queue_ledger_and_discovery_summary(tmp_path, server, monkeypatch, capsys):
    import sqlite3
    from services.source_ingestion.connectors.base import SourceRecord
    from services.source_ingestion.discovery_summary import read_discovery_summary
    from services.source_ingestion.distillation_worker import DistillationJobQueue
    from services.source_ingestion.hypothesis_card import ExtractionLedger

    now_epoch = NOW.timestamp()
    switch_iso = "2026-11-28T06:00:00Z"
    queue_path = tmp_path / "distill_job_queue.jsonl"
    ledger_path = tmp_path / "l2_extraction_ledger.sqlite"
    queue = DistillationJobQueue(queue_path, now=lambda: now_epoch)
    ledger = ExtractionLedger(ledger_path, daily_cap=400)

    def make_source(sid: str) -> SourceRecord:
        return SourceRecord(
            source_id=sid,
            connector_id="conn",
            source_type="news",
            title=f"TSMC supply chain {sid}",
            content_ref=f"https://example.test/{sid}",
            status="normalized",
            metadata={"tenant_id": "tenant-dev", "body": "台積電月營收創高"},
        )

    # 1. Carded record + pre-switch ritual event + post-switch genuine abstention -> check/main must PASS.
    assert ledger.reserve_call("s-card", "d-card")
    job_card = queue.enqueue_source_record(make_source("s-card"), enqueued_at=now_epoch - 3600)
    queue.mark_done(job_card.job_id, seed_id="seed-s-card", processed_at=now_epoch - 1800)
    ledger.put_card("s-card", job_card.source_digest, {"claim": "Monthly revenue surprise drifts", "testability": "testable"})

    with sqlite3.connect(ledger_path) as conn:
        conn.execute(
            "INSERT INTO events(ts, source_id, outcome, detail, usage) VALUES(?,?,?,?,?)",
            (
                "2026-11-28T05:00:00Z",
                "s-pre-ritual",
                "no_hypothesis",
                "First-run setup is still open; what should I call you?",
                None,
            ),
        )
        conn.execute(
            "INSERT INTO events(ts, source_id, outcome, detail, usage) VALUES(?,?,?,?,?)",
            (
                "2026-11-28T07:00:00Z",
                "s-post-genuine",
                "no_hypothesis",
                "Quarterly revenue report states no testable market mechanism.",
                None,
            ),
        )

    def project_board_from_summary(summary: dict) -> dict:
        board = good_board()
        collected = summary["collected_text_records"]
        closed = summary["closed_text_records"]
        cards = summary["hypothesis_cards"]
        llm = summary["llm_calls"]
        board["data"]["counts"]["collected_text_records"] = {"value": collected["last_24h"]["total"], "status": "ok"}
        board["data"]["counts"]["hypothesis_cards_today"] = {"value": cards["today"], "status": "ok"}
        board["data"]["counts"]["llm_calls_today"] = {
            "value": llm["calls"],
            "status": llm["status"],
            "cap": llm["daily_cap"],
            "scope": llm["scope"],
        }
        board["data"]["discovered"] = [
            {
                "kind": "hypothesis_card",
                "source_id": c["source_id"],
                "title": c["title"],
                "claim": c["claim"],
                "claim_status": c["claim_status"],
                "since": c["carded_at"],
            }
            for c in cards["newest"]
        ] + [d for d in board["data"]["discovered"] if d.get("kind") == "research_demand"]
        board["data"]["discovery_summary"] = {
            "window": summary.get("window"),
            "collected_by_source_family": collected["last_24h"]["by_source_family"],
            "closed_last_24h": closed["last_24h"],
            "closed_total": closed["total"],
            "llm_calls": llm,
            "bounds": summary.get("bounds"),
        }
        return board

    summary_clean = read_discovery_summary(
        queue_path=queue_path,
        tenant_id="tenant-dev",
        ledger_path=ledger_path,
        now=now_epoch,
        release_switched_at=switch_iso,
    )
    assert summary_clean["llm_calls"]["ritual_events_since_switch"] == 0
    Handler.board = project_board_from_summary(summary_clean)
    Handler.health = good_health()

    for key, value in {
        "ACCEPTANCE_BFF_URL": server,
        "ACCEPTANCE_VIEWER_CLIENT_ID": "viewer",
        "ACCEPTANCE_VIEWER_CLIENT_SECRET": "client-SECRET-VALUE",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("ACCEPTANCE_RELEASE_SWITCHED_AT", raising=False)

    assert dpd.main(now=NOW) == 0
    out_clean = capsys.readouterr().out
    assert "VERDICT passed" in out_clean
    assert '"ritual_events_since_switch": 0' in out_clean

    # 2. Post-switch ritual failure (outbox collapses reason to hypothesis_extraction_failed) -> check/main must FAIL.
    assert ledger.reserve_call("s-post-ritual", "d-post-ritual")
    job_ritual = queue.enqueue_source_record(make_source("s-post-ritual"), enqueued_at=now_epoch - 1200)
    queue.mark_skipped(
        job_ritual.job_id,
        reason="hypothesis_extraction_failed: openclaw_workspace_first_run_ritual: 請問該怎麼稱呼你？",
        processed_at=now_epoch - 900,
    )
    with sqlite3.connect(ledger_path) as conn:
        conn.execute(
            "INSERT INTO events(ts, source_id, outcome, detail, usage) VALUES(?,?,?,?,?)",
            (
                "2026-11-28T08:30:00Z",
                "s-post-ritual",
                "failed",
                "openclaw_workspace_first_run_ritual: 請問該怎麼稱呼你？",
                None,
            ),
        )

    summary_contaminated = read_discovery_summary(
        queue_path=queue_path,
        tenant_id="tenant-dev",
        ledger_path=ledger_path,
        now=now_epoch,
        release_switched_at=switch_iso,
    )
    assert summary_contaminated["closed_text_records"]["last_24h"]["by_reason"] == {"hypothesis_extraction_failed": 1}
    assert summary_contaminated["llm_calls"]["ritual_events_since_switch"] == 1
    Handler.board = project_board_from_summary(summary_contaminated)

    assert dpd.main(now=NOW) == 1
    out_contaminated = capsys.readouterr().out
    assert "VERDICT failed failed=hypothesis_cards" in out_contaminated
    assert "reason=ritual_marked_events_since_switch:1" in out_contaminated
    assert '"ritual_events_since_switch": 1' in out_contaminated


@pytest.mark.parametrize("card", [
    {"title": "Evidence suggests: a lottery", "claim_status": "ok"},
    {"title": "A drink-driving report", "claim_status": "card_not_in_ledger"},
    {"title": "A report", "claim": "Evidence suggests: x", "claim_status": "ok"},
])
def test_fallback_draft_fails(card):
    board = good_board()
    board["data"]["discovered"].append({"kind": "hypothesis_card", "source_id": "s2",
                                        "since": "2026-11-28T09:00:00Z", **card})
    result = stage(run(board), "no_fallback_drafts")
    assert result["status"] == "failed" and result["reason"] == "external_text_drafts_without_verified_card"
    assert result["values"]["count"] == 1


def test_draft_with_unavailable_ledger_is_not_verifiable():
    board = good_board()
    board["data"]["discovered"][0]["claim_status"] = "ledger_unavailable"
    assert stage(run(board), "no_fallback_drafts")["reason"] == "drafts_not_verifiable:ledger_unavailable"


def test_governed_dataset_missing_stale_or_not_governed():
    board = good_board()
    board["data"]["templates"] = [{"template_id": "t1", "evaluation": None}]
    assert stage(run(board), "governed_market_dataset")["reason"] == "no_governed_snapshot_recorded"
    board = good_board()
    board["data"]["templates"][0]["evaluation"]["evaluation_context"]["window_end"] = "2026-11-01"
    assert stage(run(board), "governed_market_dataset")["reason"] == "governed_snapshot_stale"
    board = good_board()
    board["data"]["templates"][0]["evaluation"]["evaluation_context"]["governed_snapshot_id"] = "persona-pool-1"
    assert stage(run(board), "governed_market_dataset")["reason"] == "snapshot_not_governed_market"


def test_template_search_unavailable_fails_but_refusals_pass():
    board = good_board()
    board["data"]["scaffolded"][0].update(outcome="failed", reason_code="mandate_universe_missing",
                                          reason="template_search_unavailable:ValueError:mandate_universe_missing")
    assert stage(run(board), "template_search")["status"] == "failed"
    for code in ("survivorship_limited", "below_threshold"):
        board = good_board()
        board["data"]["scaffolded"][0].update(reason_code=code, reason=code)
        assert stage(run(board), "template_search")["status"] == "passed"


def test_template_search_stale_not_due_and_never_searched():
    board = good_board()
    board["data"]["scaffolded"][0]["latest_at"] = "2026-11-20T10:00:00Z"
    assert stage(run(board), "template_search")["reason"] == "last_search_stale"
    board = good_board()
    board["data"]["scaffolded"] = []
    assert stage(run(board), "template_search")["reason"] == "open_demands_never_searched"
    board["data"]["discovered"][1]["since"] = "2026-11-28T06:00:00Z"
    assert stage(run(board), "template_search")["status"] == "not_due"
    board["data"]["discovered"] = [board["data"]["discovered"][0]]
    assert stage(run(board), "template_search") == dpd._stage("template_search", "not_due", "no_open_demand")


def test_no_template_evaluated_and_stale_evaluation_fail():
    board = good_board()
    count(board, "templates_evaluated", value=0, status="ok")
    assert stage(run(board), "templates_evaluated")["reason"] == "no_template_evaluated"
    board = good_board()
    board["data"]["templates"][0]["evaluation"]["evaluation_context"]["window_end"] = "2026-11-01"
    assert stage(run(board), "templates_evaluated")["reason"] == "evaluation_stale"


def test_gate_counts_by_reason_and_passes():
    board = good_board()
    recent = "2026-11-27T01:00:00Z"
    board["data"]["replicated"] = [{"id": "a", "created_at": recent}]
    board["data"]["document_checked"] = [{"id": "b", "document_check_reason": "no_passing_gate_result", "created_at": recent},
                                         {"id": "c", "document_check_reason": "retired", "created_at": recent},
                                         {"id": "old", "document_check_reason": "retired", "created_at": "2026-10-01T00:00:00Z"}]
    count(board, "passes", value=1, status="ok")
    gate = stage(run(board), "gate_attempts")
    assert gate["status"] == "passed"
    assert gate["values"]["by_reason"] == {"passed": 1, "no_passing_gate_result": 1, "retired": 1, "survivorship_limited": 1}
    assert gate["reason"] == "gate_answered:1_strategy(ies)_passed"


def test_gate_with_only_errors_or_nothing_fails():
    board = good_board()
    board["data"]["scaffolded"][0].update(outcome="failed", reason_code="mandate_universe_missing", reason="unavailable")
    gate = stage(run(board), "gate_attempts")
    assert gate["status"] == "failed" and gate["reason"] == "gate_attempts_without_result"
    board["data"]["scaffolded"] = []
    assert stage(run(board), "gate_attempts")["reason"] == "no_gate_result_in_window"


def test_dead_letters_unresolved_stale_unobserved_and_unparseable_fail():
    health = good_health()
    health["data"]["downstream_actual_state"]["summary"] = "4204 terminal drafts; 142 unresolved dead letters (working_with_unresolved_dead_letters)"
    result = stage(run(health=health), "distillation_dead_letters")
    assert result["reason"] == "unresolved_dead_letters" and result["values"]["unresolved"] == 142
    health = good_health()
    health["data"]["downstream_actual_state"]["checked_at"] = "2026-11-27T00:00:00Z"
    assert stage(run(health=health), "distillation_dead_letters")["reason"] == "distillation_state_stale"
    health = good_health()
    health["data"]["downstream_actual_state"]["authoritative"] = False
    assert stage(run(health=health), "distillation_dead_letters")["reason"] == "distillation_state_unobserved"
    health = good_health()
    health["data"]["downstream_actual_state"]["summary"] = "something else"
    assert stage(run(health=health), "distillation_dead_letters")["reason"] == "dead_letter_count_not_reported"


# ---- a stage that cannot be read is failed with its reason, never skipped or passed

def test_unreadable_routes_fail_every_stage_they_feed():
    report = dpd.judge(dpd.ReadFailed("http_503"), dpd.ReadFailed("http_403"), NOW)
    assert report["failed_stages"] == list(dpd.STAGES)
    assert all(s["status"] == "failed" for s in report["stages"])
    assert stage(report, "template_search")["reason"] == "unreadable:alpha_factory:http_503"
    assert stage(report, "distillation_dead_letters")["reason"] == "unreadable:loop_health:http_403"


@pytest.mark.parametrize("surface", ["alpha_factory", "alpha_factory_runs", "alpha_factory_templates",
                                     "alpha_factory_hypothesis_cards", "alpha_factory_demands"])
def test_unavailable_surface_never_passes_the_stages_that_need_it(surface):
    board = good_board()
    board["meta"]["surfaces"][surface] = {"status": "unavailable", "reason": "owner_read_failed"}
    report = run(board)
    needing = {"alpha_factory": set(dpd.STAGES) - {"distillation_dead_letters"},
               "alpha_factory_runs": {"template_search", "gate_attempts"},
               "alpha_factory_templates": {"governed_market_dataset"},
               "alpha_factory_hypothesis_cards": {"hypothesis_cards", "no_fallback_drafts"},
               "alpha_factory_demands": {"template_search"}}[surface]
    assert needing <= set(report["failed_stages"])
    assert all("unreadable" in stage(report, n)["reason"] or stage(report, n)["reason"].startswith("unreadable") for n in needing)


def test_unavailable_count_is_failed_not_zero():
    board = good_board()
    count(board, "templates_evaluated", value=None, status="unavailable", reason="templates_read_failed")
    assert stage(run(board), "templates_evaluated")["reason"] == "unreadable:templates_evaluated:templates_read_failed"
    assert stage(run(dict(board, data=None)), "external_records")["status"] == "failed"


def test_invalid_payloads_fail():
    assert run(board=[], health=[])["verdict"] == "failed"
    assert stage(run(health={"data": {}}), "distillation_dead_letters")["reason"] == "loop_health_payload_invalid"


def test_check_reads_exactly_two_routes_with_get_only():
    seen = []

    def fetch(path):
        seen.append(path)
        if "alpha-factory" in path:
            raise dpd.ReadFailed("http_500")
        return good_health()

    report = dpd.check(fetch, NOW)
    assert seen == [dpd.ALPHA_FACTORY, dpd.DISTILLATION_HEALTH]
    assert report["failed_stages"] == list(dpd.STAGES)[:-1]


# ---- the executable probe against a local server

class Handler(BaseHTTPRequestHandler):
    board = good_board()
    health = good_health()
    methods = []

    def log_message(self, *args):
        pass

    def _send(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        Handler.methods.append(("POST", self.path))
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self._send(200, {"access_token": "tok-SECRET-VALUE"}) if self.path == "/bff/auth/dev-login" else self._send(404, {})

    def do_GET(self):
        Handler.methods.append(("GET", self.path))
        assert self.headers.get("Authorization") == "Bearer tok-SECRET-VALUE"
        if self.path.startswith("/bff/alpha-factory"):
            self._send(200, Handler.board)
        elif self.path.startswith("/bff/v5/loop-health/strategy_distillation"):
            self._send(200, Handler.health)
        else:
            self._send(404, {})


@pytest.fixture()
def server():
    Handler.board, Handler.health, Handler.methods = good_board(), good_health(), []
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def exec_probe(url, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("ACCEPTANCE_")}
    env.update({"ACCEPTANCE_BFF_URL": url, "ACCEPTANCE_VIEWER_CLIENT_ID": "viewer",
                "ACCEPTANCE_VIEWER_CLIENT_SECRET": "client-SECRET-VALUE"}, **extra)
    return subprocess.run([sys.executable, str(PROBE)], env=env, capture_output=True, text=True, timeout=60)


def test_probe_is_an_executable_release_probe_with_an_owner():
    assert PROBE.stat().st_mode & stat.S_IXUSR
    assert "# acceptance-owner: DISCOVERY-PIPELINE-ACCEPTANCE-PROBE-20261125" in PROBE.read_text().splitlines()[:20][1]


def test_probe_skips_naming_missing_variables():
    env = {k: v for k, v in os.environ.items() if not k.startswith("ACCEPTANCE_")}
    done = subprocess.run([sys.executable, str(PROBE)], env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 77 and "ACCEPTANCE_BFF_URL" in done.stdout


def test_probe_fails_on_a_dated_world_get_only_and_never_prints_secrets(server):
    done = exec_probe(server)  # the synthetic world is from 2026-11-28; the wall clock is later
    out = done.stdout + done.stderr
    assert done.returncode == 1 and "VERDICT failed" in out
    assert "tok-SECRET-VALUE" not in out and "client-SECRET-VALUE" not in out
    assert [m for m in Handler.methods if m[0] == "POST"] == [("POST", "/bff/auth/dev-login")]
    assert [m[1] for m in Handler.methods if m[0] == "GET"] == [dpd.ALPHA_FACTORY, dpd.DISTILLATION_HEALTH]


def test_probe_main_passes_a_working_world(server, monkeypatch, capsys):
    for key, value in {"ACCEPTANCE_BFF_URL": server, "ACCEPTANCE_VIEWER_CLIENT_ID": "viewer",
                       "ACCEPTANCE_VIEWER_CLIENT_SECRET": "client-SECRET-VALUE"}.items():
        monkeypatch.setenv(key, value)
    assert dpd.main(now=NOW) == 0
    out = capsys.readouterr().out
    assert "VERDICT passed" in out and "STRATEGIES no strategy passed" in out


def test_probe_login_and_read_failures_exit_one(server, monkeypatch, capsys):
    monkeypatch.setenv("ACCEPTANCE_BFF_URL", server)
    monkeypatch.setenv("ACCEPTANCE_VIEWER_CLIENT_ID", "viewer")
    monkeypatch.setenv("ACCEPTANCE_VIEWER_CLIENT_SECRET", "client-SECRET-VALUE")
    Handler.board = None
    Handler.health = None
    assert dpd.main(now=NOW) == 1
    monkeypatch.setenv("ACCEPTANCE_BFF_URL", "http://127.0.0.1:1")
    assert dpd.main(now=NOW) == 1
    assert "auth_failed:request_failed" in capsys.readouterr().out


def test_both_routes_the_probe_reads_are_get_routes_of_the_real_bff_application():
    os.environ.setdefault("RANKING_STORE_DSN", "postgresql://test:test@localhost:5432/test")
    os.environ.setdefault("RANKING_STORE_BOOTSTRAP", "0")
    from services.control_plane.bff import main as bff_main
    from services.control_plane.bff.test_normalized_route_uniqueness import scan_fastapi_routes

    registered = {e.raw_path for e in scan_fastapi_routes(bff_main.app) if e.method == "GET"}
    assert dpd.ALPHA_FACTORY.split("?")[0] in registered
    assert dpd.DISTILLATION_HEALTH.rsplit("/", 1)[0] + "/{loop_id}" in registered
