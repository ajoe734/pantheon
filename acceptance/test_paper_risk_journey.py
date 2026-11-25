"""Synthetic fresh events, with recorded hosted DTO history. No hosted acceptance claim."""
import copy
import importlib.machinery
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import pytest
from test_paper_risk_policy_binding import evidence

ROOT = Path(__file__).parent
loader = importlib.machinery.SourceFileLoader("risk_journey_probe", str(ROOT / "probes/paper-risk-journey"))
spec = importlib.util.spec_from_loader(loader.name, loader)
probe = importlib.util.module_from_spec(spec)
loader.exec_module(probe)
NOW = datetime(2026, 11, 22, 18, tzinfo=timezone.utc)
SHA, FE = "a" * 40, "b" * 40


def fixture():
    payload = evidence()
    policy = payload["/bff/capital-pools/fixture-pool"]["data"]["metadata"]["risk_policy"]
    manifest = {"bffCommit": SHA, "frontendSha": FE, "deployedAt": "2026-11-22T16:00:00Z"}
    history = json.loads((ROOT / "fixtures/paper_risk_journey_history_20261121.json").read_text())["rows"]
    rows, sources, details = [], {}, {}
    for suffix, stages in [("fill", ["trade_decision", "fill_management", "reconciliation"]),
                           ("rejected", ["trade_decision", "order_submission"])]:
        jid = "fixture-" + suffix
        events = {}
        for stage in stages:
            eid = jid + "-" + stage
            event_type = {"trade_decision": "trade_decision", "fill_management": "paper_fill_simulated",
                          "reconciliation": "reconciliation_completed", "order_submission": "order_rejection"}[stage]
            meta = {"risk_policy_id": policy["risk_policy_id"], "risk_policy_version": policy["version"],
                    "risk_policy_digest": probe.binding.risk_policy_digest(policy), "broker_order_id": "fixture-order",
                    "pre_trade_sizing": {"binding_limit": "cash", "permitted_units": 2,
                                         "fee_inclusive_required_cash": 22, "remaining_cash": 78}}
            meta["submission_sizing_evidence"] = copy.deepcopy(meta["pre_trade_sizing"])
            meta.update(submitted_to_broker=False, reject_reason="insufficient_cash", required=110, available=100, limit=100)
            if stage == "reconciliation":
                meta["reason_detail"] = [{"check": key, "status": "passed", "expected": 2,
                                          "actual": 2, "difference": 0} for key in probe.CHECKS]
            sources[eid] = {"event_id": eid, "event_type": event_type, "created_at": "2026-11-22T17:00:00Z",
                            "source_mode": "live", "tenant_id": "fixture-tenant", "deployment_stage": "paper",
                            "binding_id": "active-binding", "journey_id": jid,
                            "authority_refs": {"persona_id": "fixture-persona"}, "metadata": meta}
            events[stage] = {"event_id": eid, "occurred_at": "2026-11-22T17:00:00Z",
                             "source_mode": "live", "accepted_live": True, "binding_id": "active-binding"}
        row = {"journey_id": jid, "persona_id": "fixture-persona", "persona_is_probe": False,
               "binding_id": "active-binding", "created_at": "2026-11-22T17:00:00Z"}
        rows.append(row)
        # Recorded hosted _detail DTO: binding lives in identifiers, events at root.
        details[jid] = {k: v for k, v in row.items() if k != "binding_id"}
        details[jid].update(identifiers={"binding_id": ["active-binding"]}, stage_events=events)
    return payload, manifest, history, rows, details, sources


def run(state):
    payload, manifest, history, rows, details, sources = state
    def fetch(path):
        if path == "/bff/version":
            return {"source_commit_sha": SHA}
        parsed = urlsplit(path)
        if parsed.path == probe.BASE:
            assert parse_qs(parsed.query)["persona_id"] == ["fixture-persona"]
            return {"data": {"items": rows}, "page_info": {"total": len(rows)}}
        if parsed.path.startswith(probe.BASE + "/"):
            jid = parsed.path.rsplit("/", 1)[1]
            return {"data": (history | details)[jid]}
        return payload[path]
    return probe.check(fetch, lambda: manifest, lambda eid, tenant: sources[eid], expected=("fixture-persona",),
                       backend_sha=SHA, frontend_sha=FE, now=NOW)


def test_positive_is_explicitly_synthetic_and_requires_source_and_all_checks():
    report = run(fixture())
    assert report["passed"], report["failed"]
    assert len(report["fresh_rejections"]) == len(report["fresh_reconciled_fills"]) == 1
    assert len(report["personas"]["fixture-persona"]["fresh_decisions"]) == 2
    assert report["personas"]["fixture-persona"]["decision_evidence"][0]["pre_trade_sizing"]["permitted_units"] == 2
    assert len(report["fresh_reconciled_fills"][0]["booking_checks"]) == 6


def test_positive_through_actual_bff_detail_composer():
    from services.control_plane.bff.trade_journeys import _detail
    from services.trade_journey.materializer import JourneyProjection
    state = fixture()
    for jid, row in state[4].items():
        timeline = [{**event, "stage": stage} for stage, event in row["stage_events"].items()]
        snapshot = {"journey_id": jid, "tenant_id": "fixture-tenant", "environment": "paper",
            "status": "completed", "created_at": row["created_at"], "updated_at": row["created_at"],
            "identifiers": {"binding_id": ["active-binding"], "persona_id": ["fixture-persona"]},
            "stages": {event["stage"]: {"status": "succeeded"} for event in timeline}}
        projection = JourneyProjection(jid, "fixture-tenant", "paper", timeline, snapshot, [], [])
        state[4][jid] = _detail(projection, now=NOW)
        assert "binding_id" not in state[4][jid] and "snapshot" not in state[4][jid]
    report = run(state)
    assert report["passed"], report["failed"]


@pytest.mark.parametrize("mutation", ["no_rows", "stale", "replay", "fixture", "foreign", "policy",
                                     "missing_cash", "variance", "no_amounts", "pair", "missing_mapping"])
def test_missing_freshness_provenance_policy_or_booking_proof_cannot_pass(mutation):
    state = fixture()
    payload, manifest, history, rows, details, sources = state
    if mutation == "no_rows":
        rows.clear()
    elif mutation == "stale":
        for row in rows:
            row["created_at"] = "2026-11-21T00:00:00Z"
    elif mutation in {"replay", "fixture", "foreign", "policy"}:
        for original in sources.values():
            if mutation == "replay":
                original["source_mode"] = "replay"
            elif mutation == "fixture":
                original["metadata"]["fixture_source"] = "test"
            elif mutation == "foreign":
                original["tenant_id"] = "foreign"
            else:
                original["metadata"]["risk_policy_version"] = "stale"
    elif mutation in {"missing_cash", "variance"}:
        checks = sources["fixture-fill-reconciliation"]["metadata"]["reason_detail"]
        if mutation == "missing_cash":
            checks[:] = [item for item in checks if item["check"] != "cash_change"]
        else:
            checks[0].update(actual=3, difference=1, status="failed")
    elif mutation == "no_amounts":
        del sources["fixture-rejected-order_submission"]["metadata"]["required"]
    elif mutation == "pair":
        manifest["frontendSha"] = "c" * 40
    else:
        payload["/api/v1/approval-decisions"]["data"].clear()
    assert not run(state)["passed"]


def test_history_never_counts_as_a_fresh_fill_or_refusal():
    state = fixture()
    state[3].clear()
    report = run(state)
    assert len(report["historical"]) == 2
    assert "no_fresh_pre_broker_rejection_with_amounts" in report["failed"]
    assert "no_fresh_broker_position_cash_reconciled_fill" in report["failed"]


@pytest.mark.parametrize("mutation", ["missing_binding", "foreign_binding", "foreign_stage", "missing_stage_binding",
                                     "snapshot_only", "source_binding", "source_persona", "source_journey", "source_stale"])
def test_hosted_dto_shape_preserves_stage_and_immutable_source_identity(mutation):
    state = fixture()
    for row in state[4].values():
        if mutation == "missing_binding":
            row["identifiers"].clear()
        elif mutation == "foreign_binding":
            row["identifiers"]["binding_id"] = ["foreign"]
        elif mutation in {"foreign_stage", "missing_stage_binding"}:
            # Even membership in a multi-binding journey cannot authorize a foreign stage.
            row["identifiers"]["binding_id"].append("foreign")
            for event in row["stage_events"].values():
                event["binding_id"] = "foreign" if mutation == "foreign_stage" else None
        elif mutation == "snapshot_only":
            row["snapshot"] = {"stage_events": row.pop("stage_events")}
    for event in state[5].values():
        if mutation == "source_binding":
            event["binding_id"] = "foreign"
        elif mutation == "source_persona":
            event["authority_refs"]["persona_id"] = "foreign"
        elif mutation == "source_journey":
            event["journey_id"] = "foreign"
        elif mutation == "source_stale":
            event["created_at"] = state[1]["deployedAt"]
    report = run(state)
    assert not report["passed"]
    assert not report["fresh_reconciled_fills"] and not report["fresh_rejections"]


def test_missing_configuration_is_skip(monkeypatch, capsys):
    monkeypatch.delenv("ACCEPTANCE_FE_URL", raising=False)
    assert probe.main() == 77
    assert "skipped: missing" in capsys.readouterr().out


NO_SAMPLE = ["no_fresh_governed_decision:fixture-persona", "no_fresh_pre_broker_rejection_with_amounts",
             "no_fresh_broker_position_cash_reconciled_fill"]


def test_only_missing_samples_is_pending_exit_77():
    state = fixture()
    state[3].clear()
    report = run(state)
    assert report["failed"] == NO_SAMPLE and report["verdict"] == "pending"
    assert report["pending"] == NO_SAMPLE and report["exit_code"] == 77 and not report["passed"]


def test_any_five_no_fresh_subset_is_pending():
    for subset in ([NO_SAMPLE[0]], NO_SAMPLE[1:], [NO_SAMPLE[1]], NO_SAMPLE):
        assert probe.verdict(subset) == "pending"
    assert probe.verdict([]) == "passed"


@pytest.mark.parametrize("other", ["served_pair_mismatch", "source_read_failed:e:x", "historical_identity_mismatch:j",
                                  "served_identity_changed_during_probe", "journey_active_binding_mismatch:j",
                                  "no_fresh_governed_decision:", "no_fresh_other", "policy_mismatch"])
def test_mixed_or_foreign_findings_are_failed_even_with_no_sample_findings(other):
    assert probe.verdict([other]) == "failed"
    assert probe.verdict(NO_SAMPLE + [other]) == "failed"


def test_real_mixed_findings_exit_1():
    state = fixture()
    state[3].clear()
    state[1]["frontendSha"] = "c" * 40
    report = run(state)
    assert "served_pair_mismatch" in report["failed"] and set(NO_SAMPLE) <= set(report["failed"])
    assert report["verdict"] == "failed" and report["exit_code"] == 1 and report["pending"] == []


def test_complete_run_exits_0():
    report = run(fixture())
    assert report["verdict"] == "passed" and report["exit_code"] == 0
