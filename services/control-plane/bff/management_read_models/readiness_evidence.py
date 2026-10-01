"""Historical repository evidence is navigation, never current readiness authority."""
from __future__ import annotations

import re
from typing import Any


def historical_reference(path: str, label: str) -> dict[str, Any]:
    # Do not read/stat the checkout: presence and old pass markers prove nothing
    # about the currently hosted version, environment, tenant or freshness.
    return {
        "id": re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-"),
        "label": label, "path": path, "href": f"/{path}",
        "exists": None, "historical_only": True,
        "readiness_authority": False,
    }


def historical_surface(path: str, label: str) -> dict[str, Any]:
    return {
        "status": "unavailable", "source": "historical_reference",
        "artifact_path": path,
        "message": f"{label} is historical only; current owner evidence is not connected.",
        "staleness": {"served_from": "unverifiable", "last_known_at": None},
    }


def current_evidence_checks(
    checks: list[dict[str, Any]], references: list[dict[str, Any]],
    surfaces: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Never let historical passes or unavailable owner reads produce ready."""
    historical = {ref["path"] for ref in references if ref.get("historical_only")}
    result = []
    for check in checks:
        check = dict(check)
        if historical.intersection(check.get("evidence_refs") or []):
            check.update(status="unknown", message=(
                "Historical reference only. Current owner evidence bound to the "
                "deployed version and environment is unavailable."
            ))
        result.append(check)
    missing = [key for key, surface in surfaces.items() if surface.get("status") != "ok"]
    if missing:
        result.append({
            "id": "current_owner_evidence", "label": "Current owner evidence",
            "status": "unknown", "blocking": True,
            "message": "Readiness cannot be established from unavailable or degraded evidence.",
            "details": {"unverified_surfaces": missing},
        })
    return result
