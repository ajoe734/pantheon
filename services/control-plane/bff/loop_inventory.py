from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional


_REPO_ROOT = Path(__file__).resolve().parents[3]
_REGISTRY_PATH = _REPO_ROOT / "docs" / "deployment" / "loop-catalog.registry.json"
_REGISTRY_REF = "docs/deployment/loop-catalog.registry.json"
_LIVE_EVIDENCE_LEVELS = ("reconciled_live_proof", "proven_live_evidence")


def _load_registry() -> Dict[str, Any]:
    return json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))


def _evidence_statuses(evidence_profile: Dict[str, Any]) -> Dict[str, str]:
    statuses: Dict[str, str] = {}
    for truth_level, evidence in evidence_profile.items():
        if isinstance(evidence, dict):
            statuses[truth_level] = str(evidence.get("status") or "missing")
    return statuses


def _has_present_live_evidence(evidence_profile: Dict[str, Any]) -> bool:
    return any(
        isinstance(evidence_profile.get(level), dict)
        and evidence_profile[level].get("status") == "present"
        for level in _LIVE_EVIDENCE_LEVELS
    )


def _is_proven_live(loop: Dict[str, Any]) -> bool:
    maturity = loop.get("maturity") if isinstance(loop.get("maturity"), dict) else {}
    controller = loop.get("controller_contract") if isinstance(loop.get("controller_contract"), dict) else {}
    evidence_profile = loop.get("evidence_profile") if isinstance(loop.get("evidence_profile"), dict) else {}
    return (
        maturity.get("current") == "proven-live"
        and controller.get("status") == "proven_live"
        and isinstance(evidence_profile.get("proven_live_evidence"), dict)
        and evidence_profile["proven_live_evidence"].get("status") == "present"
    )


def _is_reconciled_with_evidence(loop: Dict[str, Any]) -> bool:
    maturity = loop.get("maturity") if isinstance(loop.get("maturity"), dict) else {}
    controller = loop.get("controller_contract") if isinstance(loop.get("controller_contract"), dict) else {}
    evidence_profile = loop.get("evidence_profile") if isinstance(loop.get("evidence_profile"), dict) else {}
    return (
        maturity.get("current") in {"reconciled", "proven-live"}
        and controller.get("status") in {"implemented", "proven_live"}
        and isinstance(evidence_profile.get("reconciled_live_proof"), dict)
        and evidence_profile["reconciled_live_proof"].get("status") == "present"
    )


def loop_inventory_meta() -> Dict[str, Any]:
    registry = _load_registry()
    return {
        "schema_version": registry.get("schema_version"),
        "catalog_id": registry.get("catalog_id"),
        "created_at": registry.get("created_at"),
        "source_documents": list(registry.get("source_documents") or []),
        "catalog_decisions": deepcopy(registry.get("catalog_decisions") or {}),
        "maturity_levels": deepcopy(registry.get("maturity_levels") or []),
        "truth_levels": deepcopy(registry.get("truth_levels") or []),
        "registry_ref": _REGISTRY_REF,
    }


def _project_loop(loop: Dict[str, Any]) -> Dict[str, Any]:
    loop_id = str(loop.get("loop_id") or "")
    maturity = deepcopy(loop.get("maturity") or {})
    owner = deepcopy(loop.get("owner") or {})
    controller = deepcopy(loop.get("controller_contract") or {})
    evidence_profile = deepcopy(loop.get("evidence_profile") or {})
    live = _is_proven_live(loop)
    reconciled = _is_reconciled_with_evidence(loop)
    evidence_statuses = _evidence_statuses(evidence_profile)

    return {
        "id": loop_id,
        "loop_id": loop_id,
        "name": loop.get("name"),
        "policy_ref": deepcopy(loop.get("policy_ref") or {}),
        "owner": owner,
        "trigger_model": deepcopy(loop.get("trigger_model") or {}),
        "desired_state": deepcopy(loop.get("desired_state") or {}),
        "actual_state": deepcopy(loop.get("actual_state") or {}),
        "maturity": maturity,
        "current_maturity": maturity.get("current"),
        "target_maturity": maturity.get("target"),
        "controller": controller,
        "evidence": evidence_profile,
        "evidence_statuses": evidence_statuses,
        "execution_tasks": deepcopy(loop.get("execution_tasks") or []),
        "truth_source": {
            "level": "registry_metadata",
            "source": "static_json_registry",
            "registry_ref": _REGISTRY_REF,
            "live_truth_levels": list(_LIVE_EVIDENCE_LEVELS),
        },
        "live_status": {
            "is_live": live,
            "is_reconciled": reconciled,
            "has_live_evidence": _has_present_live_evidence(evidence_profile),
            "reason": (
                "proven live evidence is present in the loop catalog"
                if live
                else "catalog metadata is not live liveness proof"
            ),
        },
    }


def list_loop_inventory_entries() -> List[Dict[str, Any]]:
    registry = _load_registry()
    loops = registry.get("loops") if isinstance(registry.get("loops"), list) else []
    return [_project_loop(loop) for loop in loops if isinstance(loop, dict)]


def get_loop_inventory_entry(loop_id: str) -> Optional[Dict[str, Any]]:
    clean_id = str(loop_id or "").strip()
    for item in list_loop_inventory_entries():
        if item.get("loop_id") == clean_id:
            return item
    return None
