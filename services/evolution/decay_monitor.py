"""Durable member decay windows used by the existing consumer's pool scheduler.

PoolEvolutionScheduler resolves each member's own approved baseline and virtual
book. Persona-account telemetry is never a substitute for a member observation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

MAX_WINDOWS = 12


class WindowLedger:
    """Durable per-(tenant, persona, artifact) chronological paper windows (JSONL)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def append_if_new(self, row: Mapping[str, Any]) -> bool:
        key = (row["tenant_id"], row["persona_id"], row["artifact_id"], row["as_of"])
        if any((r["tenant_id"], r["persona_id"], r["artifact_id"], r["as_of"]) == key for r in self._rows()):
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(row), sort_keys=True) + "\n")
        return True

    def windows(self, tenant_id: str, persona_id: str, artifact_id: str) -> list[dict[str, Any]]:
        rows = [r for r in self._rows()
                if (r["tenant_id"], r["persona_id"], r["artifact_id"]) == (tenant_id, persona_id, artifact_id)]
        rows.sort(key=lambda r: r["as_of"])
        return rows[-MAX_WINDOWS:]
