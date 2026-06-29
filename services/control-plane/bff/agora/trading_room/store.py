"""Agora trading-room in-memory store.

Provides simple dict-backed stores for TradingDecisionEvent and TradingIntent
records within a single BFF process.  Not durable — each restart starts empty.

Safety invariant: this module never persists broker orders, RuntimeBinding
mutations, or capital binding changes.  Every persisted decision, intent, and
handoff record has the canonical no_order_route_proof for its schema.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional


class TradingRoomStore:
    """Thread-unsafe single-process store for trading room decision events and intents."""

    def __init__(self) -> None:
        self._decision_events: Dict[str, Dict[str, Any]] = {}
        self._intents: Dict[str, Dict[str, Any]] = {}
        self._intent_states: Dict[str, str] = {}
        self._handoffs: Dict[str, Dict[str, Any]] = {}
        self._handoffs_by_intent: Dict[str, List[str]] = {}
        self._trader_decisions: Dict[str, List[Dict[str, Any]]] = {}
        self._idempotency_keys: Dict[str, bool] = {}
        self._workspace_proposals: Dict[str, Dict[str, Any]] = {}
        self._workspaces: Dict[str, Dict[str, Any]] = {}
        self._widget_revision_proposals: Dict[str, Dict[str, Any]] = {}
        self._workspace_versions: Dict[str, List[Dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # Decision events
    # ------------------------------------------------------------------

    _REQUIRED_PROOF = "agora_decision_support_only"

    def upsert_decision_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        proof = event.get("no_order_route_proof")
        if proof != self._REQUIRED_PROOF:
            raise ValueError(
                f"D1 safety invariant: no_order_route_proof must be "
                f"'{self._REQUIRED_PROOF}', got {proof!r}"
            )
        event_id = event["decision_event_id"]
        self._decision_events[event_id] = event
        return event

    def get_decision_event(self, decision_event_id: str) -> Optional[Dict[str, Any]]:
        return self._decision_events.get(decision_event_id)

    def list_decision_events(
        self,
        *,
        event_kind: Optional[str] = None,
        state: Optional[str] = None,
        page_size: int = 20,
        next_page_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        items = list(self._decision_events.values())
        if event_kind:
            items = [e for e in items if e.get("event_kind") == event_kind]
        if state:
            items = [e for e in items if e.get("state") == state]
        # Naive offset-based pagination keyed by decision_event_id lexicographic order
        items.sort(key=lambda e: e.get("triggered_at", ""))
        start = 0
        if next_page_token:
            ids = [e["decision_event_id"] for e in items]
            if next_page_token in ids:
                start = ids.index(next_page_token) + 1  # start AFTER the token item
        page = items[start : start + page_size]
        has_more = (start + page_size) < len(items)
        token = page[-1]["decision_event_id"] if has_more and page else None
        return {
            "items": page,
            "page_info": {
                "next_page_token": token,
                "page_size": len(page),
                "has_more": has_more,
            },
        }

    def record_trader_decision(
        self, decision_event_id: str, decision_record: Dict[str, Any]
    ) -> None:
        self._trader_decisions.setdefault(decision_event_id, []).append(decision_record)
        event = self._decision_events.get(decision_event_id)
        if event is not None:
            action = decision_record.get("decision")
            state_map = {
                "approve": "decided",
                "reject": "decided",
                "defer": "decided",
                "modify": "decided",
            }
            if action in state_map:
                event["state"] = state_map[action]
                decision_state_map = {
                    "approve": "approved_by_trader",
                    "reject": "rejected_by_trader",
                    "defer": "deferred",
                    "modify": "approved_by_trader",
                }
                event["decision_state"] = decision_state_map[action]

    # ------------------------------------------------------------------
    # Trading intents
    # ------------------------------------------------------------------

    _REQUIRED_INTENT_PROOF = "agora_intent_record_only"
    _REQUIRED_HANDOFF_PROOF = "agora_request_only_no_order_route"

    def upsert_intent(self, intent: Dict[str, Any], *, state: str = "draft") -> Dict[str, Any]:
        proof = intent.get("no_order_route_proof")
        if proof != self._REQUIRED_INTENT_PROOF:
            raise ValueError(
                f"D1 safety invariant: TradingIntent.no_order_route_proof must be "
                f"'{self._REQUIRED_INTENT_PROOF}', got {proof!r}"
            )
        intent_id = intent["intent_id"]
        self._intents[intent_id] = dict(intent)
        self._intent_states[intent_id] = state
        return self._intents[intent_id]

    def get_intent(self, intent_id: str) -> Optional[Dict[str, Any]]:
        return self._intents.get(intent_id)

    def get_intent_state(self, intent_id: str) -> Optional[str]:
        if intent_id not in self._intents:
            return None
        return self._intent_states.get(intent_id, "draft")

    def set_intent_state(self, intent_id: str, state: str) -> Optional[Dict[str, Any]]:
        if intent_id not in self._intents:
            return None
        self._intent_states[intent_id] = state
        return self._intents[intent_id]

    # ------------------------------------------------------------------
    # Governed intent handoffs
    # ------------------------------------------------------------------

    def upsert_handoff(self, handoff: Dict[str, Any]) -> Dict[str, Any]:
        proof = handoff.get("no_order_route_proof")
        if proof != self._REQUIRED_HANDOFF_PROOF:
            raise ValueError(
                f"D1 safety invariant: GovernedIntentHandoff.no_order_route_proof must be "
                f"'{self._REQUIRED_HANDOFF_PROOF}', got {proof!r}"
            )
        intent_id = handoff["intent_id"]
        if intent_id not in self._intents:
            raise KeyError(intent_id)
        handoff_id = handoff["handoff_id"]
        if handoff_id in self._handoffs:
            raise ValueError(f"Duplicate handoff_id: {handoff_id}")
        self._handoffs[handoff_id] = dict(handoff)
        self._handoffs_by_intent.setdefault(intent_id, []).append(handoff_id)
        if handoff.get("state") == "submitted":
            self._intent_states[intent_id] = "submitted"
        return self._handoffs[handoff_id]

    def get_handoff(self, handoff_id: str) -> Optional[Dict[str, Any]]:
        return self._handoffs.get(handoff_id)

    def list_handoffs_for_intent(self, intent_id: str) -> List[Dict[str, Any]]:
        return [
            self._handoffs[handoff_id]
            for handoff_id in self._handoffs_by_intent.get(intent_id, [])
            if handoff_id in self._handoffs
        ]

    def withdraw_intent(self, intent_id: str, *, withdrawn_at: str) -> Optional[Dict[str, Any]]:
        intent = self.set_intent_state(intent_id, "withdrawn")
        if intent is None:
            return None

        withdrawn_handoff_ids: List[str] = []
        for handoff in self.list_handoffs_for_intent(intent_id):
            if handoff.get("state") in {"draft", "submitted"}:
                handoff["state"] = "withdrawn"
                handoff["updated_at"] = withdrawn_at
                withdrawn_handoff_ids.append(handoff["handoff_id"])

        return {
            "intent": intent,
            "state": "withdrawn",
            "withdrawn_handoff_ids": withdrawn_handoff_ids,
        }

    # ------------------------------------------------------------------
    # Idempotency
    # ------------------------------------------------------------------

    def check_and_record_idempotency_key(self, scope: str, key: str) -> bool:
        """Return True if duplicate; False on first use and record the key."""
        composite = f"{scope}:{key}"
        if composite in self._idempotency_keys:
            return True
        self._idempotency_keys[composite] = True
        return False

    # ------------------------------------------------------------------
    # Trading Room workspace proposals and workspaces
    # ------------------------------------------------------------------

    def upsert_workspace_proposal(
        self,
        proposal: Dict[str, Any],
        *,
        tenant_id: str,
        user_id: str,
    ) -> Dict[str, Any]:
        proposal_id = str(proposal["proposalId"])
        self._workspace_proposals[proposal_id] = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "proposal": copy.deepcopy(proposal),
        }
        return copy.deepcopy(proposal)

    def get_workspace_proposal_record(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        record = self._workspace_proposals.get(proposal_id)
        return copy.deepcopy(record) if record is not None else None

    def upsert_workspace(
        self,
        workspace: Dict[str, Any],
        *,
        tenant_id: str,
        user_id: str,
    ) -> Dict[str, Any]:
        workspace_id = str(workspace["id"])
        self._workspaces[workspace_id] = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "workspace": copy.deepcopy(workspace),
        }
        return copy.deepcopy(workspace)

    def get_workspace_record(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        record = self._workspaces.get(workspace_id)
        return copy.deepcopy(record) if record is not None else None

    # ------------------------------------------------------------------
    # Widget revision proposals
    # ------------------------------------------------------------------

    def upsert_widget_revision_proposal(
        self,
        proposal: Dict[str, Any],
        *,
        tenant_id: str,
        user_id: str,
    ) -> Dict[str, Any]:
        proposal_id = str(proposal["id"])
        self._widget_revision_proposals[proposal_id] = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "proposal": copy.deepcopy(proposal),
        }
        return copy.deepcopy(proposal)

    def get_widget_revision_proposal_record(
        self,
        proposal_id: str,
    ) -> Optional[Dict[str, Any]]:
        record = self._widget_revision_proposals.get(proposal_id)
        return copy.deepcopy(record) if record is not None else None

    # ------------------------------------------------------------------
    # Workspace versions and change log
    # ------------------------------------------------------------------

    def record_workspace_version(
        self,
        workspace: Dict[str, Any],
        *,
        tenant_id: str,
        user_id: str,
        created_at: str,
        change_summary: str,
        generated_by: Optional[str] = None,
        changed_by: Optional[str] = None,
        reason: Optional[str] = None,
        affected_views: Optional[List[str]] = None,
        affected_widgets: Optional[List[str]] = None,
        effect_evaluation: Optional[str] = None,
        source_revision_proposal_id: Optional[str] = None,
        rollback_of_version_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        workspace_id = str(workspace["id"])
        versions = self._workspace_versions.setdefault(workspace_id, [])
        dashboard_version = int(workspace.get("dashboardVersion") or 0)
        for version in versions:
            if int(version.get("dashboardVersion") or 0) == dashboard_version:
                return copy.deepcopy(version)

        previous_version_id = versions[-1]["id"] if versions else None
        version_id = f"trdv_{workspace_id}_v{dashboard_version}"
        record = {
            "id": version_id,
            "userId": workspace["userId"],
            "strategyId": workspace["strategyId"],
            "strategyVersion": workspace["strategyVersion"],
            "dashboardVersion": dashboard_version,
            "generatedBy": generated_by or workspace.get("generatedBy") or "user_modified",
            "previousVersionId": previous_version_id,
            "changeSummary": change_summary,
            "views": copy.deepcopy(workspace.get("views") or []),
            "createdAt": created_at,
            "status": "active",
            "changeLog": {
                "changedAt": created_at,
                "changedBy": changed_by or generated_by or workspace.get("generatedBy") or "user_modified",
                "reason": reason or change_summary,
                "affectedViews": list(affected_views or []),
                "affectedWidgets": list(affected_widgets or []),
                "effectEvaluation": effect_evaluation or "not_evaluated",
                "rollbackAvailable": True,
                "sourceRevisionProposalId": source_revision_proposal_id,
                "rollbackOfVersionId": rollback_of_version_id,
            },
        }
        for prior in versions:
            if prior.get("status") == "active":
                prior["status"] = "superseded"
                prior.setdefault("changeLog", {})["rollbackAvailable"] = True
        versions.append(record)
        return copy.deepcopy(record)

    def list_workspace_version_records(
        self,
        workspace_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> List[Dict[str, Any]]:
        workspace_record = self._workspaces.get(workspace_id)
        if (
            workspace_record is None
            or str(workspace_record.get("tenant_id") or "") != tenant_id
            or str(workspace_record.get("user_id") or "") != user_id
        ):
            return []
        return copy.deepcopy(self._workspace_versions.get(workspace_id, []))

    def get_workspace_version_record(
        self,
        workspace_id: str,
        version_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> Optional[Dict[str, Any]]:
        for version in self.list_workspace_version_records(
            workspace_id,
            tenant_id=tenant_id,
            user_id=user_id,
        ):
            if str(version.get("id") or "") == version_id:
                return version
        return None


def make_trading_room_store() -> TradingRoomStore:
    return TradingRoomStore()
