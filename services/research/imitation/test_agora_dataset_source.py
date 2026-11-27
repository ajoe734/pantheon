"""Tests for the Agora DatasetVersion -> IMT-003 translation layer (L12-IMIT-001)."""

from __future__ import annotations

import json

import pytest

from services.research.imitation.agora_dataset_source import (
    AGORA_DATASET_AUTHORITY,
    AgoraDatasetRejected,
    AgoraDatasetUnavailable,
    AgoraDatasetVersion,
    AgoraTenantScopeError,
    attach_step_probabilities,
    build_dataset_lineage,
    build_dataset_payload,
    eligible_versions,
)
from services.research.imitation.bc_trainer import train
from services.research.imitation.eval_metrics import evaluate


def _record(
    dataset_version_id: str = "dsv-1",
    *,
    tenant_id: str = "tenant-a",
    user_id: str = "user-a",
    evidence_id: str = "ev-1",
    content: dict | None = None,
    learning_eligible: bool = True,
    dataset_kind: str = "learn",
    source_refs=None,
) -> dict:
    return {
        "evidence_id": evidence_id,
        "dataset_version_id": dataset_version_id,
        "dataset_kind": dataset_kind,
        "interaction_kind": "feedback",
        "persona_id": "persona-1",
        "session_id": "session-1",
        "tenant_id": tenant_id,
        "user_id": user_id,
        "content": content
        if content is not None
        else {
            "steps": [
                {"observation": [0.9, 0.1, -0.2], "action": "buy_small", "reward": 0.3},
                {"observation": [-0.8, 0.2, 0.55], "action": "reduce_risk", "reward": 0.1},
            ]
        },
        "source_refs": ["artifact://source-1"] if source_refs is None else source_refs,
        "learning_eligible": learning_eligible,
        "captured_at": "2026-07-26T00:00:00Z",
        "extracted_at": "2026-07-26T00:00:01Z",
        "version": 1,
    }


def test_from_record_requires_lineage_identity() -> None:
    for missing in ("dataset_version_id", "tenant_id", "evidence_id"):
        record = _record()
        record[missing] = ""
        with pytest.raises(AgoraDatasetRejected) as excinfo:
            AgoraDatasetVersion.from_record(record)
        assert missing in str(excinfo.value)


def test_from_record_parses_jsonb_text_columns() -> None:
    """Some drivers hand back JSONB columns as text; lineage must survive that."""

    record = _record(source_refs=json.dumps(["artifact://a", "artifact://b"]))
    record["content"] = json.dumps(record["content"])
    version = AgoraDatasetVersion.from_record(record)
    assert version.source_refs == ("artifact://a", "artifact://b")
    assert version.content["steps"][0]["action"] == "buy_small"


def test_dataset_uri_is_tenant_qualified() -> None:
    version = AgoraDatasetVersion.from_record(_record())
    assert version.dataset_uri == "agora://dataset-version/tenant-a/dsv-1"
    assert version.to_dataset_ref()["source"] == AGORA_DATASET_AUTHORITY
    assert version.to_dataset_ref()["tenant_id"] == "tenant-a"


def test_build_payload_carries_dataset_and_evidence_lineage() -> None:
    version = AgoraDatasetVersion.from_record(_record())
    payload = build_dataset_payload([version])
    assert payload["dataset_id"] == "dsv-1"
    assert payload["tenant_id"] == "tenant-a"
    assert payload["source_dataset_refs"] == [
        "agora://dataset-version/tenant-a/dsv-1",
        "evidence://ev-1",
        "artifact://source-1",
    ]
    assert len(payload["sessions"]) == 1
    assert len(payload["sessions"][0]["steps"]) == 2


def test_build_payload_accepts_all_three_governed_content_shapes() -> None:
    sessions_shape = AgoraDatasetVersion.from_record(
        _record(
            "dsv-sessions",
            evidence_id="ev-s",
            content={
                "strategy_id": "alpha",
                "sessions": [
                    {
                        "trajectory_id": "t-1",
                        "actor_id": "a",
                        "actor_role": "operator",
                        "decision": "approve",
                        "steps": [{"observation": [1.0], "action": "buy"}],
                    }
                ],
            },
        )
    )
    assert build_dataset_payload([sessions_shape])["sessions"][0]["trajectory_id"] == "t-1"

    steps_shape = AgoraDatasetVersion.from_record(_record("dsv-steps", evidence_id="ev-t"))
    assert build_dataset_payload([steps_shape])["sessions"][0]["trajectory_id"] == "traj-ev-t"

    transition_shape = AgoraDatasetVersion.from_record(
        _record(
            "dsv-one",
            evidence_id="ev-o",
            content={"observation": [5.0, 6.0], "action": "hold", "reward": 0.2},
        )
    )
    session = build_dataset_payload([transition_shape])["sessions"][0]
    assert session["steps"] == [
        {"observation": [5.0, 6.0], "action": "hold", "feedback_event_id": "ev-o", "reward": 0.2}
    ]


def test_no_versions_is_unavailable_not_empty_success() -> None:
    with pytest.raises(AgoraDatasetUnavailable):
        build_dataset_payload([])


def test_ineligible_and_observe_records_are_rejected_not_substituted() -> None:
    opted_out = AgoraDatasetVersion.from_record(_record(learning_eligible=False))
    observe_only = AgoraDatasetVersion.from_record(_record(dataset_kind="observe"))
    assert eligible_versions([opted_out, observe_only]) == []
    with pytest.raises(AgoraDatasetRejected):
        build_dataset_payload([opted_out, observe_only])


def test_empty_content_is_rejected() -> None:
    empty = AgoraDatasetVersion.from_record(_record(content={"note": "no trajectory here"}))
    with pytest.raises(AgoraDatasetRejected):
        build_dataset_payload([empty])


def test_payload_may_not_span_tenants() -> None:
    a = AgoraDatasetVersion.from_record(_record("dsv-a", tenant_id="tenant-a"))
    b = AgoraDatasetVersion.from_record(_record("dsv-b", tenant_id="tenant-b"))
    with pytest.raises(AgoraTenantScopeError):
        build_dataset_payload([a, b])


def test_lineage_is_content_addressed_and_never_claims_seed() -> None:
    version = AgoraDatasetVersion.from_record(_record())
    payload = build_dataset_payload([version])
    lineage = build_dataset_lineage([version], payload)

    assert lineage["authority"] == AGORA_DATASET_AUTHORITY
    assert lineage["dataset_version_ids"] == ["dsv-1"]
    assert lineage["tenant_id"] == "tenant-a"
    assert lineage["user_id"] == "user-a"
    assert lineage["evidence_ids"] == ["ev-1"]
    assert lineage["trajectory_count"] == 1
    assert lineage["seed_fallback_used"] is False
    assert len(lineage["content_sha256"]) == 64
    assert len(lineage["payload_sha256"]) == 64

    # Same content -> same digests; changed content -> changed digests.
    repeat = build_dataset_lineage([version], build_dataset_payload([version]))
    assert repeat["content_sha256"] == lineage["content_sha256"]
    mutated = AgoraDatasetVersion.from_record(
        _record(content={"observation": [9.0], "action": "sell"})
    )
    changed = build_dataset_lineage([mutated], build_dataset_payload([mutated]))
    assert changed["content_sha256"] != lineage["content_sha256"]


def test_attach_step_probabilities_feeds_the_evaluator() -> None:
    version = AgoraDatasetVersion.from_record(
        _record(
            content={
                "steps": [
                    {
                        "observation": [0.9, 0.1, -0.2],
                        "action": "buy_small",
                        "reward": 0.3,
                        "feedback_event_id": "evt-1",
                    },
                    {
                        "observation": [-0.8, 0.2, 0.55],
                        "action": "reduce_risk",
                        "reward": 0.1,
                        "feedback_event_id": "evt-2",
                    },
                ]
            }
        )
    )
    payload = build_dataset_payload([version])
    artifact = train(payload)

    probabilities = attach_step_probabilities(artifact, payload)
    assert set(artifact["policy"]["probabilities_by_step"]) == set(probabilities)
    assert {"evt-1", "evt-2"} <= set(probabilities)
    for distribution in probabilities.values():
        assert pytest.approx(sum(distribution.values()), abs=1e-9) == 1.0

    evaluation = evaluate(artifact, payload)
    assert evaluation["action_match_rate"] is not None
    assert evaluation["kl_divergence"] is not None


def test_attach_step_probabilities_is_a_no_op_without_a_policy() -> None:
    assert attach_step_probabilities({}, {"sessions": []}) == {}
    assert attach_step_probabilities({"policy": {}}, {"sessions": []}) == {}


def test_four_decision_evidence_kinds_with_snapshot_translate_and_train_bc() -> None:
    snapshot_base = {
        "decision_time": "2026-11-27T02:00:00Z",
        "available_data_cutoff": "2026-11-27T01:59:00Z",
        "symbol": "2330.TW",
        "strategy_id": "strat-tw-semi",
        "strategy_version_id": "v2",
        "strategy_spec_registry_id": "reg-tw-semi",
        "event_kind": "entry",
        "suggested_action": "enter",
        "confidence": {"value": 0.8},
        "probability": {"value": 0.65},
        "expected_value": {"net": 0.03, "downside": -0.015},
    }
    versions = [
        AgoraDatasetVersion.from_record(
            {
                **_record("dsv-dec", evidence_id="ev-lock-1", content={
                    "source_kind": "decision_lock",
                    "evidence_kind": "decision",
                    "strategy_id": "strat-tw-semi",
                    "decision_time_snapshot": snapshot_base,
                }),
                "interaction_kind": "decision",
            }
        ),
        AgoraDatasetVersion.from_record(
            {
                **_record("dsv-rej", evidence_id="ev-lock-2", content={
                    "source_kind": "decision_lock",
                    "evidence_kind": "rejection",
                    "strategy_id": "strat-tw-semi",
                    "decision_time_snapshot": {**snapshot_base, "confidence": {"value": 0.35}},
                }),
                "interaction_kind": "rejection",
            }
        ),
        AgoraDatasetVersion.from_record(
            {
                **_record("dsv-def", evidence_id="ev-jrnl-3", content={
                    "source_kind": "decision_journal",
                    "evidence_kind": "deferral",
                    "strategy_id": "strat-tw-semi",
                    "decision_time_snapshot": {**snapshot_base, "probability": {"value": 0.45}},
                }),
                "interaction_kind": "deferral",
            }
        ),
        AgoraDatasetVersion.from_record(
            {
                **_record("dsv-int", evidence_id="ev-jrnl-4", content={
                    "source_kind": "decision_journal",
                    "evidence_kind": "intervention",
                    "strategy_id": "strat-tw-semi",
                    "modifications": {"action": "reduce", "size_hint": "small"},
                    "decision_time_snapshot": {**snapshot_base, "expected_value": {"net": -0.01, "downside": -0.03}},
                }),
                "interaction_kind": "intervention",
            }
        ),
    ]

    payload = build_dataset_payload(versions, dataset_id="dsv-trader-decisions")
    lineage = build_dataset_lineage(versions, payload)

    assert payload["strategy_id"] == "strat-tw-semi"
    assert len(payload["sessions"]) == 4
    assert [s["evidence_kind"] for s in payload["sessions"]] == [
        "decision",
        "rejection",
        "deferral",
        "intervention",
    ]
    assert [s["steps"][0]["action"] for s in payload["sessions"]] == [
        "enter",
        "reject",
        "defer",
        "reduce",
    ]
    assert all(s["decision_time_snapshot"]["symbol"] == "2330.TW" for s in payload["sessions"])
    assert lineage["interaction_kinds"] == ["decision", "rejection", "deferral", "intervention"]

    artifact = train(payload)
    attach_step_probabilities(artifact, payload)
    evaluation = evaluate(artifact, payload)
    assert evaluation["action_match_rate"] is not None
    assert set(artifact["policy"]["action_labels"]) == {"enter", "reject", "defer", "reduce"}


def test_single_decision_evidence_version_trains_and_evaluates() -> None:
    single = AgoraDatasetVersion.from_record(
        {
            **_record(
                "dsv-single-dec",
                evidence_id="ev-lock-single",
                content={
                    "source_kind": "decision_lock",
                    "evidence_kind": "decision",
                    "strategy_id": "strat-tw-semi",
                    "decision_time_snapshot": {
                        "decision_time": "2026-11-27T02:00:00Z",
                        "available_data_cutoff": "2026-11-27T01:59:00Z",
                        "symbol": "2330.TW",
                        "strategy_id": "strat-tw-semi",
                        "event_kind": "entry",
                        "suggested_action": "enter",
                        "confidence": {"value": 0.8},
                        "probability": {"value": 0.65},
                        "expected_value": {"net": 0.03, "downside": -0.015},
                    },
                },
            ),
            "interaction_kind": "decision",
        }
    )
    payload = build_dataset_payload([single])
    artifact = train(payload)
    attach_step_probabilities(artifact, payload)
    evaluation = evaluate(artifact, payload)
    assert evaluation["action_match_rate"] == 1.0
