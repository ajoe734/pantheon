"""Release probe acceptance/probes/mgmt-evolution-pages-readable against recorded evolution page shapes.

Pool versions are the hosted ones of persona-ee7112a829d38b9135ac (v1 initial, v2..v6 reweight refused).
"""
from __future__ import annotations

import copy
import os
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROBE = ROOT / "probes" / "mgmt-evolution-pages-readable"
_loader = SourceFileLoader("mgmt_evolution_pages_probe", str(PROBE))
probe = module_from_spec(spec_from_loader(_loader.name, _loader))
_loader.exec_module(probe)

MEMBER = {"artifact_ref": "artifact-persona-paper-a81b4fd15944698341aa", "stage": "active", "risk_weight": 1.0}
REASONS = {2: "virtual_book_source_missing", 3: "virtual_book_source_missing", 4: "virtual_book_history_missing",
           5: "virtual_book_history_missing", 6: "virtual_book_high_water_history_missing"}


def pool_item(n):
    refused = n > 1
    return {"id": f"pool_version:persona-ee7112a829d38b9135ac:v{n}", "entry_type": "pool_version",
            "pool_version": n, "status": "refused" if refused else "applied", "decision_status": "refused" if refused else "applied",
            "refused": refused, "membership_changed": False, "subject_class": "strategy",
            "summary": ("Refused reweight; the pool membership was not changed." if refused else "Initial strategy pool created with 1 member(s)."),
            "reasons": [REASONS[n]] if refused else [], "members_before": [] if n == 1 else [MEMBER], "members_after": [MEMBER]}


def journal():
    decision = {"entry_type": "evolution_decision", "id": "evolution_decision:d-1", "subject_class": "strategy",
                "decision": {"approval_decision_id": "ad-1"}}
    return {"data": {"items": [pool_item(n) for n in range(1, 7)] + [decision],
                     "summary": {"mutation_review_count": 1, "decision_count": 1,
                                 "hidden_system_counts": {"infrastructure": 380, "test": 0}},
                     "auto_proposal_counts": {"threshold": 0, "incident_followup": 48, "incident_severity": 2,
                                              "other": 3, "infrastructure": 0, "postmortem_draft": 5479, "total": 53}},
            "page_info": {"next_page_token": None},
            "meta": {"surfaces": {"strategy_pools": {"status": "ok", "reason": None}}}}


QUALIFICATION = {"data": {"member_baselines": [{
    "persona_id": "persona-ee7112a829d38b9135ac", "persona_name": "Paper One", "artifact_ref": MEMBER["artifact_ref"],
    "status": "no_approved_baseline", "reason": "gate_verdict_not_recorded"}],
    "member_baselines_surface": {"status": "ok", "reason": None}}}


def fetcher(journal_body, qualification=QUALIFICATION):
    def fetch(path):
        if path.startswith("/bff/management/evolution-journal"):
            return journal_body
        if path.startswith("/bff/management/evolution-qualification"):
            return qualification
        raise AssertionError(path)
    return fetch


def test_executable_with_owner_header():
    assert os.access(PROBE, os.X_OK)
    assert "# acceptance-owner:" in "\n".join(PROBE.read_text().splitlines()[:20])


def test_passes_on_recorded_shapes_with_refused_versions():
    report = probe.check(fetcher(journal()))
    assert report["passed"], report
    assert report["pool_version_rows"] == 6


def test_fails_when_parts_do_not_add_up():
    body = journal()
    body["data"]["auto_proposal_counts"]["total"] = 56  # the pre-fix gap: 53 vs parts
    body["data"]["auto_proposal_counts"]["other"] = 0
    assert "auto_proposal_parts_do_not_add_up" in probe.check(fetcher(body))["failed"]


def test_fails_when_refused_version_is_presented_as_applied():
    body = journal()
    item = body["data"]["items"][2]
    item.update({"status": "applied", "summary": "Applied reweight; pool membership changed."})
    report = probe.check(fetcher(body))
    assert any(f.startswith("refused_pool_version_presented_as_applied") for f in report["failed"])


def test_fails_when_strategy_pools_unavailable_or_missing():
    for surface in ({"status": "unavailable", "reason": "owner_read_failed:RuntimeError"}, None):
        body = journal()
        if surface is None:
            del body["meta"]["surfaces"]["strategy_pools"]
        else:
            body["meta"]["surfaces"]["strategy_pools"] = surface
        assert any(f.startswith("strategy_pools_unavailable") for f in probe.check(fetcher(body))["failed"])


def test_fails_on_system_entries_by_default_and_double_counted_reviews():
    body = journal()
    body["data"]["items"][-1]["subject_class"] = "infrastructure"
    body["data"]["summary"]["mutation_review_count"] = 448
    failed = probe.check(fetcher(body))["failed"]
    assert any(f.startswith("system_entries_listed_by_default") for f in failed)
    assert any(f.startswith("mutation_review_count_not_distinct") for f in failed)


def test_fails_when_member_baselines_missing_or_unavailable():
    assert "member_baselines_missing" in probe.check(fetcher(journal(), {"data": {}}))["failed"]
    q = copy.deepcopy(QUALIFICATION)
    q["data"]["member_baselines_surface"] = {"status": "unavailable", "reason": "tenant_unresolved"}
    assert any(f.startswith("member_baselines_unavailable") for f in probe.check(fetcher(journal(), q))["failed"])


def test_skips_without_credentials(monkeypatch):
    for name in probe.REQUIRED:
        monkeypatch.delenv(name, raising=False)
    assert probe.main() == 77
