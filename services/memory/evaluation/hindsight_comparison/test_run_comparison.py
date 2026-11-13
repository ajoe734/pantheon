import json

import pytest

from services.memory.evaluation.hindsight_comparison import export_real_events as ex
from services.memory.evaluation.hindsight_comparison import run_comparison as rc
from services.memory.evaluation.hindsight_comparison.test_export_real_events import clients
from services.memory.evaluation.hindsight_comparison.test_hindsight_index import FakeHindsight


def test_refuses_dataset_without_manifest(tmp_path):
    (tmp_path / ex.ENTRIES_NAME).write_text("")
    with pytest.raises(ValueError, match="manifest_missing"):
        rc.run(tmp_path, tmp_path / "o", transport=FakeHindsight())
    assert rc.main(["--data", str(tmp_path), "--out", str(tmp_path / "o")]) == 2


def test_pipeline_runs_four_arms_with_fake_hindsight(tmp_path):
    ex.export(clients(), tmp_path / "d", tenant_id="t1")
    r = rc.run(tmp_path / "d", tmp_path / "o", transport=FakeHindsight(), smoke=True,
               llm_info={"provider": "x", "model": "y"})
    assert r["kind"] == "smoke" and r["hosted_behavior_claimed"] is False
    assert set(r["arms"]) == set(rc.ARMS)
    assert all(a["scope_violations"] == 0 for a in r["arms"].values())
    assert r["arms"]["hs_default"]["rebuild_top8_jaccard"] == 1.0
    assert (tmp_path / "o" / "blind_sheet.json").exists()
    assert "api_key" not in json.dumps(r).lower()


def test_decision_query_uses_production_lesson_query():
    q, tags = rc.decision_query({"decision_type": "research_demand", "persona_id": "a", "strategy_family": "short_vol",
                                 "underlyings": ["SPX"], "missing_datasets": ["vix"]})
    assert "family:short_vol" in tags and "decision:research_demand" in tags and "dataset:vix" in tags


def test_current_arm_uses_production_hybrid_wrapper_with_isolated_backend(tmp_path):
    from types import SimpleNamespace
    ex.export(clients(), tmp_path / "d", tenant_id="t1")
    ds = rc.load_dataset(tmp_path / "d")
    ids = {rc.canonical_id(e) for e in ds["entries"]}

    class Spy:
        last_mode, last_reason = "hybrid", None
        def __init__(self): self.searches = 0
        def project(self, e): return {"projected": 1}
        def search(self, **kw):
            self.searches += 1
            return [SimpleNamespace(id=i, score=0.5) for i in ids]

    spy = Spy()
    (tmp_path / "w").mkdir(); (tmp_path / "w2").mkdir()
    cur = rc.CurrentRetrieval(ds["entries"], tmp_path / "w", spy)
    cur.retrieve("bob", "momentum", ["family:momentum"], 8)
    assert spy.searches >= 1 and cur.diagnostics["modes"].get("hybrid", 0) >= 1 and cur.hybrid
    # unconfigured: explicit keyword fallback, never the global/hosted backend
    cur2 = rc.CurrentRetrieval(ds["entries"], tmp_path / "w2")
    cur2.retrieve("bob", "momentum", ["family:momentum"], 8)
    assert not cur2.hybrid and cur2.diagnostics["modes"] == {"keyword_fallback": 2}
    assert cur2.diagnostics["fallback_reasons"]


def test_eval_backend_must_not_be_the_configured_search(monkeypatch):
    monkeypatch.setenv("PANTHEON_SEARCH_API_URL", "http://x:1")
    monkeypatch.setenv("PANTHEON_EVAL_SEARCH_API_URL", "http://x:1/")
    with pytest.raises(ValueError):
        rc.eval_search_backend()


def test_semantic_duplicates_measured_with_embedder(tmp_path):
    ex.export(clients(), tmp_path / "d", tenant_id="t1")
    embed = lambda texts: [[1.0, 0.0] for _ in texts]            # every item identical -> cosine 1.0
    r = rc.run(tmp_path / "d", tmp_path / "o", transport=FakeHindsight(), smoke=True, embed=embed)
    assert r["duplicate_embedding"]["available"] is True
    assert all(a["duplicate_basis"] == "source_event+cosine" for a in r["arms"].values())
    r2 = rc.run(tmp_path / "d", tmp_path / "o2", transport=FakeHindsight(), smoke=True, embed_unavailable_reason="x")
    assert r2["duplicate_embedding"] == {"available": False, "unavailable_reason": "x"}
