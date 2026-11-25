"""Wall time, peak RSS and storage of one full scheduled cycle at hosted scale (all 171 time_series templates).

usage: python measure_hosted_scale.py <work_dir> <symbols> <days> [max_symbols]
Builds a real daily_bars parquet store (fixture prices: deterministic random walk, traded value close*volume),
then runs the real scheduler (real DailyBarsReader, evaluator, memory registry, research store) in a fresh
subprocess so ru_maxrss is that of the cycle alone. Fixture data measures cost, not signal.
"""
import json, math, os, random, resource, shutil, subprocess, sys, time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CHILD = sys.argv[-1] == "child"
if CHILD:
    sys.argv.pop()
work, n_sym, n_days = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
max_symbols = int(sys.argv[4]) if len(sys.argv) > 4 else 60
store_root = work / "market_data_store"


def build():
    from services.source_ingestion import daily_bars_store as dbs
    from services.source_ingestion.daily_bars_store import DailyBarsStore
    store = DailyBarsStore(store_root)
    days, d = [], date(2026, 11, 24)
    while len(days) < n_days:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d -= timedelta(days=1)
    days.sort()
    rng = random.Random(7)
    price = {f"{1000 + i}.TW": 20 + rng.random() * 400 for i in range(n_sym)}
    for day in days:
        rows = []
        for sym in price:
            price[sym] *= 1 + rng.gauss(0, 0.015)
            c, v = price[sym], 2e5 + rng.random() * 5e6
            o = {"open": c * 0.998, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": v, "adjusted": False,
                 "traded_value": c * v, "available_time": datetime.fromisoformat(day + "T11:00:00+00:00"), "trading_status": "traded"}
            rows.append({"market": "TW", "symbol": sym, "trade_date": day, **o, "source_id": "s", "ingest_run_id": "r", "lineage_ref": "r",
                         "content_hash": dbs._sha({k: (x.isoformat() if isinstance(x, datetime) else x) for k, x in o.items()})})
        store.write_day(day, rows, ingest_run_id="m-" + day)


def cycle():
    os.environ["REGISTRY_STORE_BACKEND"] = "memory"
    from services.registry.split_api import RegistryService
    from services.registry.storage import get_store
    from services.research.alpha_templates.schedule import ScheduleConfig, TemplateEvaluationScheduler
    from services.research.store import ResearchOrchestratorStore
    from services.research import governed_market_dataset as gmd
    registry = RegistryService(get_store())
    now = datetime(2026, 11, 25, 6, tzinfo=timezone.utc)
    cfg = ScheduleConfig(data_dir=work / "te", max_symbols=max_symbols, batch_size=10)
    sch = TemplateEvaluationScheduler(
        cfg, registry_service_factory=lambda: registry, research_store_factory=ResearchOrchestratorStore,
        boundary_builder=lambda end_date=None, now=None: gmd.build_market_boundary(store_root, end_date=end_date, now=now or (lambda: datetime(2026, 11, 25, 6, tzinfo=timezone.utc))),
        dataset_builder=lambda b, s, strategy_id="": gmd.dataset_for(b, s, root=store_root, strategy_id=strategy_id),
        clock=lambda: now)
    t0, steps, rss_after = time.time(), 0, []
    while True:
        out = sch.step(); steps += 1
        rss_after.append(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024)
        if out["action"] != "batch_evaluated" or steps >= int(os.environ.get("MEASURE_MAX_STEPS", "0") or 10**9):
            break
    state = sch.load_state()
    state.setdefault("last_cycle_summary", {"symbol_selection": state["cycle"]["symbol_selection"], "history_depth": state["cycle"]["history_depth"], "window_end": state["cycle"]["window_end"], "governed_snapshot_id": state["cycle"]["governed_snapshot_id"], "evaluated_count": state["cycle"]["evaluated_count"]})
    size = sum(p.stat().st_size for p in (work / "te").rglob("*") if p.is_file())
    print(json.dumps({
        "final_action": out["action"], "evaluated": out.get("evaluated"), "steps": steps, "wall_seconds": round(time.time() - t0, 1),
        "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024, "rss_mib_after_first_step": rss_after[0],
        "cycle_dir_bytes": size, "state_bytes": (work / "te" / "schedule_state.json").stat().st_size,
        "summary": {k: state["last_cycle_summary"][k] for k in ("symbol_selection", "history_depth", "window_end", "governed_snapshot_id", "evaluated_count")},
    }))


if CHILD:
    cycle()
elif (work / "built").exists():
    pass
else:
    work.mkdir(parents=True, exist_ok=True)
    t = time.time(); build(); (work / "built").write_text(str(round(time.time() - t, 1)))
if not CHILD:
    shutil.rmtree(work / "te", ignore_errors=True)
    r = subprocess.run([sys.executable, __file__, *sys.argv[1:], "child"], capture_output=True, text=True)
    print(r.stdout[-3000:], r.stderr[-1500:])
