"""ExperimentRun multi-artifact lineage grouping.

This module is intentionally independent from the HTTP wrapper in main.py. It
normalizes read-side artifact nodes for one ExperimentRun and makes the
ExperimentRun -> N artifact relationship explicit across supported artifact
types.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any, Iterable, Mapping


SUPPORTED_ARTIFACT_TYPES = (
    "model_artifact",
    "feature_set",
    "signal_snapshot",
    "optimizer_result",
    "evaluation_result",
)


class MultiArtifactLineageError(ValueError):
    """Raised when an artifact cannot be attached to one ExperimentRun."""


class MultiArtifactLineageTree:
    """In-memory read model for artifacts produced by ExperimentRun records."""

    def __init__(
        self,
        *,
        experiment_runs: Iterable[Mapping[str, Any]] | None = None,
        artifacts: Iterable[Mapping[str, Any]] | None = None,
    ) -> None:
        self._known_run_ids: set[str] = set()
        self._artifacts_by_run_and_type: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
            lambda: defaultdict(list)
        )

        for run in experiment_runs or ():
            self.add_experiment_run(run)
        for artifact in artifacts or ():
            self.add_artifact(artifact)

    def add_experiment_run(self, run: Mapping[str, Any]) -> str:
        """Register one ExperimentRun id and return the normalized id."""

        run_id = _first_text(run, "run_id", "experiment_run_id", "experiment_id", "id")
        if run_id is None:
            raise MultiArtifactLineageError("experiment_run_id_missing")
        self._known_run_ids.add(run_id)
        return run_id

    def add_artifact(self, artifact: Mapping[str, Any]) -> dict[str, Any]:
        """Attach one artifact node to its parent ExperimentRun."""

        normalized = _normalize_artifact(artifact)
        parent_run_id = normalized["lineage"]["parent_run_id"]
        if self._known_run_ids and parent_run_id not in self._known_run_ids:
            raise MultiArtifactLineageError("parent_experiment_run_not_found")
        artifact_type = normalized["artifact_type"]
        self._artifacts_by_run_and_type[parent_run_id][artifact_type].append(normalized)
        return deepcopy(normalized)

    def get_run_artifacts(self, experiment_run_id: str) -> list[dict[str, Any]]:
        """Return artifact records grouped by artifact_type for one run."""

        run_id = _require_text(experiment_run_id, "experiment_run_id_missing")
        grouped = self._artifacts_by_run_and_type.get(run_id, {})
        result: list[dict[str, Any]] = []
        for artifact_type in SUPPORTED_ARTIFACT_TYPES:
            artifacts = grouped.get(artifact_type, [])
            if not artifacts:
                continue
            result.append(
                {
                    "artifact_type": artifact_type,
                    "count": len(artifacts),
                    "artifacts": deepcopy(artifacts),
                }
            )
        return result


_default_tree = MultiArtifactLineageTree()


def reset_default_tree(
    *,
    experiment_runs: Iterable[Mapping[str, Any]] | None = None,
    artifacts: Iterable[Mapping[str, Any]] | None = None,
) -> MultiArtifactLineageTree:
    """Replace the module-level tree used by get_run_artifacts()."""

    global _default_tree
    _default_tree = MultiArtifactLineageTree(
        experiment_runs=experiment_runs,
        artifacts=artifacts,
    )
    return _default_tree


def register_experiment_run(run: Mapping[str, Any]) -> str:
    """Register one ExperimentRun in the module-level tree."""

    return _default_tree.add_experiment_run(run)


def register_artifact(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Register one artifact in the module-level tree."""

    return _default_tree.add_artifact(artifact)


def get_run_artifacts(experiment_run_id: str) -> list[dict[str, Any]]:
    """Return default-tree artifacts grouped by artifact_type for one run."""

    return _default_tree.get_run_artifacts(experiment_run_id)


def _normalize_artifact(artifact: Mapping[str, Any]) -> dict[str, Any]:
    artifact_type = _first_text(artifact, "artifact_type", "type")
    if artifact_type not in SUPPORTED_ARTIFACT_TYPES:
        raise MultiArtifactLineageError("unsupported_artifact_type")

    artifact_id = _first_text(artifact, "artifact_id", "registry_id", "id")
    if artifact_id is None:
        raise MultiArtifactLineageError("artifact_id_missing")

    lineage = artifact.get("lineage")
    lineage_mapping = lineage if isinstance(lineage, Mapping) else {}
    lineage_parent_run_id = _text(lineage_mapping.get("parent_run_id"))
    record_parent_run_id = _first_text(artifact, "run_id", "experiment_run_id", "parent_run_id")

    if lineage_parent_run_id and record_parent_run_id and lineage_parent_run_id != record_parent_run_id:
        raise MultiArtifactLineageError("parent_run_id_mismatch")

    parent_run_id = lineage_parent_run_id or record_parent_run_id
    if parent_run_id is None:
        raise MultiArtifactLineageError("parent_run_id_missing")

    normalized = deepcopy(dict(artifact))
    normalized["artifact_id"] = artifact_id
    normalized["artifact_type"] = artifact_type
    normalized.setdefault("run_id", parent_run_id)

    normalized_lineage = deepcopy(dict(lineage_mapping))
    normalized_lineage["parent_run_id"] = parent_run_id
    normalized_lineage.setdefault("edge_type", f"{artifact_type}.experiment_run")
    normalized["lineage"] = normalized_lineage
    return normalized


def _first_text(record: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _text(record.get(key))
        if value is not None:
            return value
    return None


def _require_text(value: Any, reason: str) -> str:
    text = _text(value)
    if text is None:
        raise MultiArtifactLineageError(reason)
    return text


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
