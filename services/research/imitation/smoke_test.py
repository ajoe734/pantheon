"""Smoke test for the imitation dataset builder (IMT-003).

Verifies the builder assembles a governed dataset from raw session data
using only stdlib — no optional dependencies required.

Usage:
    python3 services/research/imitation/smoke_test.py
"""

from __future__ import annotations

import sys

from dataset_builder import (
    DatasetBuildRequest,
    DatasetBuilderError,
    ImitationDatasetBuilder,
    ImitationDatasetBuilderConfig,
    RawTrajectorySession,
    TrajectoryStep,
    build_dataset,
)

_SAMPLE_REQUEST = {
    "dataset_id": "traj-smoke-2026-05-16",
    "strategy_id": "alpha-mean-reversion",
    "source_dataset_refs": ["dataset://feedback/approved/2026-05-16"],
    "source_strategy_spec_id": "strat-alpha-mean-reversion-v2",
    "sessions": [
        {
            "trajectory_id": "traj-001",
            "actor_id": "trader-01",
            "actor_role": "operator",
            "decision": "approve",
            "target": {
                "registry_id": "reg-alpha-1",
                "strategy_id": "alpha-mean-reversion",
                "artifact_version": "1.2.0",
                "artifact_type": "strategy_spec",
                "promotion_state": "candidate",
            },
            "steps": [
                {"observation": [0.9, 0.1, -0.2], "action": "buy_small", "reward": 0.3, "feedback_event_id": "evt-001"},
                {"observation": [0.8, 0.2, -0.1], "action": "buy_small", "reward": 0.2, "feedback_event_id": "evt-002"},
            ],
        },
        {
            "trajectory_id": "traj-002",
            "actor_id": "trader-02",
            "actor_role": "approver",
            "decision": "edit",
            "target": {
                "registry_id": "reg-alpha-1",
                "strategy_id": "alpha-mean-reversion",
                "artifact_version": "1.2.0",
                "artifact_type": "strategy_spec",
                "promotion_state": "paper",
            },
            "steps": [
                {"observation": [-0.85, -0.2, 0.55], "action": "reduce_risk", "reward": 0.15, "feedback_event_id": "evt-003"},
            ],
        },
        {
            "trajectory_id": "traj-rejected",
            "actor_id": "external-bot",
            "actor_role": "system",
            "decision": "reject",
            "target": {
                "strategy_id": "alpha-mean-reversion",
                "promotion_state": "candidate",
            },
            "steps": [
                {"observation": [0.1, 0.1], "action": "hold"},
            ],
        },
    ],
}


def _check(condition: bool, label: str) -> None:
    if not condition:
        print(f"FAIL: {label}")
        sys.exit(1)
    print(f"OK:   {label}")


def main() -> int:
    print("=== IMT-003 dataset builder smoke test ===\n")

    # happy-path build from raw dict
    request = DatasetBuildRequest.from_dict(_SAMPLE_REQUEST)
    result = build_dataset(request)

    _check(result.num_included == 2, "two eligible sessions included")
    _check(result.num_filtered == 1, "one ineligible session filtered")
    _check("traj-rejected" in result.filtered_trajectory_ids, "rejected trajectory recorded in filter audit")
    _check(result.dataset_payload["dataset_id"] == "traj-smoke-2026-05-16", "dataset_id preserved")
    _check(result.dataset_payload["strategy_id"] == "alpha-mean-reversion", "strategy_id preserved")
    _check(len(result.dataset_payload["sessions"]) == 2, "payload contains two sessions")
    _check(result.dataset_payload.get("source_strategy_spec_id") == "strat-alpha-mean-reversion-v2", "source_strategy_spec_id preserved")
    _check(bool(result.build_id.startswith("dsb-")), "build_id has correct prefix")

    # payload round-trip check: sessions have required fields
    for session in result.dataset_payload["sessions"]:
        _check("trajectory_id" in session, f"session has trajectory_id: {session.get('trajectory_id')}")
        _check("target" in session and "promotion_state" in session["target"], "session has governed target")
        _check(len(session["steps"]) > 0, "session has steps")

    # governance: all-filtered input raises DatasetBuilderError
    bad_request = DatasetBuildRequest.from_dict({
        **_SAMPLE_REQUEST,
        "sessions": [_SAMPLE_REQUEST["sessions"][2]],  # only the rejected one
    })
    try:
        build_dataset(bad_request)
        print("FAIL: expected DatasetBuilderError for all-filtered input")
        return 1
    except DatasetBuilderError:
        print("OK:   DatasetBuilderError raised when all sessions filtered")

    # alias normalization: "approved" -> "approve"
    aliased_sessions = [dict(_SAMPLE_REQUEST["sessions"][0], decision="approved")]
    alias_request = DatasetBuildRequest.from_dict({**_SAMPLE_REQUEST, "sessions": aliased_sessions})
    alias_result = build_dataset(alias_request)
    _check(alias_result.num_included == 1, "decision alias 'approved' -> 'approve' accepted")

    # custom config: require_feedback_event_ids — mixed input (one good, one missing evt id)
    strict_config = ImitationDatasetBuilderConfig(require_feedback_event_ids=True)
    no_evt_session = {
        "trajectory_id": "traj-no-evt",
        "actor_id": "trader-03",
        "actor_role": "operator",
        "decision": "approve",
        "target": {
            "strategy_id": "alpha-mean-reversion",
            "promotion_state": "candidate",
        },
        "steps": [{"observation": [0.5, 0.5], "action": "hold"}],
    }
    strict_request = DatasetBuildRequest.from_dict({
        **_SAMPLE_REQUEST,
        "sessions": [_SAMPLE_REQUEST["sessions"][0], no_evt_session],
    })
    strict_result = build_dataset(strict_request, config=strict_config)
    _check(strict_result.num_filtered == 1, "session without feedback_event_id filtered under strict config")
    _check("traj-no-evt" in strict_result.filtered_trajectory_ids, "traj-no-evt in filter audit")

    print("\n=== All smoke tests passed ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
