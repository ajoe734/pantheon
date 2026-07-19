"""Utilization policy — Phase 7 (SUPERVISOR_REWRITE_PLAN.md §3.8, anti-pattern F).

The incumbent "underutilization sidecar" engine *manufactures new tasks* when the
fleet looks idle (259 lines, 23 helpers, five stacked throttles), then fights
them back down with a +10 sidecar priority penalty — a primary engine of "the
board keeps growing but nothing closes."

The plan's replacement principle: **utilization = reprioritize the real backlog,
never synthesize tasks.** This is that decision as one pure function. The live
sidecar engine is already switchable off today (`underutilization_dispatch.enabled
= false`, verified in test_utilization.py); this module is the clean policy the
dispatcher adopts once the sidecar path is deleted.
"""
from __future__ import annotations

import enum


class UtilizationAction(enum.Enum):
    NOOP = "noop"                  # utilization is fine, or nothing to promote
    REPRIORITIZE = "reprioritize"  # promote real backlog work — never synthesize


def select_utilization_action(
    *,
    utilization_ratio: float,
    threshold_ratio: float,
    ready_backlog: int,
) -> UtilizationAction:
    """Decide what to do about an under-utilized fleet.

    When utilization is below threshold AND real ready work exists, the answer is
    to reprioritize that real backlog up — never to invent make-work. With no
    ready backlog there is genuinely nothing to do (idle is correct), so NOOP;
    inventing tasks there is exactly the accretion being removed.
    """
    if utilization_ratio >= threshold_ratio:
        return UtilizationAction.NOOP
    if ready_backlog <= 0:
        return UtilizationAction.NOOP
    return UtilizationAction.REPRIORITIZE
