from __future__ import annotations

import sys
from pathlib import Path

import pytest


_MODULE_DIR = Path(__file__).resolve().parent
if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))

from multi_artifact_tree import (  # noqa: E402
    MultiArtifactLineageError,
    MultiArtifactLineageTree,
    get_run_artifacts,
    reset_default_tree,
)


def test_get_run_artifacts_groups_four_artifact_types_with_parent_lineage() -> None:
    run_id = "exp-run-multi-001"
    tree = MultiArtifactLineageTree(
        experiment_runs=[{"run_id": run_id}],
        artifacts=[
            {
                "artifact_id": "model-001",
                "artifact_type": "model_artifact",
                "run_id": run_id,
            },
            {
                "artifact_id": "features-001",
                "artifact_type": "feature_set",
                "experiment_run_id": run_id,
                "lineage": {"parent_run_id": run_id, "producer": "feature-store"},
            },
            {
                "artifact_id": "signals-001",
                "artifact_type": "signal_snapshot",
                "lineage": {"parent_run_id": run_id},
            },
            {
                "artifact_id": "optimizer-001",
                "artifact_type": "optimizer_result",
                "parent_run_id": run_id,
            },
        ],
    )

    groups = tree.get_run_artifacts(run_id)

    assert [group["artifact_type"] for group in groups] == [
        "model_artifact",
        "feature_set",
        "signal_snapshot",
        "optimizer_result",
    ]
    assert [group["count"] for group in groups] == [1, 1, 1, 1]

    artifact_ids_by_type = {
        group["artifact_type"]: [artifact["artifact_id"] for artifact in group["artifacts"]]
        for group in groups
    }
    assert artifact_ids_by_type == {
        "model_artifact": ["model-001"],
        "feature_set": ["features-001"],
        "signal_snapshot": ["signals-001"],
        "optimizer_result": ["optimizer-001"],
    }

    for group in groups:
        for artifact in group["artifacts"]:
            assert artifact["lineage"]["parent_run_id"] == run_id
            assert artifact["lineage"]["edge_type"] == f"{artifact['artifact_type']}.experiment_run"


def test_get_run_artifacts_supports_evaluation_result_type() -> None:
    run_id = "exp-run-eval-001"
    tree = MultiArtifactLineageTree(
        experiment_runs=[{"run_id": run_id}],
        artifacts=[
            {
                "artifact_id": "eval-001",
                "artifact_type": "evaluation_result",
                "run_id": run_id,
            }
        ],
    )

    groups = tree.get_run_artifacts(run_id)

    assert groups == [
        {
            "artifact_type": "evaluation_result",
            "count": 1,
            "artifacts": [
                {
                    "artifact_id": "eval-001",
                    "artifact_type": "evaluation_result",
                    "run_id": run_id,
                    "lineage": {
                        "parent_run_id": run_id,
                        "edge_type": "evaluation_result.experiment_run",
                    },
                }
            ],
        }
    ]


def test_module_level_get_run_artifacts_reads_default_tree() -> None:
    run_id = "exp-run-default-001"
    reset_default_tree(
        experiment_runs=[{"run_id": run_id}],
        artifacts=[
            {
                "artifact_id": "model-default-001",
                "artifact_type": "model_artifact",
                "run_id": run_id,
            }
        ],
    )

    groups = get_run_artifacts(run_id)

    assert len(groups) == 1
    assert groups[0]["artifact_type"] == "model_artifact"
    assert groups[0]["artifacts"][0]["lineage"]["parent_run_id"] == run_id


def test_artifact_lineage_parent_must_match_record_parent() -> None:
    tree = MultiArtifactLineageTree(experiment_runs=[{"run_id": "exp-run-001"}])

    with pytest.raises(MultiArtifactLineageError, match="parent_run_id_mismatch"):
        tree.add_artifact(
            {
                "artifact_id": "model-bad-parent",
                "artifact_type": "model_artifact",
                "run_id": "exp-run-001",
                "lineage": {"parent_run_id": "other-run"},
            }
        )


def test_unknown_artifact_type_is_rejected() -> None:
    tree = MultiArtifactLineageTree(experiment_runs=[{"run_id": "exp-run-001"}])

    with pytest.raises(MultiArtifactLineageError, match="unsupported_artifact_type"):
        tree.add_artifact(
            {
                "artifact_id": "unknown-001",
                "artifact_type": "temporary_blob",
                "run_id": "exp-run-001",
            }
        )


def test_unknown_run_returns_empty_group_list() -> None:
    tree = MultiArtifactLineageTree(
        experiment_runs=[{"run_id": "exp-run-001"}],
        artifacts=[
            {
                "artifact_id": "model-001",
                "artifact_type": "model_artifact",
                "run_id": "exp-run-001",
            }
        ],
    )

    assert tree.get_run_artifacts("missing-run") == []
