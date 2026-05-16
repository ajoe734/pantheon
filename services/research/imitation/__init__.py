"""services/research/imitation contracts and dataset tooling."""

from .dataset_builder import (
    DatasetBuildRequest,
    DatasetBuildResult,
    DatasetBuilderError,
    ImitationDatasetBuilder,
    ImitationDatasetBuilderConfig,
    RawTrajectorySession,
    TrajectoryStep,
    build_dataset,
)
from .preference_models import (
    CorrectionOperation,
    CorrectionOperationType,
    CorrectionTrace,
    GovernedTrainingTarget,
    PreferenceAction,
    PreferenceExample,
    PreferenceValidationError,
    validate_correction_trace_payload,
    validate_preference_example_against_correction_trace,
    validate_preference_example_payload,
)

__all__ = [
    "CorrectionOperation",
    "CorrectionOperationType",
    "CorrectionTrace",
    "DatasetBuildRequest",
    "DatasetBuildResult",
    "DatasetBuilderError",
    "GovernedTrainingTarget",
    "ImitationDatasetBuilder",
    "ImitationDatasetBuilderConfig",
    "PreferenceAction",
    "PreferenceExample",
    "PreferenceValidationError",
    "RawTrajectorySession",
    "TrajectoryStep",
    "build_dataset",
    "validate_correction_trace_payload",
    "validate_preference_example_against_correction_trace",
    "validate_preference_example_payload",
]
