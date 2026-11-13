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
