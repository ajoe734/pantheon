"""Scheduled evaluation of the time_series AlphaTemplate library on the governed market dataset.

ALPHA-TEMPLATE-EVALUATION-SCHEDULED-20261125 (RC2). ``run_alpha_template_evaluation`` was merged but nothing
called it on hosted: templates stayed ``draft``. This module is the caller. It runs inside the existing
alpha-replication worker (no API route starts it) and does one bounded unit of work per ``step``:

* decide whether a cycle is due: the governed store has a trading day newer than the last evaluated one and
  at least ``min_interval_days`` have passed since the last cycle started;
* start a cycle: pin the governed snapshot, choose the symbols (all of the boundary, or the ``max_symbols``
  most liquid by the governed liquidity rule, the choice is recorded), split the time_series templates into
  fixed batches and record why each cross-sectional template is skipped;
* evaluate one batch through ``run_alpha_template_evaluation`` (evaluation_ref, status ``evaluated``,
  ``duplicate_of``) and persist the cycle state, so a restart resumes at the next unfinished batch and never
  repeats a finished one.

Evaluation records metrics only. It never sets ``active`` or any approval/pool state.
State and the evaluation run records live under ``<data_dir>/template-evaluation`` (not in the research
service's shared JSON maps); old cycle stores are removed beyond ``keep_cycles``.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

STATE_FILE = "schedule_state.json"
SCHEMA = "alpha_template_evaluation_schedule.v1"
CROSS_SECTIONAL_SKIP_REASON = "cross_sectional_scope: the per-symbol evaluator refuses cross-sectional templates"
SOURCE = "governed_market_dataset:daily_bars"


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, "") or default))
    except ValueError:
        return default


@dataclass
class ScheduleConfig:
    data_dir: Path
    check_seconds: int = 3600
    min_interval_days: int = 7
    max_symbols: int = 60
    batch_size: int = 10
    keep_cycles: int = 3

    @classmethod
    def from_env(cls, data_dir: Path) -> "ScheduleConfig":
        state_path = os.getenv("ALPHA_TEMPLATE_EVAL_STATE_PATH")
        return cls(
            data_dir=(Path(state_path).parent if state_path else data_dir / "template-evaluation"),
            check_seconds=_env_int("ALPHA_TEMPLATE_EVAL_CHECK_SECONDS", 3600, 60),
            min_interval_days=_env_int("ALPHA_TEMPLATE_EVAL_MIN_INTERVAL_DAYS", 7),
            max_symbols=_env_int("ALPHA_TEMPLATE_EVAL_MAX_SYMBOLS", 60),
            batch_size=_env_int("ALPHA_TEMPLATE_EVAL_BATCH_SIZE", 10),
            keep_cycles=_env_int("ALPHA_TEMPLATE_EVAL_KEEP_CYCLES", 3),
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def select_symbols(liquidity: Mapping[str, float], max_symbols: int) -> dict[str, Any]:
    """The governed liquidity rule: highest median traded value first, symbol as the tie break."""
    ranked = sorted(liquidity, key=lambda s: (-liquidity[s], s))
    chosen = ranked[:max_symbols]
    return {
        "rule": "most_liquid_by_median_traded_value_of_newest_days",
        "boundary_symbol_count": len(ranked),
        "limit": max_symbols,
        "selected_count": len(chosen),
        "bounded": len(ranked) > len(chosen),
        "symbols": sorted(chosen),
    }


def universe_from_dataset(dataset: Mapping[str, Any], snapshot_id: str) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Per-symbol evaluator entries from ``governed_market_dataset.dataset_for`` records.

    Same bar shape as the governed snapshot loader (event and available time are the stored available time,
    vwap from the stored traded value). A row without a full bar is left out and counted, never filled."""
    from services.research.alpha_templates.evaluator import _normalize_symbol_panel_entry

    by_symbol: dict[str, list[dict[str, Any]]] = {}
    dropped = 0
    for r in dataset["records"]:
        fields = {k: r.get(k) for k in ("open", "high", "low", "close", "volume")}
        if any(not isinstance(v, (int, float)) for v in fields.values()) or not r.get("available_time"):
            dropped += 1
            continue
        value = r.get("traded_value")
        volume = float(fields["volume"])
        vwap = float(value) / volume if isinstance(value, (int, float)) and volume > 0 else float(fields["close"])
        ts = str(r["available_time"])
        by_symbol.setdefault(str(r["instrument"]), []).append({
            "symbol": str(r["instrument"]), "date": ts, "event_time": ts, "available_time": ts,
            **{k: float(v) for k, v in fields.items()}, "vwap": vwap})
    universe: dict[str, dict[str, Any]] = {}
    for sym, bars in sorted(by_symbol.items()):
        bars.sort(key=lambda b: b["event_time"])
        entry = _normalize_symbol_panel_entry(sym, bars)
        entry["provenance"] = {"source": SOURCE, "snapshot_id": snapshot_id, "bar_count": len(bars)}
        universe[sym] = entry
    return universe, {"bars_not_usable": dropped, "bars_not_point_in_time": int(dataset.get("bars_not_point_in_time") or 0)}


class TemplateEvaluationScheduler:
    def __init__(
        self,
        config: ScheduleConfig,
        *,
        registry_service_factory: Callable[[], Any],
        research_store_factory: Callable[[Path], Any],
        boundary_builder: Callable[..., Any] | None = None,
        dataset_builder: Callable[..., Any] | None = None,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.config = config
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = config.data_dir / STATE_FILE
        self._registry_service_factory = registry_service_factory
        self._research_store_factory = research_store_factory
        self._boundary_builder = boundary_builder
        self._dataset_builder = dataset_builder
        self._clock = clock
        self._universe: tuple[str, dict[str, dict[str, Any]], dict[str, Any]] | None = None

    # -- state -----------------------------------------------------------------------------------------
    def load_state(self) -> dict[str, Any]:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"schema": SCHEMA, "last_completed": None, "cycle": None, "last_check": None}
        if state.get("schema") != SCHEMA:
            raise ValueError(f"unsupported schedule state schema {state.get('schema')!r}")
        return state

    def save_state(self, state: dict[str, Any]) -> None:
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(self.state_path)

    def _cycle_store(self, cycle_id: str) -> Any:
        return self._research_store_factory(self.config.data_dir / "cycles" / cycle_id)

    # -- governed data ---------------------------------------------------------------------------------
    def _boundary(self, end_date: str | None = None) -> Any:
        from services.research import governed_market_dataset as gmd

        return (self._boundary_builder or gmd.build_market_boundary)(end_date=end_date, now=self._clock)

    def _dataset(self, boundary: Any, symbols: list[str]) -> Any:
        from services.research import governed_market_dataset as gmd

        return (self._dataset_builder or gmd.dataset_for)(boundary, symbols, strategy_id="alpha-template-evaluation")

    def _load_universe(self, cycle: dict[str, Any]) -> dict[str, dict[str, Any]]:
        if self._universe and self._universe[0] == cycle["cycle_id"]:
            return self._universe[1]
        boundary = self._boundary(end_date=cycle["window_end"])
        if boundary.snapshot_id != cycle["governed_snapshot_id"]:
            raise RuntimeError(f"snapshot_not_reproducible: {cycle['governed_snapshot_id']} now derives as {boundary.snapshot_id}")
        dataset = self._dataset(boundary, cycle["symbols"])
        universe, counts = universe_from_dataset(dataset, boundary.snapshot_id)
        self._universe = (cycle["cycle_id"], universe, counts)
        return universe

    # -- cycle -----------------------------------------------------------------------------------------
    def _templates(self) -> list[dict[str, Any]]:
        from services.registry.alpha_template import ALPHA_TEMPLATE_METADATA_KEY, ensure_builtin_alpha_templates
        from services.registry.models import ArtifactType

        service = self._registry_service_factory()
        ensure_builtin_alpha_templates(service)
        out = []
        for view in service.list_all_entries():
            if view.entry.artifact_type == ArtifactType.ALPHA_TEMPLATE:
                tmpl = (view.entry.metadata or {}).get(ALPHA_TEMPLATE_METADATA_KEY)
                if isinstance(tmpl, Mapping):
                    out.append(dict(tmpl))
        return sorted(out, key=lambda t: str(t["template_id"]))

    def _due(self, state: dict[str, Any], now: datetime) -> tuple[bool, str, Any]:
        last = state.get("last_completed")
        if last:
            age = now - datetime.fromisoformat(last["started_at"].replace("Z", "+00:00"))
            if age < timedelta(days=self.config.min_interval_days):
                return False, f"weekly_limit: last cycle started {_iso(now - age)}", None
        boundary = self._boundary()
        if last and boundary.window_end <= last["window_end"]:
            return False, f"no_new_trading_day: newest stored day {boundary.window_end}", None
        return True, "new_trading_day", boundary

    def _start_cycle(self, state: dict[str, Any], boundary: Any, now: datetime) -> dict[str, Any]:
        selection = select_symbols(boundary.liquidity, self.config.max_symbols)
        templates = self._templates()
        time_series = [t["template_id"] for t in templates if t.get("scope") == "time_series"]
        skipped = {t["template_id"]: CROSS_SECTIONAL_SKIP_REASON for t in templates if t.get("scope") != "time_series"}
        cycle_id = f"{boundary.window_end}-{boundary.snapshot_id.rsplit('-', 1)[-1][:12]}"
        size = self.config.batch_size
        batches = [
            {"index": i // size, "run_id": f"rrun-alpha-eval-{cycle_id}-b{i // size:03d}", "template_ids": time_series[i:i + size]}
            for i in range(0, len(time_series), size)
        ]
        cycle = {
            "cycle_id": cycle_id, "status": "running", "started_at": _iso(now),
            "governed_snapshot_id": boundary.snapshot_id, "window_start": boundary.window_start,
            "window_end": boundary.window_end, "history_depth": boundary.trade_days,
            "symbol_selection": {k: v for k, v in selection.items() if k != "symbols"},
            "symbols": selection["symbols"], "batches": batches, "done_batches": [],
            "skipped_cross_sectional": skipped, "evaluated_count": 0, "duplicates": {},
        }
        state["cycle"] = cycle
        self.save_state(state)
        return cycle

    def _run_batch(self, state: dict[str, Any], cycle: dict[str, Any], batch: dict[str, Any]) -> dict[str, Any]:
        from services.research.alpha_templates.evaluator import run_alpha_template_evaluation

        store = self._cycle_store(cycle["cycle_id"])
        existing = store.get_run(batch["run_id"])
        if existing is None:  # a batch whose run record exists finished before the restart: not repeated
            universe = self._load_universe(cycle)
            counts = self._universe[2]
            context = {
                "governed_snapshot_id": cycle["governed_snapshot_id"], "window_start": cycle["window_start"],
                "window_end": cycle["window_end"], "symbol_count": len(universe),
                "history_depth": cycle["history_depth"], "symbol_selection": cycle["symbol_selection"],
                "skipped_cross_sectional_count": len(cycle["skipped_cross_sectional"]), **counts,
            }
            result = run_alpha_template_evaluation(
                research_store=store, registry_service=self._registry_service_factory(),
                template_ids=batch["template_ids"], universe=universe, run_id=batch["run_id"],
                task_id=f"rtask-alpha-eval-{cycle['cycle_id']}", evaluation_context=context,
                actor={"id": "alpha-template-evaluation-schedule", "role": "service", "tenant": "__builtin__"})
            evaluated = len(result["evaluations"])
            duplicates = {k: v for k, v in result["duplicates_detected"].items() if k in batch["template_ids"]}
        else:
            evaluated = int(existing.get("evaluated_count") or 0)
            duplicates = dict(existing.get("duplicates_detected") or {})
        cycle["done_batches"].append(batch["index"])
        cycle["evaluated_count"] += evaluated
        cycle["duplicates"].update(duplicates)
        self.save_state(state)
        return {"action": "batch_evaluated", "run_id": batch["run_id"], "evaluated": evaluated}

    def _finish(self, state: dict[str, Any], cycle: dict[str, Any], now: datetime) -> dict[str, Any]:
        cycle["status"] = "completed"
        cycle["completed_at"] = _iso(now)
        state["last_completed"] = {
            "cycle_id": cycle["cycle_id"], "started_at": cycle["started_at"], "completed_at": cycle["completed_at"],
            "window_end": cycle["window_end"], "governed_snapshot_id": cycle["governed_snapshot_id"],
            "evaluated_count": cycle["evaluated_count"], "symbol_count": len(cycle["symbols"]),
            "skipped_cross_sectional_count": len(cycle["skipped_cross_sectional"]),
        }
        state["cycle"] = None
        state["last_cycle_summary"] = {k: v for k, v in cycle.items() if k not in ("batches", "symbols")}
        self.save_state(state)
        self._universe = None
        cycles = self.config.data_dir / "cycles"
        old = sorted(p for p in cycles.iterdir() if p.is_dir()) if cycles.is_dir() else []
        for stale in old[:-self.config.keep_cycles]:
            shutil.rmtree(stale, ignore_errors=True)
        return {"action": "cycle_completed", "cycle_id": cycle["cycle_id"], "evaluated": cycle["evaluated_count"]}

    def step(self) -> dict[str, Any]:
        """One bounded unit of work; returns what happened. Governed-data refusals are recorded, not raised."""
        from services.research import governed_market_dataset as gmd

        state = self.load_state()
        now = self._clock()
        cycle = state.get("cycle")
        try:
            if cycle is None:
                due, reason, boundary = self._due(state, now)
                state["last_check"] = {"at": _iso(now), "due": due, "reason": reason}
                if not due:
                    self.save_state(state)
                    return {"action": "not_due", "reason": reason}
                cycle = self._start_cycle(state, boundary, now)
            pending = [b for b in cycle["batches"] if b["index"] not in cycle["done_batches"]]
            if not pending:
                return self._finish(state, cycle, now)
            return self._run_batch(state, cycle, pending[0])
        except gmd.GovernedMarketDatasetError as exc:
            state["last_check"] = {"at": _iso(now), "due": False, "reason": str(exc)}
            self.save_state(state)
            return {"action": "governed_data_refused", "reason": str(exc)}
        except RuntimeError as exc:
            if not str(exc).startswith("snapshot_not_reproducible"):
                raise
            state["last_check"] = {"at": _iso(now), "due": False, "reason": str(exc), "abandoned_cycle": cycle["cycle_id"]}
            state["cycle"] = None
            self.save_state(state)
            self._universe = None
            return {"action": "cycle_abandoned", "reason": str(exc)}

    def run_forever(self, stop: threading.Event, log: Callable[[str], None] = print) -> None:
        while not stop.is_set():
            try:
                outcome = self.step()
            except Exception as exc:  # noqa: BLE001 - the controller keeps running; the next pass resumes
                outcome = {"action": "error", "reason": f"{type(exc).__name__}: {str(exc)[:300]}"}
                log(f"alpha template evaluation step failed: {outcome['reason']}")
            if outcome["action"] == "batch_evaluated":
                continue  # more work in the cycle: go on at once
            stop.wait(self.config.check_seconds)


def build_production_scheduler(data_dir: Path) -> TemplateEvaluationScheduler:
    from services.registry.split_api import RegistryService
    from services.registry.storage import get_store as get_registry_store
    from services.research.store import ResearchOrchestratorStore

    return TemplateEvaluationScheduler(
        ScheduleConfig.from_env(data_dir),
        registry_service_factory=lambda: RegistryService(get_registry_store()),
        research_store_factory=lambda path: ResearchOrchestratorStore(path),
    )


def start_background(data_dir: Path, *, log: Callable[[str], None] = print) -> threading.Event | None:
    """Start the scheduler thread in the alpha-replication worker; ``ALPHA_TEMPLATE_EVAL_ENABLED=0`` turns it off."""
    if os.getenv("ALPHA_TEMPLATE_EVAL_ENABLED", "1").strip().lower() in ("0", "false", "no"):
        return None
    scheduler = build_production_scheduler(data_dir)
    stop = threading.Event()
    threading.Thread(target=scheduler.run_forever, args=(stop, log), name="alpha-template-evaluation", daemon=True).start()
    return stop
