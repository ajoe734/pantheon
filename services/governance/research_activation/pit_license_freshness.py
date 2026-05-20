"""Research artifact admission gate for draft/candidate-only artifacts.

The filename is retained for the RES-ACT-002-V2 task artifact, but the active
scope is the re-specified admission gate: research artifacts may enter this
boundary only while they are ``artifact_state=draft`` or
``artifact_state=candidate`` and while their deployment stage remains ``none``.
PIT/license/freshness proofing is owned by RES-ACT-003-V2.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "ResearchArtifactAdmissionGate.v1"
ALLOWED_ADMISSION_ARTIFACT_STATES = frozenset({"draft", "candidate"})
REQUIRED_DEPLOYMENT_STAGE = "none"

_ARTIFACT_STATE_PATHS: tuple[tuple[str, ...], ...] = (
    ("artifact_state",),
    ("entry", "artifact_state"),
    ("registry_entry", "artifact_state"),
    ("candidate_artifact", "artifact_state"),
    ("candidate_artifact", "entry", "artifact_state"),
)

_DEPLOYMENT_STAGE_PATHS: tuple[tuple[str, ...], ...] = (
    ("deployment_stage",),
    ("deployment_summary", "current_stage"),
    ("metadata", "deployment_stage"),
    ("entry", "deployment_stage"),
    ("entry", "deployment_summary", "current_stage"),
    ("entry", "metadata", "deployment_stage"),
    ("registry_entry", "deployment_stage"),
    ("registry_entry", "deployment_summary", "current_stage"),
    ("registry_entry", "metadata", "deployment_stage"),
    ("candidate_artifact", "deployment_stage"),
    ("candidate_artifact", "deployment_summary", "current_stage"),
    ("candidate_artifact", "metadata", "deployment_stage"),
    ("candidate_artifact", "entry", "deployment_stage"),
    ("candidate_artifact", "entry", "deployment_summary", "current_stage"),
    ("candidate_artifact", "entry", "metadata", "deployment_stage"),
)


class ResearchArtifactAdmissionGateError(ValueError):
    """Raised when a research artifact fails the admission gate."""


@dataclass(frozen=True)
class AdmissionGateIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class AdmissionGateResult:
    passed: bool
    artifact_state: str | None
    deployment_stage: str | None
    errors: tuple[AdmissionGateIssue, ...] = ()
    warnings: tuple[AdmissionGateIssue, ...] = ()
    schema_version: str = SCHEMA_VERSION
    gate: str = "research_artifact_admission"
    allowed_artifact_states: tuple[str, ...] = field(
        default_factory=lambda: tuple(sorted(ALLOWED_ADMISSION_ARTIFACT_STATES))
    )
    required_deployment_stage: str = REQUIRED_DEPLOYMENT_STAGE

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "gate": self.gate,
            "passed": self.passed,
            "artifact_state": self.artifact_state,
            "deployment_stage": self.deployment_stage,
            "allowed_artifact_states": list(self.allowed_artifact_states),
            "required_deployment_stage": self.required_deployment_stage,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }

    def assert_passed(self) -> "AdmissionGateResult":
        if not self.passed:
            rendered = "; ".join(
                f"{issue.code} at {issue.path}: {issue.message}"
                for issue in self.errors
            )
            raise ResearchArtifactAdmissionGateError(
                f"Research artifact admission gate failed: {rendered}"
            )
        return self


def validate_research_artifact_admission_gate(
    artifact: Any,
    *,
    path: str = "artifact",
) -> AdmissionGateResult:
    """Validate the re-specified research artifact admission boundary.

    The validator is intentionally generic: callers may pass a plain mapping,
    a registry ``RegistryEntryView`` dataclass, or a packet with a nested
    ``candidate_artifact`` object. Any explicit deployment stage discovered in
    the supported paths must be ``none``.
    """

    root = _mapping(artifact)
    errors: list[AdmissionGateIssue] = []
    warnings: list[AdmissionGateIssue] = []
    if not root:
        errors.append(
            _issue(
                "invalid_artifact_payload",
                path,
                "artifact must be a mapping or dataclass-like registry payload",
            )
        )
        return AdmissionGateResult(
            passed=False,
            artifact_state=None,
            deployment_stage=None,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    artifact_state = _first_token(root, _ARTIFACT_STATE_PATHS)
    if artifact_state is None:
        errors.append(
            _issue(
                "missing_artifact_state",
                path,
                "artifact_state is required and must be draft or candidate",
            )
        )
    elif artifact_state not in ALLOWED_ADMISSION_ARTIFACT_STATES:
        errors.append(
            _issue(
                "forbidden_artifact_state",
                f"{path}.{_first_existing_path(root, _ARTIFACT_STATE_PATHS) or 'artifact_state'}",
                "research admission accepts only draft or candidate artifacts",
            )
        )

    stage_values = _path_tokens(root, _DEPLOYMENT_STAGE_PATHS)
    deployment_stage = _canonical_stage(stage_values)
    if not stage_values:
        errors.append(
            _issue(
                "missing_deployment_stage",
                path,
                "deployment_stage must be explicitly none at admission",
            )
        )
    else:
        distinct_stages = {value for _, value in stage_values}
        if len(distinct_stages) > 1:
            errors.append(
                _issue(
                    "conflicting_deployment_stage",
                    path,
                    "deployment stage projections disagree: "
                    + ", ".join(
                        f"{stage_path}={value}" for stage_path, value in stage_values
                    ),
                )
            )
        if any(value != REQUIRED_DEPLOYMENT_STAGE for _, value in stage_values):
            bad = [
                f"{stage_path}={value}"
                for stage_path, value in stage_values
                if value != REQUIRED_DEPLOYMENT_STAGE
            ]
            errors.append(
                _issue(
                    "deployment_stage_not_none",
                    path,
                    "research admission requires deployment_stage=none; got "
                    + ", ".join(bad),
                )
            )

    return AdmissionGateResult(
        passed=not errors,
        artifact_state=artifact_state,
        deployment_stage=deployment_stage,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def assert_research_artifact_admissible(
    artifact: Any,
    *,
    path: str = "artifact",
) -> AdmissionGateResult:
    """Validate and raise ``ResearchArtifactAdmissionGateError`` on failure."""

    return validate_research_artifact_admission_gate(artifact, path=path).assert_passed()


def _issue(code: str, path: str, message: str) -> AdmissionGateIssue:
    return AdmissionGateIssue(code=code, path=path, message=message)


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if is_dataclass(value):
        return asdict(value)
    return {}


def _token(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        value = value.value
    text = str(value).strip().lower()
    return text or None


def _path_value(root: Mapping[str, Any], path: Sequence[str]) -> Any:
    current: Any = root
    for part in path:
        current_mapping = _mapping(current)
        if part not in current_mapping:
            return None
        current = current_mapping[part]
    return current


def _path_tokens(
    root: Mapping[str, Any],
    paths: Sequence[Sequence[str]],
) -> tuple[tuple[str, str], ...]:
    values: list[tuple[str, str]] = []
    for raw_path in paths:
        token = _token(_path_value(root, raw_path))
        if token is not None:
            values.append((".".join(raw_path), token))
    return tuple(values)


def _first_token(
    root: Mapping[str, Any],
    paths: Sequence[Sequence[str]],
) -> str | None:
    values = _path_tokens(root, paths)
    if not values:
        return None
    return values[0][1]


def _first_existing_path(
    root: Mapping[str, Any],
    paths: Sequence[Sequence[str]],
) -> str | None:
    values = _path_tokens(root, paths)
    if not values:
        return None
    return values[0][0]


def _canonical_stage(stage_values: Sequence[tuple[str, str]]) -> str | None:
    if not stage_values:
        return None
    distinct = {value for _, value in stage_values}
    if len(distinct) == 1:
        return next(iter(distinct))
    return None


__all__ = [
    "ALLOWED_ADMISSION_ARTIFACT_STATES",
    "REQUIRED_DEPLOYMENT_STAGE",
    "SCHEMA_VERSION",
    "AdmissionGateIssue",
    "AdmissionGateResult",
    "ResearchArtifactAdmissionGateError",
    "assert_research_artifact_admissible",
    "validate_research_artifact_admission_gate",
]
