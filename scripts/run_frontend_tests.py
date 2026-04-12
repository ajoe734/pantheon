#!/usr/bin/env python3
"""Simple test runner for frontend tests (avoids pytest dependency).
"""
import sys

from services.frontend import state_machine


def run():
    failures = []

    try:
        gating = state_machine.compute_button_gating(state_machine.DataState.FRESH, "pending_approval")
        assert gating["approve"] is True and gating["reject"] is True
    except AssertionError:
        failures.append("test_gating_fresh failed")

    try:
        gating = state_machine.compute_button_gating(state_machine.DataState.STALE, "pending_approval")
        assert gating["approve"] is False and gating["reject"] is True
    except AssertionError:
        failures.append("test_gating_stale failed")

    try:
        gating = state_machine.compute_button_gating(state_machine.DataState.UNAVAILABLE, "pending_approval")
        assert gating["approve"] is False and gating["reject"] is False
    except AssertionError:
        failures.append("test_gating_unavailable failed")

    if failures:
        print("FAILURES:")
        for f in failures:
            print(" - ", f)
        sys.exit(1)
    print("All frontend tests passed")


if __name__ == '__main__':
    run()
