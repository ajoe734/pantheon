"""Bridge paper-runtime telemetry into Trade Journey events.

TJ-E2E built the full journey read stack (materializer -> BFF -> console) but
no producer ever wrote the live event store: ``replay_backfill.py`` is
library-only and the runtime emits ``paper_order_simulated`` /
``paper_fill_simulated`` telemetry instead of journey events. This module maps
that telemetry into materializer-compatible journey events so the store can be
(re)built from recorded truth.

Fidelity rules (per TJ-E2E-010): only stages with direct telemetry evidence
are emitted (``trade_decision``, ``order_submission``, ``fill_management``);
upstream stages stay absent so completeness diagnostics report them honestly
instead of fabricating audit truth. All emitted events carry
``source: "telemetry_backfill"`` so merges can distinguish them from seed or
future first-class producer events.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

BACKFILL_SOURCE = "telemetry_backfill"
TELEMETRY_EVENT_TYPES = ("paper_order_simulated", "paper_fill_simulated")

# Copied straight from telemetry payload metadata when present; ``signal_id``
# feeds the materializer reverse index, the rest feed the BFF list-row summary.
_COMMON_FIELDS = ("symbol", "order_type", "signal_id")


def _clean(value: Any) -> Any:
    return value if value not in ("", None) else None


def _journey_id(payload: Mapping[str, Any]) -> str:
    metadata = payload.get("metadata") or {}
    signal_id = _clean(metadata.get("signal_id"))
    if signal_id:
        return f"tj-{signal_id}"
    return f"tj-evt-{payload.get('event_id')}"


def _base_event(payload: Mapping[str, Any], *, tenant_id: str, recorded_at: str) -> dict[str, Any] | None:
    occurred_at = _clean(payload.get("created_at"))
    event_id = _clean(payload.get("event_id"))
    if not occurred_at or not event_id:
        return None
    metadata = payload.get("metadata") or {}
    event: dict[str, Any] = {
        "journey_id": _journey_id(payload),
        "tenant_id": tenant_id,
        "environment": _clean(payload.get("environment")) or "paper",
        "occurred_at": occurred_at,
        "recorded_at": recorded_at or occurred_at,
        "source": BACKFILL_SOURCE,
        "runtime_id": _clean(payload.get("runtime_id")),
        "artifact_id": _clean(payload.get("artifact_id")),
        "capital_pool_id": _clean(payload.get("capital_pool_id")),
        "strategy_id": _clean(metadata.get("strategy_id")) or _clean((payload.get("target") or {}).get("strategy_id")),
    }
    for name in _COMMON_FIELDS:
        event[name] = _clean(metadata.get(name))
    return {key: value for key, value in event.items() if value is not None}


def _order_events(payload: Mapping[str, Any], *, tenant_id: str, recorded_at: str) -> list[dict[str, Any]]:
    base = _base_event(payload, tenant_id=tenant_id, recorded_at=recorded_at)
    if base is None:
        return []
    metadata = payload.get("metadata") or {}
    events = [{
        **base,
        "event_id": f"{payload['event_id']}-decision",
        "stage": "trade_decision",
        "stage_status": _clean(metadata.get("decision_status")) or "unknown",
        "quantity": _clean(metadata.get("requested_quantity")),
        "price": _clean(metadata.get("price")),
    }]
    order_status = _clean(metadata.get("order_status"))
    if order_status:
        events.append({
            **base,
            "event_id": f"{payload['event_id']}-order",
            "stage": "order_submission",
            "stage_status": order_status,
            "quantity": _clean(metadata.get("computed_quantity")) or _clean(metadata.get("requested_quantity")),
            "price": _clean(metadata.get("price")),
        })
    return [{k: v for k, v in event.items() if v is not None} for event in events]


def _fill_events(payload: Mapping[str, Any], *, tenant_id: str, recorded_at: str) -> list[dict[str, Any]]:
    base = _base_event(payload, tenant_id=tenant_id, recorded_at=recorded_at)
    if base is None:
        return []
    metrics = payload.get("metrics") or {}
    quantity = _clean(metrics.get("fill_quantity"))
    side = None
    if isinstance(quantity, (int, float)) and quantity:
        side = "sell" if quantity < 0 else "buy"
    event = {
        **base,
        "event_id": f"{payload['event_id']}-fill",
        "stage": "fill_management",
        "stage_status": "filled" if quantity else _clean(metrics.get("action")) or "unknown",
        "quantity": abs(quantity) if isinstance(quantity, (int, float)) else None,
        "side": side,
        "price": _clean(metrics.get("fill_price")),
    }
    return [{k: v for k, v in event.items() if v is not None}]


def journey_events_from_telemetry(rows: Iterable[tuple[str, str, Mapping[str, Any]]],
                                  *, tenant_id: str = "default") -> list[dict[str, Any]]:
    """Map telemetry rows ``(event_type, recorded_at, payload)`` to journey events."""
    events: list[dict[str, Any]] = []
    for event_type, recorded_at, payload in rows:
        if not isinstance(payload, Mapping):
            continue
        if event_type == "paper_order_simulated":
            events.extend(_order_events(payload, tenant_id=tenant_id, recorded_at=recorded_at))
        elif event_type == "paper_fill_simulated":
            events.extend(_fill_events(payload, tenant_id=tenant_id, recorded_at=recorded_at))
    events.sort(key=lambda event: (event["occurred_at"], event["event_id"]))
    return events


def merge_with_store(store_events: Iterable[Mapping[str, Any]],
                     backfill_events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Overlay fresh backfill events while preserving everything already stored.

    ``public.telemetry_events`` is routinely truncated, so a fresh derivation
    only covers the current retention window. Prior ``telemetry_backfill``
    events must therefore be kept, not replaced -- derived event ids are
    deterministic (telemetry event id + stage suffix), so re-derived rows
    overlay their older copies instead of duplicating them.
    """
    merged: dict[str, dict[str, Any]] = {}
    for event in store_events:
        if not isinstance(event, Mapping):
            continue
        event_id = event.get("event_id")
        if isinstance(event_id, str) and event_id:
            merged[event_id] = dict(event)
    for event in backfill_events:
        merged[event["event_id"]] = dict(event)
    return sorted(merged.values(), key=lambda event: (event.get("occurred_at") or "", event.get("event_id") or ""))


def load_store_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, Mapping)]
    if isinstance(raw, Mapping) and isinstance(raw.get("events"), list):
        return [dict(item) for item in raw["events"] if isinstance(item, Mapping)]
    return []


def write_store_atomic(path: Path, events: list[dict[str, Any]]) -> None:
    """Atomic replace so the BFF's mtime-cached reader never sees a torn file."""
    payload = json.dumps({"events": events}, ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
