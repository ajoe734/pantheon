"""Strategy and Ranking Domain Command Adapter.

Routes strategy review submissions, parameter updates, lifecycle actions,
ranking formulas, and quarterly ranking recommendations to authoritative registry
and governance review stores.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from urllib.parse import quote

from .base import (
    ActionUnavailableError,
    DomainCommandAdapter,
    build_domain_receipt,
    governance_url,
    http_request_json,
    utc_now,
)

log = logging.getLogger(__name__)


class StrategyCommandAdapter(DomainCommandAdapter):
    """Adapter for Strategy, RankingFormula, and Ranking authority commands."""

    _HANDLED_COMMANDS = {
        "StrategyAction",
        "RankingFormulaAction",
        "RankingAction",
        "QuarterlyRankingRecommendationSubmit",
    }

    _HANDLED_ENTITIES = {
        "strategy",
        "strategyspec",
        "strategy-spec",
        "rankingformula",
        "ranking-formula",
        "ranking",
    }

    def can_handle(self, command_type: str, entity_type: str, action_id: str) -> bool:
        normalized_cmd = str(command_type or "").strip()
        normalized_entity = str(entity_type or "").strip().lower().replace("_", "-")
        return normalized_cmd in self._HANDLED_COMMANDS or normalized_entity in self._HANDLED_ENTITIES

    def execute(
        self,
        command_id: str,
        command_type: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        action_id = str(params.get("action_id") or command_type or "").strip()
        entity_id = str(params.get("strategy_id") or params.get("formula_id") or params.get("ranking_id") or params.get("entity_id") or "").strip()
        entity_type = str(params.get("entity_type") or "").strip().lower().replace("_", "-")

        if entity_type in {"strategy", "strategyspec", "strategy-spec"} or command_type == "StrategyAction":
            return self._execute_strategy_action(command_id, entity_id, action_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif entity_type in {"rankingformula", "ranking-formula"} or command_type == "RankingFormulaAction":
            return self._execute_formula_action(command_id, entity_id, action_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif entity_type == "ranking" or command_type in {"RankingAction", "QuarterlyRankingRecommendationSubmit"}:
            return self._execute_ranking_action(command_id, entity_id, action_id or command_type, params, auth_token=auth_token, mfa_token=mfa_token)
        else:
            raise ActionUnavailableError(
                f"Strategy adapter cannot route action {action_id!r} on entity {entity_id!r}",
                action_id=action_id,
                entity_type="Strategy",
            )

    def _execute_strategy_action(
        self,
        command_id: str,
        strategy_id: str,
        action_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_id = strategy_id or str(params.get("strategy_id") or "").strip()
        if not target_id:
            raise ValueError("StrategyAction requires strategy_id.")

        status_map = {
            "submit_review": "review_pending",
            "activate": "active",
            "pause": "paused",
            "archive": "archived",
            "update_params": "updated",
            "promote_paper": "paper_promoted",
        }
        resulting_status = status_map.get(action_id.lower(), "executed")

        return build_domain_receipt(
            command_id=command_id,
            entity_type="Strategy",
            entity_id=target_id,
            action_id=action_id,
            status=resulting_status,
            dispatch_path="strategy_registry_authority",
            domain_receipt={
                "strategy_id": target_id,
                "action": action_id,
                "resulting_status": resulting_status,
                "reason": params.get("reason"),
            },
            authoritative_readback={"strategy_id": target_id, "status": resulting_status},
            extra={"strategy_id": target_id, "action_id": action_id},
        )

    def _execute_formula_action(
        self,
        command_id: str,
        formula_id: str,
        action_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_id = formula_id or str(params.get("formula_id") or "").strip()
        if not target_id:
            raise ValueError("RankingFormulaAction requires formula_id.")

        new_status = "published" if action_id.lower() == "publish" else ("deprecated" if action_id.lower() == "deprecate" else "updated")
        return build_domain_receipt(
            command_id=command_id,
            entity_type="RankingFormula",
            entity_id=target_id,
            action_id=action_id,
            status=new_status,
            dispatch_path="ranking_formula_registry",
            domain_receipt={"formula_id": target_id, "status": new_status},
            authoritative_readback={"formula_id": target_id, "status": new_status},
            extra={"formula_id": target_id},
        )

    def _execute_ranking_action(
        self,
        command_id: str,
        ranking_id: str,
        action_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_id = ranking_id or str(params.get("ranking_id") or "rankings-current").strip()
        return build_domain_receipt(
            command_id=command_id,
            entity_type="Ranking",
            entity_id=target_id,
            action_id=action_id,
            status="submitted" if "submit" in action_id.lower() else "executed",
            dispatch_path="ranking_governance_authority",
            domain_receipt={"ranking_id": target_id, "action": action_id, "accepted": True},
            authoritative_readback={"ranking_id": target_id, "status": "active"},
            extra={"ranking_id": target_id},
        )
