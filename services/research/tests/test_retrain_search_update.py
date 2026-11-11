"""The retrain proposal is a bounded in-sample search, never a fixed toggle."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture()
def research(monkeypatch):
    service_dir = ROOT / "services" / "research"
    monkeypatch.setenv("RESEARCH_ORCHESTRATOR_DATA_DIR", tempfile.mkdtemp())
    monkeypatch.syspath_prepend(str(service_dir))
    spec = importlib.util.spec_from_file_location("research_retrain_search_update", service_dir / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["research_retrain_search_update"] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("store", None)
    return module


def test_no_toggle_helper_remains(research):
    assert not hasattr(research, "_retrain_parameter_update")


def test_search_without_governed_dataset_fails_closed(research):
    from services.research.vectorbt.adapter.vectorbt_adapter import build_canonical_tw_strategy_artifact

    with pytest.raises(research._RetrainFinished) as caught:
        research._retrain_search_update(build_canonical_tw_strategy_artifact(), {"decision_id": "d"}, "run-1")
    assert caught.value.outcome == "no_candidate"
    assert caught.value.reason == "in_sample_dataset_unavailable"
