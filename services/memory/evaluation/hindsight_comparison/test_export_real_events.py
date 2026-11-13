import json
from pathlib import Path

import pytest

from services.memory.evaluation.hindsight_comparison import export_real_events as ex


class FakeClient:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, path, params=None):
        self.calls.append(path)
        v = self.routes[path]
        if isinstance(v, Exception):
            raise v
        return v

    def describe(self, path):
        return "https://owner.invalid" + path


PM = {"data": [
    {"report_id": "pm-1", "summary": "Gamma squeeze broke the short-vol book", "persona_id": "alice",
     "strategy_family": "short_vol", "underlyings": ["SPX"], "created_at": "2026-10-01T00:00:00Z"},
    {"report_id": "pm-2", "persona_id": "alice"},                       # no summary -> unmappable
]}
EVO = [{"decision_id": "ev-1", "status": "approved", "rationale": "調整動能策略權重", "persona_id": "bob",
        "strategy_family": "momentum", "underlyings": ["TXF"], "created_at": "2026-10-02T00:00:00Z"}]


def clients():
    return {"incident": FakeClient({"/api/v1/postmortems": PM}),
            "evolution": FakeClient({"/api/evolution/proposals": EVO}),
            "research": FakeClient({"/api/research/findings": [], "/api/research/persona-demands": {
                "demands": [{"demand_id": "d1", "persona_id": "alice", "strategy_family": "short_vol",
                             "missing_datasets": ["vix_term"]}]}}),
            "persona": None}


def test_export_uses_production_writer_and_manifest(tmp_path):
    m = ex.export(clients(), tmp_path, tenant_id="t1", persona_ids=["alice"])
    assert m["hosted_written"] is False and m["counts"]["memory_entries"] >= 3
    rows = [json.loads(l) for l in (tmp_path / ex.ENTRIES_NAME).read_text().splitlines()]
    layers = {r["layer"] for r in rows}
    assert layers == {"persona", "institutional"}
    assert all("learn_feedback" in r["entry"]["content"]["tags"] for r in rows)   # production writer tag
    assert any("family:short_vol" in r["entry"]["content"]["tags"] for r in rows)
    reasons = {u["reason"] for u in json.loads((tmp_path / ex.UNMAPPED_NAME).read_text())}
    assert "summary_text_absent" in reasons and "owner_endpoint_unconfigured" in reasons
    decisions = [json.loads(l) for l in (tmp_path / ex.DECISIONS_NAME).read_text().splitlines()]
    assert {d["decision_type"] for d in decisions} == {"evolution_proposal", "research_demand"}
    ex.validate_manifest(tmp_path)
    assert "Bearer" not in json.dumps(m)


def test_manifest_required_and_tamper_detected(tmp_path):
    with pytest.raises(ValueError, match="manifest_missing"):
        ex.validate_manifest(tmp_path)
    ex.export(clients(), tmp_path)
    (tmp_path / ex.DECISIONS_NAME).write_text("tampered\n")
    with pytest.raises(ValueError, match="manifest_sha_mismatch"):
        ex.validate_manifest(tmp_path)


def test_owner_failure_listed_not_fatal(tmp_path):
    c = clients()
    c["incident"] = FakeClient({"/api/v1/postmortems": OSError("down")})
    ex.export(c, tmp_path)
    assert any(u["reason"].startswith("owner_unreachable") for u in json.loads((tmp_path / ex.UNMAPPED_NAME).read_text()))


def test_client_is_get_only():
    import inspect
    assert "method=\"GET\"" in inspect.getsource(ex.ReadOnlyClient.get)
    assert not hasattr(ex.ReadOnlyClient, "post")
