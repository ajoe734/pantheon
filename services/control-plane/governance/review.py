"""
Governance — High-Risk Operation Review
Blocks execution and waits for human confirmation when risk_level == "high".

In skeleton mode: always returns requires_approval=False (stub).
Real gate logic deferred until control-plane is wired end-to-end.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ReviewResult:
    requires_approval: bool
    reason: str


HIGH_RISK_ACTIONS = {
    "place_live_order",
    "cancel_all_orders",
    "withdraw_funds",
    "update_policy",
}


def check(action: str, payload: dict) -> ReviewResult:
    """
    Return whether the action requires human approval.

    TODO: integrate with Telegram/Discord approval flow once channels are wired.
    """
    if action in HIGH_RISK_ACTIONS:
        return ReviewResult(
            requires_approval=True,
            reason=f"'{action}' is a high-risk action and requires operator confirmation.",
        )
    return ReviewResult(requires_approval=False, reason="")
