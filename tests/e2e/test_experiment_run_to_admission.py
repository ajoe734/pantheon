"""OODA E2E #3: ExperimentRun -> CandidateArtifact -> Registry admission test.

Orient->Decide stage: proves that a completed ExperimentRun can be written
back to the registry as a CandidateArtifact with correct lineage, and that
the admission gate rejects malformed evaluation_summary inputs.

Dependencies: EXP-005 (registry_writeback) + REG-002 (RegistryService/split_api).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from services.registry.models import ArtifactState
from services.registry.split_api import RegistryService
from services.registry.storage import RegistryStore
from services.research.experiments.models import ExperimentRun
from services.research.experiments.registry_writeback import (
    ExperimentRegistryWritebackError,
    write_experiment_run_artifact_to_registry,
)

_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "experiment_run_for_admission.json"


def _load_fixture() -> dict[str, Any]:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _make_service() -> RegistryService:
    return RegistryService(RegistryStore())


def _admission_gate(
    run_dict: dict[str, Any],
    artifact: dict[str, Any],
    *,
    service: RegistryService,
    source_strategy_spec_id: str | None = None,
) -> Any:
    """Admission gate: validates evaluation_summary then writes to registry.

    Raises ValueError when evaluation_summary is provided but is not a Mapping
    (e.g., a bare string or list), which is structurally malformed for admission.
    """
    eval_summary = artifact.get("evaluation_summary")
    if eval_summary is not None and not isinstance(eval_summary, Mapping):
        raise ValueError(
            "Admission gate rejected: evaluation_summary must be a mapping when provided; "
            f"got {type(eval_summary).__name__!r}"
        )
    run = ExperimentRun.from_dict(run_dict)
    return write_experiment_run_artifact_to_registry(
        run,
        artifact,
        registry_service=service,
        source_strategy_spec_id=source_strategy_spec_id,
    )


def test_candidate_artifact_registered_with_candidate_state() -> None:
    """Writeback registers entry with artifact_state=candidate, not draft or approved."""
    fixture = _load_fixture()
    service = _make_service()

    view = _admission_gate(
        fixture["experiment_run"],
        fixture["artifact"],
        service=service,
        source_strategy_spec_id=fixture["source_strategy_spec_id"],
    )

    assert view.entry.artifact_state == ArtifactState.CANDIDATE, (
        f"Expected candidate, got {view.entry.artifact_state.value!r}"
    )
    assert view.entry.artifact_state != ArtifactState.DRAFT
    assert view.entry.artifact_state != ArtifactState.APPROVED


def test_lineage_refs_include_experiment_run_id_and_source_strategy_spec_id() -> None:
    """CandidateArtifact lineage links back to both the ExperimentRun and the StrategySpec."""
    fixture = _load_fixture()
    run_dict = fixture["experiment_run"]
    service = _make_service()

    view = _admission_gate(
        run_dict,
        fixture["artifact"],
        service=service,
        source_strategy_spec_id=fixture["source_strategy_spec_id"],
    )

    lineage = view.entry.lineage
    assert view.entry.producer_run_id == run_dict["run_id"], (
        f"producer_run_id {view.entry.producer_run_id!r} must match run_id {run_dict['run_id']!r}"
    )
    assert lineage.source_run_ids and run_dict["run_id"] in lineage.source_run_ids, (
        f"lineage.source_run_ids {lineage.source_run_ids!r} must include experiment run_id"
    )
    assert lineage.source_strategy_spec_id is not None, (
        "lineage.source_strategy_spec_id must be set for candidate admission"
    )
    assert fixture["source_strategy_spec_id"] in lineage.source_strategy_spec_id, (
        f"source_strategy_spec_id {lineage.source_strategy_spec_id!r} must reference fixture spec id"
    )


def test_admission_gate_passes_for_valid_evaluation_summary() -> None:
    """Admission gate accepts ExperimentRun whose artifact carries a valid evaluation_summary mapping."""
    fixture = _load_fixture()
    artifact = dict(fixture["artifact"])
    artifact["evaluation_summary"] = {
        "sharpe_ratio": 1.42,
        "ic_mean": 0.068,
        "max_drawdown": -0.12,
    }
    service = _make_service()

    view = _admission_gate(
        fixture["experiment_run"],
        artifact,
        service=service,
        source_strategy_spec_id=fixture["source_strategy_spec_id"],
    )

    assert view.entry.artifact_state == ArtifactState.CANDIDATE
    assert view.entry.evaluation_summary is not None
    assert view.entry.evaluation_summary.get("sharpe_ratio") == 1.42


def test_admission_gate_rejects_malformed_evaluation_summary() -> None:
    """Admission gate rejects when evaluation_summary is not a mapping (e.g., a bare string)."""
    fixture = _load_fixture()
    artifact = dict(fixture["artifact"])
    artifact["evaluation_summary"] = "invalid-not-a-dict"
    service = _make_service()

    with pytest.raises(ValueError, match="evaluation_summary must be a mapping"):
        _admission_gate(
            fixture["experiment_run"],
            artifact,
            service=service,
        )
