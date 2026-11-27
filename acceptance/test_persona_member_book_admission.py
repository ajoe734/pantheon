"""Release probe acceptance/probes/persona-member-book-admission (POOL-MEMBER-JOINED-AT-CARRY-20261126).

The detail below is the hosted persona detail shape read on 2026-11-26 (persona-ee7112a829d38b9135ac):
data.strategyPool.current.members[0].joined_at 2026-11-08T18:10:02Z; the memberVirtualBooks row from
services/control-plane/bff/ports/persona_pool.py with the MemberVirtualBooks snapshot as its book,
admitted_at null, since_inception false and marks 2026-11-19 to 2026-11-24. Those values are the hosted
facts; artifact ref, checksums and returns are illustrative stand-ins of the same shape.
"""
from __future__ import annotations

import copy
import os
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROBE = ROOT / "probes" / "persona-member-book-admission"
PID = "persona-ee7112a829d38b9135ac"
REF = "artifact-persona-paper-ee7112a829d38b9135ac-v1"
JOINED = "2026-11-08T18:10:02Z"
CHECKSUM = "sha256:" + "4" * 64

_loader = SourceFileLoader("persona_member_book_admission_probe", str(PROBE))
probe = module_from_spec(spec_from_loader(_loader.name, _loader))
_loader.exec_module(probe)

MARKS = ["2026-11-19", "2026-11-20", "2026-11-21", "2026-11-24"]
HOSTED_DETAIL = {
    "meta": {"surfaces": {"strategy_pool": {"status": "ok", "owner": "persona_owner", "reason": None},
                          "member_virtual_books": {"status": "ok", "owner": "persona_cycle_owner", "reason": None}}},
    "data": {"persona": {"id": PID, "lifecycle_state": "paper_running"}, "strategyPool": {
        "current": {"persona_id": PID, "pool_version": 7, "members": [
            {"artifact_ref": REF, "checksum": CHECKSUM, "stage": "active", "risk_weight": 1.0,
             "baseline_ref": "apv-baseline-ee71", "joined_at": JOINED}],
            "change_reason": "paper pool reweight: refused; virtual_book_high_water_history_missing"},
        "versions": [],
        "memberVirtualBooks": [{
            "artifact_ref": REF, "checksum": CHECKSUM, "stage": "active", "risk_weight": 1.0,
            "baseline_ref": "apv-baseline-ee71", "status": "ok", "reason": None,
            "book": {"binding_id": "rb-persona-ee71", "envelope_checksum": "sha256:" + "e" * 64,
                     "member_id": REF, "checksum": CHECKSUM, "artifact_id": REF, "version": "1.0.0",
                     "risk_weight": 1.0, "virtual_only": False, "bar_frequency": "1d",
                     "admitted_at": None, "since_inception": False, "marks_complete": True,
                     "dates": MARKS[1:], "returns": [0.004, -0.002, 0.003],
                     "underlyings": [{"symbol": "2330.TW", "as_of": "2026-11-24T05:30:00+00:00"}],
                     "net_of_costs": {"total_return": 0.004, "sharpe_ratio": 1.1, "max_drawdown": 0.002,
                                      "observation_count": 3, "basis": "net_taiwan_costs"},
                     "source_ref": ["recorded:" + d for d in MARKS], "as_of": "2026-11-24T05:30:00+00:00"}}]}}}
LISTING = {"data": [{"id": PID, "lifecycle_state": "paper_running"},
                    {"id": "persona-retired", "lifecycle_state": "retired"}]}


def detail(*, admitted=None, as_of="2026-11-24T05:30:00+00:00", joined=JOINED, since_inception=False,
           reason=None):
    out = copy.deepcopy(HOSTED_DETAIL)
    book = out["data"]["strategyPool"]["memberVirtualBooks"][0]["book"]
    book["admitted_at"], book["as_of"] = admitted, as_of
    book["since_inception"] = since_inception
    if reason:
        book["since_inception_reason"] = reason
    book["underlyings"][0]["as_of"] = as_of
    out["data"]["strategyPool"]["current"]["members"][0]["joined_at"] = joined
    return out


def run(build_time, persona_detail, calls=None):
    def fetch(path):
        if calls is not None:
            calls.append(path)
        if path == "/bff/version":
            return {"service": "operator-bff", "build_time": build_time}
        if path.startswith("/bff/personas?"):
            return LISTING
        assert path == f"/bff/personas/{PID}", path  # the retired persona is never read
        return persona_detail
    return probe.run(fetch)


def test_probe_is_executable_with_owner_header():
    assert os.access(PROBE, os.X_OK)
    assert "# acceptance-owner: POOL-MEMBER-JOINED-AT-CARRY-20261126" in PROBE.read_text().splitlines()[1]


def test_hosted_shape_today_is_skipped_because_no_book_was_marked_after_the_build():
    calls = []
    report = run("2026-11-26T10:00:00Z", HOSTED_DETAIL, calls)
    assert report["skipped"] is True and report["reason"] == "no_member_book_marked_after_bff_build"
    assert report["passed"] is False and report["books_checked"] == 0
    assert calls == ["/bff/version", "/bff/personas?page_size=200", f"/bff/personas/{PID}"]


def test_book_marked_after_the_build_without_admission_fails():
    report = run("2026-11-26T10:00:00Z", detail(as_of="2026-11-27T05:30:00+00:00"))
    assert report["passed"] is False and report["skipped"] is False and report["books_checked"] == 1
    # Changed assertion (MEMBER-BOOK-ADMISSION-GAP-FILL-20261126): the same book also has since_inception
    # false in the hosted fixture, which the probe now reports after the admission failure.
    assert report["failed"][PID] == [f"admitted_at_not_joined_at:{REF}:admitted_at=None:joined_at={JOINED}",
                                     f"book_not_since_inception:{REF}:reason=None"]


def test_book_marked_after_the_build_with_another_admission_fails():
    report = run("2026-11-26T10:00:00Z", detail(admitted="2026-11-19T05:30:00Z", as_of="2026-11-27T05:30:00+00:00"))
    assert report["results"] == {PID: "failed"}


def test_book_marked_after_the_build_with_the_member_joined_at_passes():
    # MEMBER-BOOK-ADMISSION-GAP-FILL-20261126: the book must also be since_inception; this passing case
    # now carries since_inception true (it was false in the hosted fixture, which the probe now fails).
    report = run("2026-11-26T10:00:00Z",
                 detail(admitted=JOINED, as_of="2026-11-27T05:30:00+00:00", since_inception=True))
    assert report["passed"] is True and report["results"] == {PID: "ok"} and report["failed"] == {}


def test_book_marked_after_the_build_with_the_admission_but_not_since_inception_fails():
    reason = "admission_gap_unfilled:admission_replay_unavailable:daily_bars_reader_unavailable"
    report = run("2026-11-26T10:00:00Z", detail(admitted=JOINED, as_of="2026-11-27T05:30:00+00:00", reason=reason))
    assert report["passed"] is False and report["skipped"] is False and report["books_checked"] == 1
    assert report["failed"][PID] == [f"book_not_since_inception:{REF}:reason={reason}"]


def test_book_not_since_inception_with_a_mark_not_newer_than_the_build_is_not_judged():
    report = run("2026-11-26T10:00:00Z", detail(admitted=JOINED, as_of="2026-11-24T05:30:00+00:00"))
    assert report["skipped"] is True and report["books_checked"] == 0


def test_unavailable_book_row_and_unknown_build_time_are_not_passes():
    unavailable = detail(as_of="2026-11-27T05:30:00+00:00")
    row = unavailable["data"]["strategyPool"]["memberVirtualBooks"][0]
    row.update(status="unavailable", book=None, reason="member_book_missing_incomplete_or_mismatched")
    assert run("2026-11-26T10:00:00Z", unavailable)["skipped"] is True
    unknown = run("unknown", HOSTED_DETAIL)
    assert unknown["passed"] is False and unknown["reason"] == "bff_build_time_unknown"


def test_missing_credentials_skip_without_network():
    env = {k: v for k, v in os.environ.items() if not k.startswith("ACCEPTANCE_")}
    env.update(ACCEPTANCE_BFF_URL="http://127.0.0.1:9", ACCEPTANCE_VIEWER_CLIENT_ID="viewer")
    done = subprocess.run([sys.executable, str(PROBE)], env=env, capture_output=True, text=True, timeout=30)
    assert done.returncode == 77 and "ACCEPTANCE_VIEWER_CLIENT_SECRET" in done.stdout


def test_unreachable_bff_fails_without_leaking_secret():
    env = {k: v for k, v in os.environ.items() if not k.startswith("ACCEPTANCE_")}
    env.update(ACCEPTANCE_BFF_URL="http://127.0.0.1:9", ACCEPTANCE_VIEWER_CLIENT_ID="viewer",
               ACCEPTANCE_VIEWER_CLIENT_SECRET="s3cret-value")
    done = subprocess.run([sys.executable, str(PROBE)], env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 1 and "s3cret-value" not in done.stdout + done.stderr
