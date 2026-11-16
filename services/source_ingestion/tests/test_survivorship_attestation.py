from services.source_ingestion.survivorship_attestation import (
    MISSING_SUSPENSION_DATASET, attest_survivorship)

S = ["2026-10-12", "2026-10-13", "2026-10-14"]


def caps(sessions, venues=("TWSE", "TPEx")):
    return {(s, v): {"ref": f"ref/{v}/{s}"} for s in sessions for v in venues}


def test_complete_price_and_suspension_evidence_attests_both():
    a = attest_survivorship(S, caps(S), caps(S))
    assert a["includes_delisted"] and a["includes_suspended"]
    assert a["delisted"]["sessions_covered"] == 3 and len(a["delisted"]["evidence_refs"]) == 6
    assert a["window"] == {"first": S[0], "last": S[-1], "sessions": 3}


def test_missing_venue_session_is_a_gap_and_false():
    a = attest_survivorship(S, caps(S[:2]) | caps(S[2:], ("TWSE",)), caps(S))
    assert a["includes_delisted"] is False and a["includes_suspended"] is False
    assert a["delisted"]["gaps"][0]["session"] == S[2]


def test_unaccounted_weekday_is_a_gap_unless_closed_day_is_recorded():
    sessions = ["2026-10-12", "2026-10-14"]
    assert attest_survivorship(sessions, caps(sessions), caps(sessions))["includes_delisted"] is False
    closed = {"2026-10-13": {"ref": "twse/holiday/2026-10-13"}}
    a = attest_survivorship(sessions, caps(sessions), caps(sessions), closed)
    assert a["includes_delisted"] is True and "twse/holiday/2026-10-13" in a["delisted"]["evidence_refs"]


def test_no_suspension_dataset_names_the_missing_dataset():
    a = attest_survivorship(S, caps(S), {})
    assert a["includes_delisted"] is True and a["includes_suspended"] is False
    assert a["suspended"]["missing_dataset"] == MISSING_SUSPENSION_DATASET


def test_empty_window_is_false():
    a = attest_survivorship([], {}, {})
    assert a["includes_delisted"] is False and a["includes_suspended"] is False
