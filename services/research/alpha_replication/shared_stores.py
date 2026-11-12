"""Multi-writer-safe stores behind the section-10 ReplicationGate.

``FamilyTrialCounterStore`` and ``BacktestRunStore`` cache their JSON file at
construction and rewrite it whole, so two writers (the OOS gate in the
revalidation worker, the evolution search / mutate executor, other processes,
restarts) silently overwrite one another's trials and runs.  These variants
reload under one exclusive file lock and replace the file atomically on every
write; ``SharedReplicationGate`` is the gate wired with them.
"""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from services.research.replication.gate import BacktestRunStore, FamilyTrialCounterStore, ReplicationGate


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Path(str(path) + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


class SharedFamilyTrialCounterStore(FamilyTrialCounterStore):
    def __init__(self, path: Optional[Path] = None) -> None:
        super().__init__(path)

    def _reload(self, strict: bool) -> None:
        if self._path is None or not self._path.exists():
            if self._path is not None:
                self._counts = {}
            return
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("counter file is not an object")
            self._counts = {str(k): int(v) for k, v in loaded.items()}
        except Exception:
            if strict:  # a shared counter that cannot be read must never reset to zero
                raise
            self._counts = {}

    def get(self, family_id: str) -> int:
        self._reload(strict=True)
        return super().get(family_id)

    def increment(self, family_id: str, preset_count: Optional[int] = None) -> int:
        if self._path is None:
            return super().increment(family_id, preset_count)
        with _locked(self._path):
            self._reload(strict=True)
            next_val = int(self._counts.get(family_id, 0)) + 1
            if preset_count is not None and int(preset_count) > next_val:
                next_val = int(preset_count)
            self._counts[family_id] = next_val
            _atomic_write(self._path, self._counts)
            return next_val


class SharedBacktestRunStore(BacktestRunStore):
    def record(self, evaluation: Dict[str, Any]) -> Optional[str]:
        if self._path is None:
            return super().record(evaluation)
        with _locked(self._path):
            try:  # merge what other writers recorded since this instance loaded
                loaded = json.loads(self._path.read_text(encoding="utf-8")) if self._path.exists() else {}
                if isinstance(loaded, dict):
                    self._runs = {**{str(k): dict(v) for k, v in loaded.items() if isinstance(v, dict)}}
            except Exception:
                pass
            run_id = super().record(evaluation)  # rewrites the merged map
            return run_id


class SharedReplicationGate(ReplicationGate):
    """ReplicationGate whose family trial counter is safe across writers and restarts."""

    def __init__(self, config=None, trial_counter_path: Optional[Path] = None, backtest_run_store=None) -> None:
        super().__init__(config=config, trial_counter_path=trial_counter_path, backtest_run_store=backtest_run_store)
        self._trial_counter_store = SharedFamilyTrialCounterStore(trial_counter_path)
