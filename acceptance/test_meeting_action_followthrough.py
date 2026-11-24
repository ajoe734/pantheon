"""Falsification tests for acceptance/meeting_action_followthrough.py (component validation, not hosted acceptance)."""
from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import meeting_action_followthrough as maf  # noqa: E402

SERIES = {"tenant_id": "tenant-dev", "meeting_type": "MT-1", "subject_ref": "pool-1"}
P, H = "rt-action-" + "p" * 8, "rt-action-" + "h" * 8
NOTE = {"kind": "research_note", "artifact_id": "note-1", "checksum": "c1"}


def ticket(tid, kind, state, **kw):
    t = {"ticket_id": tid, "tenant_id": "tenant-dev", "owner": "persona-1" if kind == "persona" else "human-1",
         "assignee": "persona-1" if kind == "persona" else "human-1", "action_state": state, "revision": 4,
         "meeting_action": {"meeting_id": "mtg-1", "meeting_type": "MT-1", "subject_ref": "pool-1",
                            "assignee_kind": kind, "deliverable": "Report", "acceptance_criterion": "Compare A/B",
                            "due_at": "2026-11-20T00:00:00Z"},
         "evidence_refs": [], "evidence_notes": [], "lifecycle_history": []}
    t.update(kw)
    return t


def world():
    accepted = ticket(P, "persona", "accepted", evidence_refs=[NOTE],
        evidence_notes=[{"note_id": "note-1", "body": "A over B", "checksum": "c1", "ticket_id": P, "tenant_id": "tenant-dev"}],
        lifecycle_history=[{"action": "submit", "transitioned_by": "persona-pool-cycle"},
                           {"action": "accept", "transitioned_by": "consultation-svc"}],
        review_decision={"reason": "meets criterion", "review_source": "mtg-2"})
    waiting = ticket(H, "human", "waiting_for_input")
    m1 = {"meeting_series": SERIES, "actions": [{"action_id": P, "assignee_kind": "persona", "state": "dispatch_pending"},
                                                 {"action_id": H, "assignee_kind": "human", "state": "dispatch_pending"}]}
    m2 = {"meeting_series": SERIES,
          "prior_actions": [{"action_id": H, "meeting_id": "mtg-1", "state": "waiting_for_input"}],
          "rounds": [{"positions": [{"citations": [{"item_id": "meeting_action:" + H}]}]}]}
    return {"/bff/management/meetings/mtg-1": m1, "/bff/management/meetings/mtg-2": m2,
            maf.TICKET_PATH.format(P): accepted, maf.TICKET_PATH.format(H): waiting}


def run(docs, m1="mtg-1", m2="mtg-2"):
    def fetch(path):
        if path not in docs:
            raise RuntimeError("http_404")
        return copy.deepcopy(docs[path])
    return maf.check_chain(fetch, m1, m2)


def test_valid_chain_passes_with_distinct_submitter_and_verifier():
    r = run(world())
    assert r["passed"], r["failed"]
    t = {x["ticket_id"]: x for x in r["tickets"]}
    assert t[P]["submitter"] != t[P]["verifier"] and r["open_or_returned"] == [H]
    assert r["next_meeting"]["presented"] == [H]


def test_pending_is_never_passed():
    r = maf.check_chain(lambda p: None, None, "mtg-2")
    assert r["pending"] and not r["passed"]


def test_same_meeting_and_legacy_w47_fail():
    assert run(world(), "mtg-1", "mtg-1")["failed"] == ["same_meeting_twice"]
    assert "legacy_w47_is_historical_context_not_proof" in run(world(), maf.LEGACY_W47_MEETING_ID, "mtg-2")["failed"]


def mutate(fn):
    d = world()
    fn(d)
    return run(d)


@pytest.mark.parametrize("name,fn,expected", [
    ("nonexistent evidence", lambda d: d[maf.TICKET_PATH.format(P)].update(evidence_notes=[]), "evidence_unresolved"),
    ("checksum drift", lambda d: d[maf.TICKET_PATH.format(P)]["evidence_notes"][0].update(checksum="x"), "evidence_unresolved"),
    ("inbox ack as evidence", lambda d: d[maf.TICKET_PATH.format(P)].update(evidence_refs=[{"kind": "membership_ack", "artifact_id": "joined"}]), "evidence_not_owner_artifact"),
    ("generic closed demand", lambda d: d[maf.TICKET_PATH.format(P)].update(evidence_refs=[{"kind": "research_demand", "artifact_id": "dm-1"}]), "evidence_not_owner_artifact"),
    ("wrong tenant", lambda d: d[maf.TICKET_PATH.format(P)].update(tenant_id="tenant-b"), "ticket_wrong_tenant"),
    ("wrong series", lambda d: d[maf.TICKET_PATH.format(H)]["meeting_action"].update(subject_ref="pool-2"), "ticket_wrong_series"),
    ("wrong meeting", lambda d: d[maf.TICKET_PATH.format(H)]["meeting_action"].update(meeting_id="mtg-0"), "ticket_wrong_meeting"),
    ("self verified", lambda d: d[maf.TICKET_PATH.format(P)]["lifecycle_history"][1].update(transitioned_by="persona-pool-cycle"), "submitter_is_verifier"),
    ("no ticket", lambda d: d.pop(maf.TICKET_PATH.format(H)), "ticket_unreadable"),
    ("series differs across meetings", lambda d: d["/bff/management/meetings/mtg-2"].update(meeting_series={**SERIES, "subject_ref": "pool-9"}), "series_mismatch_or_unstable"),
    ("missing opportunity", lambda d: d["/bff/management/meetings/mtg-2"].update(prior_actions=[]), "missing_opportunity"),
    ("only persona items", lambda d: d["/bff/management/meetings/mtg-1"].update(actions=d["/bff/management/meetings/mtg-1"]["actions"][:1]), "m1_missing_persona_or_human_item"),
])
def test_falsification_fails(name, fn, expected):
    r = mutate(fn)
    assert not r["passed"] and any(f.startswith(expected) for f in r["failed"]), (name, r["failed"])


def test_submitted_work_must_be_presented_with_evidence_and_answered_by_decision():
    d = world()
    d[maf.TICKET_PATH.format(H)].update(action_state="submitted", evidence_refs=[NOTE],
        evidence_notes=[{"note_id": "note-1", "body": "x", "checksum": "c1", "ticket_id": H, "tenant_id": "tenant-dev"}],
        lifecycle_history=[{"action": "submit", "transitioned_by": "human-1"}])
    d["/bff/management/meetings/mtg-2"]["prior_actions"] = [{"action_id": H, "meeting_id": "mtg-1", "state": "submitted"}]
    d["/bff/management/meetings/mtg-2"]["rounds"] = []
    failed = run(d)["failed"]
    assert f"submitted_evidence_not_presented:{H}" in failed and f"decision_neither_reviews_nor_cites:{H}" in failed
    d["/bff/management/meetings/mtg-2"].update(prior_action_evidence=[{"action_id": H, "status": "ok"}],
        synthesis={"action_reviews": [{"action_id": H, "decision": "return"}]})
    assert run(d)["passed"]


def test_probe_without_meetings_is_pending_exit_77_and_never_leaks_secret():
    probe = Path(__file__).resolve().parent / "probes" / "meeting-action-followthrough"
    env = {"PATH": "/usr/bin:/bin", "ACCEPTANCE_BFF_URL": "http://127.0.0.1:9", "ACCEPTANCE_VIEWER_CLIENT_ID": "v",
           "ACCEPTANCE_VIEWER_CLIENT_SECRET": "s3cret-value"}
    done = subprocess.run([sys.executable, "-I", str(probe)], env=env, capture_output=True, text=True)
    assert done.returncode == 77 and "pending" in done.stdout and "s3cret-value" not in done.stdout + done.stderr
    done = subprocess.run([sys.executable, "-I", str(probe)], env={"PATH": "/usr/bin"}, capture_output=True, text=True)
    assert done.returncode == 77 and "missing" in done.stdout
