from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("meeting_hosted_day", HERE / "meeting_hosted_day.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _meeting(mid, mtype="MT-1", status="closed", chair="persona-a", participants=None, refusal=None):
    return {"meeting_id": mid, "meeting_type": mtype, "status": status, "chair_persona_id": chair,
            "closed_at": "2026-11-25T10:00:00Z", "created_at": "2026-11-25T09:00:00Z", "refusal_reason": refusal,
            "convened_by": {"kind": "trigger"},
            "participants": participants if participants is not None else [
                {"persona_id": "persona-a", "origin": "persona"}, {"persona_id": "persona-b", "origin": "persona"}]}


def _detail(m, rounds=2, proposals=True, writebacks=True, cite="item-1"):
    return {"data": dict(m, rounds=[{"round": i + 1, "positions": [{"label": "A", "citations": [cite]}]}
                                    for i in range(rounds)],
                         case_file_items=[{"item_id": "item-1"}],
                         proposals=[{"proposal_id": "prop-1", "kind": "research_demand",
                                     "downstream": {"target": "research", "id": "rd-1", "status": "pending"}}]
                         if proposals else [],
                         writebacks=[{"persona_id": "persona-a", "entry_id": "mem-a"},
                                     {"persona_id": "persona-b", "entry_id": "mem-b"}] if writebacks else [],
                         private_items_withheld=0)}


def _fetcher(meetings, details, proposals=("prop-1",), memory=None, persona_lists=None, calls=None):
    memory = memory if memory is not None else {
        "persona-a": [{"entry_id": "mem-a"}, {"entry_id": "mem-priv-a", "memory_layer": "persona_private"}],
        "persona-b": [{"entry_id": "mem-b"}]}

    def fetch(path):
        if calls is not None:
            calls.append(path)
        if path.startswith("/bff/management/meetings/proposals"):
            return {"items": [{"proposal_id": p} for p in proposals]}
        if path.startswith("/bff/management/meetings?") and "persona_id=" in path:
            pid = path.split("persona_id=")[1]
            return {"items": [m for m in meetings if m["chair_persona_id"] == pid]
                    if persona_lists is None else persona_lists.get(pid, [])}
        if path.startswith("/bff/management/meetings?"):
            return {"items": meetings}
        if path.startswith("/bff/management/meetings/"):
            return details[path.rsplit("/", 1)[1]]
        if path.startswith("/bff/agora/committee/meetings"):
            return {"data": {"items": meetings}}
        if path.endswith("/memory"):
            pid = path.split("/")[3]
            return {"items": memory.get(pid, []), "meta": {"memory_source": {"available": True}}}
        raise AssertionError(path)
    return fetch


def test_natural_meeting_observed_and_others_missing_is_pending():
    m = _meeting("mtg-1")
    report = mod.run(_fetcher([m], {"mtg-1": _detail(m)}))
    assert report["items"]["AC1_natural_two_round_meeting_with_proposals"] == {"status": "observed", "meetings": ["mtg-1"]}
    assert report["items"]["AC2_outcome_in_own_memory_no_private_leak"]["status"] == "observed"
    assert report["items"]["AC3a_agora_advisor_participated"]["status"] == "missing"
    assert report["verdict"] == "pending" and report["violations"] == []


def test_all_observed_passes():
    m = _meeting("mtg-1", participants=[{"persona_id": "persona-a", "origin": "persona"},
                                        {"persona_id": "persona-b", "origin": "persona"},
                                        {"persona_id": "agora-adv-1", "origin": "agora_advisor"}])
    r = _meeting("mtg-2", mtype="MT-4", status="refused", refusal="private_servant_not_allowed")
    report = mod.run(_fetcher([m, r], {"mtg-1": _detail(m)}))
    assert report["verdict"] == "passed", report


def test_single_round_or_no_proposal_is_never_observed():
    m = _meeting("mtg-1")
    assert mod.run(_fetcher([m], {"mtg-1": _detail(m, rounds=1)}))["items"][
        "AC1_natural_two_round_meeting_with_proposals"]["status"] == "missing"
    assert mod.run(_fetcher([m], {"mtg-1": _detail(m, proposals=False)}))["items"][
        "AC1_natural_two_round_meeting_with_proposals"]["status"] == "missing"


def test_proposal_absent_from_entry_point_is_violation():
    m = _meeting("mtg-1")
    report = mod.run(_fetcher([m], {"mtg-1": _detail(m)}, proposals=()))
    assert report["verdict"] == "failed" and report["violations"] == ["proposal_not_at_entry_point:mtg-1"]


def test_private_memory_cited_is_violation_and_writeback_missing_is_reported():
    m = _meeting("mtg-1")
    report = mod.run(_fetcher([m], {"mtg-1": _detail(m, cite="mem-priv-a")}))
    assert report["verdict"] == "failed"
    assert report["violations"] == ["private_memory_cited:mtg-1:mem-priv-a"]
    report = mod.run(_fetcher([m], {"mtg-1": _detail(m)}, memory={"persona-a": [{"entry_id": "mem-a"}],
                                                                     "persona-b": [{"entry_id": "other"}]}))
    assert report["memory_outcomes"]["mtg-1"]["participants"]["persona-b"] == "not_in_latest_100_entries"
    assert report["items"]["AC2_outcome_in_own_memory_no_private_leak"]["status"] == "missing"


def test_persona_tab_missing_meeting_is_violation():
    m = _meeting("mtg-1")
    report = mod.run(_fetcher([m], {"mtg-1": _detail(m)}, persona_lists={"persona-a": []}))
    assert "persona_tab_missing:mtg-1" in report["violations"]


def test_get_only_and_no_text_in_output():
    m = dict(_meeting("mtg-1"), topic="SECRET TOPIC TEXT")
    calls: list[str] = []
    report = mod.run(_fetcher([m], {"mtg-1": _detail(m)}, calls=calls))
    assert "SECRET TOPIC TEXT" not in json.dumps(report)
    assert all(c.startswith("/bff/") for c in calls)


def test_main_without_credentials_fails_and_without_url_skips():
    env = {k: v for k, v in os.environ.items() if not k.startswith("ACCEPTANCE_")}
    out = subprocess.run([sys.executable, "-I", str(HERE / "meeting_hosted_day.py")], env=env, capture_output=True, text=True)
    assert out.returncode == 77
    env["ACCEPTANCE_BFF_URL"] = "http://127.0.0.1:9"
    out = subprocess.run([sys.executable, "-I", str(HERE / "meeting_hosted_day.py")], env=env, capture_output=True, text=True)
    assert out.returncode == 1 and "credential_missing" in out.stdout
