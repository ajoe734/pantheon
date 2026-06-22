"""Agora trading-room in-memory store.

Provides simple dict-backed stores for TradingDecisionEvent and TradingIntent
records within a single BFF process.  Not durable — each restart starts empty.

Safety invariant: this module never persists broker orders, RuntimeBinding
mutations, or capital binding changes.  Every record has no_order_route_proof.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class TradingRoomStore:
    """Thread-unsafe single-process store for trading room decision events and intents."""

    def __init__(self) -> None:
        self._decision_events: Dict[str, Dict[str, Any]] = {}
        self._intents: Dict[str, Dict[str, Any]] = {}
        self._trader_decisions: Dict[str, List[Dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # Decision events
    # ------------------------------------------------------------------

    def upsert_decision_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
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
                start = ids.index(next_page_token)
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

    def upsert_intent(self, intent: Dict[str, Any]) -> Dict[str, Any]:
        intent_id = intent["intent_id"]
        self._intents[intent_id] = intent
        return intent

    def get_intent(self, intent_id: str) -> Optional[Dict[str, Any]]:
        return self._intents.get(intent_id)


def make_trading_room_store() -> TradingRoomStore:
    return TradingRoomStore()
