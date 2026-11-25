"""ALPHA-TEMPLATE-EVALUATION-SCHEDULED-20261125: the scheduled evaluation of the time_series template library.

The governed store (parquet) is replaced by a boundary/dataset pair of the same shape; everything after it is the
real evaluator, the real registry service (memory store) and the real research store.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from services.registry.alpha_template import ALPHA_TEMPLATE_METADATA_KEY, load_library_templates
from services.registry.models import ArtifactType
from services.registry.split_api import RegistryService
from services.registry.storage import get_store, reset_store
from services.research import governed_market_dataset as gmd
from services.research.alpha_templates import schedule as sched
from services.research.alpha_templates.schedule import ScheduleConfig, TemplateEvaluationScheduler
from services.research.store import ResearchOrchestratorStore

SYMBOLS = [f"{2300 + i}.TW" for i in range(6)]
START = datetime(2026, 1, 5, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _memory_registry(monkeypatch):
    monkeypatch.setenv("REGISTRY_STORE_BACKEND", "memory")
    reset_store()
    yield
    reset_store()


class FakeStore:
    """A governed store with ``days`` trading days; ``days`` grows when a new day is stored."""

    def __init__(self, days=120):
        self.days = days

    def end(self):
        return (START + timedelta(days=self.days - 1)).date().isoformat()

    def boundary(self, end_date=None, now=None):
        end = end_date or self.end()
        days = (datetime.fromisoformat(end).date() - START.date()).days + 1
        return SimpleNamespace(
            snapshot_id=f"gmd-{end}-{days:04d}", window_start=START.date().isoformat(), window_end=end,
            trade_days=days, symbols=tuple(SYMBOLS),
            liquidity={s: 1e8 - i * 1e6 for i, s in enumerate(SYMBOLS)})

    def dataset(self, boundary, symbols, strategy_id=""):
        records = []
        for k, sym in enumerate(symbols):
            price = 100.0 + 10 * k
            for d in range(boundary.trade_days):
                day = START + timedelta(days=d)
                price *= 1 + 0.01 * math.sin(d * 0.7 + k) + 0.002 * math.cos(d * 0.13 * (k + 1))
                records.append({
                    "instrument": sym, "date": day.date().isoformat(), "open": price * 0.995, "high": price * 1.01,
                    "low": price * 0.985, "close": price, "volume": 1e6 + 1e4 * d, "traded_value": price * 1e6,
                    "available_time": (day + timedelta(hours=14, minutes=30)).isoformat().replace("+00:00", "Z")})
        return {"dataset_id": boundary.snapshot_id, "records": records, "bars_not_point_in_time": 0}


def make(tmp_path, fake, *, now, batch_size=60, max_symbols=60, registry=None):
    config = ScheduleConfig(data_dir=tmp_path / "te", batch_size=batch_size, max_symbols=max_symbols)
    registry = registry or RegistryService(get_store())
    return TemplateEvaluationScheduler(
        config, registry_service_factory=lambda: registry, research_store_factory=ResearchOrchestratorStore,
        boundary_builder=lambda end_date=None, now=None: fake.boundary(end_date),
        dataset_builder=fake.dataset, clock=lambda: now[0]), registry


def drive(scheduler, limit=50):
    actions = []
    for _ in range(limit):
        out = scheduler.step()
        actions.append(out)
        if out["action"] in ("cycle_completed", "not_due", "governed_data_refused", "cycle_abandoned"):
            return actions
    raise AssertionError("scheduler did not settle")


def templates_by_scope(registry):
    out = {}
    for view in registry.list_all_entries():
        if view.entry.artifact_type == ArtifactType.ALPHA_TEMPLATE:
            tmpl = view.entry.metadata[ALPHA_TEMPLATE_METADATA_KEY]
            out.setdefault(tmpl["scope"], []).append(view)
    return out


def test_cycle_evaluates_every_time_series_template_and_skips_cross_sectional(tmp_path):
    fake, now = FakeStore(), [datetime(2026, 5, 20, tzinfo=timezone.utc)]
    scheduler, registry = make(tmp_path, fake, now=now)
    actions = drive(scheduler)
    assert actions[-1]["action"] == "cycle_completed"
    library = load_library_templates()
    time_series = [t for t in library if t["scope"] == "time_series"]
    assert actions[-1]["evaluated"] == len(time_series) == 171
    by_scope = templates_by_scope(registry)
    for view in by_scope["time_series"]:
        meta = view.entry.metadata
        tmpl = meta[ALPHA_TEMPLATE_METADATA_KEY]
        assert tmpl["status"] == "evaluated" and tmpl["evaluation_ref"].startswith("rrun-alpha-eval-")
        ctx = meta["alpha_template_evaluation"]["evaluation_context"]
        assert ctx["governed_snapshot_id"] == fake.boundary().snapshot_id
        assert ctx["symbol_count"] == len(SYMBOLS) and ctx["history_depth"] == fake.days
        assert isinstance(ctx["horizons_without_enough_observations"], list)
        assert ctx["symbol_selection"]["bounded"] is False
    for view in by_scope["cross_sectional"]:  # never evaluated, never promoted
        tmpl = view.entry.metadata[ALPHA_TEMPLATE_METADATA_KEY]
        assert tmpl["status"] == "draft" and not tmpl.get("evaluation_ref")
    state = scheduler.load_state()
    assert state["last_cycle_summary"]["skipped_cross_sectional"].keys() == {
        v.entry.metadata[ALPHA_TEMPLATE_METADATA_KEY]["template_id"] for v in by_scope["cross_sectional"]}
    assert all(r.startswith("cross_sectional_scope") for r in state["last_cycle_summary"]["skipped_cross_sectional"].values())


def test_evaluation_never_grants_active_or_pool_state(tmp_path):
    fake, now = FakeStore(), [datetime(2026, 5, 20, tzinfo=timezone.utc)]
    scheduler, registry = make(tmp_path, fake, now=now)
    drive(scheduler)
    statuses = {
        v.entry.metadata[ALPHA_TEMPLATE_METADATA_KEY]["status"]
        for views in templates_by_scope(registry).values() for v in views}
    assert statuses <= {"draft", "evaluated"}


def test_weekly_limit_and_new_trading_day(tmp_path):
    fake, now = FakeStore(), [datetime(2026, 5, 20, tzinfo=timezone.utc)]
    scheduler, _ = make(tmp_path, fake, now=now)
    drive(scheduler)
    assert scheduler.step()["reason"].startswith("weekly_limit")  # inside seven days
    now[0] += timedelta(days=8)
    assert scheduler.step()["reason"].startswith("no_new_trading_day")  # a week later but no new stored day
    fake.days += 1
    now[0] += timedelta(hours=1)
    again = drive(scheduler)
    assert again[-1]["action"] == "cycle_completed"
    assert scheduler.load_state()["last_completed"]["window_end"] == fake.end()
    fake.days += 1
    now[0] += timedelta(days=1)  # a new day, but the last cycle started less than seven days ago
    assert scheduler.step()["reason"].startswith("weekly_limit")


def test_restart_resumes_without_repeating_finished_batches(tmp_path, monkeypatch):
    from services.research.alpha_templates import evaluator

    calls = []
    real = evaluator.run_alpha_template_evaluation

    def spy(**kw):
        calls.append(kw["run_id"])
        return real(**kw)

    monkeypatch.setattr(evaluator, "run_alpha_template_evaluation", spy)
    fake, now = FakeStore(), [datetime(2026, 5, 20, tzinfo=timezone.utc)]
    first, registry = make(tmp_path, fake, now=now, batch_size=60)
    assert first.step()["action"] == "batch_evaluated"
    assert first.step()["action"] == "batch_evaluated"
    assert len(calls) == 2
    # restart: a new scheduler object over the same state directory, nothing cached
    second, _ = make(tmp_path, fake, now=now, batch_size=60, registry=registry)
    # a crash after the third batch ran but before its state was saved: the batch run record exists
    state = second.load_state()
    done_before = list(state["cycle"]["done_batches"])
    assert done_before == [0, 1]
    rest = drive(second)
    assert rest[-1]["action"] == "cycle_completed"
    assert len(calls) == len(set(calls)) == 3  # 171 templates / 60 = 3 batches, none repeated
    # lose the saved progress of the last batch: the stored run record stops it from running again
    state = second.load_state()
    summary = state["last_cycle_summary"]
    assert summary["evaluated_count"] == 171
    redo, _ = make(tmp_path, fake, now=now, batch_size=60, registry=registry)
    state = redo.load_state()
    state["cycle"] = {**summary, "status": "running", "batches": _batches(summary, calls), "done_batches": [0, 1],
                      "symbols": SYMBOLS, "evaluated_count": 120}
    redo.save_state(state)
    assert redo.step()["action"] == "batch_evaluated"
    assert len(calls) == 3  # no fourth evaluation run


def _batches(summary, calls):
    ids = [t["template_id"] for t in load_library_templates() if t["scope"] == "time_series"]
    ids.sort()
    return [{"index": i, "run_id": calls[i], "template_ids": ids[i * 60:(i + 1) * 60]} for i in range(3)]


def test_symbol_bound_follows_liquidity_rule_and_is_recorded(tmp_path):
    fake, now = FakeStore(), [datetime(2026, 5, 20, tzinfo=timezone.utc)]
    scheduler, registry = make(tmp_path, fake, now=now, max_symbols=3)
    drive(scheduler)
    selection = scheduler.load_state()["last_cycle_summary"]["symbol_selection"]
    assert selection["bounded"] is True and selection["limit"] == 3 and selection["boundary_symbol_count"] == len(SYMBOLS)
    view = templates_by_scope(registry)["time_series"][0]
    ctx = view.entry.metadata["alpha_template_evaluation"]["evaluation_context"]
    assert ctx["symbol_count"] == 3 and ctx["symbol_selection"]["rule"].startswith("most_liquid")
    assert sched.select_symbols({"a": 1.0, "b": 3.0, "c": 3.0}, 2)["symbols"] == ["b", "c"]


def test_governed_data_refusal_is_recorded_not_raised(tmp_path):
    fake, now = FakeStore(), [datetime(2026, 5, 20, tzinfo=timezone.utc)]
    scheduler, registry = make(tmp_path, fake, now=now)

    def refuse(end_date=None, now=None):
        raise gmd.GovernedMarketDatasetError("daily_bars_store_stale", "newest stored day is 12 days old")

    scheduler._boundary_builder = refuse
    out = scheduler.step()
    assert out["action"] == "governed_data_refused" and "daily_bars_store_stale" in out["reason"]
    assert scheduler.load_state()["cycle"] is None
    assert not any(
        v.entry.metadata[ALPHA_TEMPLATE_METADATA_KEY].get("evaluation_ref")
        for views in templates_by_scope(registry).values() for v in views)


def test_changed_snapshot_abandons_the_cycle(tmp_path):
    fake, now = FakeStore(), [datetime(2026, 5, 20, tzinfo=timezone.utc)]
    scheduler, _ = make(tmp_path, fake, now=now)
    assert scheduler.step()["action"] == "batch_evaluated"
    again, _ = make(tmp_path, fake, now=now)
    again._boundary_builder = lambda end_date=None, now=None: SimpleNamespace(**{**fake.boundary(end_date).__dict__, "snapshot_id": "gmd-changed"})
    out = again.step()
    assert out["action"] == "cycle_abandoned" and again.load_state()["cycle"] is None


def test_synthetic_or_unprovenanced_data_is_refused_as_before():
    from services.research.alpha_templates import TaiwanEvaluationDataError, summarize_universe_provenance

    with pytest.raises(TaiwanEvaluationDataError):
        summarize_universe_provenance({"2330": {"source_kind": "synthetic_fixture", "decision_times": []}})


def test_list_endpoint_returns_scheduled_evaluation(tmp_path):
    from services.research.main import app

    fake, now = FakeStore(), [datetime(2026, 5, 20, tzinfo=timezone.utc)]
    scheduler, _ = make(tmp_path, fake, now=now)
    drive(scheduler)
    with TestClient(app) as client:
        body = client.get("/api/research/alpha-templates/evaluations", params={"status": "evaluated"}).json()
    assert body["count"] == 171
    item = next(i for i in body["items"] if i["template_id"] == "qlib_alpha158:KMID")
    assert item["evaluation_ref"].startswith("rrun-alpha-eval-") and item["duplicate_of"] is None
    assert item["evaluation"]["evaluation_context"]["governed_snapshot_id"] == fake.boundary().snapshot_id
    assert item["status"] == "evaluated"
