"""Pure Management loop-health truth projection.

Current loop-health truth is exactly one join: the static loop catalog
(``docs/deployment/loop-catalog.registry.json``, via ``loop_inventory``)
joined with the durable ``LoopControllerStore`` record for that loop,
scoped by tenant and environment.

There is no other source of current truth here:

- no BFF local snapshot fallback (``ReadSurfaceStore`` loop_health files are
  historical evidence only, never joined as current state);
- no read-side controller-truth publication (publishing a controller record
  is owned exclusively by that loop's own background writer cycle); and
- no downstream-health-monitor synthesis or cross-loop row manufacture
  (component probe failures stay under ``/bff/v5/downstream-health`` and
  never mutate or manufacture a canonical loop-health row).

The projection always returns exactly the twelve canonical loop rows; the
composite overlay (for example ``per_persona_ooda``) is static catalog
inventory, not current controller truth, and is surfaced separately by the
inventory read model instead.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from ..loop_inventory import get_loop_health_entry, list_loop_health_entries

log = logging.getLogger(__name__)


async def fetch_controller_store_health_records(
    tenant_id: str,
    environment: str,
) -> Tuple[bool, List[Dict[str, Any]]]:
    """Load current controller-runtime records from ``LoopControllerStore``.

    Returns ``(available, records)``.  This never reads a BFF local snapshot
    and never triggers a controller-runtime write.
    """

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return False, []
    try:
        import importlib

        loop_control = importlib.import_module("services.loop-control")
        store = loop_control.LoopControllerStore(dsn)
        project_controller_record_to_bff = loop_control.project_controller_record_to_bff
        rows = await store.list_records(tenant_id, environment)
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to load loop health from LoopControllerStore: %s", exc)
        return False, []

    scoped_rows = [
        row
        for row in rows
        if isinstance(row, dict)
        and str(row.get("tenant_id") or "").strip() == tenant_id
        and str(row.get("environment") or "").strip() == environment
    ]
    if not scoped_rows:
        return False, []

    records = [project_controller_record_to_bff(row) for row in scoped_rows]
    for record in records:
        record["_health_source"] = "controller_store"
    return True, records


def project_canonical_loop_health(
    health_records: List[Dict[str, Any]],
    *,
    health_source: str,
) -> List[Dict[str, Any]]:
    """Project exactly the twelve canonical loop-health rows."""

    return list_loop_health_entries(health_records, health_source=health_source)


def project_canonical_loop_health_entry(
    loop_id: str,
    health_records: List[Dict[str, Any]],
    *,
    health_source: str,
) -> Optional[Dict[str, Any]]:
    """Project one canonical loop-health row, or ``None`` when unknown.

    A composite overlay id (for example ``per_persona_ooda``) is not part of
    loop-health truth and resolves to ``None`` here.
    """

    return get_loop_health_entry(loop_id, health_records, health_source=health_source)


async def fetch_canonical_loop_health(
    tenant_id: str,
    environment: str,
) -> Tuple[bool, List[Dict[str, Any]], str]:
    """Fetch and project the twelve-row canonical loop-health truth."""

    available, health_records = await fetch_controller_store_health_records(
        tenant_id, environment
    )
    health_source = "controller_store" if available else "missing"
    projected = project_canonical_loop_health(health_records, health_source=health_source)
    return available, projected, health_source
