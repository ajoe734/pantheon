"""Read-only view of the persona evaluator's saved recommendations.

The persona evaluator agent is the only producer of persona recommendations; the
BFF never computes advice. When the evaluator or its saved result is unavailable
this returns nothing: there is no heuristic fallback.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Dict, Optional


def saved_evaluator_result(quarter: str, snapshot_id: str = "") -> Optional[Dict[str, Any]]:
    base = os.getenv("PERSONA_EVALUATOR_URL", "").rstrip("/")
    if not base:
        return None
    request = urllib.request.Request(
        f"{base}/api/persona-evaluator/recommendations?quarter={quarter}&snapshot_id={snapshot_id}",
        headers={"X-Pantheon-Service-Token": os.getenv("PERSONA_EVALUATOR_READ_TOKEN", "")},
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            result = json.loads(response.read().decode())["data"]["result"]
    except Exception:
        return None
    return result if isinstance(result, dict) else None


def saved_recommendation(quarter: str, snapshot_id: str, recommendation_id: str) -> Optional[Dict[str, Any]]:
    result = saved_evaluator_result(quarter, snapshot_id) or {}
    for rec in result.get("items") or []:
        if isinstance(rec, dict) and rec.get("recommendation_id") == recommendation_id:
            return {**rec, "evaluator_run_id": result.get("run_id"), "evaluated_at": result.get("evaluated_at")}
    return None
